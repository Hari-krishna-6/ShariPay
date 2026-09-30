# Payment Transaction State Machine

## Atomic SubmitPayment

The application payment path is a single `SubmitPayment` invocation. It validates identities, monetary inputs, risk band, and policy; reads or initializes both account ledger keys; and either records a non-settling review state or settles an approval. For `APPROVE`, it writes `CREATED`, `RISK_ASSESSED`, `APPROVED`, and `COMPLETED` history snapshots, debits the sender, credits the receiver, and stores final payment state in the same Fabric transaction. If any step returns an error, Fabric commits none of those writes.

Account balances and amounts are integer minor currency units (paise). The initial account balance argument is used only when that account key is absent; an existing ledger balance is authoritative. No application lock participates in settlement.

## States and transitions

| From | Function / path | To | Guard |
| --- | --- | --- | --- |
| absent | `CreatePayment` or `SubmitPayment` | `CREATED` | unique non-empty transaction ID; valid distinct parties; positive amount; three-letter uppercase currency |
| `CREATED` | `AssessRisk` / `SubmitPayment` | `RISK_ASSESSED` | finite score in `[0,1]`; risk level matches its score band; supported decision |
| `RISK_ASSESSED` | `ApprovePayment` / approved `SubmitPayment` | `APPROVED` | policy decision permits approval |
| `RISK_ASSESSED` | `HoldPayment` / hold `SubmitPayment` | `HELD` | policy decision is `HOLD` |
| `RISK_ASSESSED` | `RequestVerification` / verify `SubmitPayment` | `VERIFICATION_REQUIRED` | policy decision is `VERIFY` |
| `HELD` | `RequestVerification` | `VERIFICATION_REQUIRED` | supported review transition |
| `VERIFICATION_REQUIRED` | `ApprovePayment` | `APPROVED` | operator approval clears the verification flag |
| `APPROVED` | `CompletePayment` / approved `SubmitPayment` | `COMPLETED` | account ownership/currency match and sender has sufficient ledger balance |
| `RISK_ASSESSED` with `REJECT`, `HELD`, or `VERIFICATION_REQUIRED` | `RejectPayment` / reject `SubmitPayment` | `REJECTED` | supported policy/terminal transition |

All other transitions are rejected. `HELD -> COMPLETED` is invalid. A direct `CompletePayment` after `APPROVED` also performs the account debit/credit when account IDs are present.

## Risk bands and history

The chaincode enforces `0.0 <= score < 0.30` as LOW/APPROVE, `0.30 <= score < 0.70` as MEDIUM/VERIFY, and `0.70 <= score <= 1.0` as HIGH/HOLD. `REJECT` is permitted for a valid score/level. A mismatched level or unsupported decision is rejected.

Successful state writes append numbered snapshots under `payment-history:<transactionId>:<sequence>`. Rejected invocations do not commit payment, account, or history writes. Timestamps use the Fabric transaction timestamp (UTC RFC3339Nano) for deterministic endorsement.

## Idempotency and concurrency

An exact `SubmitPayment` replay returns the existing payment without another debit, credit, or history entry. Reusing a transaction ID with different identity, amount, currency, risk, or decision is rejected. Competing writes to the same account key are resolved by Fabric read/write-set MVCC validation; the demonstrated live test produced one VALID spend and one `MVCC_READ_CONFLICT` (code 11).

## Legacy compatibility

Older ledger payments may omit sender/receiver account IDs and minor-unit fields. Keyed payment queries handle these historical records. The aggregate `GetAllPayments` response can still fail Contract API schema validation on such records; it is not used by the backend payment flow.