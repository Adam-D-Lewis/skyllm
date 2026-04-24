# Roadmap: model catalog + Python CLI + pixi

Target end state: `skyllm up <model>` launches the right SkyPilot preset with the right engine and GPU tier, from a declarative model catalog. Multi-provider is a later phase.

Design decisions already settled (see conversation 2026-04-23):
- **Engine axis (vllm / llama.cpp)** maps to pixi environments.
- **GPU tier axis (24 GB / 48–80 GB)** maps to sky YAML presets. Both vllm presets share the same pixi env.
- Three sky YAMLs stay as separate, self-contained files — no templating yet.
- Model config drives tier selection; user picks model, CLI picks preset.
- CLI in Python (SkyPilot is Python, so we can `import sky` instead of shelling out).
- Model catalog is directory-per-model (`models/<name>/model.yaml`), not a single big file.

---

## Phase 1 — Pixi swap ✅ (2026-04-24)

Goal: replace `conda create + pip install` and the llama.cpp source build with a pinned `pixi.toml` + `pixi.lock`. Gives reproducible envs across launches and across providers.

Status: **Done.** Pixi.toml shape validated locally on driver 580/CUDA 13.0 (2026-04-23). E2E on RunPod validated 2026-04-24: `sky.yaml` (vllm) and `sky-llamacpp.yaml` both reached a green `/v1/models` endpoint and tore down cleanly; cold start ~1 min for llama.cpp. Lessons captured in `docs/pixi.md`.

What shipped:
- `pixi.toml` + `pixi.lock` at repo root with two environments: `vllm`, `llamacpp`.
- All three sky YAMLs rewritten: setup installs pixi + `pixi install -e <env>`; run invokes via `pixi run -e <env> ...`.
- `sky-llamacpp.yaml` lost its entire source-build section (~10–15 min cold start → ~1–2 min).
- `.pixi/` added to `.gitignore`.

Not validated (deferred, not blocking):
- `sky-big.yaml` e2e on RunPod. Shares the vllm pixi env with `sky.yaml`, so the pip/conda side is already proven; only the accelerator list and `MAX_RUNTIME_MINUTES=60` are untested. Will validate lazily the first time a big model is actually needed.

Confirmed via web checks + spike v1 (2026-04-23):
- **conda-forge ships CUDA builds of llama.cpp for linux-64** (CUDA 12.9 and 13.0 variants; v8722 latest). Source-build in `sky-llamacpp.yaml` can be deleted — cold start drops from 10–15 min to ~1 min.
- **conda-forge also ships vllm** (0.10.2) but it's behind pypi (0.19.1). Unused in our plan — we get vllm from pypi.
- **Pixi installs cleanly on the RunPod default pod image** (Ubuntu 22.04 + driver 570.211.01). `curl pixi.sh/install.sh | bash` works unprivileged.
- **Driver 570.211.01** on the RunPod default image natively supports CUDA ≤ 12.8. Conda-forge's `cuda129` and `cuda130` llama.cpp builds technically exceed that; they usually still run because conda-forge bundles runtime libs, but it's empirically verified by spike v2, not assumed.
- **`[system-requirements] cuda = "X"` in pixi.toml is REQUIRED** in the RunPod container — pixi's `__cuda` virtual-package detection fails there, and no CUDA conda-forge packages resolve without the declaration.
- **Why `pixi add --pypi vllm` fails** (root cause, not a workaround): vllm's wheel depends on torch-with-CUDA. The `torch` package on pypi.org is CPU-only; CUDA-linked torch wheels live on `https://download.pytorch.org/whl/cuXYZ/`. Uv/pixi looking only at pypi.org can't satisfy the CUDA-torch dep, gives up on the wheel, falls back to the vllm sdist — whose `setup.py` hits its `CUDA_HOME` assertion and dies. Misleading error; the real problem is torch resolution, not CUDA toolkit absence.
- **Fix: pull pytorch from conda-forge** (which has native CUDA builds) and vllm from pypi. Vllm's wheel then installs against the already-present torch without invoking sdist. This is Option B in the 2026-04-23 discussion. Cleaner than the `--torch-backend=auto` approach (Option A) because conda-forge handles the CUDA runtime consistently with llama.cpp.

Locked-in pixi.toml shape (validated by spike v2 — or will be):

