# Eval report: juniper_dental (dev)

Model: `triage=deepseek-flash, handle=deepseek-flash` | cases: 13 | run at 2026-11-25T21:01:44 | wall time 10.4 s

## Headline

| Metric | Value |
|---|---|
| Automation rate (no person needed) | 69.2% |
| Accuracy when automated | 100.0% |
| Missed handoffs (should have gone to a person) | 0 (0.0%) |
| Unnecessary handoffs | 0 (0.0%) |
| End-to-end accuracy (all checks pass) | 100.0% |
| Category accuracy | 100.0% |
| Routing accuracy | 100.0% |
| Field accuracy | 100.0% |
| Reply checks passed | 100.0% |
| Planned actions correct | 100.0% |
| Adversarial cases passed | 100.0% of 1 |
| Cost per email | $0.0010 (total $0.0135) |
| Latency p50 / p95 | 4357.6 ms / 6776.2 ms |
| LLM calls per email | 2.77 |
| Tokens in / out | 56,957 / 4,778 |
| Staff minutes saved (config estimate) | 59.0 |
| Cases with pipeline errors | 0 |

## Threshold sweep

Re-routes every case from its stored signals; no model calls. The configured threshold is 1.0.

| Threshold | Automation rate | Accuracy when automated | Missed handoffs |
|---|---|---|---|
| 0.3 | 69.2% | 100.0% | 0 |
| 0.5 | 69.2% | 100.0% | 0 |
| 0.7 | 69.2% | 100.0% | 0 |
| 1.0 | 69.2% | 100.0% | 0 |
| 1.3 | 76.9% | 90.0% | 1 |
| 1.7 | 76.9% | 90.0% | 1 |
| 2.1 | 76.9% | 90.0% | 1 |
| 3.0 | 76.9% | 90.0% | 1 |

## Categories

| Category | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| appointment_request | 100.0% | 100.0% | 1.00 | 5 |
| billing_insurance | 100.0% | 100.0% | 1.00 | 3 |
| complaint | 100.0% | 100.0% | 1.00 | 2 |
| other | 100.0% | 100.0% | 1.00 | 2 |
| spam | 100.0% | 100.0% | 1.00 | 1 |

Confusion matrix (rows = expected, columns = predicted):

| | appointment_request | billing_insurance | complaint | other | spam |
|---|---|---|---|---|---|
| **appointment_request** | 5 |  |  |  |  |
| **billing_insurance** |  | 3 |  |  |  |
| **complaint** |  |  | 2 |  |  |
| **other** |  |  |  | 2 |  |
| **spam** |  |  |  |  | 1 |

## Fields

| Field | Accuracy |
|---|---|
| amount | 100.0% |
| appointment_type | 100.0% |
| insurance_provider | 100.0% |
| issue_type | 100.0% |
| new_patient | 100.0% |
| patient_age | 100.0% |
| patient_name | 100.0% |
| preferred_time | 100.0% |
| question_type | 100.0% |
| request_type | 100.0% |
| severity | 100.0% |

## Cases that did not fully pass (0)

None.
