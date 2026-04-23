# Alternatives considered

Snapshot of the competitive landscape evaluated before writing this scaffold. If you're about to re-investigate whether SkyServe or dstack is a better fit, read this first — the answer was "no, keep the regular-task design" as of 2026-04-22.

Evaluation date: **2026-04-22**. Re-verify if >6 months stale.

## Why vLLM and not llama.cpp (pivoted mid-build)

This scaffold originally targeted llama.cpp, on the theory that config parity with the owner's local `llama-router` (GGUF + CPU-MoE offload + router-mode hot-swap) would be valuable. Pivoted to vLLM at the first real launch attempt after finding:

- **RunPod's default SkyPilot pod image has no docker.** Our planned "docker pull llama.cpp image as a sidecar" architecture failed immediately: `bash: docker: command not found`. Installing docker inside a RunPod pod (already a container) requires privileged DinD — not guaranteed.
- **No prebuilt Linux CUDA `llama-server` binary exists.** ggml-org's GitHub releases ship Windows CUDA + Linux CPU/Vulkan — no Linux CUDA. Options to fix: build from source in setup (~5–10 min per cold launch) or publish our own Docker image (adds registry infra + maintenance burden).
- **vLLM is pip-installable with official support for the pattern.** SkyPilot's canonical `docs.skypilot.co/en/latest/examples/models/llama-3.html` example uses the default cloud image and does `pip install vllm` inside a conda env during `setup:`. This works on every cloud including RunPod.
  - Worth flagging a near-miss we hit: `image_id: docker:vllm/vllm-openai:latest` *sounds* nicer (fast cold start, no pip install) and SkyPilot's `llm/vllm/serve-openai-api-docker.yaml` uses exactly that. But it only works on AWS/GCP where SkyPilot runs the image as a container *on top of* a VM that has sshd. **On RunPod the pod IS the container**, so the container itself has to run sshd — vllm/vllm-openai doesn't, and launches fail with `ConnectionRefusedError [Errno 111]` on port 22 after 600s. Do not re-attempt this on RunPod without building a custom image that bundles vLLM + sshd.
- **vLLM exposes `/v1/*` (OpenAI), `/health`, and `/metrics` (Prometheus counter `vllm:generation_tokens_total`) natively.** The existing scaffold's idle-watch, tunnel, auth, and safeguards work unmodified after a one-line metric name change.
- **Throughput is meaningfully better** on a full-VRAM GPU. The reasons to pick llama.cpp locally (aggressive quant + MoE CPU offload on 12 GB 3060) don't apply once we have 24 GB+ of VRAM to spend.

**Conclusion:** the owner's local `llama-router` stays llama.cpp / GGUF. This cloud scaffold is vLLM-native. That's a cleaner separation than the hybrid originally planned.

If you're tempted to flip this back to llama.cpp, first verify the original blockers have changed:
1. Does ggml-org now ship Linux CUDA prebuilt binaries? (check their Releases page)
2. OR is there a maintained, publicly-pulled-without-auth llama.cpp image with sshd + python pre-installed that SkyPilot's bootstrap can use? The upstream `ghcr.io/ggml-org/llama.cpp:server-cuda` at time of pivot had llama-server + CUDA libs only — no Python, no sshd.

If both stay "no," stay on vLLM.

## Use case being evaluated against

- Spin up one cheap RunPod GPU (RTX 3090/4090 class) on demand.
- Run llama.cpp (or vLLM) as an OpenAI-compatible server.
- Expose at a stable public URL (`llm.yourdomain.com`) across launches.
- Auto-shutdown on idle to keep cost near zero.
- Home use, occasional, ≤5 concurrent users.
- `$0` always-on floor is meaningful (not enterprise).

## Current design (baseline)

Regular SkyPilot `sky launch` task + three custom pieces:

| Piece | Purpose | ~LOC |
|---|---|---|
| cloudflared docker sidecar | Stable public URL without opening ports | sky.yaml lines |
| `scripts/idle-watch.sh` | Polls llama.cpp `/metrics`, exits run block after N idle min so `--down` fires | ~35 |
| `scripts/budget-check.sh` | Cron-able; parses `sky cost-report`, `sky down` over monthly cap | ~30 |

Always-on cost: **$0**. Pay only while the GPU is up.

## SkyPilot SkyServe — evaluated, rejected

SkyServe is SkyPilot's first-class serving primitive (`sky/serve/`). It *does* support scale-to-zero, but has three concrete problems for this use case:

### 1. Always-on controller VM (~$6/mo floor)

The SkyServe controller is a separate VM that runs even at `min_replicas=0`. It only **stops** on idle, doesn't terminate. Defaults: 4 CPUs, 8 GB RAM, 200 GB disk (`sky/serve/constants.py:73`), 10 min idle → stop (`constants.py:76–79`). Provisioning logic: `sky/serve/controller_utils.py:627–650`.

