# Mailroom

A config-driven **email and document processing agent** for small businesses. It reads incoming
mail and attachments, classifies each message, extracts structured fields, looks facts up with
tools, records the result, drafts a reply, and hands anything uncertain to a person.

**A new client is a new folder under `tenants/`, not a code change.** The repository ships two
fictional clients that run on the same code:

| Tenant | Business | What it handles |
|---|---|---|
| `larkspur_freight` | Regional LTL freight carrier | quote requests (priced from a rate card), shipment status, vendor invoices (PDF, PO matching), complaints and damage claims |
| `juniper_dental` | Family dental practice | appointment booking/rescheduling with slot holds, insurance and billing questions, complaints; stricter privacy (member IDs never reach the model) |

All companies, people and data are synthetic. Email addresses use the reserved `.example` domain.

## What is interesting here

- **Hand-written agent loop** ([`agent/loop.py`](src/mailroom/agent/loop.py)): bounded by steps and
  dollars, feeds validation errors back to the model, intercepts side-effect tools, caches repeated
  lookups, nudges plain-text answers. No framework.
- **Propose, then execute**: the model never sends mail or writes data. It produces data; a
  deterministic policy decides *automated* or *handoff*, and each action is *auto* or *needs
  approval*, with an idempotency ledger.
- **"Not sure" is computed**: weighted signals (missing fields, failed business rules, unreadable
  attachments, injection screens, forced rules, low confidence) produce a risk score. The eval sweeps the
  threshold from stored signals to show automation rate against accuracy.
- **Business rules in YAML**, such as `fields.total > 25000`, run by a whitelist interpreter
  ([`expr.py`](src/mailroom/expr.py)), never `eval`.
- **Model-agnostic**: OpenAI-compatible and Anthropic adapters over raw HTTP, with one retry policy.
  DeepSeek, OpenAI, Claude, Ollama, or the author's own [llm-gateway](../llm-gateway) are all one
  alias away.
- **Untrusted input handled as such**: email wrapped as data, recipients bound by the host,
  owner-scoped lookups (you only see your own shipments), approval for side effects, plus
  adversarial and fraud cases in the eval set.
- **Eval harness**: 80 gold-labelled emails (60 freight, 20 dental) covering clean cases, missing info,
  hazmat, overweight, PO mismatches, arithmetic errors, scanned PDFs, bank-detail fraud, prompt
  injection and spam. Reports cover category F1, field accuracy, missed handoffs, accuracy when
  automated, cost, and latency.

See [DESIGN.md](DESIGN.md) for the architecture and the reasoning behind each decision.

## Quick start (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
copy .env.example .env        # then put your DEEPSEEK_API_KEY in .env
.\.venv\Scripts\mailroom validate
.\.venv\Scripts\python -m pytest
```

Process one email and see every step:

```powershell
.\.venv\Scripts\mailroom process --tenant larkspur_freight --case lf-q01
```

Run the eval set (dev split) and write a report to `evals/reports/`:

```powershell
.\.venv\Scripts\mailroom eval --tenant larkspur_freight --split dev
```

Compare another model without touching tenant config:

```powershell
.\.venv\Scripts\mailroom eval --tenant larkspur_freight --model gpt-4o-mini
```

Run for real against the local sinks, then work the approval queue:

```powershell
.\.venv\Scripts\mailroom process --tenant larkspur_freight --case lf-q01 --execute
.\.venv\Scripts\mailroom pending --tenant larkspur_freight
.\.venv\Scripts\mailroom approve --tenant larkspur_freight <action_id>
```

Records land in `var/<tenant>/records/*.csv`, replies in `var/<tenant>/outbox/*.eml`.

## Adding a client

1. `mailroom init-tenant acme_plumbing --name "Acme Plumbing"`
2. Edit `tenants/acme_plumbing/tenant.yaml`: categories, fields, tools, record columns, rules, approvals.
3. Put policies and FAQs in `kb/docs/*.md` and lookup tables in `kb/tables/*.csv`.
4. `mailroom validate acme_plumbing`, then write a few cases in `evals/acme_plumbing/cases.yaml` and run `mailroom eval`.

## Layout

```
config/models.yaml        model aliases, providers, prices
tenants/<slug>/           tenant.yaml + kb/ (docs, tables, rate cards)
evals/<slug>/             cases.yaml + attachments/
src/mailroom/
  llm/                    adapters, retry, pricing, structured output, scripted fake
  tools/                  knowledge search (BM25), table lookup, freight quote, slots, claims
  agent/loop.py           the tool-calling loop
  pipeline/               ingest, prompts, schemas, policy, actions, executor, runner
  evals/                  dataset, metrics, report, runner
  expr.py redact.py tracing.py cli.py
scripts/make_invoices.py  generates the synthetic invoice PDFs
```

## Status

P0 (core) is done: pipeline, two tenants, eval set and harness, CLI, and 61 unit tests that use a
scripted model and no network. Next: MCP connectors (Gmail, Sheets, Slack, Webhook), Langfuse
tracing, Postgres, a Next.js dashboard and playground, and a LangGraph version for comparison. See
the roadmap in [DESIGN.md](DESIGN.md#8-roadmap).

## Baseline results

`deepseek-flash` (thinking disabled) for both triage and handling, run 2026-11-25. Changes were made
and checked on `dev` only. `test` is run after a change has passed on `dev`, and is never used to tune.

| Split | Cases | Automation rate | Accuracy when automated | Missed handoffs | End-to-end | Cost / email | p50 latency |
|---|---|---|---|---|---|---|---|
| Freight dev | 40 | 65.0% | 100% | 0 | 100% | $0.0010 | 4.3 s |
| Freight test | 20 | 65.0% | 100% | 0 | 100% | $0.0009 | 4.6 s |
| Dental dev | 13 | 69.2% | 100% | 0 | 100% | $0.0010 | 4.4 s |
| Dental test | 7 | 71.4% | 100% | 0 | 100% | $0.0008 | 4.6 s |

The 80 cases cost $0.075 per full pass. Full reports are in
[`evals/reports/baseline-*.md`](evals/reports/). The other roughly 35% are handed off on purpose:
hazmat, overweight, PO mismatches, arithmetic errors, scans, fraud, injection, serious complaints and
mail that fits no category.

What the runs taught us (first run, then the fixes):
- **Over-cautious handoffs.** At first the model escalated complaints whenever a policy said a manager
  "will follow up" (`lf-c02`, `jd-c03`). This was safe, but it cost automation (dental test was 57.1%). The
  handle prompt now defines `needs_human` as "a person must take this email over", not "someone
  will follow up later". Re-checked on dev: `lf-s05`, which relies on model escalation alone, is
  still handed off. Dental test rose to 71.4%. `lf-c02` also accepts handoff, because the knowledge base
  does call for manager review.
- **26 of 43 replies added their own sign-off** although the prompt forbids it. This is now stripped
  deterministically before the configured signature is appended (`strip_signoff`), instead of being
  left to the prompt. After the fix, 0 of 30 sent replies had a duplicate sign-off.
- The loop's error feedback mattered in practice: the model sometimes called `submit` with empty
  arguments, got the validation error back, and corrected itself on the next turn.
- Caveat: the dataset is small, synthetic and labelled by one person, so these numbers show the
  pipeline works and is measured. They are not a production accuracy claim.
