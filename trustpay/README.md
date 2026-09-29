# TrustPay

TrustPay is a prototype programmable trust layer for simulated real-time payments. Its Go chaincode records payment metadata, risk assessments, policy decisions, and authoritative lifecycle state on the already-running NPCI DRUNIX network. Users, participants, and INR values are synthetic; no funds move and no UPI/NPCI payment rail is connected.

## Current milestone

This milestone contains only the chaincode, its unit tests, and integration notes. Frontend, PostgreSQL, authentication, and ML are intentionally not implemented.

## Test chaincode

From `trustpay/chaincode/trustpay`:

```bash
go test ./...
```

See [DRUNIX integration](docs/drunix-integration.md) for the network-specific deployment and smoke-test commands.

## On-chain data

The ledger stores synthetic sender/receiver references, whole-INR integer amount, currency, risk score and level, policy decision, lifecycle status, and deterministic transaction timestamps. It must not store credentials, tokens, keys, or unnecessary personal information.

This is a hackathon prototype, not a production settlement system.