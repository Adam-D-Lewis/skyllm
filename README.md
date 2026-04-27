<p align="center">
  <img src="docs/assets/busy-logo-cropped.png" alt="skyllm logo" width="320">
</p>

# skyllm

Cheap, on-demand cloud GPU running an OpenAI-compatible vLLM or llama.cpp endpoint, reachable from any tool via a stable public URL.

One `skyllm up` spins up a 24 GB+ NVIDIA GPU on RunPod, starts the engine selected by the model you launched (vLLM for safetensors/AWQ/GPTQ, llama.cpp for GGUF), and exposes it through a Cloudflare Tunnel at a hostname you control. Clients point at `https://llm.yourdomain.com/v1` forever — the actual GPU comes and goes, the URL stays.

## Why

Run bigger models than your local GPU can handle, without paying for a 24/7 cloud instance. Designed for occasional home use: spin up, poke at a model for an hour, tear down, pay cents.

> **Single-user design.** This project is optimized for one person using one model at a time — especially the llama.cpp variants, which are tuned for single-thread inference. You *can* configure it for concurrent users, but that's not currently the goal. If you need multi-user serving with request queuing, consider a managed API (Together, Fireworks, Modal).

## Stack

| Piece | What it does |
|---|---|
| **SkyPilot** | Provisions the GPU on RunPod, handles autostop/teardown |
| **vLLM** or **llama.cpp** | Serves the model with an OpenAI-compatible API. Engine is selected per catalog entry — vLLM for safetensors/AWQ/GPTQ, llama.cpp for GGUF (incl. CPU-offloaded MoE). vLLM runs `vllm/vllm-openai:latest` directly; llama.cpp installs via pixi on the pod. No custom Docker image to maintain either way. |
| **Cloudflare Tunnel** | Gives you a stable public URL without opening ports |

## Safeguards against surprise bills

Belt, suspenders, and a third belt:

1. **Idle auto-shutdown.** `scripts/idle-watch.sh` watches vLLM's Prometheus metrics (`vllm:generation_tokens_total`); when no tokens have been generated for `$IDLE_MINUTES` (default 15), it exits the SkyPilot run block. Combined with `sky launch --down`, this terminates the cluster.
2. **Wall-clock cap.** `sudo shutdown -h +$MAX_RUNTIME_MINUTES` runs at launch (4 h default on `sky.yaml`, 1 h on the 80 GB preset since hourly rates are several × higher). Even if the idle-watcher wedges, the box powers off.
3. **SkyPilot autostop.** `--idle-minutes-to-autostop 30 --down` tells SkyPilot itself to terminate the cluster if the whole job finishes and nothing takes its place.
4. **Monthly budget check.** `scripts/budget-check.sh` is cron-able on your laptop; it reads `sky cost-report` and runs `sky down` if you've spent over `$MONTHLY_BUDGET_USD` this month.
5. **Provider-side spend limit** (*the real backstop*). Set a hard monthly limit at <https://www.runpod.io/console/user/billing>. The other safeguards protect against mistakes; this one protects against bugs in the other safeguards.

## Setup

This section walks you through everything from zero to a working endpoint. If you're already familiar with Cloudflare and RunPod, you can skim.

### Prerequisites

| Requirement | Why you need it | How to get it |
|---|---|---|
| **Cloudflare account** | Provides a stable public URL via Tunnel (no port forwarding needed) | Free at <https://dash.cloudflare.com/sign-up> |
| **A domain on Cloudflare** | The tunnel routes `llm.yourdomain.com` to your pod | ~$10/yr domain registration, or use a free subdomain on a domain you already manage. The domain *must* be managed by Cloudflare (DNS settings → nameservers point to CF). |
| **RunPod account** | Spins up the GPU on demand | Sign up at <https://www.runpod.io/> and add a payment method |
| **pixi** | Manages the local CLI environment (single static binary, no Python install needed) | <https://pixi.sh/latest/> — one-liner install on Linux/macOS |
| **SkyPilot CLI** | Provisions the GPU on RunPod | Installed automatically by `pixi install` (step 5) — listed as a dependency in `pyproject.toml` |
| **Docker** (optional) | Only needed if SkyPilot asks for it — most setups work without it |

