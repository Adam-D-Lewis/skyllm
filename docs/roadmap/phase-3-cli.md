# Phase 3 — Python CLI (`skyllm`) ✅ (2026-04-24)

Goal: `skyllm up <model>` replaces `make up`. Catalog drives identity, no bash.

Status: **Done.** E2E on RunPod validated 2026-04-24: `skyllm up qwen-0.5b` provisions an RTXA5000 in RunPod CA, `pixi install -e vllm` runs against the file-mounted `pod/pixi.toml`, vLLM serves `/v1/models` via the Cloudflare tunnel, `skyllm down` tears down cleanly.

## What shipped

- `skyllm/cli.py` — typer app with `list / up / down / status / logs / health / cost / budget` + `--dry-run` on `up`. Shells out to `sky launch`. Default model is `qwen-0.5b`.
- `pyproject.toml` at repo root — declares the `skyllm` package (hatchling) with `[project.scripts] skyllm = "skyllm.cli:app"`. Runtime deps: pydantic, pyyaml, typer, requests.
- **Pixi split** — root `pixi.toml` now holds *only* the `cli` env (set as default: `pixi run skyllm …` works with no `-e`). Pod engine envs (`vllm`, `llamacpp`) moved to `pod/pixi.toml` + `pod/pixi.lock`. Clean isolation — nothing from the root workspace can accidentally ship to RunPod.
- **`workdir: .` → explicit `file_mounts`** in all sky YAMLs. Only three files ride up to the pod: `pod/pixi.toml`, `pod/pixi.lock`, `scripts/idle-watch.sh`. Accidental-secret-leakage surface = zero.
- **Makefile deleted.** `Makefile` targets (`make up/down/health/…`) are gone; README fully switched to `pixi run skyllm …`.
- **`.env.example` stripped** of `LLM_MODEL` / `LLM_HF_REPO` / `LLM_HF_FILE` — model identity lives in the catalog now. The CLI injects those three as `--env KEY=VAL` to SkyPilot, which the preset YAMLs' `envs:` blocks consume at launch.
- **Tier retirement (late Phase 3, 2026-04-24).** The `48-80gb` tier was dropped entirely (never validated, never used). Replaced with two purpose-built llama.cpp tiers:
  - `24gb-cpumoe` — cheap 24 GB card + ~96 GB system RAM, expert weights CPU-offloaded via `--n-cpu-moe 48`. `sky/sky-llamacpp-cpumoe.yaml`.
  - `80gb` — A100-80GB / H100 pure-GPU, for GGUFs up to ~50 GB. `sky/sky-llamacpp-80gb.yaml` (renamed from `sky-big-llamacpp.yaml`).
  - Catalog now: `qwen-0.5b` (vllm/24gb), `qwen3-coder-next` (llamacpp/24gb-cpumoe), `qwen3-coder-next-80gb` (llamacpp/80gb).
- `skyllm up qwen3-coder-next` and `skyllm up qwen3-coder-next-80gb` both validated e2e on RunPod with the llama.cpp #21280 workaround (see below).

## Decisions made during Phase 3 (not in the original scope)

- **Override surface deleted, not narrowed.** Original plan had `--override KEY=VAL`; dropped entirely. Anyone wanting a one-off override just edits `.env` (which wins over `--env`, see "Gotchas"). Catalog is the source of truth; `.env` is an escape hatch.
- **`skypilot[runpod]` is NOT installed in the cli pixi env.** Rely on the user's global `pip install skypilot[runpod]` (per README step 1) for the `sky` binary. Keeps the cli env small and CUDA-free.
- **Pre-checks for stale `.env` vars were considered and rejected.** If the user leaves `LLM_HF_REPO` in `.env`, it silently overrides the catalog's value (see Gotchas). Decision: leave `.env` as an override mechanism rather than add defensive validation.
- **Dropped `sky-big.yaml` and `sky-big-llamacpp.yaml` without validating them.** Both had been carried since Phase 2 as "will validate when a big model is first launched." When that moment came for Qwen3-Coder-Next, the 48 GB MXFP4 weights didn't fit on a 48 GB card (see L40S usable-VRAM note below) and the 80 GB split made more sense as two specialized presets than one wide-tier one. Kept `sky-llamacpp.yaml` as the orphaned-but-working 24 GB template; dropped the two untested ones.

