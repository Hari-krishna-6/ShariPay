# ShariPay

## AI-Powered Trust Layer for Real-Time Payments

**Team SKYE**  
**Creators:** Hari Krishna & Subiksha

ShariPay is an AI-powered trust and risk-assessment layer for real-time payments. It combines **machine learning, deterministic policy evaluation, and blockchain-based transaction settlement** to make payment decisions transparent, auditable, and programmatically enforceable.

Built for the **India Blockchain Forum — Build the Future of Payments in India** challenge.

---

## Overview

Real-time payment systems prioritize speed, but payment processing also requires:

- Fraud and risk assessment
- Deterministic payment policies
- Duplicate-payment protection
- Double-spend protection
- Transaction traceability
- Auditable state transitions
- Consistent distributed state

ShariPay introduces an intelligent decision layer between a payment request and its final settlement state.

For every payment, ShariPay:

1. Validates the payment request.
2. Extracts transaction features.
3. Calculates a fraud-risk score using a machine-learning model.
4. Classifies the transaction as `LOW`, `MEDIUM`, or `HIGH` risk.
5. Applies deterministic payment policies.
6. Submits the resulting payment operation to the DRUNIX blockchain.
7. Updates sender and receiver ledger balances during blockchain settlement.
8. Uses Hyperledger Fabric's MVCC validation to protect against concurrent conflicting transactions.
9. Maintains an auditable transaction history.

---

# Key Features

## 1. AI-Based Risk Assessment

ShariPay uses a **Random Forest** machine-learning model to evaluate payment-related features and produce a fraud-risk score.

| Risk Level | Score | Default Policy |
|---|---:|---|
| LOW | `< 0.30` | APPROVE |
| MEDIUM | `0.30 – 0.69` | VERIFY |
| HIGH | `>= 0.70` | HOLD |

The ML model performs **risk assessment only**. It does not directly modify blockchain state. The final payment decision is produced by a separate deterministic policy engine.

---

## 2. Deterministic Policy Engine

The policy engine converts the ML risk assessment and payment rules into an explicit decision.

Possible decisions:

- `APPROVE`
- `VERIFY`
- `HOLD`
- `REJECT`

Separating risk prediction from authorization logic makes payment decisions easier to audit and test.

---

## 3. Blockchain-Based Payment Settlement

ShariPay uses **DRUNIX with Hyperledger Fabric** for distributed payment state management.

The blockchain maintains:

- Payment state
- Payment history
- Sender account balance
- Receiver account balance
- Risk information
- Policy decision
- Transaction identity

The Fabric chaincode identifier is intentionally maintained as:

```text
trustpay
```

This preserves continuity with the existing Fabric ledger and lifecycle history. The product itself is branded **ShariPay**.

### Current verified deployment

```text
Channel:  mychannel
Chaincode: trustpay
Version:  1.9
Sequence: 10
```

Package:

```text
trustpay_1.9:112b1c49559d05e217fe4babc05f6112cf73ffcf28d9c3834646b2d17ac94789
```

The v1.9 chaincode was installed and approved by both organizations and committed successfully.

---

# 4. Real Ledger Settlement

Approved payments are settled directly through the Fabric transaction.

```text
CREATED
   ↓
RISK_ASSESSED
   ↓
APPROVED
   ↓
COMPLETED
```

During settlement, sender and receiver account records are modified as part of the same Fabric transaction.

The backend does not use a PostgreSQL lock as the authoritative mechanism for preventing competing ledger spends.

---

# 5. Double-Spend Protection Using Fabric MVCC

Two concurrent payment transactions that attempt to spend the same sender balance access the same Fabric account state.

Hyperledger Fabric records transaction read/write sets and validates them during block processing. A transaction based on stale account state can be invalidated using MVCC validation.

### Live verification

A real concurrent test was performed:

```text
Initial sender balance: 10,000

Transaction A: spend 7,000
Transaction B: spend 7,000
```

Both transactions were submitted concurrently against the same sender account.

