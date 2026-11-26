"""Tool library. Tenants pick tools by name in tenant.yaml; nothing here is tenant-specific."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..config.schema import TenantConfig
from .base import Tool, ToolContext, ToolError, ToolRegistry
from .freight import make_freight_quote_tool
from .kb import make_kb_tool
from .side_effects import make_find_slots_tool, make_hold_slot_tool, make_open_claim_tool
from .tables import make_lookup_tool

PluginFactory = Callable[[dict, Path], Tool]

# Plugins are configured by data (settings + tenant files). Adding a vertical adds an entry here;
# the loop, pipeline, policy and evals stay untouched.
PLUGINS: dict[str, PluginFactory] = {
    "calculate_freight_quote": make_freight_quote_tool,
    "open_damage_claim": make_open_claim_tool,
    "find_open_slots": make_find_slots_tool,
    "hold_appointment_slot": make_hold_slot_tool,
}


def build_registry(config: TenantConfig, tenant_root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    if config.knowledge.docs_dir:
        registry.register(make_kb_tool(tenant_root / config.knowledge.docs_dir, config.company.name))
    for table, spec in config.knowledge.tables.items():
        registry.register(make_lookup_tool(table, spec, tenant_root))
    for name, settings in config.tools.items():
        if name not in PLUGINS:
            raise ValueError(f"unknown plugin tool '{name}'. Available: {sorted(PLUGINS)}")
        registry.register(PLUGINS[name](settings or {}, tenant_root))
    return registry


__all__ = ["PLUGINS", "Tool", "ToolContext", "ToolError", "ToolRegistry", "build_registry"]