### 2. Cold-start requests fail with 503

Load balancer retries 3× (`constants.py:30`, `load_balancer.py:242–254`) then returns 503. **No request queue.** Friend hits the URL after an idle period → first request errors → autoscaler spins up a replica → ~30–120s later service is ready → manual retry. Rough UX.

### 3. Still no stable URL

Endpoint IP is resolved fresh per launch (`server/impl.py:399–413`, `load_balancer.py`). You still need `cloudflared` or similar. SkyServe does not replace this piece.

### What SkyServe does replace

- **idle-watch.sh** — the autoscaler handles scale-to-zero via `min_replicas=0` + request-rate policy (`autoscalers.py:226–236, 458–523`). Example at `examples/serve/min_replicas_zero.yaml`. Implementation is solid; the gotcha is the cold-start UX above.

### Verdict

Equivalent cost, worse cold-start UX, non-zero always-on floor. **Revisit only if you need concurrent-user burst handling** — SkyServe's `min_replicas=0` + `max_replicas=N` with request-rate scaling would become genuinely useful then, and the $6/mo controller floor would be fair pay for it. Pivot would be a `sky.yaml` → `service.yaml` rewrite, not a full redesign.

## dstack — evaluated, rejected

dstack (`github.com/dstackai/dstack`) markets a "services + gateway" primitive that sounds like a clean fit. The reality was disappointing for this specific use case.

### Disqualifying: no RunPod support

27 backends in `src/dstack/_internal/core/backends/`, RunPod not among them. Vast.ai and Lambda are supported, so you'd be forced onto pricier enterprise-cloud consumer-GPU paths.

### Also bad: self-hosted gateway

dstack's "gateway" (`src/dstack/_internal/proxy/gateway/`) is an nginx-on-a-VM that **you run**. Same cost profile as the FRP migration path (see `../caddy/Caddyfile.placeholder`), just dstack-flavored.

### Also bad: two always-on components

- dstack server (control plane, `src/dstack/_internal/server/app.py`) — ~$5/mo VPS.
- Gateway VM — ~$6–15/mo.
- **Floor: ~$11–20/mo** before you've launched a single GPU.

### What dstack does nicely

- First-class service auth at the gateway (`src/dstack/_internal/proxy/gateway/auth.py`) — cleaner than our `--api-key` layer cake.
- Let's Encrypt TLS at a VM *you control* (`src/dstack/_internal/core/models/gateways.py:74–85`) — no third-party sees plaintext, unlike CF Tunnel.
- Stable public URL as long as the gateway stays up.
- Scale-to-zero via RPS autoscaler (`server/services/services/autoscalers.py:131`).

### Verdict

**Revisit if dstack adds RunPod support.** The gateway + auth + TLS story is genuinely nicer than our current stack — it just can't reach the cheap-GPU providers we care about. No RunPod + $11–20/mo floor = not a fit for home use today.

## How each handles the three custom pieces

| Piece | SkyServe | dstack | Our baseline |
|---|---|---|---|
| Stable public URL (cloudflared) | Still needed | Gateway (but self-hosted VM) | cloudflared sidecar |
| Idle detection (idle-watch.sh) | Replaced, cold-start 503s | Partially replaced (RPS only, no time) | Works today |
| Budget cap (budget-check.sh) | Still needed | Still needed | Works today |
| Always-on cost floor | ~$6/mo | ~$11–20/mo | $0 |

## Borjagodoy/gpt-oss-runpod-on-demand — also looked at

Closest pre-existing scaffold (<https://github.com/Borjagodoy/gpt-oss-runpod-on-demand>). Covers ~70% of the need: RunPod + OpenAI API + auto-shutdown + monthly budget + `.env` config. Three mismatches that undo our deliberate decisions:

1. vLLM, not llama.cpp.
2. RunPod-only with no SkyPilot abstraction (no multi-provider escape hatch).
3. Cloudflare **Workers** (custom JS proxy) instead of a simple `cloudflared` sidecar — makes the v2 FRP migration substantially harder.

One idea stolen from it: cron-able monthly spend guard (`scripts/budget-check.sh`).

## When to revisit this doc

- **dstack + RunPod**: check `dstack/src/dstack/_internal/core/backends/` for a `runpod/` directory. If it appears, re-evaluate — their gateway + auth story is genuinely better.
- **SkyServe for burst**: if you start having 2+ simultaneous users hit this regularly, the SkyServe pivot becomes attractive — pay $6/mo for the controller, get real autoscaling.
- **LiteLLM-style reverse proxy**: not evaluated. If you ever want to multiplex across local llama-router + this cloud endpoint + Anthropic + OpenAI under one URL with routing, that's a different problem (LLM gateway) and worth its own investigation.
