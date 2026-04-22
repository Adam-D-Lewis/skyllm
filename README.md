# skypilot-llms

Cheap, on-demand cloud GPU running an OpenAI-compatible llama.cpp endpoint, reachable from any tool via a stable public URL.

One `make up` spins up a 24 GB+ NVIDIA GPU on RunPod, starts `llama-server` inside a Docker container, and exposes it through a Cloudflare Tunnel at a hostname you control. Clients point at `https://llm.yourdomain.com/v1` forever — the actual GPU comes and goes, the URL stays.

## Why

Run bigger models than your local GPU can handle, without paying for a 24/7 cloud instance. Designed for occasional home use: spin up, poke at a model for an hour, tear down, pay cents.

## Stack

| Piece | What it does |
|---|---|
| **SkyPilot** | Provisions the GPU on RunPod, handles autostop/teardown |
| **llama.cpp** | Serves the model with an OpenAI-compatible API (`ghcr.io/ggml-org/llama.cpp:server-cuda`) |
| **Cloudflare Tunnel** | Gives you a stable public URL without opening ports |
| **Docker** | Runs llama.cpp + cloudflared as sidecars inside the SkyPilot task |

## Safeguards against surprise bills

Belt, suspenders, and a third belt:

1. **Idle auto-shutdown.** `scripts/idle-watch.sh` watches llama-server's Prometheus metrics; when no tokens have been generated for `$IDLE_MINUTES` (default 15), it exits the SkyPilot run block. Combined with `sky launch --down`, this terminates the cluster.
2. **Wall-clock cap.** `sudo shutdown -h +$MAX_RUNTIME_MINUTES` runs at launch (4 h default on `sky.yaml`, 1 h on `sky-big.yaml` since hourly rates are several × higher). Even if the idle-watcher wedges, the box powers off.
3. **SkyPilot autostop.** `--idle-minutes-to-autostop 30 --down` tells SkyPilot itself to terminate the cluster if the whole job finishes and nothing takes its place.
4. **Monthly budget check.** `scripts/budget-check.sh` is cron-able on your laptop; it reads `sky cost-report` and runs `sky down` if you've spent over `$MONTHLY_BUDGET_USD` this month.
5. **Provider-side spend limit** (*the real backstop*). Set a hard monthly limit at <https://www.runpod.io/console/user/billing>. The other safeguards protect against mistakes; this one protects against bugs in the other safeguards.

## Setup

### Prerequisites

- A domain managed by Cloudflare (free CF account + ~$10/yr registration).
- A RunPod account with a payment method and an API key.
- Python 3.10+ and Docker locally (for the SkyPilot CLI).

### 1. Install SkyPilot and verify RunPod

```bash
pip install "skypilot[runpod]"
# Provide the RunPod API key when prompted (or export RUNPOD_API_KEY first)
sky check runpod
```

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

Generate an API key with `openssl rand -hex 32`.

### 4. Set a RunPod spend limit

Non-optional. Go to <https://www.runpod.io/console/user/billing> and cap monthly spend at whatever you're willing to lose if everything else breaks. $20/mo is plenty for occasional home use.

### 5. Launch

```bash
make up
```

