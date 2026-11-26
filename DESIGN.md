# Mailroom — Design

Mailroom is a template for an **email + document processing agent** for small businesses. It reads
incoming mail and attachments, classifies each message, extracts structured fields, looks facts up
with tools, records the result, drafts a reply, and hands anything uncertain to a person.

The template rule: **a new client is a new folder under `tenants/`, not a code change.**

All companies, people and data in this repository are fictional and synthetic. Email addresses use
the reserved `.example` top-level domain.

---

## 1. Goals and non-goals

**Goals**

- One codebase serves several businesses; each is described by `tenants/<slug>/tenant.yaml` plus its
  knowledge files.
- Every change is measured: an eval set reports accuracy, automation rate, cost and latency.
- Irreversible actions (sending mail, opening claims, booking slots) are proposed, checked by
  policy, and approved before they run.
- Any model can be swapped in by changing an alias: DeepSeek, OpenAI, Claude, or a local model.
- Each step of each email is traceable, with personal data masked in logs.

**Non-goals (for now)**

- Production-grade multi-tenancy (SSO, billing, key rotation).
- Real customer data. Everything is synthetic.

## 2. Demo tenants

| Tenant | Business | Categories |
|---|---|---|
| `larkspur_freight` | Regional LTL freight carrier, Midwest/US | quote request, shipment status, vendor invoice, complaint, spam, other |
| `juniper_dental` | Two-dentist family practice | appointment request, billing & insurance, complaint, spam, other |

The two are deliberately different: different fields, tools, policies and privacy rules all run on
the same code. The dental tenant runs a stricter posture: every reply needs approval, insurance member
IDs are masked before text reaches the model, and clinical questions always go to staff.

## 3. Architecture

```
               tenants/<slug>/tenant.yaml + kb/   (the only thing that changes per client)
                              │
inbox (fixture | IMAP | Gmail)▼
   ──▶ ① Ingest     parse MIME, extract attachment text (PDF text layer; scans flagged unreadable)
   ──▶ ② Triage     one structured LLM call: category, confidence, urgency, suspicious?
   ──▶ ③ Handle     hand-written tool loop for that category
                       · read-only tools run immediately (rates, records, knowledge base)
                       · side-effect tools are intercepted and become ProposedActions
                       · finishes by calling `submit` (fields + reply draft + escalation flag)
   ──▶ ④ Policy     deterministic: field validation, business rules, uncertainty signals → risk score
                       → automated  or  handoff to a person
   ──▶ ⑤ Actions    plan: record row, send reply, notify team, intercepted tool calls
                       each action: auto | needs approval  (per tenant policy)
   ──▶ ⑥ Execute    sinks: local CSV / outbox (P0) → MCP connectors: Sheets, Gmail, Slack, Webhook (P1+)
                              │
       trace of every step (tokens, cost, latency, tool I/O; PII masked) → JSON (P0) → Langfuse (P1)
```

### Code layout

```
src/mailroom/
  config/      TenantConfig schema (Pydantic) + loader + model catalog
  llm/         provider-neutral types, OpenAI-compatible and Anthropic adapters over raw HTTP,
               retry/backoff, pricing, structured-output helper, scripted fake for tests
  tools/       Tool base + registry; built-ins: knowledge-base search (BM25), table lookup,
               freight quote, appointment slots
  agent/       the tool-calling loop
  pipeline/    ingest, triage, handle, dynamic schemas, policy, safe expressions, actions, executor
  evals/       dataset loader, runner, metrics, report
  redact.py, tracing.py, cli.py
tenants/<slug>/   tenant.yaml, kb/ (docs + tables), prompts (optional overrides)
evals/<slug>/     cases.jsonl + attachments/
config/models.yaml  model aliases, providers, prices
```

## 4. Key design decisions

### D1. Workflow skeleton, agent loop inside one stage
Email handling has a known shape. Letting the model pick every next step costs more, varies more
between runs and is harder to evaluate. The pipeline stages are fixed code; the model gets autonomy
only inside **Handle**, where it chooses which lookups to make. Each stage can be measured alone.

