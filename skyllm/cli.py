"""`skyllm` CLI — launches on-demand cloud LLM endpoints from the model catalog.

Replaces the old `make up` / `make down` / ... Makefile. `up` loads
`models/<name>/model.yaml`, maps `(engine, tier)` to a SkyPilot preset, and
shells out to `sky launch` with the model-identity env vars the preset's
`envs:` block expects.

Run via `pixi run -e cli skyllm <cmd>` (or bare `skyllm` inside `pixi shell
-e cli`). The `sky` binary is expected on PATH — install it globally per
the README (`pip install 'skypilot[runpod]'`).
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import typer

from skyllm import schema

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "models"
ENV_FILE = ROOT / ".env"
BUDGET_SCRIPT = ROOT / "scripts" / "budget-check.sh"

CLUSTER = "llm"
DEFAULT_MODEL = "qwen-0.5b"

# (engine, tier) → sky preset path (relative to ROOT). Exhaustive for the
# current catalog. All preset YAMLs live under sky/ to keep the repo root lean.
PRESETS: dict[tuple[str, str], str] = {
    ("vllm", "24gb"): "sky/sky.yaml",
    ("vllm", "48-80gb"): "sky/sky-big.yaml",
    ("llamacpp", "24gb"): "sky/sky-llamacpp.yaml",
    ("llamacpp", "48-80gb"): "sky/sky-big-llamacpp.yaml",
    ("llamacpp", "24gb-cpumoe"): "sky/sky-llamacpp-cpumoe.yaml",
}

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Launch on-demand cloud LLM endpoints from a model catalog.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_spec(name: str) -> schema.ModelSpec:
    model_dir = CATALOG / name
    if not (model_dir / "model.yaml").is_file():
        avail = sorted(
            d.name for d in CATALOG.iterdir() if (d / "model.yaml").is_file()
        )
        raise typer.BadParameter(
            f"unknown model '{name}'. Available: {', '.join(avail) or '(none)'}"
        )
    return schema.load(model_dir)


def _preset_for(spec: schema.ModelSpec) -> Path:
    try:
        return ROOT / PRESETS[(spec.engine, spec.tier)]
    except KeyError as e:
        raise typer.BadParameter(
            f"no preset wired for engine={spec.engine} tier={spec.tier}"
        ) from e


def _model_env(spec: schema.ModelSpec) -> list[tuple[str, str]]:
    """Model-identity env vars to pass to `sky launch` — engine-dependent."""
    if spec.engine == "vllm":
        return [("LLM_MODEL", spec.hf_repo)]
    # llamacpp — schema guarantees hf_file is set
    assert spec.hf_file is not None
    return [("LLM_HF_REPO", spec.hf_repo), ("LLM_HF_FILE", spec.hf_file)]


def _read_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env reader for local CLI commands (health, etc.).

    Handles KEY=VAL, # comments, and matching single/double quotes around the
    value. Does NOT do bash-like variable expansion — we don't need it.
    """
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        out[k] = v
    return out


def _run(cmd: list[str]) -> int:
    """Exec `cmd` in ROOT; inherit stdio; return exit code."""
    return subprocess.call(cmd, cwd=ROOT)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("list")
def cmd_list() -> None:
    """List catalog entries."""
    specs = schema.load_all(CATALOG)
    if not specs:
        typer.echo("no models in catalog", err=True)
        raise typer.Exit(1)
    name_w = max(len("NAME"), max(len(n) for n in specs))
    typer.echo(f"{'NAME':<{name_w}}  ENGINE    TIER      HF_REPO")
    for name, spec in specs.items():
        hf = spec.hf_repo + (f" ({spec.hf_file})" if spec.hf_file else "")
        typer.echo(f"{name:<{name_w}}  {spec.engine:<8}  {spec.tier:<8}  {hf}")


@app.command("up")
def cmd_up(
    model: str = typer.Argument(
        DEFAULT_MODEL,
        help=f"Model name from models/*/ (default: {DEFAULT_MODEL}).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the resolved sky launch command instead of executing it.",
    ),
) -> None:
    """Provision the GPU cluster and serve the selected model."""
    spec = _load_spec(model)
    preset = _preset_for(spec)

    if not ENV_FILE.is_file() and not dry_run:
        typer.echo(
            f".env not found at {ENV_FILE}.\n  cp .env.example .env\n"
            f"  # then fill in LLM_HOSTNAME, CF_TUNNEL_TOKEN, LLM_API_KEY, RUNPOD_API_KEY",
            err=True,
        )
        raise typer.Exit(1)

    cmd = [
        "sky", "launch", "-c", CLUSTER, "-y", str(preset),
        "--env-file", str(ENV_FILE),
        "--idle-minutes-to-autostop", "30", "--down",
    ]
    for k, v in _model_env(spec):
        cmd += ["--env", f"{k}={v}"]

    if dry_run:
        typer.echo(" ".join(shlex.quote(p) for p in cmd))
        return
    raise typer.Exit(_run(cmd))


@app.command("down")
def cmd_down() -> None:
    """Terminate the cluster."""
    raise typer.Exit(_run(["sky", "down", "-y", CLUSTER]))


@app.command("status")
def cmd_status() -> None:
    """Show cluster status."""
    raise typer.Exit(_run(["sky", "status", CLUSTER]))


@app.command("logs")
def cmd_logs() -> None:
    """Tail the cluster's logs."""
    raise typer.Exit(_run(["sky", "logs", CLUSTER]))


@app.command("health")
def cmd_health() -> None:
    """Hit the public /v1/models endpoint and confirm it responds."""
    import requests

    env = {**_read_dotenv(ENV_FILE), **os.environ}
    host = env.get("LLM_HOSTNAME")
    key = env.get("LLM_API_KEY")
    if not host:
        typer.echo("LLM_HOSTNAME not set in .env", err=True)
        raise typer.Exit(1)
    if not key:
        typer.echo("LLM_API_KEY not set in .env", err=True)
        raise typer.Exit(1)
    url = f"https://{host}/v1/models"
    try:
        r = requests.get(
            url, headers={"Authorization": f"Bearer {key}"}, timeout=10
        )
    except requests.RequestException as e:
        typer.echo(f"FAIL — {e}", err=True)
        raise typer.Exit(1) from e
    if r.ok:
        typer.echo(r.text)
        typer.echo("OK")
    else:
        typer.echo(f"FAIL — HTTP {r.status_code}: {r.text}", err=True)
        raise typer.Exit(1)


@app.command("cost")
def cmd_cost() -> None:
    """Show SkyPilot's cost report."""
    raise typer.Exit(_run(["sky", "cost-report"]))


@app.command("budget")
def cmd_budget() -> None:
    """Run the monthly budget guard once (scripts/budget-check.sh)."""
    raise typer.Exit(_run(["bash", str(BUDGET_SCRIPT)]))


if __name__ == "__main__":
    app()