First launch takes ~5 minutes (provisioning + image pull + model download). Subsequent launches are faster if you've configured an HF cache bucket (see [Bigger models](#bigger-models)).

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
make down
```

If you forget, the safeguards kick in. But `make down` is instant and saves you pennies per minute.

## Daily use

| Command | What it does |
|---|---|
| `make help` | List all targets |
| `make up` | Launch GPU + start serving (override preset: `YAML=sky-big.yaml make up`) |
| `make down` | Terminate cluster |
| `make status` | Is it running? |
| `make logs` | Tail llama-server logs |
| `make health` | Hit the public URL and confirm it responds |
| `make cost` | SkyPilot's running cost report |
| `make budget` | Run the budget guard once (also cron-able) |
| `make check` | Verify SkyPilot can talk to RunPod |

## Bigger models

The default `LLM_HF_REPO` is a 230 MB toy model — fine for testing the pipeline, useless for real work. To try something bigger, edit `.env`:

```ini
LLM_HF_REPO=ggml-org/Qwen2.5-7B-Instruct-GGUF
LLM_HF_FILE=qwen2.5-7b-instruct-q4_k_m.gguf
```

`make down && make up` to apply.

**If the model is > a few GB**, the re-download on every launch gets annoying. Add an HF cache bucket:

1. Pick any S3/GCS-compatible bucket you control (Cloudflare R2 is cheap and you already have a CF account).
2. Add to whichever preset YAML you're using (`sky.yaml` and/or `sky-big.yaml`):
   ```yaml
   file_mounts:
     ~/.cache/huggingface:
       name: <your-bucket-name>
       store: r2   # or s3, gcs
       mode: MOUNT
   ```
3. First launch caches the download into the bucket; subsequent launches mount the bucket and skip the download.

### Scaling up to bigger GPUs

`sky.yaml` targets the 24 GB tier (RTX 3090/4090/A5000/A6000/L40S) — fine for models up to ~14B at Q4 or ~7B at Q8. For bigger models, there's `sky-big.yaml` which targets the **48–80 GB tier** (A6000, L40S, A100, A100-80GB, H100):

```bash
# Point LLM_HF_REPO / LLM_HF_FILE at a big model first
YAML=sky-big.yaml make up
```

`sky-big.yaml` ships with a shorter `MAX_RUNTIME_MINUTES` default (60 vs 240) because hourly costs are several × higher — an overnight wedge on H100 is a $200+ mistake. Everything else (tunnel, auth, idle-watch, budget-check) is identical.

Rough fit table:

| Tier | GPU options | Models that fit | ~$/hr |
|---|---|---|---|
| `sky.yaml` (24 GB) | 3090/4090/A5000/A6000/L40S | ≤14B at Q4, 7B at Q8, 30B MoE w/ CPU offload | 0.35–1.00 |
| `sky-big.yaml` (48–80 GB) | A6000/L40S/A100/A100-80GB/H100 | ≤70B at Q4, ≤30B at Q8, big MoEs fully on GPU | 1.00–4.00 |
| (future multi-GPU) | A100-80GB:2, H100:2–4 | 70B at Q8, 120B+ at Q4, DeepSeek-V3 | 4.00–20.00 |

**Multi-GPU** is a one-line diff when you need it: change `accelerators: A100-80GB:1` to `:2`, and add `-ts auto` (or omit — llama.cpp auto-splits with `-ngl 99`). For vLLM it's `--tensor-parallel-size N`. No other scaffold changes.

**Multi-node** (8+ GPUs across boxes) is out of scope — rarely needed since even 405B models fit on a single 4× or 8× H100 box.

## Hot-swapping multiple models (v1.5)

`models.ini` is a placeholder for llama.cpp's router mode, which keeps several models ready and loads them on demand. To enable:

1. Confirm the `ghcr.io/ggml-org/llama.cpp:server-cuda` image supports `--models-preset` (it should — run `docker run --rm ghcr.io/ggml-org/llama.cpp:server-cuda --help | grep models-preset`).
2. Mount `models.ini` into the container (add to `sky.yaml`'s `docker run`: `-v $PWD/models.ini:/models.ini:ro`).
3. Replace `--hf-repo ... --hf-file ...` with `--models-preset /models.ini`.
4. Clients pick the model via the `"model"` field in their request.

See `~/CodingProjects/llama-router/` (if you have it) for a working reference of this pattern tuned for a local 3060.

## Multi-provider (unlock if you want)

v1 targets RunPod because it's simplest. To have SkyPilot pick the cheapest GPU across providers:

1. Run `sky check` for each provider you want (`aws`, `gcp`, `lambda`, `vast`, etc.) — fill in creds as prompted.
2. Edit `sky.yaml`:
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

```
skypilot-llms/
├── .env.example       # every config var, documented
├── .gitignore
├── Makefile           # up / down / status / logs / health / cost / budget
├── README.md          # you are here
├── sky.yaml           # SkyPilot task — 24 GB tier (default)
├── sky-big.yaml       # SkyPilot task — 48–80 GB tier (YAML=sky-big.yaml make up)
├── models.ini         # reference for router-mode (v1.5)
├── docs/
│   └── alternatives.md   # why not SkyServe / dstack / etc.
├── scripts/
│   ├── idle-watch.sh    # exits the run block when llama-server is idle
│   └── budget-check.sh  # cron-able spend guard
└── caddy/
    └── Caddyfile.placeholder  # v2 FRP migration stub
```

## Alternatives considered

Before writing this scaffold I evaluated SkyPilot SkyServe, dstack, and an existing reference implementation (`Borjagodoy/gpt-oss-runpod-on-demand`). None fit cleanly — write-up at [`docs/alternatives.md`](docs/alternatives.md). TL;DR: SkyServe has a $6/mo controller floor and cold-start 503s; dstack doesn't support RunPod and needs ~$11–20/mo of always-on infra. Revisit if dstack adds RunPod, or if you start needing real concurrent-user burst handling (SkyServe becomes attractive then).

## License

MIT.