```toml
[workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[system-requirements]
cuda = "12.8"  # driver floor we support

[dependencies]
python = "3.11"

[feature.vllm.dependencies]
pytorch = { version = "*", build = "cuda*" }

[feature.vllm.pypi-dependencies]
vllm = "*"

[feature.llamacpp.dependencies]
"llama.cpp" = { version = "*", build = "cuda129*" }

[environments]
vllm = { features = ["vllm"] }
llamacpp = { features = ["llamacpp"] }
```

Remaining questions (for spike v2):
- Does the `cuda129` llama.cpp build actually run under driver 570 (binary links + `llama-server --version` works)?
- Does `pixi install -e vllm` pull a CUDA pytorch and `import vllm` succeed?
- If either fails, fall back to `cuda128*` for llama.cpp / older vllm / explicit pytorch build pin.

Success: `YAML=sky.yaml make up`, `YAML=sky-big.yaml make up`, `YAML=sky-llamacpp.yaml make up` all reach `/health` OK and shut down cleanly. `/v1/models` returns the expected model.

---

## Phase 2 — Model catalog

Goal: make "what models we support" a piece of data, not instructions in the README.

Scope:
- Create `models/` with one subdir per model. Initial set:
  - `qwen-0.5b/` — vllm + 24gb (stays as the tiny stack-test default)
  - `qwen-7b/` — vllm + 24gb
  - `llama-8b/` — vllm + 24gb
  - `llama-70b-fp8/` — vllm + 48-80gb
  - `smollm-gguf/` — llamacpp + 24gb
- `model.yaml` schema (minimum viable):
  ```yaml
  hf_repo: Qwen/Qwen2.5-7B-Instruct
  engine: vllm            # vllm | llamacpp
  tier: 24gb              # 24gb | 48-80gb
  # optional:
  hf_file: ...            # for llamacpp gguf models
  extra_args: []          # passed through to the engine
  min_disk_gb: 100
  notes: ""               # free text, shown in `skyllm list`
  ```
- Each dir is also a natural place for future per-model assets (chat templates, eval notes) without reshaping the catalog.

Open questions:
- Strict schema validation at CLI level, or best-effort + trust? (Lean strict — pydantic model. Bad model.yaml should fail loudly.)
- Where does `LLM_MODEL` live now? In `.env` it stops being required; the CLI computes it from the chosen model. `.env` keeps only infra secrets (CF token, API key, HF token, runpod key).

---

## Phase 3 — Python CLI (`skyllm`)

Goal: `skyllm up <model>` replaces `make up`. Flexible overrides, no bash.

Scope:
- `pyproject.toml` (pixi workspace or plain hatchling — probably pixi so the whole repo is a pixi project).
- Deps: typer (CLI), pydantic (model.yaml schema), ruamel.yaml or pyyaml, `skypilot[runpod]`.
- Commands:
  - `skyllm list` — scan `models/`, print `name | engine | tier | hf_repo | notes`.
  - `skyllm up <model> [--tier=...] [--override KEY=VAL ...] [--dry-run]`
  - `skyllm down`
  - `skyllm status`
  - `skyllm logs`
  - `skyllm health` (hits the public URL like the current Makefile target)
  - `skyllm cost`
  - `skyllm budget` (runs `scripts/budget-check.sh`)
- `up` control flow:
  1. Load `models/<name>/model.yaml`, validate via pydantic.
  2. Resolve `(engine, tier)` → preset path (`sky.yaml` / `sky-big.yaml` / `sky-llamacpp.yaml`). `--tier` overrides.
  3. Build env dict: `.env` values + model fields (`LLM_MODEL`, `LLM_HF_REPO`, `LLM_HF_FILE`, …) + `--override KEY=VAL` entries.
  4. Either shell out to `sky launch -c llm -y <preset> --env-file .env --env KEY=VAL …` or call `sky.api.launch(...)`. Start with shell-out; migrate to the Python API if it gives us something concrete.
  5. `--dry-run` prints the resolved command + env without launching.
- Keep the Makefile as a thin alias layer for muscle memory (`make up` → `skyllm up <default-model>`), or delete it. Defer the call.