Result:

```text
Transaction A → VALID
Transaction B → MVCC_READ_CONFLICT (code 11)
```

Final ledger:

```text
Sender balance: 3,000
```

Only the receiver of the committed transaction received `7,000`.

The losing payment and its receiver account were not committed.

This demonstrates that double-spend protection was enforced by the **live Fabric ledger's MVCC validation**, rather than application-level serialization.

---

# 6. Concurrent Lifecycle Conflict Protection

A `VERIFICATION_REQUIRED` payment was simultaneously targeted by:

```text
ApprovePayment
RejectPayment
```

Fabric produced:

```text
ApprovePayment → VALID
RejectPayment  → MVCC_READ_CONFLICT (code 11)
```

The final payment state was:

```text
APPROVED
```

This demonstrates protection against competing stale-state lifecycle mutations.

---

# 7. Idempotent Payments

ShariPay supports idempotent payment processing.

### Exact replay

The same payment request and idempotency key return the existing transaction.

No second ledger settlement occurs.

### Changed request with the same idempotency key

A request using the same idempotency key but different payment data is rejected.

### Transaction ID replay

The chaincode validates existing transaction data to prevent a transaction ID from being reused with different payment information.

---

# 8. Insufficient Balance Protection

The ledger validates the sender's available balance before settlement.

An attempted payment exceeding the sender's actual Fabric ledger balance is rejected.

The test confirmed:

```text
Sender balance: unchanged
Receiver balance: unchanged
Payment: not created
```

---

# Payment Lifecycle

### Approved Payment

```text
CREATED
   ↓
RISK_ASSESSED
   ↓
APPROVED
   ↓
COMPLETED
```

### Verification Payment

```text
CREATED
   ↓
RISK_ASSESSED
   ↓
VERIFICATION_REQUIRED
```

### High-Risk Payment

```text
CREATED
   ↓
RISK_ASSESSED
   ↓
HELD
```

### Rejected Payment

```text
CREATED
   ↓
RISK_ASSESSED
   ↓
REJECTED
```

Invalid blockchain state transitions are rejected by the smart contract.

---

# System Architecture

```text
                    ┌─────────────────────┐
                    │   React Frontend    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    FastAPI Backend  │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ▼                ▼                ▼
       ┌─────────────┐  ┌──────────────┐  ┌──────────────┐
       │ PostgreSQL  │  │  ML Risk     │  │   Policy     │
       │             │  │   Engine     │  │   Engine     │
       └─────────────┘  └──────┬───────┘  └──────┬───────┘
                                │                 │
                                └────────┬────────┘
                                         │
                                         ▼
                              ┌─────────────────────┐
                              │       DRUNIX        │
                              │  Hyperledger Fabric │
                              └──────────┬──────────┘
                                         │
                                         ▼
                              ┌─────────────────────┐
                              │   Payment Ledger    │
                              │ & Transaction       │
                              │      History        │
                              └─────────────────────┘
```

---

# Payment Decision Flow

```text
Payment Request
      │
      ▼
Authentication & Validation
      │
      ▼
Payment Creation
      │
      ▼
Feature Extraction
      │
      ▼
ML Risk Assessment
      │
      ├──────── LOW ────────► APPROVE
      │
      ├──────── MEDIUM ─────► VERIFY
      │
      └──────── HIGH ───────► HOLD
                                 │
                                 ▼
                         Verification / Review

REJECT ───────────────────────► REJECTED
```

---

# Machine Learning

## Model

ShariPay uses a Random Forest classifier.

| Parameter | Value |
|---|---:|
| Algorithm | Random Forest |
| Estimators | 600 |
| Maximum Depth | 10 |
| Minimum Samples per Leaf | 2 |
| Max Features | `sqrt` |
| Class Weight | `balanced` |
| Random State | 42 |

The model was trained using synthetic payment data for the hackathon implementation.

## Model Performance

