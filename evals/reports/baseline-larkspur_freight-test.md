# Eval report: larkspur_freight (test)

Model: `triage=deepseek-flash, handle=deepseek-flash` | cases: 20 | run at 2026-11-25T21:02:19 | wall time 16.4 s

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
| Cost per email | $0.0008 (total $0.0171) |
| Latency p50 / p95 | 4643.4 ms / 7116.4 ms |
| LLM calls per email | 2.7 |
| Tokens in / out | 93,354 / 7,396 |
| Staff minutes saved (config estimate) | 83.0 |
| Cases with pipeline errors | 0 |

## Threshold sweep

Re-routes every case from its stored signals; no model calls. The configured threshold is 1.0.

| Threshold | Automation rate | Accuracy when automated | Missed handoffs |
|---|---|---|---|
| 0.3 | 65.0% | 100.0% | 0 |
| 0.5 | 65.0% | 100.0% | 0 |
| 0.7 | 65.0% | 100.0% | 0 |
| 1.0 | 65.0% | 100.0% | 0 |
| 1.3 | 80.0% | 87.5% | 2 |
| 1.7 | 80.0% | 87.5% | 2 |
| 2.1 | 90.0% | 77.8% | 4 |
| 3.0 | 90.0% | 77.8% | 4 |

## Categories

| Category | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| complaint | 100.0% | 100.0% | 1.00 | 3 |
| invoice | 100.0% | 100.0% | 1.00 | 4 |
| other | 100.0% | 100.0% | 1.00 | 2 |
| quote_request | 100.0% | 100.0% | 1.00 | 5 |
| shipment_status | 100.0% | 100.0% | 1.00 | 3 |
| spam | 100.0% | 100.0% | 1.00 | 3 |

Confusion matrix (rows = expected, columns = predicted):

| | complaint | invoice | other | quote_request | shipment_status | spam |
|---|---|---|---|---|---|---|
| **complaint** | 3 |  |  |  |  |  |
| **invoice** |  | 4 |  |  |  |  |
| **other** |  |  | 2 |  |  |  |
| **quote_request** |  |  |  | 5 |  |  |
| **shipment_status** |  |  |  |  | 3 |  |
| **spam** |  |  |  |  |  | 3 |

## Fields

| Field | Accuracy |
|---|---|
| accessorials | 100.0% |
| claimed_amount | 100.0% |
| destination_state | 100.0% |
| due_date | 100.0% |
| found | 100.0% |
| invoice_date | 100.0% |
| invoice_number | 100.0% |
| issue_type | 100.0% |
| origin_state | 100.0% |
| pallets | 100.0% |
| po_match | 100.0% |
| po_number | 100.0% |
| quote_total | 100.0% |
| service | 100.0% |
| severity | 100.0% |
| total | 100.0% |
| tracking_number | 100.0% |
| vendor_name | 100.0% |
| weight_lbs | 100.0% |

## Cases that did not fully pass (0)

None.
