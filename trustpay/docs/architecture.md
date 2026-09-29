# TrustPay Architecture

## Current milestone

The current implementation is Go chaincode on the existing NPCI DRUNIX network. The planned application path is:

```text
Future React client -> future FastAPI -> DRUNIX Gateway client -> TrustPay chaincode -> DRUNIX ledger
```

Only the chaincode and its local unit tests are in scope now. The already-running network is maintained separately in `../drunix`; this project does not reconfigure or replace it.

## Authority boundary

DRUNIX is authoritative for payment transaction records, risk assessment facts supplied to the contract, policy decision, lifecycle state, and explicit append-only payment history entries stored by the chaincode. A future PostgreSQL database may contain users, authentication data, refresh-token hashes, simulated balances, beneficiaries, and application metadata, but it must not override or be treated as the authority for on-chain payment state.

## Data minimization and simulation

On-chain sender and receiver identifiers are synthetic references. Do not store passwords, JWTs, private keys, or unnecessary personal data. Amounts in this first contract revision are whole INR units, and all payment participants and values are simulated. No real money moves and no UPI/NPCI production payment integration is claimed.

## Consistency limitations

The ledger contract protects transaction-state transitions. It does not atomically settle funds in an off-chain database or implement a production payment settlement protocol. A future application must define idempotency, reconciliation, and compensation around its simulated balance updates.