### Step 1 — Install pixi

```bash
# Install pixi (Linux/macOS one-liner):
curl -fsSL https://pixi.sh/install.sh | sh
# Then restart your shell or run: source ~/.bashrc (or ~/.zshrc)
```

SkyPilot (with RunPod support) is declared in `pyproject.toml`, so `pixi install` in step 5 will pull it into the local env automatically — no separate `pip install` needed.

### Step 2 — Configure RunPod

1. Go to <https://www.runpod.io/console/user/settings> → **API Keys** → **Create New Key**.
2. Copy the key — you'll paste it into `.env` (step 4).
3. **Set a monthly spend limit** (non-optional — protects you from surprise bills):
   Go to <https://www.runpod.io/console/user/billing> and cap monthly spend at whatever you're willing to lose. $20/mo is plenty for occasional home use.

### Step 3 — Create a Cloudflare Tunnel

This gives you a stable public URL (`llm.yourdomain.com`) that always points to your pod, even though the pod itself comes and goes.

1. Go to <https://one.dash.cloudflare.com/> → **Networks** → **Tunnels** → **Create a tunnel**.
2. Choose connector type: **Cloudflared**.
3. Name it something like `llm-gpu`.
4. Under **Public Hostname**, add a route:
   - **Subdomain**: `llm` (or whatever you like — this becomes `llm.yourdomain.com`)
   - **Domain**: your Cloudflare-managed domain
   - **Service type**: `HTTP`
   - **URL**: `localhost:8080`
5. Click **Save tunnel**.
6. Go to the **Tunnels** page, click your tunnel name, then **Public Hostname** → **Edit** → scroll to **Token**.
7. Copy the **token** (a long base64 string) — you'll paste it into `.env` (step 4).

> 💡 Cloudflare auto-creates the DNS record for you. The hostname is now permanently pointed at whichever machine runs `cloudflared` with that token. You don't need to do anything with DNS manually.

### Step 4 — Fill in `.env`

```bash
cp .env.example .env
```

Edit `.env` and set these four values:

| Variable | Where to get it | Example |
|---|---|---|
| `LLM_HOSTNAME` | Your chosen hostname | `llm.yourdomain.com` |
| `CF_TUNNEL_TOKEN` | Cloudflare Tunnel page (step 3, item 7) | `abc123+longbase64string==` |
| `LLM_API_KEY` | Generate with `openssl rand -hex 32` | `a1b2c3d4...` (64 hex chars) |
| `RUNPOD_API_KEY` | RunPod settings → API Keys (step 2, item 2) | `pod-abc123...` |

Generate a strong API key:

```bash
openssl rand -hex 32
```

> ⚠️ **`LLM_API_KEY` is the only thing gating your endpoint from the public internet.** The Cloudflare Tunnel routes traffic to your pod but does *not* authenticate clients — anyone who resolves the hostname can probe it. A strong random key (the `openssl` command produces 256 bits of entropy) is what keeps scanners out. Do **not** use a short or memorable string. If you want edge-level auth (Cloudflare Access, etc.), see [`docs/roadmap/edge-auth.md`](docs/roadmap/edge-auth.md).

Optional: if you plan to use gated HuggingFace models (Llama, Gemma, Mistral-Instruct, etc.), add your HF token:

```bash
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx
```

### Step 5 — Create the local environment and launch

```bash
# Create and install the local CLI environment
pixi install

# Drop into the environment (optional — you can also prefix every command with `pixi run`)
pixi shell

# Launch the default model (qwen-0.5b, vLLM, 24 GB tier) — fast stack-test
skyllm up

# Or pick any model from the catalog:
skyllm list          # see all available models
skyllm up qwen3.6-27b   # 27B dense VLM on 24 GB, ~40 tok/s
```

First launch takes ~5 minutes (provisioning + image pull + model download). The `vllm/vllm-openai` image is ~10 GB — the first pull is slow, but it's cached by RunPod thereafter.

### Step 6 — Use it

Because the endpoint speaks the OpenAI API format, it plugs into virtually any consumer tool that accepts an OpenAI-compatible base URL. Just point the tool at `https://llm.yourdomain.com/v1` and supply your `LLM_API_KEY`.

Popular options:

| Tool | What it is | How to connect |
|---|---|---|
| **[Open WebUI](https://github.com/open-webui/open-webui)** | Full-featured browser chat UI (Ollama-compatible) | Add a new OpenAI-compatible provider with your hostname + API key |
| **[Cherry Studio](https://github.com/kangfenmao/cherry-studio)** | Desktop chat client with multi-provider support | Add OpenAI provider, set base URL and key |
| **[AnythingLLM](https://github.com/Mintplex-Labs/anything-llm)** | RAG chat with document upload | Add OpenAI endpoint in settings |
| **[FastChat](https://github.com/lm-sys/FastChat)** | Web UI for chatting with LLMs | Set `--server-base-url` to your hostname |
| **Any OpenAI SDK client** | Your own scripts, bots, automations | `base_url="https://llm.yourdomain.com/v1"`, `api_key="..."` |

The key is always the same two values:
- **Base URL**: `https://llm.yourdomain.com/v1`
- **API key**: the `LLM_API_KEY` you set in `.env`

#### Quick curl test

From anywhere on the internet:

```bash
curl https://llm.yourdomain.com/v1/chat/completions \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "any", "messages": [{"role": "user", "content": "hi"}]}'
```

#### Usage with the OpenAI Python SDK

```python
from openai import OpenAI
client = OpenAI(base_url="https://llm.yourdomain.com/v1", api_key="<your LLM_API_KEY>")
response = client.chat.completions.create(model="any", messages=[{"role": "user", "content": "hi"}])
print(response.choices[0].message.content)
```

### Step 7 — Tear down when done

```bash
skyllm down
```

If you forget, the safeguards (idle auto-shutdown, wall-clock cap, budget check) will eventually shut it down. But `skyllm down` is instant and saves you pennies per minute.

---

**That's it!** You now have a stable public URL that spins up a GPU on demand. Read on for daily-use commands, bigger models, and cost-saving tips.

## Daily use

All commands below are `pixi run skyllm <cmd>` (drop the `pixi run` prefix inside `pixi shell`). `cli` is the default pixi env at the repo root — no `-e <name>` ever needed.

| Command | What it does |
|---|---|
| `skyllm --help` | List all commands |
| `skyllm list` | List available models (name / engine / tier / HF repo) |
| `skyllm up [<model>]` | Launch GPU + start serving. Default model: `qwen-0.5b`. `--dry-run` prints the resolved `sky launch` command |
| `skyllm down` | Terminate cluster |
| `skyllm status` | Is it running? |
| `skyllm logs` | Tail engine + cloudflared logs |
| `skyllm health` | Hit the public URL and confirm it responds |
| `skyllm cost` | SkyPilot's running cost report |
| `skyllm budget` | Run the budget guard once (also cron-able) |

Each model lives in its own directory under `models/<name>/model.yaml` — that's the "catalog". Run `skyllm list` to see what's available, or add a new model by dropping in another directory with a `model.yaml` conforming to `skyllm/schema.py` (auto-discovered, no registration step). Model identity is *not* set in `.env`.

## Bigger models

The default `qwen-0.5b` model is a 0.5B toy — fine for testing the pipeline, useless for real work. To launch something bigger, either pick an existing model (`skyllm list`):

```bash
pixi run skyllm up qwen3.6-27b   # 27B dense VLM on 24 GB, ~40 tok/s
```

…or add a new model by dropping a `models/<name>/model.yaml` (`pixi run validate` checks the schema). Gated HF models (Llama, Gemma, Mistral-Instruct, etc.) need `HF_TOKEN=...` in `.env`. `skyllm down && skyllm up <name>` to apply.

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
pixi run skyllm up qwen3.6-27b        # 24 GB dense (fits on 3090/4090)
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
skyllm/
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
│   ├── qwen-0.5b/model.yaml                 # vLLM, 24gb (default stack-test)
│   ├── qwen3.6-27b/model.yaml               # llama.cpp, 24gb (dense 27B Q4_K_M)
│   ├── qwen3-coder-next/model.yaml          # llama.cpp, 24gb-cpumoe route
│   └── qwen3-coder-next-80gb/model.yaml     # llama.cpp, 80gb pure-GPU route
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
