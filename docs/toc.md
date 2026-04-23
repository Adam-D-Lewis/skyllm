# Repo tour

A map of what's in this repo and what each piece does. The headline: this is a SkyPilot scaffold that spins up a RunPod GPU on demand, runs vLLM's OpenAI-compatible server on it, and exposes it through a Cloudflare Tunnel at a stable hostname. Everything else is safeguards against surprise cloud bills or documentation of the decisions made.

## Top-level files

- **`README.md`** — User-facing docs: stack overview, setup walkthrough (SkyPilot install → CF tunnel → `.env` → `make up`), budget safeguards, how to switch models / GPU tiers, the planned v2 migration path from CF Tunnel to self-hosted FRP.
- **`Makefile`** — Thin wrapper over `sky` commands. Sources `.env`, then exposes `up` / `down` / `status` / `logs` / `health` / `cost` / `check` / `budget`. `up` runs `sky launch -c llm -y $(YAML) --idle-minutes-to-autostop 30 --down`, so `YAML=sky-big.yaml make up` swaps the preset.
- **`.env.example`** — Documented template for every user-specific config variable: hostname, CF tunnel token, LLM API key, RunPod API key, model repo IDs for both engines, HF token, idle/wallclock caps, monthly budget.
- **`.env`** — Your filled-in copy (gitignored).
- **`.gitignore`** — Ignores `.env`, `*.log`, `.DS_Store`, `__pycache__/`, `.sky/`.

## The three SkyPilot presets (sibling YAMLs)

All three define `envs:` (passed in via `--env-file .env`), `resources:` (RunPod + a GPU family), `setup:` (provision software on the pod), and `run:` (start server + tunnel + idle-watcher).

- **`sky.yaml`** *(default)* — vLLM on the 24 GB tier (RTX 3090/4090/A5000/A6000/L40S). `setup` creates a conda env and `pip install vllm`; `run` starts `vllm.entrypoints.openai.api_server` on :8080, waits for `/health`, starts `cloudflared`, then blocks on the idle watcher. 240 min wall-clock cap.
- **`sky-big.yaml`** — Same stack, 48–80 GB tier (A6000 / L40S / A100 / A100-80GB / H100), 250 GB disk, 60 min wall-clock cap (because an overnight H100 wedge costs real money).
- **`sky-llamacpp.yaml`** — Alternative engine: builds `llama.cpp` from source with CUDA in `setup` (~3–5 min per cold start because it auto-detects the pod's compute capability and only compiles that arch), then runs `llama-server` against a GGUF file. Overrides `IDLE_METRIC` so the idle watcher polls llama.cpp's Prometheus counter instead of vLLM's. The engine split exists because `vllm/vllm-openai` and the llama.cpp images don't ship sshd, and a RunPod pod *is* a container — SkyPilot's bootstrap fails without sshd.

## `scripts/`

- **`idle-watch.sh`** — Runs on the pod at the end of `run:`. Polls `localhost:8080/metrics` every 60 s for the engine's "generated tokens" Prometheus counter (vLLM: `vllm:generation_tokens_total`, llama.cpp override: `llamacpp:n_tokens_predicted_total`). If the counter doesn't advance for `IDLE_MINUTES`, it exits — which ends the SkyPilot job, which triggers `--down`, which kills the cluster.
- **`budget-check.sh`** — Runs locally (not on the pod); cron-able. Parses `sky cost-report` for the month-to-date total and runs `sky down` if you're over `MONTHLY_BUDGET_USD`. Documented as belt-and-suspenders — the real backstop is the RunPod account-level spend limit.

## `caddy/`

- **`Caddyfile.placeholder`** — Stub + commentary for the v2 migration. When you outgrow Cloudflare Tunnel (because CF sees plaintext prompts), you'd stand up a VPS with `frps` + `caddy`, swap the `cloudflared` block in `sky.yaml` for `frpc`, and repoint DNS. The clients never change URL. This file is just the starting point for the VPS-side Caddy config.

## `docs/`

- **`alternatives.md`** — Decision log. Covers why vLLM beat llama.cpp for the cloud case mid-build (the `vllm/vllm-openai` / `ggml-org/*` images don't ship sshd, so they can't be pinned on RunPod; llama.cpp has no prebuilt Linux CUDA binary), why SkyServe and dstack were rejected, and what would have to change to re-evaluate. Dated so you know when to re-verify.
- **`toc.md`** — This file.

## The shape of it

Control flow on `make up`: Makefile → `sky launch` reads the chosen YAML + `.env` → SkyPilot provisions a RunPod pod → `setup:` installs the engine + cloudflared → `run:` arms the wall-clock shutdown, starts the engine, starts the tunnel, then blocks on `idle-watch.sh`. When any of {idle-watch exits, wall-clock fires, SkyPilot autostop, budget-check trips, RunPod spend limit hits} happens, the GPU goes away and billing stops. Clients talking to `https://llm.yourdomain.com/v1` don't know or care which pod is behind it.