### D2. Propose, then execute
The model never performs a side effect. Side-effect tools (`open_damage_claim`,
`hold_appointment_slot`) are intercepted by the loop: arguments are validated, a `ProposedAction` is
stored, and the model receives `{"status": "queued_for_review"}`. Mail and sheet writes are planned
by the pipeline from validated fields, not written freely by the model. This gives:
- approvals without pausing and resuming an LLM conversation;
- **dry-run evals**: the same code processes 100 emails with zero side effects, and planned actions
  are compared with expected ones;
- idempotency: each action carries a key derived from (tenant, message id, action, payload), so a
  retry never writes a second row;
- an audit trail of who approved what.

### D3. "Not sure" is computed, not self-reported
LLM self-reported confidence is poorly calibrated, so it is only one signal among several:

| Signal | Source |
|---|---|
| `low_triage_confidence` | triage confidence below the tenant threshold |
| `missing_required` | required field null after extraction (unless the category asks for the info by reply) |
| `validation_failed:<rule>` | tenant rule false, e.g. `subtotal + tax == total` |
| `unreadable_attachment` | a document category whose attachment has no text layer |
| `suspected_injection` | triage flag or pattern match |
| `agent_escalated` / `agent_incomplete` | the model asked for a human, or hit step/budget limits |
| `forced:<rule>` | hard rules, e.g. hazmat, invoice over $25,000, bank-detail change |

Each signal has a weight; the sum is a **risk score**, and the email goes to a person when it crosses
the threshold. Signals are stored per email, so the eval can sweep the threshold offline and draw the
**automation-rate vs. accuracy-when-automated** curve. The sales sentence becomes: "handles X% of
mail on its own, and is right Y% of the time on those; the rest go to your team."

### D4. Money and facts come from tools
Quotes come from `calculate_freight_quote` over the tenant's rate card; shipment status from a table
lookup; policies from knowledge-base search. The model extracts parameters and writes prose. The
reply checks in the eval confirm that the quoted dollar amount matches the tool's output.

### D5. Fields are configuration
Each category declares its fields in YAML (`type`, `required`, bounds, enum values). At load time they
become Pydantic models via `create_model`, and those models are the JSON schema of the `submit` tool.
All fields are optional to the model, so it can report "not stated" honestly; requiredness is checked
by policy, not by forcing the model to invent a value.

### D6. Business rules are safe expressions
`force_handoff_if` and `validations` are strings like `fields.total > 25000`. They are parsed with
Python's `ast` and evaluated by a whitelist interpreter (comparisons, boolean logic, arithmetic,
`abs/len/min/max/round/lower`). No `eval`, no attribute access beyond dictionary keys. Syntax is
checked when the config loads.

### D7. Email is untrusted input
- Email text is wrapped in `<email>` tags and described as data in the system prompt.
- Replies always go to the original sender. The model cannot choose recipients.
- Table lookups can be **owner-scoped**: `lookup_shipments` only returns rows whose
  `customer_domain` matches the sender's domain, so "give me all shipments for Acme" leaks nothing.
- Side effects need approval; there is no bulk-export tool.
- The eval set contains injection and invoice-fraud cases (changed bank details), scored as a
  safety metric.

### D8. Vertical tools are plugins, configured by data
Generic tools (knowledge search, table lookup) cover most needs. Some verticals need logic, like
a freight rate calculation. Those live in the tool library, and their behaviour comes from tenant data
(`kb/rate_card.yaml`). A second carrier reuses the same tool with its own rate card. A new vertical may
add a plugin; the core (loop, pipeline, policy, eval) does not change.

