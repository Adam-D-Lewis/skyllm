# Pixi lessons

Notes from setting up the pixi-based environments in this repo (vLLM + llama.cpp), for future reference when adding more engines or models. The decisions here were not obvious and several of them are documented gotchas rather than preferences.

If you're about to add a new environment (another engine, a model with specific deps, a different quantization toolchain), read this first.

---

## The winning shape (for context)

```toml
[workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]

[system-requirements]
cuda = "12"          # intentionally lenient — see §"system-requirements"
libc = "2.31"

[dependencies]
python = "3.12.*"

[feature.vllm.dependencies]
cuda-toolkit = "12.9.*"   # sets CUDA_HOME in the env, see §"cuda-toolkit helper"

[feature.vllm.pypi-dependencies]
vllm = "*"                # torch 2.10.0+cu128 comes as a transitive

[feature.llamacpp.dependencies]
"llama.cpp" = { version = "*", build = "cuda129*" }  # conda-forge prebuilt

[environments]
vllm = { features = ["vllm"], solve-group = "vllm" }
llamacpp = { features = ["llamacpp"], solve-group = "llamacpp" }
```

This resolves, installs, and runs on both driver 570 and 580 RunPod pods. It did not work the first five tries — the failure modes below are what got us here.

---

## The three pixi PyTorch patterns

Pixi docs document three ways to install PyTorch. Pick **one** — do not mix:

1. **All conda-forge.** `pytorch-gpu = "*"` (or `pytorch = { build = "cuda*" }`). Clean, lockfile-pure, but conda-forge packages downstream of torch (like `vllm`) lag pypi significantly. conda-forge vllm was 0.10.2 when pypi had 0.19.1.
2. **All pypi.** `vllm = "*"` in `pypi-dependencies`; torch comes as a transitive from pypi. **PyPI's torch wheel is CUDA-enabled for linux-x86_64** (873 MB, yes really — I assumed otherwise and burned a lot of time). This is what we use.
3. **PyTorch's own conda channel.** Deprecated. Don't.

### The don't-mix rule (load-bearing)

