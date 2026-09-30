# ShariPay Backend

The FastAPI service provides registration/login, synthetic INR accounts, owner-scoped beneficiaries, risk/policy assessment, payment records, audit events, and an optional DRUNIX client. Product branding is ShariPay; the Fabric chaincode setting remains `trustpay` for ledger compatibility.

## Payment behavior

The application database stores users and payment projections. With `DRUNIX_MODE=off` (the safe default), payment requests remain `PENDING_RISK` and no ledger mutation occurs. With `DRUNIX_MODE=real`, the service sends one `SubmitPayment` invocation and projects the terminal chaincode state and ledger account balances after its commit wait and keyed queries confirm the result. Fabric, not PostgreSQL, serializes conflicting settlement writes. PostgreSQL does not lock ledger accounts or substitute for MVCC.

The application also stores simulated account balances for UI/API context. These are not bank balances or production funds. First-time ledger account initialization can seed a synthetic ledger balance; subsequent ledger account state is authoritative for settlement.

## Components

- SQLAlchemy models and Alembic migrations for users, refresh tokens, accounts, beneficiaries, payments, risk/policy decisions, and audit logs.
- JWT access tokens, hashed/rotated refresh tokens, password hashing, and owner-scoped API queries.
- Random Forest probability from the committed `ml/models/fraud_model.joblib` artifact, cached for the process; requests do not train the model.
- A deterministic policy engine that applies risk bands and hard rejection rules independently of ML inference.
- `app/drunix/service.py`, which uses the existing local network wrapper and `mychannel`/`trustpay` configuration. Protect the local X.509 admin credentials; never expose them to users.

## Setup

From this directory, create a virtual environment, install `requirements.txt`, copy `.env.example` to `.env`, and replace the development JWT secret before exposing the service. Start PostgreSQL with `docker compose up -d postgres`, then run `python -m alembic upgrade head` and `uvicorn app.main:app --reload`.

DRUNIX remains disabled unless explicitly enabled in the local environment. The sample environment values are development-only. No UPI/NPCI production rails, external bank APIs, frontend, or real money movement are implemented.

## Validation

Run the full backend suite from this directory with `python -m pytest -q`. Run the DRUNIX client/integration tests with `python -m pytest tests/test_drunix_integration.py -q`. These automated tests do not replace the separately documented live Fabric transaction/MVCC evidence.

## Known compatibility note

Legacy records without the new account/minor-amount fields can make the chaincode `GetAllPayments` response fail schema validation. The backend uses keyed payment/account/history queries for its payment flow; no backend code path calls `GetAllPayments`. This is documented rather than triggering an additional chaincode lifecycle upgrade.
