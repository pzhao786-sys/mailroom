# Eval report: larkspur_freight (dev)

Model: `triage=deepseek-flash, handle=deepseek-flash` | cases: 40 | run at 2026-11-25T21:01:33 | wall time 27.8 s

## Headline

| Metric | Value |
|---|---|
| Automation rate (no person needed) | 65.0% |
| Accuracy when automated | 100.0% |
| Missed handoffs (should have gone to a person) | 0 (0.0%) |
| Unnecessary handoffs | 0 (0.0%) |
| End-to-end accuracy (all checks pass) | 100.0% |
| Category accuracy | 100.0% |
| Routing accuracy | 100.0% |
| Field accuracy | 100.0% |
| Reply checks passed | 100.0% |
| Planned actions correct | 100.0% |
| Adversarial cases passed | 100.0% of 2 |
| Cost per email | $0.0010 (total $0.0388) |
| Latency p50 / p95 | 4268.1 ms / 6329.0 ms |
| LLM calls per email | 2.67 |
| Tokens in / out | 181,601 / 14,836 |
| Staff minutes saved (config estimate) | 182.0 |
| Cases with pipeline errors | 0 |

## Threshold sweep

Re-routes every case from its stored signals; no model calls. The configured threshold is 1.0.

| Threshold | Automation rate | Accuracy when automated | Missed handoffs |
|---|---|---|---|
| 0.3 | 65.0% | 100.0% | 0 |
| 0.5 | 65.0% | 100.0% | 0 |
| 0.7 | 65.0% | 100.0% | 0 |
| 1.0 | 65.0% | 100.0% | 0 |
| 1.3 | 77.5% | 83.9% | 5 |
| 1.7 | 77.5% | 83.9% | 5 |
| 2.1 | 82.5% | 78.8% | 7 |
| 3.0 | 82.5% | 78.8% | 7 |

## Categories

| Category | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| complaint | 100.0% | 100.0% | 1.00 | 7 |
| invoice | 100.0% | 100.0% | 1.00 | 8 |
| other | 100.0% | 100.0% | 1.00 | 4 |
| quote_request | 100.0% | 100.0% | 1.00 | 10 |
| shipment_status | 100.0% | 100.0% | 1.00 | 7 |
| spam | 100.0% | 100.0% | 1.00 | 4 |

Confusion matrix (rows = expected, columns = predicted):

| | complaint | invoice | other | quote_request | shipment_status | spam |
|---|---|---|---|---|---|---|
| **complaint** | 7 |  |  |  |  |  |
| **invoice** |  | 8 |  |  |  |  |
| **other** |  |  | 4 |  |  |  |
| **quote_request** |  |  |  | 10 |  |  |
| **shipment_status** |  |  |  |  | 7 |  |
| **spam** |  |  |  |  |  | 4 |

## Fields

| Field | Accuracy |
|---|---|
| accessorials | 100.0% |
| bank_details_changed | 100.0% |
| claimed_amount | 100.0% |
| destination_state | 100.0% |
| due_date | 100.0% |
| found | 100.0% |
| hazmat | 100.0% |
| invoice_date | 100.0% |
| invoice_number | 100.0% |
| issue_type | 100.0% |
| origin_state | 100.0% |
| pallets | 100.0% |
| po_match | 100.0% |
| po_number | 100.0% |
| quote_total | 100.0% |
| reference | 100.0% |
| service | 100.0% |
| severity | 100.0% |
| subtotal | 100.0% |
| tax | 100.0% |
| total | 100.0% |
| tracking_number | 100.0% |
| vendor_name | 100.0% |
| weight_lbs | 100.0% |

## Cases that did not fully pass (0)

None.
