# Phase 1 — Pixi swap ✅ (2026-04-24)

Goal: replace `conda create + pip install` and the llama.cpp source build with a pinned `pixi.toml` + `pixi.lock`. Gives reproducible envs across launches and across providers.

Status: **Done.** Pixi.toml shape validated locally on driver 580/CUDA 13.0 (2026-04-23). E2E on RunPod validated 2026-04-24: `sky.yaml` (vllm) and `sky-llamacpp.yaml` both reached a green `/v1/models` endpoint and tore down cleanly; cold start ~1 min for llama.cpp. Lessons captured in [`../pixi.md`](../pixi.md).

## What shipped

- `pixi.toml` + `pixi.lock` at repo root with two environments: `vllm`, `llamacpp`.
- All three sky YAMLs rewritten: setup installs pixi + `pixi install -e <env>`; run invokes via `pixi run -e <env> ...`.
- `sky-llamacpp.yaml` lost its entire source-build section (~10–15 min cold start → ~1–2 min).
- `.pixi/` added to `.gitignore`.

## Not validated (deferred, not blocking)

- `sky-big.yaml` e2e on RunPod. Shares the vllm pixi env with `sky.yaml`, so the pip/conda side is already proven; only the accelerator list and `MAX_RUNTIME_MINUTES=60` are untested. Will validate lazily the first time a big model is actually needed.

  *(Superseded 2026-04-24: `sky-big.yaml` was dropped before ever being validated — see [Phase 3](phase-3-cli.md). The 80 GB path now lives in `sky-llamacpp-80gb.yaml`, and the large-vllm path was never exercised in practice.)*

## Confirmed via web checks + spike v1 (2026-04-23)

- **conda-forge ships CUDA builds of llama.cpp for linux-64** (CUDA 12.9 and 13.0 variants; v8722 latest). Source-build in `sky-llamacpp.yaml` can be deleted — cold start drops from 10–15 min to ~1 min.
- **conda-forge also ships vllm** (0.10.2) but it's behind pypi (0.19.1). Unused in our plan — we get vllm from pypi.
- **Pixi installs cleanly on the RunPod default pod image** (Ubuntu 22.04 + driver 570.211.01). `curl pixi.sh/install.sh | bash` works unprivileged.
- **Driver 570.211.01** on the RunPod default image natively supports CUDA ≤ 12.8. Conda-forge's `cuda129` and `cuda130` llama.cpp builds technically exceed that; they usually still run because conda-forge bundles runtime libs, but it's empirically verified by spike v2, not assumed.
- **`[system-requirements] cuda = "X"` in pixi.toml is REQUIRED** in the RunPod container — pixi's `__cuda` virtual-package detection fails there, and no CUDA conda-forge packages resolve without the declaration.
- **Why `pixi add --pypi vllm` fails** (root cause, not a workaround): vllm's wheel depends on torch-with-CUDA. The `torch` package on pypi.org is CPU-only; CUDA-linked torch wheels live on `https://download.pytorch.org/whl/cuXYZ/`. Uv/pixi looking only at pypi.org can't satisfy the CUDA-torch dep, gives up on the wheel, falls back to the vllm sdist — whose `setup.py` hits its `CUDA_HOME` assertion and dies. Misleading error; the real problem is torch resolution, not CUDA toolkit absence.
- **Fix: pull pytorch from conda-forge** (which has native CUDA builds) and vllm from pypi. Vllm's wheel then installs against the already-present torch without invoking sdist. This is Option B in the 2026-04-23 discussion. Cleaner than the `--torch-backend=auto` approach (Option A) because conda-forge handles the CUDA runtime consistently with llama.cpp.

## Locked-in pixi.toml shape (validated by spike v2)

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

Success criterion: `YAML=sky.yaml make up`, `YAML=sky-big.yaml make up`, `YAML=sky-llamacpp.yaml make up` all reach `/health` OK and shut down cleanly. `/v1/models` returns the expected model.