> "If you install PyTorch from pypi, all packages that depend on torch must also come from PyPI." — [pixi.prefix.dev/latest/python/pytorch/](https://pixi.prefix.dev/latest/python/pytorch/)

Mixing conda-forge `pytorch` with pypi `vllm` (or any pypi torch consumer) causes `ImportError: ... undefined symbol: _ZN3c104cuda9SetDeviceEi` at import. Reason: pypi's vllm wheel was compiled against PyTorch's official wheels, whose libtorch is ABI-different from conda-forge's libtorch even at the same version number. Symbol lookup at import fails.

If you hit a `c10::cuda::...` undefined-symbol error at import, you are violating this rule.

---

## cuda-toolkit helper (the trick)

The non-obvious move that made everything work: add `cuda-toolkit` from conda-forge as a non-torch dep.

```toml
[feature.vllm.dependencies]
cuda-toolkit = "12.9.*"
```

`cuda-toolkit` doesn't depend on torch, so it doesn't violate the don't-mix rule. What it *does* do is populate `CUDA_HOME` in the activated pixi env. This matters because:

- If uv ever falls through to building a CUDA package from sdist (e.g. when no wheel matches), the build finds `CUDA_HOME` and succeeds.
- Some pypi packages check `CUDA_HOME` at install time even when they have wheels available, to decide whether to emit a CPU fallback.
- It's cheap — `cuda-toolkit` from conda-forge is lightweight metapackage, pulls in `nvcc`/headers/libs into the env only.

Without `cuda-toolkit`, the canonical vllm failure is `AssertionError: CUDA_HOME is not set` in `setup.py` during sdist fallback.

---

## system-requirements (the container gotcha)

Pixi detects CUDA availability via the `__cuda` virtual package. **On RunPod pods (and most container environments), this auto-detection fails**, so you must declare it explicitly:

```toml
[system-requirements]
cuda = "12"
libc = "2.31"
```

Without this, every conda-forge CUDA package fails to resolve with:
```
llama.cpp * cuda129* cannot be installed because there are no viable options:
  └─ __cuda *, for which no candidates were found.
```

### Pick the loosest CUDA version you can

`cuda = "12"` not `cuda = "12.9"`. The stricter you pin, the more pods/machines your config will refuse to install on. We've seen:
- RunPod driver 570.211.01 → native CUDA 12.8
- RunPod driver 580.126.xx → native CUDA 13.0

A stricter `cuda = "12.9"` excludes driver 570 pods at solve time even though cuda129-tagged packages run fine on them (conda-forge bundles runtime libs). Prefer `cuda = "12"` unless a specific package in your deps requires the tighter constraint.

`libc = "2.31"` matches the manylinux_2_31 wheel baseline most pypi packages target. Ubuntu 22.04 has glibc 2.35 — comfortably satisfies.

---

## Build-string pinning for conda-forge packages

conda-forge ships CUDA-tagged variants for many packages. The syntax to pin a specific build:

```toml
"llama.cpp" = { version = "*", build = "cuda129*" }
```

Available variants for `llama.cpp` on conda-forge (linux-64) as of 2026-04:
- `cuda129_*` — CUDA 12.9 (works on driver 570+ empirically)
- `cuda130_*` — CUDA 13.0 (needs driver 580+)

To see what's available for a package:
```bash
pixi search -c conda-forge <package> --platform linux-64 | tail -40
```

---

## Environments + features + solve-groups

The pattern used here:

```toml
[feature.vllm.dependencies]    # or .pypi-dependencies
...

[environments]
vllm = { features = ["vllm"], solve-group = "vllm" }
llamacpp = { features = ["llamacpp"], solve-group = "llamacpp" }
```

- **Features** are composable bundles of deps. A `feature.X` is not an environment on its own.
- **Environments** pick a set of features. Each environment resolves independently.
- **solve-group** forces multiple environments to be resolved together (shared package versions). We use one solve-group per environment here because vllm and llamacpp have no overlapping deps worth sharing; each env stays independent.

When adding a new engine/model, follow the same shape:
1. Add `[feature.<name>.dependencies]` (conda deps) and/or `[feature.<name>.pypi-dependencies]`.
2. Add `<name> = { features = ["<name>"], solve-group = "<name>" }` under `[environments]`.
3. Reference via `pixi install -e <name>` and `pixi run -e <name> ...`.

---

## The libstdc++ load-order trap (RunPod + vllm)

On RunPod's default pod image (Ubuntu 22.04 — system `libstdc++` from gcc 11, max `CXXABI_1.3.13`), running vllm fails at import with:

```
ImportError: /lib/x86_64-linux-gnu/libstdc++.so.6: version `CXXABI_1.3.15' not found
  (required by .pixi/envs/vllm/.../libicui18n.so.78)
```

The env has a newer `libstdc++` (from `libstdcxx 15.2`) and the conda-forge `.so` files have correct `DT_RPATH = $ORIGIN/.`. `ldd` on `libicui18n.so.78` correctly resolves to the env's libstdc++, and direct `import sqlite3` in the env's python prints `sqlite OK`. But importing `vllm.entrypoints.openai.api_server` still fails — because something in vllm's transitive imports (torch's CUDA extensions, xgrammar, or similar) dlopens system `libstdc++.so.6` *before* `diskcache → sqlite3 → _sqlite3 → libicui18n` needs the newer one. Once a `libstdc++.so.6` is loaded into the process, subsequent dlopens reuse that instance — the RPATH-correct env lib never gets a chance.

This is not an interactive-shell problem (we tested `bash --norc`, `bash -i`, `bash -c`; all fail). Not a `pixi run` problem (also fails with direct binary invocation). Not a conda-init problem (unsetting `CONDA_*` doesn't help). The root cause is deep in vllm's C-extension load order.

**Fix — LD_PRELOAD the env's libstdc++** so it's mapped first and pins the *correct* one for the whole process:

```bash
VLLM_ENV="$HOME/sky_workdir/.pixi/envs/vllm"
LD_PRELOAD="$VLLM_ENV/lib/libstdc++.so.6" "$VLLM_ENV/bin/python" -m vllm.entrypoints.openai.api_server ...
```

Scope the LD_PRELOAD to the specific command (prefix, not global export) — otherwise it bleeds into unrelated binaries (cloudflared, curl, pkill) that may misbehave when forced to use conda-forge's libstdc++.

Also use the **env's python directly** (not `pixi run`) — `pixi run` doesn't set `LD_LIBRARY_PATH` and adds nothing helpful here. Env binaries have correct RPATH so they resolve the rest of their deps from the env's lib dir without needing activation.

All three sky YAMLs in this repo apply both (direct invoke + LD_PRELOAD) to the engine command. Cloudflared doesn't need either (it's statically-linked Go).

(If a future vllm release stops triggering this, we can drop the LD_PRELOAD — no change to pixi.toml needed.)

---

## Failure-mode catalog (what these errors mean)

| Error | Cause | Fix |
|---|---|---|
| `__cuda *, for which no candidates were found` | Virtual CUDA package not detected | Add `[system-requirements] cuda = "12"` |
| `AssertionError: CUDA_HOME is not set` during sdist build | Package fell through to source build; no CUDA toolkit in env | Add `cuda-toolkit = "12.X.*"` as a conda dep |
| `undefined symbol: _ZN3c104cuda9SetDeviceEi` at import | conda-forge torch mixed with pypi torch-consumer | Don't mix: pick one pattern |
| `libstdc++.so.6: version 'CXXABI_1.3.15' not found` at import | System libstdc++ pinned by an earlier C-extension load before sqlite3 needs the newer one | `LD_PRELOAD="$env/lib/libstdc++.so.6" $env/bin/python ...` — scope to the command |
| `cuda-toolkit 12.X.* cannot be installed because ... strict channel priority` | Mixing the `nvidia` channel with conda-forge | Drop the nvidia channel; conda-forge ships cuda-toolkit too |
| `Failed to build <pypi-package>` with wheel available | uv couldn't satisfy dep graph via wheels, tried sdist | Check if the package's torch pin matches what pypi has |

---

## Things to test locally before cloud spikes

Resolution failures and ABI errors reproduce on any machine with the same python + pixi. Don't spin up a GPU pod to debug them:

```bash
mkdir /tmp/pixi-experiment && cd /tmp/pixi-experiment
# paste a test pixi.toml here
pixi install -e <feature>
pixi run -e <feature> python -c "import <your-package>"
```

Even the ABI import check (`import vllm`) works on CPU — the `.so` linkage check fires at import time, not at GPU-call time.

Reserve cloud spikes for things that actually require a GPU / a specific provider environment: inference throughput, specific driver combos, shared-library presence questions.

### Reproducing RunPod-specific issues locally

Pure resolution failures reproduce on any Linux box. But issues that depend on the *OS's* system libraries (old libstdc++ on Ubuntu 22.04, for example — which we hit with the CXXABI_1.3.15 bug) won't show up on a modern bare-metal host.

Use the RunPod base image directly:

```bash
docker run --rm -it -v "$HOME:$HOME" -w "$HOME/CodingProjects/skyllm" \
  runpod/base:1.0.2-ubuntu2204 bash
# inside the container:
curl -fsSL https://pixi.sh/install.sh | bash
export PATH=~/.pixi/bin:$PATH
pixi install -e vllm
.pixi/envs/vllm/bin/python -m vllm.entrypoints.openai.api_server --help
```

Advantages over plain `ubuntu:22.04`:
- Matches exactly what SkyPilot provisions on RunPod.
- Has miniconda pre-installed with `conda init` in `.bashrc` — reproduces the bash-i/conda-activation behavior too.
- No GPU needed for ABI and import-time failures.

Add `--gpus all` (needs nvidia-container-toolkit) if you also want to test CUDA runtime stuff locally.

**Rule of thumb**: if the bug is "X fails to import / install / resolve", reproduce in Docker. If the bug is "inference is slow" or "OOM on H100", spin up the actual hardware.

---

## References

- Pixi PyTorch guide: [pixi.prefix.dev/latest/python/pytorch/](https://pixi.prefix.dev/latest/python/pytorch/)
- Pixi system-requirements: [pixi.prefix.dev/latest/workspace/system_requirements/](https://pixi.prefix.dev/latest/workspace/system_requirements/)
- Related issue (still open): [prefix-dev/pixi#2033 — Building PIP packages with PyTorch/CUDA deps](https://github.com/prefix-dev/pixi/issues/2033)
- Working reference in this owner's other projects: `~/CodingProjects/qwen3-model/pixi.toml`
