# TrustPay Backend

This directory contains the TrustPay application API, including authentication, simulated accounts, beneficiaries, and application-side payment requests.

Scope is intentionally limited to:

- PostgreSQL configuration via SQLAlchemy
- Alembic migration scaffolding
- FastAPI health endpoints
- JWT authentication and refresh-token rotation
- simulated INR accounts with a fixed demo balance created at registration
- owner-scoped beneficiaries and application-side payment records
- ML risk assessment on payment creation, persisted separately from payment state
- deterministic policy evaluation persisted separately from ML risk results
- user, device, risk-assessment, and audit-log domain models

Account balances are simulated demo values, not banking funds. Creating a payment validates the simulated balance and records a `CREATED` request; it does not transfer or reserve money.

The following remain intentionally out of scope:

- DRUNIX network integration
- chaincode changes
- frontend code
- UPI, NPCI, external banks, and payment gateways

## Local setup

1. Copy the repo-level environment template and fill in the database values.
2. Install dependencies:

   ```bash
   pip install -r backend/requirements.txt
   ```

3. Run Alembic migrations:

   ```bash
   cd backend
   alembic upgrade head
   ```

4. Start the app:

   ```bash
   uvicorn app.main:app --reload
   ```

## Application endpoints

- `GET /api/v1/accounts` and `GET /api/v1/accounts/{account_id}` return only the authenticated user's simulated accounts.
- `POST /api/v1/beneficiaries` and `GET /api/v1/beneficiaries` manage TrustPay-user beneficiaries.
- `POST /api/v1/payments` validates and records a simulated payment request without moving balances.
- `GET /api/v1/payments` and `GET /api/v1/payments/{transaction_id}` show only payments where the authenticated user is sender or receiver.
- Payment responses include server-generated ML and policy results; clients cannot set either result.

## ML risk integration

Payment creation loads the existing `ml/models/fraud_model.joblib` artifact on first use and caches it for the application process. Override the path with `ML_MODEL_PATH` when deploying the backend separately from the repository layout. The backend reuses `ml/src/features.py` for validation and canonical feature order, and `ml/src/predict.py` risk-factor and threshold functions; it does not train or regenerate data at startup or during requests.

Amount, time, account age, new-beneficiary status, payment frequency/velocity, average historical payment amount, deviation, and failed payment-attempt audits are derived from application records. The model currently has no device or location context, and no adjudicated fraud history exists, so `is_new_device`, `is_location_change`, and `previous_fraud_count` use deterministic zero values. A new account with no payment history uses a documented neutral historical average of INR 2,200.00; later assessments use the sender's observed prior payment average.

Successful payment requests remain `PENDING_RISK` after policy evaluation. Risk score, level, and rule-based factors are stored in `risk_assessments`; policy decision, reason, version, and triggered rules are stored separately in `policy_decisions`. Both results are returned only to the payment sender and receiver. LOW risk maps to `APPROVE`, MEDIUM to `VERIFY`, and HIGH to `HOLD`. Inactive parties/accounts, self-payment, invalid or insufficient amounts, excessive failed attempts, invalid lifecycle status, or inconsistent risk metadata map to `REJECT`. Prior-fraud history and repeated failures can escalate to `HOLD`.

Policy evaluation consumes only backend-generated context, runs without model inference, and cannot transfer or reserve balances. Policy evaluation failure rolls back payment, risk, and policy rows. No DRUNIX call is made; a later milestone will consume the persisted policy output.
