# Landscape: what else already does this

Several commercial products and a few open-source peers overlap with what
skypilot-llms does. None are identical. The interesting question is not
"who's cheapest" but "which axis did each project optimize," because the
answer differs a lot.

Three categories, loosest overlap first, tightest last:

1. **Managed API** — they host everything, you send tokens.
2. **Hosted runtime, BYO model** — you pick the GPU + model, they run it.
3. **DIY siblings** — same stack we use, with a thinner wrapper or none.

Category 2 is the actual peer category. Category 1 is what people mention
first (because Ollama Cloud is well-known) but it's a structurally
different product. Category 3 is the "why does this repo exist at all"
counterweight.

---

## 1. Managed API: they host the model, you send tokens

You don't pick a GPU, you don't pick a region, you don't see the infra.
Bill is either a fixed subscription or per-token.

| Product | Pricing model | Notes |
|---|---|---|
| **Ollama Cloud** | $0 / $20 / $100 fixed tiers; opaque 5 h / 7 d session caps | NVIDIA Cloud Partner backend, specific provider undisclosed. Curated catalog (~20 models) including frontier open models (kimi-k2 ~1T, glm-5 744B, deepseek-v4) that are impractical to rent enough GPU for. No per-token option yet. |
| **Together AI** | Per-token, broad OSS catalog | Mature OpenAI-compatible API. No region selection. |
| **Fireworks AI** | Per-token, OSS + fine-tunes | Similar to Together; strong function-calling story. |
| **Groq** | Per-token, custom LPU silicon | Extremely fast tokens/sec on supported models. Limited catalog. US-only. |
| **Replicate** | Per-second, arbitrary containers | Can run any Cog container, not just chat. Flexible but chat isn't its strength. |
| **OpenRouter** | Per-token, routes across providers | A marketplace/facade, not a host. Useful as a failover router. |
| **Featherless** | $10–$75/mo subscription, HF catalog | Subscription access to a large slice of HuggingFace. Structurally closest to Ollama Cloud. |

**Where this category beats skypilot-llms:**

- Zero cold start.
- Access to frontier open models without sourcing the GPUs yourself.
- No ops — no tunnels, no pixi, no GPU availability hunting.
- Per-token pricing is genuinely cheaper at low sustained volume.

**Where skypilot-llms wins:**

- You know where the GPU is. Ollama won't tell you; Together / Fireworks / Groq don't let you pick.
- Your prompts don't traverse a vendor whose privacy policy or business model can change without notice.
- You pick the exact engine, quant, and flags (e.g. `--n-cpu-moe 48`, `--ctx-size 131072`, `--cache-type-k q8_0`). Managed APIs ship one fixed configuration per model.
- No per-month minimum.

---

## 2. Hosted runtime, BYO model: the actual peer category

You pick the GPU, the region, and the model. They run it. This is what
skypilot-llms *is* — we just own the control plane ourselves instead of
renting one.

| Product | Region selection | Lock-in |
|---|---|---|
| **HuggingFace Inference Endpoints** | AWS, GCP, Azure regions | HF's control plane. GGUF supported natively. Closest single alternative. |
| **Modal** | Their US/EU pools | Their Python SDK + decorator model. Serverless semantics, scale-to-zero. |
| **Baseten** | Their managed pools | Truss model-packaging spec. Polished product surface. |
| **RunPod Serverless** | Same RunPod regions we use | Their serverless runtime + worker API. Cold start on first request. |

All four price per compute-hour (or per compute-second for serverless).
All accept a container or a Python entrypoint. All handle the
infrastructure bits we handle ourselves — broken nodes, autoscaling,
dashboards.

**Where this category beats skypilot-llms:**

- Warm pools → faster cold starts.
- Polished dashboards, versioning, per-revision rollout.
- Someone else fixes broken GPU nodes at 3 a.m.
- Autoscaling is a checkbox, not a rewrite.

**Where skypilot-llms wins:**

- Not locked into one vendor's control plane. SkyPilot targets a dozen+ clouds; Phase 4 of our roadmap moves to AWS/GCP without rewriting configs. HF Endpoints, Modal, Baseten, RunPod Serverless — each ties you to their runtime.
- Reproducible envs via `pod/pixi.lock`. The exact versions that worked last week work today, deterministically.
- Catalog + presets are plain YAML in a git repo. Forkable, diffable, code-reviewable.
- Zero proprietary product surface to learn. If you know SkyPilot and pixi, you know this repo.
- MIT-licensed, no subscription, no auth service between you and the GPU.

---

## 3. DIY siblings: same stack, different (or no) wrapper

| Project | Relation |
|---|---|
| **Raw SkyPilot + a vLLM / llama.cpp YAML** | What skypilot-llms wraps. If you need one model once, you don't need us. |
| **dstack** | SkyPilot peer. Rejected for this project — see `docs/alternatives.md`. |
| **LM Studio / KoboldCPP / llama.cpp locally** | For when you already have a capable GPU. No cloud at all. |

These aren't competitors — they're the stack below us or next to us.
Category 1 and 2's existence is the argument that a small opinionated
wrapper is useful; category 3's existence is the argument against. The
honest answer is that if your needs are simple and one-shot, raw
SkyPilot is fine. This repo earns its keep once you have a handful of
models with different engines and tiers and want `skyllm up <name>` to
pick the right preset.

---

## Honest positioning

skypilot-llms is the right choice when all three of these hold:

1. You want a specific provider / region — for compliance, latency, or because you just want to know where your prompts go.
2. You care about reproducibility (exact versions, exact flags) more than ops polish.
3. Your usage is bursty enough that idle cost matters — $0/month when idle beats $20/month for capacity you don't touch.

It is *not* the right choice when:

- You want a frontier open model (kimi-k2, glm-5) and don't want to stitch together 8×H100.
- You need sub-second cold start.
- You don't want to think about pixi, tunnels, or RunPod GPU availability.

For those cases, Ollama Cloud's $20 Pro tier is a genuinely good default,
and HuggingFace Inference Endpoints is the cleanest upgrade path when you
still want BYO-model but don't want to own the control plane.

---

*Last re-verified: 2026-04-24. Categories, products, and pricing models
change fast — re-check before citing specifics.*
