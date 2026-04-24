# skypilot-llms

Cheap, on-demand cloud GPU running an OpenAI-compatible vLLM endpoint, reachable from any tool via a stable public URL.

One `skyllm up` spins up a 24 GB+ NVIDIA GPU on RunPod, starts vLLM's OpenAI-compatible server, and exposes it through a Cloudflare Tunnel at a hostname you control. Clients point at `https://llm.yourdomain.com/v1` forever — the actual GPU comes and goes, the URL stays.

## Why

Run bigger models than your local GPU can handle, without paying for a 24/7 cloud instance. Designed for occasional home use: spin up, poke at a model for an hour, tear down, pay cents.

## Stack

| Piece | What it does |
|---|---|
| **SkyPilot** | Provisions the GPU on RunPod, handles autostop/teardown |
| **vLLM** | Serves the model with an OpenAI-compatible API. Pod runs `vllm/vllm-openai:latest` directly — no custom Docker image to maintain. |
| **Cloudflare Tunnel** | Gives you a stable public URL without opening ports |

For llama.cpp / GGUF workflows, see the companion `llama-router` project — this repo is vLLM-native for cloud. See [`docs/alternatives.md`](docs/alternatives.md) for why the split.

## Safeguards against surprise bills

Belt, suspenders, and a third belt:

1. **Idle auto-shutdown.** `scripts/idle-watch.sh` watches vLLM's Prometheus metrics (`vllm:generation_tokens_total`); when no tokens have been generated for `$IDLE_MINUTES` (default 15), it exits the SkyPilot run block. Combined with `sky launch --down`, this terminates the cluster.
2. **Wall-clock cap.** `sudo shutdown -h +$MAX_RUNTIME_MINUTES` runs at launch (4 h default on `sky.yaml`, 1 h on the 80 GB preset since hourly rates are several × higher). Even if the idle-watcher wedges, the box powers off.
3. **SkyPilot autostop.** `--idle-minutes-to-autostop 30 --down` tells SkyPilot itself to terminate the cluster if the whole job finishes and nothing takes its place.
4. **Monthly budget check.** `scripts/budget-check.sh` is cron-able on your laptop; it reads `sky cost-report` and runs `sky down` if you've spent over `$MONTHLY_BUDGET_USD` this month.
5. **Provider-side spend limit** (*the real backstop*). Set a hard monthly limit at <https://www.runpod.io/console/user/billing>. The other safeguards protect against mistakes; this one protects against bugs in the other safeguards.

## Setup

### Prerequisites

