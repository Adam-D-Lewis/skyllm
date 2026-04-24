from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

Engine = Literal["vllm", "llamacpp"]
Tier = Literal["24gb", "24gb-cpumoe", "80gb"]


class ModelSpec(BaseModel):
    hf_repo: str
    engine: Engine
    tier: Tier
    hf_file: str | None = None
    extra_args: list[str] = Field(default_factory=list)
    min_disk_gb: int = 100
    notes: str = ""

    @model_validator(mode="after")
    def _llamacpp_needs_hf_file(self) -> ModelSpec:
        if self.engine == "llamacpp" and not self.hf_file:
            raise ValueError("engine=llamacpp requires hf_file (the GGUF filename)")
        if self.engine == "vllm" and self.hf_file:
            raise ValueError("engine=vllm does not take hf_file — set engine=llamacpp or remove it")
        return self


def load(model_dir: Path) -> ModelSpec:
    with (model_dir / "model.yaml").open() as f:
        return ModelSpec.model_validate(yaml.safe_load(f))


def load_all(catalog_root: Path) -> dict[str, ModelSpec]:
    return {d.name: load(d) for d in sorted(catalog_root.iterdir()) if (d / "model.yaml").is_file()}
