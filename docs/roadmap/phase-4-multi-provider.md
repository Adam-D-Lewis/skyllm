# Phase 4 — Multi-provider (deferred, don't start)

Goal: let SkyPilot pick the cheapest GPU across clouds.

Not started until phases 1–3 ship. When we get here:

- Drop `cloud: runpod` from presets, expand accelerator lists.
- Per-cloud image selection: `vllm/vllm-openai` works on AWS/GCP (VM with sshd underneath) but not RunPod (pod IS the container, no sshd in that image). Could cut cold-start on non-RunPod clouds, at the cost of branching logic in presets.
- Docs for enabling each cloud (`sky check aws`, etc.).
- Test matrix — this is most of the work.

Pixi helps here: once setup is "install pixi binary + `pixi install`," the setup block is genuinely identical across providers, so the multi-provider story gets simpler as a side effect of [Phase 1](phase-1-pixi.md).

## Sequencing note

[Phase 1](phase-1-pixi.md) was sequenced first because it touched all three YAMLs once and made phases 2–3 cleaner (no more "did vllm drift between launches" troubleshooting). [Phase 2](phase-2-catalog.md) was data-only and cheap. [Phase 3](phase-3-cli.md) was the real work. Phase 4 is the last piece and only becomes load-bearing once RunPod availability becomes a real pain point.
