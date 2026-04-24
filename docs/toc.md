# Repo tour

A map of what's in this repo and what each piece does. The headline: this is a SkyPilot scaffold that spins up a RunPod GPU on demand, runs vLLM's OpenAI-compatible server on it, and exposes it through a Cloudflare Tunnel at a stable hostname. Everything else is safeguards against surprise cloud bills or documentation of the decisions made.

## Top-level files

- **`README.md`** — User-facing docs: stack overview, setup walkthrough (SkyPilot install → pixi install → CF tunnel → `.env` → `skyllm up`), budget safeguards, how to switch models / GPU tiers, the planned v2 migration path from CF Tunnel to self-hosted FRP.
- **`pyproject.toml`** — Packages the repo's own `skyllm` CLI. Declares deps (pydantic, pyyaml, typer, requests) and the `skyllm` console entry point. Pixi installs this editable into the `cli` env.
- **`pixi.toml` / `pixi.lock` (root)** — Local-only workspace: the `cli` env (no CUDA), set as the default so `pixi run skyllm …` / `pixi shell` / `pixi install` all just work without `-e`. Contains the editable install of the `skyllm` package.
- **`pod/pixi.toml` / `pod/pixi.lock`** — Pod-only workspace, deliberately isolated from the root. Two envs on conda-forge + pypi: `vllm` (CUDA-enabled torch + vllm wheel) and `llamacpp` (prebuilt cuda129 llama.cpp binary). These are the only pixi files that get rsynced to the RunPod pod (via each sky YAML's `file_mounts:` allowlist). See `docs/pixi.md` for the non-obvious shape.
- **`.env.example`** — Documented template for secrets + infra knobs only: hostname, CF tunnel token, LLM API key, RunPod API key, HF token, idle/wallclock caps, monthly budget. Model identity lives in the catalog, not here.
- **`.env`** — Your filled-in copy (gitignored).
- **`.gitignore`** — Ignores `.env`, `*.log`, `.DS_Store`, `__pycache__/`, `.sky/`, `.pixi/`.

## `skyllm/` (the CLI + catalog schema)

- **`cli.py`** — Typer app exposing `list` / `up` / `down` / `status` / `logs` / `health` / `cost` / `budget`. `up <model>` loads `models/<model>/model.yaml`, maps `(engine, tier)` to a preset YAML, and shells out to `sky launch -c llm -y <preset> --env-file .env --env LLM_MODEL=… --idle-minutes-to-autostop 30 --down`. `--dry-run` prints the resolved command without executing.
- **`schema.py`** — Pydantic `ModelSpec`: `hf_repo`, `engine` (`vllm` | `llamacpp`), `tier` (`24gb` | `48-80gb`), optional `hf_file` (required for llamacpp), `extra_args`, `min_disk_gb`, `notes`. Cross-field validation enforces that llamacpp requires `hf_file` and vllm forbids it.
- **`validate.py`** — Loads every `models/*/model.yaml`, reports schema failures, exits nonzero on any. Wired to `pixi run validate`.

## `models/` (the catalog)

One directory per model, each with a `model.yaml` matching `skyllm/schema.py`. Currently seeded with:

- **`qwen-0.5b/`** — vllm + 24 GB, `Qwen/Qwen2.5-0.5B-Instruct`. Default model for `skyllm up`; fast stack-test.
- **`qwen3-coder-next/`** — llamacpp + 48–80 GB, `unsloth/Qwen3-Coder-Next-GGUF` (MXFP4 MoE, ~48 GB). Exercises the big-tier llama.cpp preset.

Drop a new directory + `model.yaml` in here to add a model; no code changes needed.

## The four SkyPilot presets (under `sky/`)

All four live under `sky/` and define `envs:` (passed in via `--env-file .env` + `--env KEY=VAL` from the catalog), `file_mounts:` (an explicit allowlist uploading only `pod/pixi.toml`, `pod/pixi.lock`, and `scripts/idle-watch.sh` — no `workdir: .`, so stray files / secrets never ride up with the workdir), `resources:` (RunPod + a GPU family), `setup:` (install pixi + the right env), and `run:` (start server + tunnel + idle-watcher). `skyllm up` picks one based on the catalog entry's `(engine, tier)` fields.

- **`sky.yaml`** — vLLM, 24 GB tier (RTX 3090/4090/A5000/A6000/L40S). `setup` installs pixi + the `vllm` env; `run` starts `vllm.entrypoints.openai.api_server` on :8080, waits for `/health`, starts `cloudflared`, then blocks on the idle watcher. 240 min wall-clock cap.
- **`sky-big.yaml`** — Same stack, 48–80 GB tier (A6000 / L40S / A100 / A100-80GB / H100), 250 GB disk, 60 min wall-clock cap (because an overnight H100 wedge costs real money).
- **`sky-llamacpp.yaml`** — Alternative engine: installs the conda-forge `llama.cpp` package (cuda129 build, ~1 min cold start) and runs `llama-server` against a GGUF file. Overrides `IDLE_METRIC` so the idle watcher polls llama.cpp's Prometheus counter instead of vLLM's. 24 GB tier.
- **`sky-big-llamacpp.yaml`** — Same stack as `sky-llamacpp.yaml`, 48–80 GB tier, 250 GB disk, 60 min wall-clock cap. Routed to when a catalog entry is `engine: llamacpp` + `tier: 48-80gb` (e.g. Qwen3-Coder-Next MXFP4).

The engine split exists because `vllm/vllm-openai` and the llama.cpp images don't ship sshd, and a RunPod pod *is* a container — SkyPilot's bootstrap fails without sshd. That's why we install via pixi onto RunPod's default sshd-capable base image rather than pinning a container.

## `scripts/`

- **`idle-watch.sh`** — Runs on the pod at the end of `run:`. Polls `localhost:8080/metrics` every 60 s for the engine's "generated tokens" Prometheus counter (vLLM: `vllm:generation_tokens_total`, llama.cpp override: `llamacpp:n_tokens_predicted_total`). If the counter doesn't advance for `IDLE_MINUTES`, it exits — which ends the SkyPilot job, which triggers `--down`, which kills the cluster.
- **`budget-check.sh`** — Runs locally (not on the pod); cron-able. Parses `sky cost-report` for the month-to-date total and runs `sky down` if you're over `MONTHLY_BUDGET_USD`. Documented as belt-and-suspenders — the real backstop is the RunPod account-level spend limit.

## `caddy/`

- **`Caddyfile.placeholder`** — Stub + commentary for the v2 migration. When you outgrow Cloudflare Tunnel (because CF sees plaintext prompts), you'd stand up a VPS with `frps` + `caddy`, swap the `cloudflared` block in `sky.yaml` for `frpc`, and repoint DNS. The clients never change URL. This file is just the starting point for the VPS-side Caddy config.

## `docs/`

- **`alternatives.md`** — Decision log. Covers why vLLM was chosen as the default engine (SkyServe + dstack rejection rationale, the sshd-on-RunPod constraint that rules out pinning app images), and what would have to change to re-evaluate. Dated so you know when to re-verify.
- **`pixi.md`** — Why the pixi envs look the way they do: pypi vllm pulling CUDA-enabled torch, the conda-forge cuda129 llama.cpp build, the `[system-requirements] cuda` declaration that RunPod forces, and the `LD_PRELOAD` / direct-env-binary workaround for the libstdc++ / libicui18n ABI trap.
- **`roadmap.md`** — Phased plan. Phase 1 (pixi swap) ✅, Phase 2 (model catalog) ✅, Phase 3 (`skyllm` CLI) ✅, Phase 4 (multi-provider) deferred.
- **`toc.md`** — This file.

## The shape of it

Control flow on `skyllm up <model>`: CLI reads `models/<model>/model.yaml` → resolves `(engine, tier)` to one of the four preset YAMLs → shells out to `sky launch -c llm -y <preset> --env-file .env --env LLM_MODEL=…` → SkyPilot provisions a RunPod pod → `setup:` installs pixi + the engine's pixi env + cloudflared → `run:` arms the wall-clock shutdown, starts the engine, starts the tunnel, then blocks on `idle-watch.sh`. When any of {idle-watch exits, wall-clock fires, SkyPilot autostop, budget-check trips, RunPod spend limit hits} happens, the GPU goes away and billing stops. Clients talking to `https://llm.yourdomain.com/v1` don't know or care which pod is behind it.
