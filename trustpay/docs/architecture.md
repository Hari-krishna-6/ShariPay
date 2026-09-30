# ShariPay Architecture

## System components

```text
FastAPI client/API -> ML risk inference -> deterministic policy -> SubmitPayment
									  PostgreSQL <- confirmed status/balance projection
													 |
													 v
									  DRUNIX / Fabric ledger (authoritative settlement)
```

The repository contains a FastAPI backend, PostgreSQL models/migrations, a synthetic-data ML package, and Go chaincode. A browser frontend is not implemented. The DRUNIX/Fabric test network is maintained in the adjacent `../drunix` checkout and is not recreated by the application.

## Payment flow

The API validates the authenticated sender, active beneficiary and accounts, request idempotency, and application context. It builds canonical model features, persists an ML risk assessment separately from a deterministic policy decision, then uses the DRUNIX client when `DRUNIX_MODE=real`. The client submits a single `SubmitPayment` transaction and waits for a peer commit event before querying the payment and account keys. A disabled client leaves the application request pending risk; it does not settle anything.

Inside Fabric, `SubmitPayment` validates risk/policy fields, reads or initializes account keys, creates the payment, records the four lifecycle snapshots for an approved payment, checks the ledger balance, and writes sender debit, receiver credit, and `COMPLETED` state in one transaction. Fabric MVCC validates competing reads/writes to the same sender account. No backend lock or PostgreSQL transaction is used to serialize Fabric spends.

## Authority and reconciliation

PostgreSQL is authoritative for application identities, authentication, beneficiaries, audit records, and user-facing projections. Fabric is authoritative for settled payment lifecycle state and account balances after a ledger account exists. The initial account state may be seeded from a synthetic application balance; subsequent payments use the existing ledger value. Reconcile any divergence before wider rollout. Do not report a database projection as settlement unless the Fabric commit and keyed ledger queries succeeded.

## Risk and policy boundary

The ML model returns a probability and separately derived rule factors. It neither writes ledger state nor determines the final status by itself. The deterministic backend policy validates party/account status, amount, risk metadata, and failed attempts, then selects `APPROVE`, `VERIFY`, `HOLD`, or `REJECT`. The chaincode independently enforces risk-band compatibility and performs the requested lifecycle/settlement operation.

## Privacy and scope

Ledger identities are synthetic references. Never place passwords, JWTs, API credentials, private keys, or unnecessary personal data on chain. Values and accounts are simulated; there is no UPI/NPCI production connectivity, external bank settlement, or real money movement.