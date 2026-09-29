# Payment Transaction State Machine

## States and transitions

| From | Function | To | Guard |
| --- | --- | --- | --- |
| absent | `CreatePayment` | `CREATED` | unique non-empty ID; valid parties, positive whole-INR amount, 3-letter uppercase currency |
| `CREATED` | `AssessRisk` | `RISK_ASSESSED` | finite risk score in `[0,1]`, recognized risk level and policy decision |
| `RISK_ASSESSED` | `ApprovePayment` | `APPROVED` | assessed decision is `APPROVE` |
| `RISK_ASSESSED` | `HoldPayment` | `HELD` | assessed decision is `HOLD` |
| `RISK_ASSESSED` | `RequestVerification` | `VERIFICATION_REQUIRED` | assessed decision is `VERIFY` |
| `HELD` | `RequestVerification` | `VERIFICATION_REQUIRED` | held payment enters the supported verification path |
| `VERIFICATION_REQUIRED` | `ApprovePayment` | `APPROVED` | verification-required flag is set |
| `APPROVED` | `CompletePayment` | `COMPLETED` | no further guard |
| `HELD` or `VERIFICATION_REQUIRED` | `RejectPayment` | `REJECTED` | terminal rejection |

All other transitions are rejected. In particular, `HELD -> COMPLETED` is invalid. A verification approval changes the policy decision to `APPROVE` and clears the verification-required flag. Every successful payment write also appends a numbered snapshot to `payment-history:<transactionId>:<sequence>` in the same ledger transaction; rejected invocations write no history entry.

## Risk and amount conventions

Risk score is a probability-like decimal from `0.0` through `1.0`. Risk levels are `LOW`, `MEDIUM`, and `HIGH`; policy decisions are `APPROVE`, `HOLD`, and `VERIFY`. The initial prototype accepts whole INR amounts only, represented as positive integer rupees. Decimal amounts/minor units need a deliberate schema revision before application integration.

In `CREATED`, score is `0` and risk level/policy decision are both `UNASSESSED`; the status indicates that this is a placeholder, not a completed assessment. These fields are always serialized because the Fabric Contract API response schema requires them.

The chaincode enforces these prototype policy bands: `0.0 <= score < 0.30` is `LOW`/`APPROVE`; `0.30 <= score < 0.70` is `MEDIUM`/`VERIFY`; `0.70 <= score <= 1.0` is `HIGH`/`HOLD`. A mismatch is rejected during `AssessRisk`.

Timestamps are derived from the Fabric transaction timestamp and stored as UTC RFC3339Nano strings. This keeps endorsers deterministic for a given proposal.