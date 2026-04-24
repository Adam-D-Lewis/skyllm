# Roadmap

Target end state: `skyllm up <model>` launches the right SkyPilot preset with the right engine and GPU tier, from a declarative model catalog. Multi-provider is a later phase.

## Design decisions (stable across phases)

- **Engine axis (vllm / llama.cpp)** maps to pixi environments.
- **GPU tier axis (24 GB / 24 GB + CPU-offloaded MoE / 80 GB pure-GPU)** maps to sky YAML presets. All llama.cpp presets share the same pixi env.
- Sky YAMLs stay as separate, self-contained files — no templating yet.
- Model config drives tier selection; user picks model, CLI picks preset.
- CLI in Python (SkyPilot is Python, so we can `import sky` instead of shelling out).
- Model catalog is directory-per-model (`models/<name>/model.yaml`), not a single big file.

## Phases

| Phase | Status | Summary |
|---|---|---|
| [Phase 1 — Pixi swap](phase-1-pixi.md) | ✅ 2026-04-24 | Replace `conda create + pip install` with pinned `pixi.toml` + `pixi.lock`. Cold start on llama.cpp dropped from ~10–15 min to ~1 min. |
| [Phase 2 — Model catalog](phase-2-catalog.md) | ✅ 2026-04-24 | Pydantic `ModelSpec` + `models/<name>/model.yaml` entries + validator. Makes "what we support" data, not README instructions. |
| [Phase 3 — Python CLI (`skyllm`)](phase-3-cli.md) | ✅ 2026-04-24 | `skyllm up <model>` replaces `make up`. Catalog drives identity. Pixi split between local `cli` env and pod `vllm`/`llamacpp` envs; explicit `file_mounts:` allowlist replaces `workdir: .`. |
| [Phase 4 — Multi-provider](phase-4-multi-provider.md) | ⏸ deferred | Drop `cloud: runpod`, let SkyPilot pick cheapest across AWS/GCP/Lambda/etc. Not started. |

## Cross-phase reference

- [Provider / machine variance tracker](variance-tracker.md) — things that differ across providers or machines that we don't control (CUDA driver, sshd, writable paths, etc.). The single source of truth for "why does this pixi pin exist."

## Current status

Phases 1–3 are closed. Scaffold is production-shaped for single-provider 24 GB-tier use plus the two big-tier llama.cpp paths (`24gb-cpumoe`, `80gb`).

The natural next move is **Phase 4 (multi-provider)** when/if RunPod availability becomes a real pain point, OR one of the Phase 3 deferrals if a second model wants the same two-deployment-shape treatment (see [Phase 3 → Known schema gap](phase-3-cli.md#known-schema-gap)). Neither is urgent.
