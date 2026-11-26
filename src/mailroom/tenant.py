"""Load and validate a tenant: YAML -> TenantConfig -> tools, dynamic schemas, prompt files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from . import REPO_ROOT
from .config.schema import TenantConfig
from .pipeline.schemas import build_fields_model, build_submission_model, build_triage_model
from .tools import ToolRegistry, build_registry

TENANTS_DIR = REPO_ROOT / "tenants"


class TenantConfigError(ValueError):
    pass


@dataclass
class Tenant:
    config: TenantConfig
    root: Path
    registry: ToolRegistry
    triage_model: type[BaseModel]
    fields_models: dict[str, type[BaseModel]] = field(default_factory=dict)
    submission_models: dict[str, type[BaseModel]] = field(default_factory=dict)
    var_dir: Path = REPO_ROOT / "var"

    @property
    def slug(self) -> str:
        return self.config.tenant

    def prompt_override(self, name: str) -> str | None:
        rel = self.config.prompts.get(name)  # type: ignore[call-overload]
        return (self.root / rel).read_text(encoding="utf-8") if rel else None


def list_tenants() -> list[str]:
    return sorted(p.parent.name for p in TENANTS_DIR.glob("*/tenant.yaml"))


def load_tenant(slug_or_path: str | Path, *, var_root: Path | None = None) -> Tenant:
    path = Path(slug_or_path)
    if not path.exists():
        path = TENANTS_DIR / str(slug_or_path)
    config_file = path / "tenant.yaml" if path.is_dir() else path
    if not config_file.exists():
        raise TenantConfigError(f"no tenant.yaml at {config_file}")
    root = config_file.parent

    try:
        config = TenantConfig.model_validate(yaml.safe_load(config_file.read_text(encoding="utf-8")))
    except ValidationError as exc:
        raise TenantConfigError(f"{config_file}:\n{exc}") from exc
    if config.tenant != root.name:
        raise TenantConfigError(f"tenant id '{config.tenant}' must match its folder name '{root.name}'")

    try:
        registry = build_registry(config, root)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        raise TenantConfigError(f"{config_file}: tool setup failed: {exc}") from exc

    problems = []
    for cat, spec in config.categories.items():
        for tool_name in spec.tools:
            if tool_name not in registry:
                problems.append(f"category '{cat}' uses unknown tool '{tool_name}' (have: {registry.names()})")
    for name, rel in config.prompts.items():
        if not (root / rel).exists():
            problems.append(f"prompt override '{name}' not found: {rel}")
    if problems:
        raise TenantConfigError(f"{config_file}:\n  " + "\n  ".join(problems))

    tenant = Tenant(
        config=config,
        root=root,
        registry=registry,
        triage_model=build_triage_model(list(config.categories)),
        var_dir=(var_root or REPO_ROOT / "var") / config.tenant,
    )
    for cat, spec in config.categories.items():
        fields_model = build_fields_model(cat, spec.fields)
        tenant.fields_models[cat] = fields_model
        tenant.submission_models[cat] = build_submission_model(cat, spec, fields_model)
    return tenant