Open questions:
- Default model when `skyllm up` is called with no argument — probably `qwen-0.5b` to match current behavior.
- Override granularity. `--override accelerators='{H100:1}'` requires mutating the YAML before `sky launch` sees it. v1 can restrict overrides to env vars (which SkyPilot passes through natively) and punt on resource-block overrides.
- Status of `$LLM_MODEL` in `.env` — probably removed; the CLI is now the source of truth.

---

## Phase 4 — Multi-provider (deferred, don't start)

Goal: let SkyPilot pick the cheapest GPU across clouds.

Not started until phases 1–3 ship. When we get here:
- Drop `cloud: runpod` from presets, expand accelerator lists.
- Per-cloud image selection: `vllm/vllm-openai` works on AWS/GCP (VM with sshd underneath) but not RunPod (pod IS the container, no sshd in that image). Could cut cold-start on non-RunPod clouds, at the cost of branching logic in presets.
- Docs for enabling each cloud (`sky check aws`, etc.).
- Test matrix — this is most of the work.

Pixi helps here: once setup is "install pixi binary + `pixi install`," the setup block is genuinely identical across providers, so the multi-provider story gets simpler as a side effect of phase 1.

---

## Provider / machine variance tracker

Things we don't control that differ across providers (and sometimes between machines at the same provider). Track them here so the assumptions are explicit — when multi-provider bites us in phase 4, this is where to look first.

| Axis | Varies by | Current assumption | What we do if the assumption breaks |
|---|---|---|---|
| **CUDA driver version** | provider × base image × GPU pool | RunPod default image has driver ≥570 (supports CUDA 12.8+) | Pin pixi's CUDA build to the highest version the *oldest* supported driver can run. Forward-compat runs one way: newer driver ↦ older runtime OK; older driver ↦ newer runtime fails. |
| **CUDA runtime in our pixi env** | our choice | Pin a single conservative version across all providers (currently: llama.cpp `cuda129*` or `cuda130*` depending on spike result) | Separate pixi features per provider if a single pin stops covering everyone |
| **GPU compute capability (SM)** | accelerator type | conda-forge llama.cpp ships fat binaries; vllm wheels cover 7.0+ | Check before adding an unusual accelerator to the catalog |
| **sshd in the base image** | provider | RunPod default image has sshd (that's why we use it instead of pinning `vllm/vllm-openai`); AWS/GCP put sshd on the VM, not the container, so they CAN pin app images | Keep "install engine via pixi on a generic image" as the default path; add per-cloud image overrides only if cold-start becomes a real pain point |
| **sudo availability** | provider × image | Available on RunPod default; assume available | Install pixi to `~/.pixi` only (unprivileged) so we don't need sudo for the critical path |
| **Writable paths** | provider × image | `~`, `~/.cache`, `~/.pixi`, `/opt` (via sudo) | Keep state under `~` wherever possible |
| **Docker inside the pod** | provider | Not available on RunPod default (pod is already a container) | Don't introduce Docker-in-pod dependencies |
| **Disk size defaults** | provider | Small (~20–50 GB); we override via `disk_size:` in each preset | Raise `disk_size:` if a model's `min_disk_gb` exceeds the preset's |
| **HF model download bandwidth** | provider × region | Variable; not enforced | User can mount an R2/S3 HF cache bucket (README §"Bigger models") |
| **Default NVIDIA driver CUDA runtime shown by `nvidia-smi`** | provider × pool | Not the same as our pinned pixi runtime — they're independent as long as driver ≥ runtime | Only the pinned runtime matters; ignore `nvidia-smi`'s "CUDA Version:" for our runtime choice |

Policy: the pixi.lock is our single source of CUDA runtime truth. We pick *one* conservative pin, test against it on all providers we ship, and update the pin deliberately (not automatically).

---

## Sequencing

Phase 1 first — biggest leverage, touches all three YAMLs once and makes phases 2–3 cleaner (no more "did vllm drift between launches" troubleshooting). Phase 2 is data only, cheap, can happen in parallel with phase 3 if you want. Phase 3 is the real work. Phase 4 deferred.

## Next step

Phase 1 is closed. Pick up with **Phase 2 (model catalog)** or **Phase 3 (skyllm CLI)** — Phase 2 is cheap data-only work and can run in parallel with Phase 3 if desired, but Phase 3 is the real leverage (replaces `make up` with `skyllm up <model>`).
