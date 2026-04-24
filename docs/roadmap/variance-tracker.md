# Provider / machine variance tracker

Things we don't control that differ across providers (and sometimes between machines at the same provider). Track them here so the assumptions are explicit — when multi-provider bites us in [Phase 4](phase-4-multi-provider.md), this is where to look first.

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
| **Container CPU quota (cgroup)** | provider × SKU | Can be far lower than advertised vCPUs — e.g. 7.65 effective on a RunPod L40S SKU advertising 12 | Filter `cpus: N+` in presets that depend on CPU bandwidth (notably `sky-llamacpp-cpumoe.yaml`). Measure, don't trust SKU metadata. |
| **Default NVIDIA driver CUDA runtime shown by `nvidia-smi`** | provider × pool | Not the same as our pinned pixi runtime — they're independent as long as driver ≥ runtime | Only the pinned runtime matters; ignore `nvidia-smi`'s "CUDA Version:" for our runtime choice |

Policy: the pixi.lock is our single source of CUDA runtime truth. We pick *one* conservative pin, test against it on all providers we ship, and update the pin deliberately (not automatically).
