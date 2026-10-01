# DRUNIX Integration

## Current environment

The live local test network uses channel `mychannel` and Fabric chaincode identifier `trustpay`. The current verified definition is version `1.9`, sequence `1`, approved by Org1 and Org2. On 2026-10-01, both committing peers reported channel height `25`. The `peer0` nodes are lite peers and report block 0; use committing peers `peer1` (ports 7061 and 9061) when assessing ledger height.

The sequence-10 definition and block-163 commit below are from a previous network ledger and are historical only. Query both committing peers before any future lifecycle change. This documentation does not authorize or perform an upgrade.

## Legacy CreatePayment compatibility

The current frontend posts payments to `/api/v1/payments`; the backend path calls `SubmitPayment`, which creates the payment and performs the selected transition atomically. `CreatePayment` is not called by the current frontend or backend payment flow. Its remaining backend sequence helper is not used in production submission.

The deployed `CreatePayment` response is incompatible with its current contract schema: it returns empty account IDs that are omitted from JSON, while the schema requires `senderAccountId` and `receiverAccountId`. The peer rejects that legacy invocation during response-schema validation. No chaincode or lifecycle change was made; do not use this compatibility entry point for current payments.

## SubmitPayment contract

`SubmitPayment` arguments are, in order: transaction ID, sender ID, receiver ID, sender account ID, receiver account ID, amount minor units, sender initial balance minor units, receiver initial balance minor units, currency, risk score, risk level, and policy decision. Amount and balances are integer paise. For `APPROVE`, payment creation, risk/policy fields, sender debit, receiver credit, `COMPLETED` state, and four history entries are written in one Fabric transaction.

Example synthetic invocation payload (use only through the trusted test-network CLI and authorized local identity):

```json
{"Args":["SubmitPayment","demo-001","sender-demo","receiver-demo","account-s-demo","account-r-demo","1200","10000","500","INR","0.12","LOW","APPROVE"]}
```

Keyed queries are preferred for payment verification:

```json
{"Args":["GetPayment","demo-001"]}
{"Args":["GetPaymentHistory","demo-001"]}
{"Args":["GetAccount","account-s-demo"]}
```

Exact request replay is idempotent; the same ID with changed data is rejected. Insufficient ledger balance is rejected without account/payment writes. PostgreSQL is not used as an authoritative settlement lock. Competing transactions that read/write the same sender-account key are validated by Fabric MVCC.

## Previous-network evidence (2026-09-30; historical)

- Basic `SubmitPayment`: VALID transaction `dc6fffa45ed2400255398359ee68a5a13032744260a27b9f4b965e759d9536e6` in block 164; final status `COMPLETED`, history sequences 1–4, sender 10,000 → 8,800 minor units, receiver 500 → 1,700.
- Exact replay: VALID transaction `5c08d3ef463b946d20e9d51685546e34e149388e1b620ee7351b3da9e903b9e1` in block 168; no second debit/credit or history entry.
- Concurrent double spend: block 170; one VALID (code 0), one `MVCC_READ_CONFLICT` (code 11); only one 7,000-unit spend from 10,000 committed, leaving 3,000.
- Concurrent lifecycle conflict: block 177; one VALID approval, one `MVCC_READ_CONFLICT` (code 11); final status `APPROVED`.

The peer `querycommitted` definition and the peer block validation filter were both inspected. Endorsement success alone is not treated as a committed transaction.

## Commit-event wait behavior

The test-network `ccutils.sh` invoke helper uses the peer CLI's `--waitForEvent` with a 60-second timeout and targets the lite peers on ports 7051 and 9051. In this Fabric CLI, `--waitForEvent` listens through each target peer's `DeliverFiltered` service. DRUNIX lite-peer forwarding is configured for the Gateway `CommitStatus` API, not for `DeliverFiltered`, so the CLI event wait can time out even after a committing peer validates and commits the transaction. Recent logs also show orderer delivery streams receiving `ENHANCE_YOUR_CALM` / `too_many_pings` GOAWAYs and reconnecting; this is a transport/event-delivery issue, not evidence that a transaction was lost or invalid.

The backend does not treat invoke acceptance or a timeout as proof of commit. After a non-conflict invoke error it queries `GetPayment` and accepts the outcome only if all submitted fields match and the payment is terminal. MVCC and insufficient-ledger-balance errors remain failures. The wait timeout was not increased.

## Runtime and credentials

`DRUNIX_MODE` defaults to `off`. Enable `real` only in a trusted local environment with the existing network running and configured. Mutations use the existing `Admin@org1.example.com` / `Admin@org2.example.com` X.509 operator identities. Protect those credentials as secrets; do not put them in source control or expose them to browser clients. The backend wrapper waits for commit events and then verifies payment and account keys; ambiguous outcomes are not blindly replayed.

## Limits

Participants and INR balances are synthetic. No UPI/NPCI production rail, external bank, gateway, or real money movement is implemented. Initial account creation can seed synthetic balances from the application projection; reconcile any pre-existing differences before wider rollout. `GetAllPayments` can fail response-schema validation when old records lack newer account/minor-amount fields; the payment flow uses keyed payment/account/history queries and does not call that list transaction.