| Metric | Value |
|---|---:|
| Accuracy | 0.9500 |
| Precision | 0.6718 |
| Recall | 0.8103 |
| F1 Score | 0.7346 |
| ROC-AUC | 0.9205 |
| False Positive Rate | 0.0370 |

The ML component is a risk-assessment layer and does not independently authorize blockchain settlement.

---

# Technology Stack

## Frontend

- React
- JavaScript / TypeScript
- REST API integration

## Backend

- Python
- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL
- JWT authentication
- Argon2id password hashing

## Machine Learning

- Python
- NumPy
- Pandas
- Scikit-learn
- Random Forest

## Blockchain

- DRUNIX
- Hyperledger Fabric
- Go chaincode
- Fabric channels
- Smart-contract-based payment settlement
- Fabric MVCC validation

---

# Backend API

## Authentication

```text
POST /auth/register
POST /auth/login
POST /auth/refresh
POST /auth/logout
```

## Accounts

```text
GET /accounts
GET /accounts/{account_id}
```

## Beneficiaries

```text
POST /beneficiaries
GET /beneficiaries
```

## Payments

```text
POST /payments
GET /payments/{payment_id}
```

Payment information includes application state, risk assessment, policy decision, and blockchain synchronization status.

---

# Database

PostgreSQL stores application-level information including:

- Users
- Accounts
- Beneficiaries
- Payments
- Risk assessments
- Policy decisions
- Devices
- Audit logs

The Fabric ledger maintains the corresponding blockchain payment state and transaction history.

---

# Project Structure

```text
ShariPay/
│
├── drunix/
│   ├── drunix-network/
│   └── vendor/
│
├── trustpay/
│   ├── backend/
│   │   ├── alembic/
│   │   │   └── versions/
│   │   ├── app/
│   │   │   ├── api/
│   │   │   ├── auth/
│   │   │   ├── drunix/
│   │   │   ├── models/
│   │   │   ├── payments/
│   │   │   ├── policy/
│   │   │   └── risk/
│   │   └── tests/
│   │
│   └── ml/
│       ├── data/
│       ├── models/
│       ├── src/
│       │   ├── generate_data.py
│       │   ├── train.py
│       │   ├── predict.py
│       │   └── features.py
│       └── tests/
│
├── frontend/
│
└── README.md
```

The lowercase `trustpay` directory and chaincode identifier are retained for technical compatibility with the existing Fabric deployment.

---

# Security

ShariPay implements:

- JWT-based authentication
- Refresh-token rotation
- Refresh-token revocation
- Argon2id password hashing
- Role-based access control
- Payment ownership validation
- Idempotency protection
- Deterministic policy evaluation
- Blockchain transaction history
- Database constraints
- Environment-based secret configuration
- Fabric MVCC conflict validation

Sensitive files such as `.env`, generated private keys, generated certificates, Python virtual environments, and cache/build files are excluded from version control where applicable.

---

# Running the Project

## Prerequisites

- Windows 11
- WSL2
- Ubuntu 24.04
- Docker Desktop
- Python 3.14+
- Go
- Node.js
- npm
- PostgreSQL

The DRUNIX network must be available before enabling real blockchain synchronization.

## 1. Clone

```bash
git clone https://github.com/Hari-krishna-6/ShariPay.git
cd ShariPay
```

## 2. Start DRUNIX

Follow the official DRUNIX network setup and ensure the required Fabric organizations, peers, identities, and channel are available.

## 3. Configure Backend

Create:

```text
trustpay/backend/.env
```

Configure the required database, authentication, ML model, and DRUNIX settings.

Do not commit this file.

## 4. Install Backend Dependencies

