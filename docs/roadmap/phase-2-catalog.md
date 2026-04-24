# Phase 2 — Model catalog ✅ (2026-04-24, initial scaffold)

Goal: make "what models we support" a piece of data, not instructions in the README.

Status: **Scaffold shipped.** Pydantic schema + validator + two initial entries + a new big-tier llama.cpp preset.

## What shipped

- `skyllm/schema.py` — pydantic `ModelSpec` with strict enums for engine (`vllm|llamacpp`) and tier (`24gb|48-80gb` at the time; see [Phase 3](phase-3-cli.md) for the current tier set). `engine=llamacpp` requires `hf_file`; `engine=vllm` forbids it.
- `skyllm/validate.py` — loads every `models/*/model.yaml` and exits nonzero on any schema failure. Wired to `pixi run -e cli validate`.
- `pixi.toml` gains a `cli` feature/env (pydantic + pyyaml, no CUDA) — same env Phase 3 will grow into for typer + skypilot.
- Catalog entries:
  - `models/qwen-0.5b/` — vllm + 24gb (stack-test default, identity with `.env.example`'s `LLM_MODEL`).
  - `models/qwen3-coder-next/` — llamacpp + 48-80gb, `unsloth/Qwen3-Coder-Next-GGUF` / `Qwen3-Coder-Next-MXFP4_MOE.gguf` (~48 GB).
- `sky-big-llamacpp.yaml` — new preset bridging the llama.cpp engine with the 48–80 GB tier (parallels `sky-big.yaml`'s shape; required because Qwen3-Coder-Next ships only as GGUF and vLLM's GGUF support is still experimental).

  *(Superseded 2026-04-24: renamed to `sky-llamacpp-80gb.yaml` and narrowed to A100-80GB / H100; see [Phase 3](phase-3-cli.md).)*

## Deferred (to Phase 3 or first real use)

- E2E RunPod validation of `sky-big-llamacpp.yaml` with the Qwen3-Coder-Next MXFP4 model — will happen naturally when the model is first launched via `skyllm up qwen3-coder-next` (Phase 3).
- Pulling the earlier catalog suggestions (qwen-7b, llama-8b, llama-70b-fp8, smollm-gguf) — owner scoped this down to "stack-test + qwen3-coder-next" only. Add others lazily as they're actually used.
- Removing `LLM_MODEL` / `LLM_HF_REPO` / `LLM_HF_FILE` from `.env.example` — can't happen until Phase 3's CLI becomes the source of truth; today `make up` still reads those.

## Schema reference (Phase-2-era shape)

```yaml
hf_repo: Qwen/Qwen2.5-7B-Instruct
engine: vllm            # vllm | llamacpp
tier: 24gb              # 24gb | 48-80gb
# optional:
hf_file: ...            # required when engine=llamacpp
extra_args: []          # passed through to the engine
min_disk_gb: 100
notes: ""               # free text, shown in `skyllm list`
```

Each dir is also a natural place for future per-model assets (chat templates, eval notes) without reshaping the catalog.

See [Phase 3](phase-3-cli.md) for the current tier set (`24gb | 24gb-cpumoe | 80gb`) and the `48-80gb` retirement.
