# DRUNIX Integration

## Verified environment

The live local test network uses channel `mychannel` and Fabric chaincode identifier `trustpay`. The verified definition is version `1.9`, sequence `10`, package `trustpay_1.9:112b1c49559d05e217fe4babc05f6112cf73ffcf28d9c3834646b2d17ac94789`. Org1 and Org2 installed and approved that package. Commit transaction `85c89946c40d54fff55fea0c62c4c433e23a71b253f46f0909f7372327c06f9b` was VALID in block 163.

Do not use the old version `1.6` / sequence `7` example from earlier project revisions. Sequence 9 reported version 1.8 but was bound to the old v1.7 package; sequence 10 is the corrected package. Query the full committing peers for the current definition before any future lifecycle change. This documentation does not authorize or perform an upgrade.

In this network, `peer0` is configured as a lite peer and reports only block 0. The committing peers are `peer1` (ports 7061 and 9061); both reported height 178 and identical hashes during verification. Use the configured committing peers when assessing ledger height.

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

## Live evidence (2026-09-30)

- Basic `SubmitPayment`: VALID transaction `dc6fffa45ed2400255398359ee68a5a13032744260a27b9f4b965e759d9536e6` in block 164; final status `COMPLETED`, history sequences 1–4, sender 10,000 → 8,800 minor units, receiver 500 → 1,700.
- Exact replay: VALID transaction `5c08d3ef463b946d20e9d51685546e34e149388e1b620ee7351b3da9e903b9e1` in block 168; no second debit/credit or history entry.
- Concurrent double spend: block 170; one VALID (code 0), one `MVCC_READ_CONFLICT` (code 11); only one 7,000-unit spend from 10,000 committed, leaving 3,000.
- Concurrent lifecycle conflict: block 177; one VALID approval, one `MVCC_READ_CONFLICT` (code 11); final status `APPROVED`.

The peer `querycommitted` definition and the peer block validation filter were both inspected. Endorsement success alone is not treated as a committed transaction.

## Runtime and credentials

`DRUNIX_MODE` defaults to `off`. Enable `real` only in a trusted local environment with the existing network running and configured. Mutations use the existing `Admin@org1.example.com` / `Admin@org2.example.com` X.509 operator identities. Protect those credentials as secrets; do not put them in source control or expose them to browser clients. The backend wrapper waits for commit events and then verifies payment and account keys; ambiguous outcomes are not blindly replayed.

## Limits

Participants and INR balances are synthetic. No UPI/NPCI production rail, external bank, gateway, or real money movement is implemented. Initial account creation can seed synthetic balances from the application projection; reconcile any pre-existing differences before wider rollout. `GetAllPayments` can fail response-schema validation when old records lack newer account/minor-amount fields; the payment flow uses keyed payment/account/history queries and does not call that list transaction.