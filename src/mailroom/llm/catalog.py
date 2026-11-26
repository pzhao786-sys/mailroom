"""Model catalog: aliases in config/models.yaml -> configured clients."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from pydantic import BaseModel, Field

from .. import REPO_ROOT
from .anthropic import AnthropicClient
from .openai_compat import OpenAICompatClient
from .pricing import Pricing
from .types import LLMClient


class ProviderSpec(BaseModel):
    kind: Literal["openai_compat", "anthropic"]
    base_url: str
    api_key_env: str | None = None


class ModelSpec(BaseModel):
    provider: str
    model: str
    structured_output: Literal["json", "tool"] = "json"
    extra_body: dict[str, Any] = Field(default_factory=dict)
    pricing: Pricing | None = None


class ModelCatalog(BaseModel):
    providers: dict[str, ProviderSpec]
    models: dict[str, ModelSpec]

    def check(self) -> None:
        for alias, spec in self.models.items():
            if spec.provider not in self.providers:
                raise ValueError(f"model '{alias}' references unknown provider '{spec.provider}'")


def load_catalog(path: Path | None = None) -> ModelCatalog:
    path = path or REPO_ROOT / "config" / "models.yaml"
    catalog = ModelCatalog.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    catalog.check()
    return catalog


class MissingCredential(RuntimeError):
    pass


class LLMRegistry:
    """Builds one client per alias on first use and shares an HTTP connection pool.

    `override` replaces every alias (used by `mailroom eval --model ...` to compare models).
    """

    def __init__(self, catalog: ModelCatalog, *, override: str | None = None, timeout: float = 90.0):
        self.catalog = catalog
        self.override = override
        self._http = httpx.Client(timeout=timeout)
        self._clients: dict[str, LLMClient] = {}
        self._lock = threading.Lock()

    def get(self, alias: str) -> LLMClient:
        alias = self.override or alias
        with self._lock:
            if alias not in self._clients:
                self._clients[alias] = self._build(alias)
            return self._clients[alias]

    def _build(self, alias: str) -> LLMClient:
        if alias not in self.catalog.models:
            raise KeyError(f"unknown model alias '{alias}'. Known: {', '.join(self.catalog.models)}")
        spec = self.catalog.models[alias]
        provider = self.catalog.providers[spec.provider]
        api_key = os.environ.get(provider.api_key_env) if provider.api_key_env else None
        if provider.api_key_env and not api_key:
            raise MissingCredential(f"model '{alias}' needs the environment variable {provider.api_key_env} (see .env.example)")
        common = dict(
            alias=alias, model=spec.model, api_key=api_key, base_url=provider.base_url, pricing=spec.pricing,
            structured_output=spec.structured_output, extra_body=spec.extra_body, http_client=self._http,
        )
        if provider.kind == "anthropic":
            return AnthropicClient(**common)
        return OpenAICompatClient(**common)