- A domain managed by Cloudflare (free CF account + ~$10/yr registration).
- A RunPod account with a payment method and an API key.
- Python 3.10+ and Docker locally (for the SkyPilot CLI).
- [pixi](https://pixi.sh/) for the local CLI environment (single static binary).

### 1. Install SkyPilot and the `skyllm` CLI

```bash
# SkyPilot CLI + RunPod provider — puts `sky` on your PATH.
pip install "skypilot[runpod]"
# Provide the RunPod API key when prompted (or export RUNPOD_API_KEY first).
sky check runpod

# The repo's own CLI (local env; no CUDA). The root pixi.toml has `cli` as
# its default env, so no `-e` flag is needed. This creates the `skyllm`
# entry point inside the pixi env.
pixi install
```

Run the CLI as `pixi run skyllm <cmd>`, or drop into the env once
with `pixi shell` and then call bare `skyllm`. The pod-side pixi workspace
(`pod/pixi.toml` + `pod/pixi.lock`) is deliberately isolated from this
one — see "Layout" below.

### 2. Create a Cloudflare Tunnel

1. <https://one.dash.cloudflare.com/> → **Networks** → **Tunnels** → **Create a tunnel**.
2. Connector type: **Cloudflared**.
3. Name it something like `llm-gpu`.
4. Under **Public Hostname**, add:
   - Subdomain: `llm` (or whatever you want — matches `LLM_HOSTNAME` in `.env`)
   - Domain: *your CF-managed domain*
   - Service: `HTTP` → `localhost:8080`
5. Copy the **tunnel token** shown under "Install and run a connector" → paste into `.env` as `CF_TUNNEL_TOKEN`.

CF auto-creates the DNS record for you. The hostname is now *permanently* pointed at whichever machine is running `cloudflared` with that token.

### 3. Fill in `.env`

```bash
cp .env.example .env
# Edit .env — at minimum set LLM_HOSTNAME, CF_TUNNEL_TOKEN, LLM_API_KEY, RUNPOD_API_KEY
```

Generate `LLM_API_KEY` with `openssl rand -hex 32`.

> ⚠️ **`LLM_API_KEY` is the only thing gating your endpoint from the public internet.** A Cloudflare Tunnel routes `https://llm.yourdomain.com/` to your pod but does *not* authenticate clients at the CF edge — anyone who resolves the hostname can probe it. llama-server rejects anything without the bearer, so a strong random key (the `openssl` command above produces 256 bits of entropy) is what keeps scanners out. Do **not** use a short memorable string. If you want edge-level auth (Cloudflare Access, etc.) on top, see [`docs/roadmap/edge-auth.md`](docs/roadmap/edge-auth.md).

### 4. Set a RunPod spend limit

Non-optional. Go to <https://www.runpod.io/console/user/billing> and cap monthly spend at whatever you're willing to lose if everything else breaks. $20/mo is plenty for occasional home use.

### 5. Launch

```bash
# Default model (qwen-0.5b, vLLM, 24 GB tier) — fast stack-test.
pixi run skyllm up

# Or pick any entry from `skyllm list`:
pixi run skyllm up qwen3-coder-next
```

First launch takes ~5 minutes (provisioning + image pull + model download). Subsequent cold launches re-pay the model download unless you've configured an HF cache bucket (see [Bigger models](#bigger-models)). The `vllm/vllm-openai` image is ~10 GB — first pull is slow, cached thereafter by RunPod.

### 6. Use it

```bash
# From anywhere on the internet:
curl https://llm.yourdomain.com/v1/chat/completions \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "any", "messages": [{"role": "user", "content": "hi"}]}'
```

Any OpenAI-SDK client works:

```python
from openai import OpenAI
client = OpenAI(base_url="https://llm.yourdomain.com/v1", api_key="<your LLM_API_KEY>")
```

### 7. Tear down when done

```bash
pixi run skyllm down
```

If you forget, the safeguards kick in. But `skyllm down` is instant and saves you pennies per minute.

## Daily use

All commands below are `pixi run skyllm <cmd>` (drop the `pixi run` prefix inside `pixi shell`). `cli` is the default pixi env at the repo root — no `-e <name>` ever needed.

| Command | What it does |
|---|---|
| `skyllm --help` | List all commands |
| `skyllm list` | Show catalog entries (name / engine / tier / HF repo) |
| `skyllm up [<model>]` | Launch GPU + start serving. Default model: `qwen-0.5b`. `--dry-run` prints the resolved `sky launch` command |
| `skyllm down` | Terminate cluster |
| `skyllm status` | Is it running? |
| `skyllm logs` | Tail engine + cloudflared logs |
| `skyllm health` | Hit the public URL and confirm it responds |
| `skyllm cost` | SkyPilot's running cost report |
| `skyllm budget` | Run the budget guard once (also cron-able) |

Model identity comes from the catalog (`models/<name>/model.yaml`), not `.env`. Use `skyllm list` to see what's available, or add a new entry (any directory under `models/` with a `model.yaml` conforming to `skyllm/schema.py` is auto-discovered).

## Bigger models

The default `qwen-0.5b` catalog entry is a 0.5B toy model — fine for testing the pipeline, useless for real work. To launch something bigger, either pick an existing catalog entry:

```bash
pixi run skyllm up qwen3-coder-next   # 80B MoE on 24 GB + CPU offload, llama.cpp
```

…or add a new model by dropping a `models/<name>/model.yaml` in the catalog (`pixi run validate` checks the schema). Gated HF models (Llama, Gemma, Mistral-Instruct, etc.) need `HF_TOKEN=...` in `.env`. `skyllm down && skyllm up <name>` to apply.

**If the model is > a few GB**, the re-download on every launch gets annoying. Add an HF cache bucket:

1. Pick any S3/GCS-compatible bucket you control (Cloudflare R2 is cheap and you already have a CF account).
2. Add to whichever preset YAML the catalog entry resolves to (one of the files in `sky/`):
   ```yaml
   file_mounts:
     ~/.cache/huggingface:
       name: <your-bucket-name>
       store: r2   # or s3, gcs
       mode: MOUNT
   ```
3. First launch caches the download into the bucket; subsequent launches mount the bucket and skip the download.

### Engine presets

Four sibling YAMLs cover the `(engine, tier)` matrix. `skyllm up <model>` picks the right one from each catalog entry's `engine` + `tier` fields:

| YAML | Engine | GPU tier | Used when the catalog entry has… |
|---|---|---|---|
| `sky.yaml` | vLLM | 24 GB | `engine: vllm`, `tier: 24gb` (default for stack-test) |
| `sky-llamacpp.yaml` | llama.cpp | 24 GB | `engine: llamacpp`, `tier: 24gb` |
| `sky-llamacpp-cpumoe.yaml` | llama.cpp | 24 GB + CPU-offloaded MoE | `engine: llamacpp`, `tier: 24gb-cpumoe` |
| `sky-llamacpp-80gb.yaml` | llama.cpp | 80 GB pure-GPU | `engine: llamacpp`, `tier: 80gb` |

See `docs/alternatives.md` for why we don't pin a custom Docker image on RunPod.

### Scaling up to bigger GPUs

The 24 GB tier (RTX 3090/4090/A5000/A6000/L40S) is fine for models up to ~14B at Q4 or ~7B at Q8. For bigger MoE models, two paths are wired up — pick based on cost vs. speed:

- `tier: 24gb-cpumoe` — cheap 24 GB card + ~96 GB system RAM, expert weights offloaded to CPU. Order-of-magnitude slower than pure-GPU but 3–5× cheaper per hour and far better availability. Good for correctness smoke tests.
- `tier: 80gb` — A100-80GB or H100, everything in VRAM. Fast (~100 tok/s gen on Qwen3-Coder-Next MXFP4) but several × more expensive and availability-constrained.

```bash
pixi run skyllm up qwen3-coder-next        # cpumoe route
pixi run skyllm up qwen3-coder-next-80gb   # pure-GPU route
```

The 80 GB preset ships with a shorter `MAX_RUNTIME_MINUTES` default (60 vs 240) because hourly costs are several × higher — an overnight wedge on H100 is a $200+ mistake. Everything else (tunnel, auth, idle-watch, budget-check) is identical.

Rough fit table. All prices are for RunPod Secure Cloud (SkyPilot's RunPod catalog [is Secure-Cloud-only by design](https://github.com/skypilot-org/skypilot/blob/master/sky/catalog/data_fetchers/fetch_runpod.py#L576-L578), so there's no "random host with root" in the data path — just RunPod itself):

| Tier | GPU options | Models that fit | ~$/hr |
|---|---|---|---|
| `24gb` | 3090/4090/A5000/A6000/L40S | ≤8B FP16, ≤13B FP8/AWQ/GPTQ (vLLM); small GGUFs (llama.cpp) | 0.50–1.20 |
| `24gb-cpumoe` | same, + 96 GB RAM floor | Big MoE GGUFs (e.g. 80B/3B-active at MXFP4) with experts in CPU RAM | 0.80–1.20 |
| `80gb` | A100-80GB / H100 | Large GGUFs up to ~50 GB pure-GPU | 1.40–4.50 |

**Multi-node** (8+ GPUs across boxes) is out of scope — rarely needed since even 405B models fit on a single 4× or 8× H100 box.

## Multi-provider (unlock if you want)

v1 targets RunPod because it's simplest. To have SkyPilot pick the cheapest GPU across providers:

1. Run `sky check` for each provider you want (`aws`, `gcp`, `lambda`, `vast`, etc.) — fill in creds as prompted.
2. Edit the preset YAML your catalog entry resolves to (e.g. `sky.yaml`):
   ```yaml
   resources:
     # remove: cloud: runpod
     accelerators: {RTX4090:1, RTX3090:1, L4:1, A10:1, A10G:1, L40S:1}
   ```
3. SkyPilot will try providers in cheapest-first order.

## Migrating to FRP (v2)

The Cloudflare Tunnel in v1 terminates TLS at Cloudflare's edge — CF has the plaintext of every request. For an LLM API where the prompts *are* the sensitive content, that may not be what you want long-term.

The migration path is intentionally small:

1. Stand up a $5/mo VPS (Hetzner, Vultr, Oracle Free Tier) with a public IP.
2. Install `frps` on the VPS and `caddy` in front of it. Use `caddy/Caddyfile.placeholder` as a starting point.
3. In `sky.yaml`, swap the `cloudflared` docker block for `frpc` pointing at your VPS.
4. In Cloudflare DNS, change `llm.yourdomain.com` from the tunnel CNAME to an A-record pointing at your VPS's IP.
5. **Clients change nothing.** Same URL, same API key, same everything.

This is the reason we used a stable hostname from day one.

## Privacy note

Even with FRP, your VPS provider can see plaintext traffic unless you also arrange end-to-end TLS (e.g. by having `frpc` speak HTTPS to a self-signed cert on the origin and letting Caddy act as a pure TCP pass-through). For the threat model "I don't want Cloudflare Inc. reading my prompts" the FRP swap is sufficient. For the threat model "I don't want my VPS provider reading my prompts either," pick a VPS provider you trust and/or do E2E.

Tailscale + WireGuard is the only configuration in this repo's design space that's end-to-end encrypted by architecture, but it requires every client device to run the Tailscale daemon — which is why it wasn't picked here.

## Layout

Two pixi workspaces, kept deliberately separate:

- **Root (`pixi.toml` + `pixi.lock`)** — the `cli` env, used locally. No CUDA. This is what `pixi install` / `pixi run skyllm` / `pixi run validate` use.
- **`pod/pixi.toml` + `pod/pixi.lock`** — the `vllm` + `llamacpp` envs that run on RunPod. Nothing else from this repo is ever uploaded to the pod; each sky YAML's `file_mounts:` allowlist rsyncs only `pod/pixi.toml`, `pod/pixi.lock`, and `scripts/idle-watch.sh`. This prevents accidental secret leakage (stray files, `.env`, scratch work) from ever riding up with the workdir.

```
skypilot-llms/
├── .env.example              # secrets + infra knobs (no model identity)
├── .gitignore
├── README.md                 # you are here
├── pyproject.toml            # skyllm package + `skyllm` entry point
├── pixi.toml / pixi.lock     # LOCAL — cli env (default)
├── pod/
│   ├── pixi.toml             # POD — vllm + llamacpp envs
│   └── pixi.lock
├── sky/                          # SkyPilot preset YAMLs (one per (engine, tier))
│   ├── sky.yaml                  # vLLM, 24 GB tier
│   ├── sky-llamacpp.yaml         # llama.cpp, 24 GB tier (small GGUFs)
│   ├── sky-llamacpp-cpumoe.yaml  # llama.cpp, 24 GB + CPU-offloaded MoE experts
│   └── sky-llamacpp-80gb.yaml    # llama.cpp, 80 GB pure-GPU (A100-80GB / H100)
├── skyllm/                       # CLI + catalog schema
│   ├── cli.py                    # list / up / down / status / logs / health / cost / budget
│   ├── schema.py                 # pydantic ModelSpec
│   └── validate.py               # `pixi run validate`
├── models/                       # model catalog — one dir per entry
│   ├── qwen-0.5b/model.yaml
│   ├── qwen3-coder-next/model.yaml        # 24gb-cpumoe route
│   └── qwen3-coder-next-80gb/model.yaml   # 80gb pure-GPU route
├── docs/
│   ├── alternatives.md       # why not SkyServe / dstack
│   ├── landscape.md          # commercial / open-source competitors
│   ├── pixi.md               # pixi env shape + RunPod lessons
│   ├── roadmap/              # phased plan (pixi → catalog → CLI → multi-provider)
│   └── toc.md                # repo tour
├── scripts/
│   ├── idle-watch.sh         # exits the run block when the engine is idle
│   └── budget-check.sh       # cron-able spend guard
└── caddy/
    └── Caddyfile.placeholder # v2 FRP migration stub
```

## Alternatives considered

Before writing this scaffold I evaluated SkyPilot SkyServe, dstack, and an existing reference implementation (`Borjagodoy/gpt-oss-runpod-on-demand`). None fit cleanly — write-up at [`docs/alternatives.md`](docs/alternatives.md). TL;DR: SkyServe has a $6/mo controller floor and cold-start 503s; dstack doesn't support RunPod and needs ~$11–20/mo of always-on infra. Revisit if dstack adds RunPod, or if you start needing real concurrent-user burst handling (SkyServe becomes attractive then).

For the *commercial* landscape — Ollama Cloud, HuggingFace Inference Endpoints, Modal, Baseten, Together / Fireworks / Groq, etc. — see [`docs/landscape.md`](docs/landscape.md). Short version: category 1 (managed APIs) genuinely wins for low-volume hobbyist use; category 2 (HF Endpoints, Modal) is the closest peer and wins for ops polish; this repo wins when you care about reproducibility, region transparency, and not being locked into a vendor's control plane.

## License

MIT.