## Gotchas worth knowing for whoever picks this up

- **`--env-file` overrides `--env`** in SkyPilot (documented [here](https://docs.skypilot.co/en/latest/running-jobs/environment-variables.html); counterintuitive relative to CLI conventions). Any key that lives in both `.env` and the CLI's `--env` flag will take its value from `.env`. This is why stale model-identity lines in `.env` silently broke our first `qwen3-coder-next` launch.
- **llama.cpp #21280 (build 8722 on conda-forge).** `common_pull_file` fails with `status: -1` on Xet-backed HF GGUFs. Both big-tier presets work around it by pre-downloading via `hf download --format quiet` and passing `-m <path>` to `llama-server`. Revisit when conda-forge ships a newer build.
- **L40S reports 44.4 GB usable VRAM**, not the nominal 48 GB. A 48 GB MXFP4 model can't live on L40S pure-GPU — use the `24gb-cpumoe` path instead (CPU offload) or jump to A100-80GB / H100 (`80gb` tier).
- **RunPod cgroup CPU quota** can limit a container to a fraction of the advertised vCPUs (e.g. 7.65 effective CPUs on a SKU advertising 12). This tanks CPU-offloaded inference. `sky-llamacpp-cpumoe.yaml` now filters `cpus: 16+` to skip the worst SKUs; re-measure before declaring the cpu-moe path usable for real workloads.

## Known schema gap

**Multiple deployment profiles for the same model aren't cleanly expressible.** Today a catalog entry declares one `(engine, tier)` pair and the CLI routes to one preset. Qwen3-Coder-Next has two legitimate shapes — pure-GPU on an 80 GB card vs. CPU-offloaded MoE on a 24 GB card + big RAM — so we shipped two colocated catalog entries (`qwen3-coder-next` + `qwen3-coder-next-80gb`) that share `hf_repo` / `hf_file` but differ in `tier`.

The right fix is a schema refactor (one model, N deployment shapes), but defer until a third model actually wants the same treatment — two data points isn't enough to design the right shape.

## Perf notes

### `24gb-cpumoe` path (e2e 2026-04-24, RunPod `1x_L40S_SECURE`, $0.86/hr)

- Correctness validated end-to-end (download via `hf`, llama-server loads 48 GB MXFP4 MoE, real `/v1/chat/completions` returns generated text).
- But **~0.6 tok/s** on L40S (7.65 effective vCPUs after cgroup quota, out of the 12 advertised). Orders of magnitude below the owner's local 3060 setup. Root cause is CPU core-count + memory-bandwidth during token generation — every token waits on MoE experts paging from system RAM through a thin CPU slice.
- `sky/sky-llamacpp-cpumoe.yaml` now filters `cpus: 16+` + `memory: 64+` to skip the smallest RunPod SKUs. Un-validated whether that's actually faster; availability may narrow. Re-test before declaring the cpu-moe path practical for anything beyond correctness smoke tests.
- Use this path for functional validation, not real inference workloads.

### `80gb` pure-GPU path (e2e 2026-04-24, RunPod `1x_A100-80GB_SECURE`, $1.39/hr)

- Prompt eval: ~1,500 tok/s (7,118-token prompt in 4.7 s).
- Generation: ~97 tok/s (200 tokens in 2.1 s).
- Cold start: ~2 min provisioning (after exhausting several regions for 80 GB card availability) + ~45 s for the 48 GB GGUF download from HF via `hf` + ~1 min for CUDA init / weight load.
- ~170× faster than the cpumoe path per generated token. The cost multiple is ~1.6× ($1.39 vs $0.86/hr); it's effectively always worth paying for when availability allows.
