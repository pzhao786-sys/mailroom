# Eval report: juniper_dental (test)

Model: `triage=deepseek-flash, handle=deepseek-flash` | cases: 7 | run at 2026-11-25T21:02:26 | wall time 5.8 s

## Headline

| Metric | Value |
|---|---|
| Automation rate (no person needed) | 71.4% |
| Accuracy when automated | 100.0% |
| Missed handoffs (should have gone to a person) | 0 (0.0%) |
| Unnecessary handoffs | 0 (0.0%) |
| End-to-end accuracy (all checks pass) | 100.0% |
| Category accuracy | 100.0% |
| Routing accuracy | 100.0% |
| Field accuracy | 100.0% |
| Reply checks passed | 100.0% |
| Planned actions correct | 100.0% |
| Adversarial cases passed | n/a of 0 |
| Cost per email | $0.0008 (total $0.0057) |
| Latency p50 / p95 | 4552.2 ms / 5749.4 ms |
| LLM calls per email | 2.57 |
| Tokens in / out | 27,680 / 2,527 |
| Staff minutes saved (config estimate) | 31.0 |
| Cases with pipeline errors | 0 |

## Threshold sweep

Re-routes every case from its stored signals; no model calls. The configured threshold is 1.0.

| Threshold | Automation rate | Accuracy when automated | Missed handoffs |
|---|---|---|---|
| 0.3 | 71.4% | 100.0% | 0 |
| 0.5 | 71.4% | 100.0% | 0 |
| 0.7 | 71.4% | 100.0% | 0 |
| 1.0 | 71.4% | 100.0% | 0 |
| 1.3 | 85.7% | 83.3% | 1 |
| 1.7 | 85.7% | 83.3% | 1 |
| 2.1 | 85.7% | 83.3% | 1 |
| 3.0 | 85.7% | 83.3% | 1 |

## Categories

| Category | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| appointment_request | 100.0% | 100.0% | 1.00 | 2 |
| billing_insurance | 100.0% | 100.0% | 1.00 | 2 |
| complaint | 100.0% | 100.0% | 1.00 | 1 |
| other | 100.0% | 100.0% | 1.00 | 1 |
| spam | 100.0% | 100.0% | 1.00 | 1 |

Confusion matrix (rows = expected, columns = predicted):

| | appointment_request | billing_insurance | complaint | other | spam |
|---|---|---|---|---|---|
| **appointment_request** | 2 |  |  |  |  |
| **billing_insurance** |  | 2 |  |  |  |
| **complaint** |  |  | 1 |  |  |
| **other** |  |  |  | 1 |  |
| **spam** |  |  |  |  | 1 |

## Fields

| Field | Accuracy |
|---|---|
| amount | 100.0% |
| appointment_type | 100.0% |
| insurance_provider | 100.0% |
| issue_type | 100.0% |
| new_patient | 100.0% |
| patient_name | 100.0% |
| preferred_time | 100.0% |
| question_type | 100.0% |
| request_type | 100.0% |
| severity | 100.0% |

## Cases that did not fully pass (0)

None.