```bash
cd trustpay/backend
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Linux / WSL:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## 5. Run Database Migrations

```bash
alembic upgrade head
```

## 6. Start Backend

```bash
uvicorn app.main:app --reload
```

API:

```text
http://localhost:8000
```

FastAPI docs:

```text
http://localhost:8000/docs
```

---

# Frontend

```bash
cd frontend
npm install
npm run dev
```

The React frontend communicates with the FastAPI backend through REST APIs.

---

# Testing

Backend:

```bash
cd trustpay/backend
pytest
```

ML:

```bash
pytest trustpay/ml/tests
```

Chaincode:

```bash
go test ./...
```

Static analysis:

```bash
go vet ./...
```

## Final verified test results

```text
Backend:          63 passed, 1 skipped
DRUNIX integration: 18 passed
Chaincode:        Passed
go vet:           Passed
```

---

# Live Blockchain Verification

## Basic Settlement

A live `SubmitPayment` transaction was committed successfully.

```text
Payment: COMPLETED
Risk: LOW
Risk Score: 0.12
Decision: APPROVE
```

History:

```text
CREATED
→ RISK_ASSESSED
→ APPROVED
→ COMPLETED
```

Live balance test:

```text
Sender:   10,000 → 8,800
Receiver:    500 → 1,700
```

## Exact Replay

The same payment was submitted again with identical transaction data.

Result:

```text
VALID
```

No additional balance mutation occurred and no duplicate payment history was created.

## Transaction-ID Substitution

The same transaction ID was reused with different payment information.

Result:

```text
Rejected
```

The original payment and balances remained unchanged.

## Insufficient Balance

A payment exceeding the sender's ledger balance was attempted.

Result:

```text
Rejected: insufficient ledger balance
```

No receiver account or completed payment was created.

---

# Distributed Concurrency Verification

## Concurrent Double Spend

Starting balance:

```text
10,000
```

Concurrent payments:

```text
7,000
7,000
```

Result:

```text
Transaction 1 → VALID
Transaction 2 → MVCC_READ_CONFLICT (11)
```

Final balance:

```text
3,000
```

Only one receiver received `7,000`.

The conflicting payment and receiver account were not committed.

This is live Fabric MVCC evidence.

## Concurrent Lifecycle Conflict

A `VERIFICATION_REQUIRED` payment was concurrently targeted by:

```text
ApprovePayment
RejectPayment
```

Result:

```text
ApprovePayment → VALID
RejectPayment  → MVCC_READ_CONFLICT (11)
```

Final state:

```text
APPROVED
```

---

# Important Scope

ShariPay is a **hackathon prototype and payment trust layer**.

It uses simulated payment accounts and balances for demonstration.

It does **not** directly connect to:

- UPI production infrastructure
- NPCI production payment infrastructure
- Real bank accounts
- Real bank settlement systems
- Production payment networks

The blockchain component demonstrates programmable payment-state management, distributed conflict handling, and auditability using the DRUNIX environment.

The ML dataset used for the prototype is synthetic.

---

# Known Limitation

Some older ledger records may not contain newer account/amount fields expected by the current `GetAllPayments` response schema.

This is a backward-compatibility issue with historical records and does not affect the verified payment settlement, idempotency, insufficient-balance, or live MVCC tests described above.

---

# Future Improvements

- Real payment-network integration
- Real-time streaming fraud detection
- Device fingerprinting
- Location-based risk signals
- Behavioral transaction profiling
- Explainable AI dashboards
- Advanced anomaly detection
- Human verification workflows
- Real-time notification system
- Payment analytics dashboard
- Model monitoring and retraining pipeline
- Production-grade key management
- Multi-organization blockchain deployment
- Historical ledger schema compatibility improvements

---

# Challenge

**Organization:** India Blockchain Forum

**Challenge:** Build the Future of Payments in India

**Problem Statement:** Real-Time Payments

**Project:** ShariPay — AI-Powered Trust Layer for Real-Time Payments

**Team:** SKYE

---

# Team

## Hari Krishna

Backend • Machine Learning • Blockchain

## Subiksha

Frontend • User Experience

---

# Disclaimer

This project is developed as a hackathon prototype to demonstrate an AI-assisted and blockchain-backed architecture for real-time payment risk management.

Payment accounts, balances, and transaction flows used in the demonstration are simulated.

ShariPay does not represent a production banking, UPI, or payment-settlement system.
