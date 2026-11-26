"""Command line: mailroom validate | models | process | eval | pending | approve | reject | init-tenant"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from . import REPO_ROOT


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(REPO_ROOT / ".env")


# ---- commands -------------------------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> int:
    from .evals.dataset import cases_file, load_cases
    from .tenant import TenantConfigError, list_tenants, load_tenant

    slugs = args.tenants or list_tenants()
    failed = False
    for slug in slugs:
        try:
            t = load_tenant(slug)
            n_cases = len(load_cases(slug, split="all")) if cases_file(slug).exists() else 0
            print(f"OK   {slug}: {len(t.config.categories)} categories, tools {t.registry.names()}, {n_cases} eval cases")
        except (TenantConfigError, ValueError) as exc:
            failed = True
            print(f"FAIL {slug}:\n{exc}")
    return 1 if failed else 0


def cmd_models(args: argparse.Namespace) -> int:
    from .llm.catalog import load_catalog

    catalog = load_catalog()
    for alias, spec in catalog.models.items():
        provider = catalog.providers[spec.provider]
        has_key = provider.api_key_env is None or bool(os.environ.get(provider.api_key_env))
        price = f"${spec.pricing.input}/${spec.pricing.output} per 1M" if spec.pricing else "price not set"
        print(f"{alias:22} {spec.provider:12} {spec.model:22} {'key ok' if has_key else 'NO KEY (' + str(provider.api_key_env) + ')':28} {price}")
    return 0


def _check_credentials(tenant_slug: str, override: str | None) -> bool:
    """Fail fast with a readable message instead of 80 identical errors."""
    from .llm.catalog import LLMRegistry, MissingCredential, load_catalog
    from .tenant import load_tenant

    cfg = load_tenant(tenant_slug).config
    registry = LLMRegistry(load_catalog(), override=override)
    try:
        for alias in {cfg.models.triage, cfg.models.handle}:
            registry.get(alias)
    except (MissingCredential, KeyError) as exc:
        print(f"error: {exc}")
        return False
    return True


def _print_result(result, show_trace: bool) -> None:
    d = result.decision
    print(f"\nEmail {result.message_id}")
    print(f"  category      : {result.category}  (triage confidence {result.triage.get('confidence') if result.triage else 'n/a'})")
    print(f"  route/outcome : {d.route} / {d.outcome}   risk {d.risk_score} (threshold {d.threshold})")
    for sig in d.signals:
        print(f"    signal  {sig.name} (+{sig.weight}) {sig.detail}")
    if result.fields:
        print("  fields:")
        for k, v in result.fields.items():
            print(f"    {k:22} {v}")
    if result.tool_events:
        print("  tool calls:")
        for e in result.tool_events:
            flag = " [queued for approval]" if e.intercepted else (" [cached]" if e.cached else "")
            print(f"    step {e.step}: {e.tool}({json.dumps(e.arguments)[:90]}){flag}{'' if e.ok else ' -> ERROR'}")
    if result.reply_body:
        print("  reply draft:\n    " + result.reply_body.strip().replace("\n", "\n    "))
    print("  actions:")
    for a in result.actions:
        print(f"    {a.id}  {a.type:12} {a.mode:9} {a.status:17} {a.summary}")
    cost = f"${result.cost_usd:.5f}" if result.cost_usd is not None else "unknown"
    print(f"  cost {cost} | {result.llm_calls} LLM calls | {result.usage.input_tokens} in / {result.usage.output_tokens} out tokens | {result.latency_ms:.0f} ms")
    if result.errors:
        print("  errors: " + "; ".join(result.errors))
    if show_trace:
        print(json.dumps(result.trace, indent=2, default=str))


def cmd_process(args: argparse.Namespace) -> int:
    from .evals.dataset import case_email, load_cases
    from .llm.catalog import LLMRegistry, load_catalog
    from .pipeline.ingest import from_fixture, parse_eml
    from .pipeline.runner import process_email
    from .tenant import load_tenant

    if not _check_credentials(args.tenant, args.model):
        return 2
    tenant = load_tenant(args.tenant)
    if args.case:
        cases = load_cases(args.tenant, split="all", ids=[args.case])
        if not cases:
            print(f"no case {args.case}")
            return 2
        email = case_email(args.tenant, cases[0])
    elif args.eml:
        email = parse_eml(Path(args.eml).read_bytes())
    else:
        path = Path(args.fixture)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        email = from_fixture(data, path.parent)
    result = process_email(tenant, email, LLMRegistry(load_catalog(), override=args.model), dry_run=not args.execute)
    _print_result(result, args.trace)
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .evals.report import _pct
    from .evals.runner import run_eval

    def progress(score) -> None:
        mark = "PASS" if score.fully_correct else "FAIL"
        cost = f"${score.cost_usd:.4f}" if score.cost_usd is not None else "?"
        print(f"  {mark} {score.id:10} {str(score.predicted_category):20} {score.outcome:15} {cost:>9} {score.latency_ms / 1000:5.1f}s", flush=True)

    if not _check_credentials(args.tenant, args.model):
        return 2
    run = run_eval(
        args.tenant, split=args.split, ids=args.ids.split(",") if args.ids else None,
        tags=args.tags.split(",") if args.tags else None, limit=args.limit, model=args.model,
        workers=args.workers, progress=progress,
    )
    s = run["summary"]
    print(
        f"\n{s['cases']} cases | automation {_pct(s['automation_rate'])} | accuracy when automated "
        f"{_pct(s['accuracy_when_automated'])} | missed handoffs {s['missed_handoffs']} | end-to-end "
        f"{_pct(s['end_to_end_accuracy'])} | cost {s['cost_total_usd']} USD | p50 {s['latency_p50_ms']} ms"
    )
    print(f"report: {run['paths']['markdown']}")
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    from .pipeline.executor import Executor
    from .tenant import load_tenant

    items = Executor(load_tenant(args.tenant), dry_run=False).pending()
    if not items:
        print("no pending actions")
    for action, email in items:
        print(f"{action.id}  {action.type:12} {email.sender:40} {action.summary}")
        if action.type == "send_reply":
            print("    " + action.payload["body"].replace("\n", "\n    "))
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    from .pipeline.executor import Executor
    from .tenant import load_tenant

    action = Executor(load_tenant(args.tenant), dry_run=False).approve(args.action_id, approver=args.by)
    print(f"{action.id}: {action.status} {action.result or action.error or ''}")
    return 0 if action.status == "executed" else 1


def cmd_reject(args: argparse.Namespace) -> int:
    from .pipeline.executor import Executor
    from .tenant import load_tenant

    action = Executor(load_tenant(args.tenant), dry_run=False).reject(args.action_id, reason=args.reason, approver=args.by)
    print(f"{action.id}: {action.status}")
    return 0


TEMPLATE = """# {name}: generated by `mailroom init-tenant`. Fill in, then run `mailroom validate {slug}`.
tenant: {slug}
display_name: {name}
locale: en-US
secrets_prefix: {prefix}_