### D9. Model adapters over raw HTTP
Two adapters cover the field: **OpenAI-compatible** (OpenAI, DeepSeek, Qwen, vLLM, Ollama,
OpenRouter, and the author's own [llm-gateway](../llm-gateway)) and **Anthropic Messages**. Both are
written directly against HTTP with one retry policy (exponential backoff with jitter, honours
`Retry-After`). Differences the adapters hide:

| | OpenAI-compatible | Anthropic |
|---|---|---|
| system prompt | first message, `role: system` | top-level `system` |
| tool call | `message.tool_calls[].function.arguments` (JSON **string**) | `content[]` block `type: tool_use`, `input` object |
| tool result | message `role: tool`, `tool_call_id` | block `type: tool_result` inside a **user** message; consecutive results merged |
| force a tool | `tool_choice: {type: function, function: {name}}` | `tool_choice: {type: tool, name}` |
| JSON mode | `response_format: {type: json_object}` | none: use a forced tool |
| cached input | `prompt_tokens_details.cached_tokens` / DeepSeek `prompt_cache_hit_tokens` | `cache_read_input_tokens` |

Each pipeline stage names a model alias, so triage can use a cheaper model than handling.

**Data residency.** The demo runs on DeepSeek because the data is synthetic and the cost is tiny. A
US client's real mail would point the same aliases at OpenAI, Anthropic, Azure, or a self-hosted
model, and that is a one-line config change.

### D10. Tenant isolation
- Runtime data under `var/<tenant>/` (P0) → `tenant_id` on every row and per-tenant schemas (P1).
- Secrets are never in YAML; the config names environment variables with a tenant prefix.
- One Langfuse project per tenant (P1).
- Log and trace redaction (emails, phones, SSNs, card numbers, plus tenant-defined patterns);
  optional redaction of model input as well (the dental tenant does this for member IDs).

## 5. The agent loop

```
messages = [user: <email> + triage summary]
for step in 1..max_steps:
    stop if spent >= max_cost_usd
    response = llm.chat(system, messages, tools = category tools + submit)
    if no tool calls:  nudge "call submit"  (twice at most)
    for each tool call:
        unknown tool                 → error result listing available tools
        arguments not JSON / invalid → error result with Pydantic messages (model self-corrects)
        side-effect tool             → store ProposedAction, result "queued_for_review"
        repeated identical call      → cached result + note (stops lookup loops)
        submit (valid)               → done
        otherwise                    → run handler, truncate result, return JSON
```

Every LLM call records tokens, cost and latency. Every tool call records arguments (masked),
duration and errors.

## 6. Evaluation

**Dataset.** `evals/<tenant>/cases.jsonl`: one email per line with gold labels: category, handoff
yes/no, expected fields, strings the reply must or must not contain, and expected actions. A realistic
mix: clean cases, missing information, mixed intent, spam, other, scanned documents, and adversarial
mail. Split `dev` (tune on this) and `test` (report only).

**Metrics**
- Category: accuracy, per-class precision/recall/F1, confusion matrix.
- Fields: per-field accuracy after normalisation (numbers ±0.5%, dates ISO, strings case/punctuation
  insensitive). An expected `null` must be predicted `null` (catches invented values).
- Routing: automation rate; **missed handoffs** (should have gone to a person, didn't: the key safety
  metric); unnecessary handoffs.
- **Accuracy when automated**: among automated emails, the share where category, fields, reply checks
  and actions are all correct.
- Safety: share of adversarial cases routed to a person.
- Cost per email, tokens, latency p50/p95.
- Threshold sweep from stored signals.

`mailroom eval` writes a JSON and a Markdown report to `evals/reports/`.

## 7. Demo (P2)

- **Dashboard:** emails today, automation rate, hours saved (`minutes_saved` per category; the
  assumption is shown), approval queue with editable drafts, handoff list with reasons, per-email
  timeline, cost per email.
- **Playground:** pick a sample email or write one and watch each step stream in.
- **Tenant switcher:** Larkspur Freight ↔ Juniper Dental on the same deployment.
- Public demo safety: rate limit, daily spend cap, cheapest model, outbound mail only to a sandbox.
- Stack: Next.js + Tailwind + shadcn/ui front end, FastAPI back end.

## 8. Roadmap

| Phase | Scope | Status |
|---|---|---|
| **P0 Core** | adapters, loop, pipeline, policy, local sinks, 2 tenants, eval set + runner, CLI | in progress |
| **P1 Real I/O** | MCP connectors (Gmail, Sheets, Slack, Webhook), Postgres, Langfuse, Mailpit, LLM-judge for replies, CI evals | planned |
| **P2 Product** | FastAPI + Next.js dashboard and playground, deployment, spend caps | planned |
| **P3 Depth** | LangGraph version of the same pipeline with a written comparison; model comparison table; 2-minute video | planned |

## 9. Cost budget

The demo model is `deepseek-flash` with thinking disabled. A typical email uses one triage call and
two or three handle turns, about 6–10k input tokens in total. At the list price this is a fraction of
a cent per email, so a full 80-email eval run costs a few cents. Prices live in `config/models.yaml`
and should be checked against the provider's page before reporting numbers.
