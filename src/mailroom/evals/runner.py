"""Run a tenant's eval set through the real pipeline in dry-run mode and write a report."""

from __future__ import annotations

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .. import REPO_ROOT
from ..llm.catalog import LLMRegistry, load_catalog
from ..pipeline.runner import LLMSource, ProcessingResult, process_email
from ..tenant import load_tenant
from .dataset import EvalCase, case_email, load_cases
from .metrics import CaseScore, aggregate, score_case
from .report import render_markdown

REPORTS_DIR = REPO_ROOT / "evals" / "reports"


def run_eval(
    tenant_slug: str,
    *,
    split: str = "dev",
    ids: list[str] | None = None,
    tags: list[str] | None = None,
    limit: int | None = None,
    model: str | None = None,
    workers: int = 4,
    llms: LLMSource | None = None,
    out_dir: Path | None = None,
    progress: Callable[[CaseScore], None] | None = None,
) -> dict[str, Any]:
    cases = load_cases(tenant_slug, split=split, ids=ids, tags=tags, limit=limit)
    if not cases:
        raise ValueError("no eval cases match the filters")
    with tempfile.TemporaryDirectory(prefix="mailroom-eval-") as tmp:
        tenant = load_tenant(tenant_slug, var_root=Path(tmp))  # dry run: nothing is written, but isolate anyway
        registry = llms or LLMRegistry(load_catalog(), override=model)
        model_label = model or f"triage={tenant.config.models.triage}, handle={tenant.config.models.handle}"

        def run_one(case: EvalCase) -> tuple[CaseScore, ProcessingResult | None]:
            try:
                result = process_email(tenant, case_email(tenant_slug, case), registry, dry_run=True)
            except Exception as exc:  # a crash is a failed case, not a failed run
                return score_case(case, None, f"{type(exc).__name__}: {exc}"), None
            return score_case(case, result), result

        started = time.perf_counter()
        scores: dict[str, CaseScore] = {}
        results: dict[str, ProcessingResult | None] = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(run_one, c): c for c in cases}
            for fut in as_completed(futures):
                score, result = fut.result()
                scores[score.id], results[score.id] = score, result
                if progress:
                    progress(score)
        wall_s = time.perf_counter() - started

    ordered = [scores[c.id] for c in cases]
    summary = aggregate(ordered)
    run = {
        "tenant": tenant_slug,
        "split": split,
        "model": model_label,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "wall_seconds": round(wall_s, 1),
        "summary": summary,
        "cases": [s.model_dump(mode="json") for s in ordered],
        "results": {k: (v.model_dump(mode="json", exclude={"trace"}) if v else None) for k, v in results.items()},
    }
    out = out_dir or REPORTS_DIR
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = out / f"{tenant_slug}-{split}-{stamp}"
    base.with_suffix(".json").write_text(json.dumps(run, indent=2, default=str), encoding="utf-8")
    base.with_suffix(".md").write_text(render_markdown(run), encoding="utf-8")
    run["paths"] = {"json": str(base.with_suffix(".json")), "markdown": str(base.with_suffix(".md"))}
    return run