company:
  name: {name}
  description: a small business that ... (one line, used in every prompt)
  mailbox: hello@{slug}.example
  signature: |
    {name}
  reply_style: friendly, concise and professional

models:
  triage: deepseek-flash
  handle: deepseek-flash

knowledge:
  docs_dir: kb/docs
  tables: {{}}

categories:
  inquiry:
    description: A customer asks a question about our products or services.
    fields:
      customer_name: {{ type: str }}
      topic: {{ type: str, required: true }}
    tools: [search_knowledge_base]
    record:
      tab: Inquiries
      columns: [received_at, sender, customer_name, topic]
    minutes_saved: 5
  spam:
    description: Unsolicited marketing, newsletters and junk.
    skip_handle: true
    archive: true
    reply: none
  other:
    description: Anything else.
    skip_handle: true
    always_handoff: true
    reply: none
"""


def cmd_init_tenant(args: argparse.Namespace) -> int:
    from .tenant import TENANTS_DIR

    root = TENANTS_DIR / args.slug
    if root.exists():
        print(f"{root} already exists")
        return 1
    (root / "kb" / "docs").mkdir(parents=True)
    (root / "tenant.yaml").write_text(
        TEMPLATE.format(slug=args.slug, name=args.name, prefix=args.slug.upper()), encoding="utf-8"
    )
    (root / "kb" / "docs" / "faq.md").write_text(f"# {args.name} FAQ\n\n## Hours\nReplace with real policies.\n", encoding="utf-8")
    print(f"created {root}. Next: edit tenant.yaml and kb/docs, then `mailroom validate {args.slug}`.")
    return 0


# ---- entry point ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles
    _load_env()
    parser = argparse.ArgumentParser(prog="mailroom", description="Config-driven email and document agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate tenant configs and eval sets")
    p.add_argument("tenants", nargs="*")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("models", help="list model aliases and whether their API keys are set")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("process", help="process one email (dry run unless --execute)")
    p.add_argument("--tenant", required=True)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--case", help="an eval case id")
    src.add_argument("--fixture", help="a YAML/JSON fixture file")
    src.add_argument("--eml", help="an .eml file")
    p.add_argument("--model", help="override every model alias")
    p.add_argument("--execute", action="store_true", help="run auto actions and queue approvals under var/<tenant>/")
    p.add_argument("--trace", action="store_true", help="print the full trace")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("eval", help="run the eval set and write a report")
    p.add_argument("--tenant", required=True)
    p.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    p.add_argument("--ids", help="comma-separated case ids")
    p.add_argument("--tags", help="comma-separated tags")
    p.add_argument("--limit", type=int)
    p.add_argument("--model", help="override every model alias")
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("pending", help="list actions waiting for approval")
    p.add_argument("--tenant", required=True)
    p.set_defaults(func=cmd_pending)

    for name, func in (("approve", cmd_approve), ("reject", cmd_reject)):
        p = sub.add_parser(name, help=f"{name} a pending action")
        p.add_argument("--tenant", required=True)
        p.add_argument("action_id")
        p.add_argument("--by", default=os.environ.get("USERNAME", "cli"))
        if name == "reject":
            p.add_argument("--reason", default="")
        p.set_defaults(func=func)

    p = sub.add_parser("init-tenant", help="scaffold a new tenant folder")
    p.add_argument("slug")
    p.add_argument("--name", required=True)
    p.set_defaults(func=cmd_init_tenant)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
