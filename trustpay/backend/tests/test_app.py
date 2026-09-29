from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import hash_password, hash_refresh_token
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models.refresh_token import RefreshToken
from app.models.account import Account
from app.models.audit_log import AuditLog
from app.models.beneficiary import Beneficiary
from app.models.payment import Payment
from app.models.risk_assessment import RiskAssessment
from app.models.user import User
from app.api.payments import get_policy_service, get_risk_assessment_service
from app.models.policy_decision import PolicyDecisionRecord
from app.policy import PolicyEvaluationError
from app.risk.service import RiskAssessmentService, clear_model_cache
from ml.src.features import FEATURE_NAMES

client = TestClient(app)


def reset_db() -> None:
    with SessionLocal() as db:
        db.query(Payment).delete()
        db.query(AuditLog).delete()
        db.query(RefreshToken).delete()
        db.query(User).delete()
        db.commit()


def unique_email(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}@example.com"


def create_user(email: str, password: str = "StrongPass1", role: str = "USER", is_active: bool = True) -> User:
    with SessionLocal() as db:
        user = User(
            email=email,
            full_name="Test User",
            password_hash=hash_password(password),
            role=role,
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def register_and_login(prefix: str) -> tuple[str, str]:
    email = unique_email(prefix)
    registered = client.post("/api/v1/auth/register", json={"email": email, "password": "StrongPass1"})
    assert registered.status_code == 201
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "StrongPass1"})
    assert login.status_code == 200
    return email, login.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def add_beneficiary(token: str, email: str) -> dict:
    response = client.post(
        "/api/v1/beneficiaries",
        json={"email": email},
        headers=auth_headers(token),
    )
    assert response.status_code == 201
    return response.json()


def test_root_status() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "TrustPay"


def test_health_status() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_database_health_when_db_is_available() -> None:
    response = client.get("/api/v1/health/db")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"] == "connected"


def test_register_valid_user_succeeds() -> None:
    reset_db()
    email = unique_email("register")
    response = client.post("/api/v1/auth/register", json={"email": email, "password": "StrongPass1"})
    assert response.status_code == 201
    payload = response.json()
    assert payload["email"] == email
    assert "password" not in payload
    assert "password_hash" not in payload

    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        assert user.role == "USER"
        assert user.password_hash != "StrongPass1"
        assert user.password_hash.startswith("$argon2")
        account = db.query(Account).filter(Account.user_id == user.id).one()
        assert account.currency == "INR"
        assert account.balance == Decimal("100000.00")
        assert account.is_active is True


def test_register_duplicate_email_rejected() -> None:
    reset_db()
    email = unique_email("duplicate")
    first = client.post("/api/v1/auth/register", json={"email": email, "password": "StrongPass1"})
    assert first.status_code == 201
    second = client.post("/api/v1/auth/register", json={"email": email, "password": "AnotherPass2"})
    assert second.status_code == 409


def test_login_valid_credentials_and_password_hash_not_returned() -> None:
    reset_db()
    email = unique_email("login")
    create_user(email, password="StrongPass1", role="USER", is_active=True)
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "StrongPass1"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert "access_token" in payload
    assert "refresh_token" in payload
    assert "password_hash" not in payload


def test_login_wrong_password_rejected() -> None:
    reset_db()
    email = unique_email("wrongpass")
    create_user(email, password="StrongPass1")
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "WrongPass1"})
    assert response.status_code == 401


def test_login_inactive_user_rejected() -> None:
    reset_db()
    email = unique_email("inactive")
    create_user(email, password="StrongPass1", is_active=False)
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "StrongPass1"})
    assert response.status_code == 401


def test_access_token_and_me_work() -> None:
    reset_db()
    email = unique_email("me")
    create_user(email, password="StrongPass1")
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "StrongPass1"})
    token = login.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    payload = me.json()
    assert payload["email"] == email
    assert payload["role"] == "USER"
    assert "password_hash" not in payload


def test_invalid_token_rejected() -> None:
    response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid.token.value"})
    assert response.status_code == 401


def test_expired_token_rejected() -> None:
    expired_token = jwt.encode(
        {"sub": str(uuid.uuid4()), "role": "USER", "token_type": "access", "iat": int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()), "exp": int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())},
        "development-only-jwt-secret-change-me",
        algorithm="HS256",
    )
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert response.status_code == 401


def test_valid_refresh_rotates_token() -> None:
    reset_db()
    email = unique_email("refresh")
    create_user(email, password="StrongPass1")
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "StrongPass1"})
    assert login.status_code == 200
    old_refresh = login.json()["refresh_token"]
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"]
    assert payload["refresh_token"] != old_refresh

    with SessionLocal() as db:
        old_record = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_refresh_token(old_refresh)).first()
        assert old_record is not None
        assert old_record.revoked_at is not None
        new_record = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_refresh_token(payload["refresh_token"])).one()
        assert old_record.replaced_by == new_record.id
        assert new_record.replaced_by is None

    reused = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reused.status_code == 401


def test_revoked_refresh_token_rejected() -> None:
    reset_db()
    email = unique_email("revoked")
    create_user(email, password="StrongPass1")
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "StrongPass1"})
    refresh = login.json()["refresh_token"]
    logout = client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert logout.status_code == 200
    second = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert second.status_code == 401


def test_expired_refresh_token_rejected() -> None:
    reset_db()
    email = unique_email("expiredrefresh")
    user = create_user(email, password="StrongPass1")
    with SessionLocal() as db:
        expired = RefreshToken(
            id=uuid.uuid4(),
            user_id=user.id,
            token_hash=hash_refresh_token("expired-refresh-token"),
            token_family="family-expired",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            revoked_at=None,
            replaced_by=None,
        )
        db.add(expired)
        db.commit()

    response = client.post("/api/v1/auth/refresh", json={"refresh_token": "expired-refresh-token"})
    assert response.status_code == 401


def test_logout_makes_refresh_token_unusable() -> None:
    reset_db()
    email = unique_email("logout")
    create_user(email, password="StrongPass1")
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "StrongPass1"})
    refresh = login.json()["refresh_token"]
    logout = client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert logout.status_code == 200
    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert refreshed.status_code == 401


def test_user_not_admin_and_admin_access() -> None:
    reset_db()
    user_email = unique_email("user")
    admin_email = unique_email("admin")
    create_user(user_email, password="StrongPass1", role="USER")
    create_user(admin_email, password="StrongPass1", role="ADMIN")

    user_login = client.post("/api/v1/auth/login", json={"email": user_email, "password": "StrongPass1"})
    user_token = user_login.json()["access_token"]
    user_response = client.get("/api/v1/auth/admin-check", headers={"Authorization": f"Bearer {user_token}"})
    assert user_response.status_code == 403

    admin_login = client.post("/api/v1/auth/login", json={"email": admin_email, "password": "StrongPass1"})
    admin_token = admin_login.json()["access_token"]
    admin_response = client.get("/api/v1/auth/admin-check", headers={"Authorization": f"Bearer {admin_token}"})
    assert admin_response.status_code == 200


def test_account_and_beneficiary_access_is_owner_scoped() -> None:
    reset_db()
    owner_email, owner_token = register_and_login("accountowner")
    recipient_email, _ = register_and_login("accountrecipient")
    other_email, other_token = register_and_login("accountother")

    owner_accounts = client.get("/api/v1/accounts", headers=auth_headers(owner_token))
    assert owner_accounts.status_code == 200
    assert len(owner_accounts.json()) == 1
    owner_account = owner_accounts.json()[0]
    assert owner_account["account_type"] == "SIMULATED"
    assert Decimal(str(owner_account["balance"])) == Decimal("100000.00")
    assert owner_account["currency"] == "INR"
    assert owner_account["is_active"] is True

    other_accounts = client.get("/api/v1/accounts", headers=auth_headers(other_token))
    assert len(other_accounts.json()) == 1
    hidden = client.get(f"/api/v1/accounts/{owner_account['id']}", headers=auth_headers(other_token))
    assert hidden.status_code == 404

    beneficiary = add_beneficiary(owner_token, recipient_email)
    owner_beneficiaries = client.get("/api/v1/beneficiaries", headers=auth_headers(owner_token))
    other_beneficiaries = client.get("/api/v1/beneficiaries", headers=auth_headers(other_token))
    assert [item["id"] for item in owner_beneficiaries.json()] == [beneficiary["id"]]
    assert other_beneficiaries.json() == []

    same_recipient = client.post(
        "/api/v1/beneficiaries",
        json={"email": recipient_email},
        headers=auth_headers(other_token),
    )
    assert same_recipient.status_code == 201
    duplicate = client.post(
        "/api/v1/beneficiaries",
        json={"email": recipient_email},
        headers=auth_headers(owner_token),
    )
    assert duplicate.status_code == 409
    assert owner_email != other_email


def test_payment_creation_is_idempotent_visible_to_parties_and_does_not_transfer_balance() -> None:
    reset_db()
    sender_email, sender_token = register_and_login("paymentsender")
    receiver_email, receiver_token = register_and_login("paymentreceiver")
    _, unrelated_token = register_and_login("paymentunrelated")
    beneficiary = add_beneficiary(sender_token, receiver_email)
    payload = {
        "beneficiary_id": beneficiary["id"],
        "amount": "25.50",
        "currency": "INR",
        "idempotency_key": "payment-test-key-1",
    }

    sender_account_before = client.get("/api/v1/accounts", headers=auth_headers(sender_token)).json()[0]
    receiver_account_before = client.get("/api/v1/accounts", headers=auth_headers(receiver_token)).json()[0]
    created = client.post("/api/v1/payments", json=payload, headers=auth_headers(sender_token))
    assert created.status_code == 201
    payment = created.json()
    assert payment["status"] == "PENDING_RISK"
    assert payment["risk_assessment"]["risk_level"] in {"LOW", "MEDIUM", "HIGH"}
    assert Decimal(str(payment["risk_assessment"]["risk_score"])) >= Decimal("0")
    assert payment["sender_user_id"] != payment["receiver_user_id"]
    assert Decimal(str(payment["amount"])) == Decimal("25.50")

    replay = client.post("/api/v1/payments", json=payload, headers=auth_headers(sender_token))
    assert replay.status_code == 200
    assert replay.json()["transaction_id"] == payment["transaction_id"]
    assert replay.json()["policy_result"] == payment["policy_result"]

    changed_payload = {**payload, "amount": "26.00"}
    conflict = client.post("/api/v1/payments", json=changed_payload, headers=auth_headers(sender_token))
    assert conflict.status_code == 409

    sender_account_after = client.get("/api/v1/accounts", headers=auth_headers(sender_token)).json()[0]
    receiver_account_after = client.get("/api/v1/accounts", headers=auth_headers(receiver_token)).json()[0]
    assert Decimal(str(sender_account_after["balance"])) == Decimal(str(sender_account_before["balance"]))
    assert Decimal(str(receiver_account_after["balance"])) == Decimal(str(receiver_account_before["balance"]))

    for token in (sender_token, receiver_token):
        visible = client.get(f"/api/v1/payments/{payment['transaction_id']}", headers=auth_headers(token))
        assert visible.status_code == 200
        assert visible.json()["transaction_id"] == payment["transaction_id"]
        assert len(client.get("/api/v1/payments", headers=auth_headers(token)).json()) == 1

    hidden = client.get(f"/api/v1/payments/{payment['transaction_id']}", headers=auth_headers(unrelated_token))
    assert hidden.status_code == 404
    assert client.get("/api/v1/payments", headers=auth_headers(unrelated_token)).json() == []
    assert sender_email != receiver_email

    with SessionLocal() as db:
        assert db.query(Payment).filter(Payment.sender_user_id == uuid.UUID(payment["sender_user_id"])).count() == 1
        stored = db.query(Payment).filter(Payment.transaction_id == payment["transaction_id"]).one()
        assert stored.amount == Decimal("25.50")
        assessment = db.query(RiskAssessment).filter(RiskAssessment.payment_id == stored.id).one()
        assert assessment.policy_decision is None
        assert assessment.risk_level == payment["risk_assessment"]["risk_level"]


def test_payment_rejects_unauthenticated_invalid_currency_and_invalid_amounts() -> None:
    reset_db()
    sender_email, sender_token = register_and_login("validation_sender")
    receiver_email, _ = register_and_login("validation_receiver")
    beneficiary = add_beneficiary(sender_token, receiver_email)
    payload = {
        "beneficiary_id": beneficiary["id"],
        "amount": "1.00",
        "currency": "INR",
        "idempotency_key": "validation-key",
    }

    unauthenticated = client.post("/api/v1/payments", json=payload)
    assert unauthenticated.status_code == 401
    unsupported_currency = client.post(
        "/api/v1/payments",
        json={**payload, "currency": "USD", "idempotency_key": "unsupported-currency"},
        headers=auth_headers(sender_token),
    )
    assert unsupported_currency.status_code == 422

    for invalid_amount in ("0", "-1", "1.001"):
        response = client.post(
            "/api/v1/payments",
            json={**payload, "amount": invalid_amount, "idempotency_key": f"invalid-{invalid_amount}"},
            headers=auth_headers(sender_token),
        )
        assert response.status_code == 422

    assert sender_email != receiver_email


def test_payment_rejects_missing_or_foreign_beneficiary_and_self_payment() -> None:
    reset_db()
    sender_email, sender_token = register_and_login("beneficiary_sender")
    receiver_email, _ = register_and_login("beneficiary_receiver")
    other_email, other_token = register_and_login("beneficiary_other")
    foreign_beneficiary = add_beneficiary(other_token, receiver_email)
    own_beneficiary = add_beneficiary(sender_token, receiver_email)
    base_payload = {"amount": "10.00", "currency": "INR"}

    missing = client.post(
        "/api/v1/payments",
        json={**base_payload, "beneficiary_id": str(uuid.uuid4()), "idempotency_key": "missing-beneficiary"},
        headers=auth_headers(sender_token),
    )
    assert missing.status_code == 404
    foreign = client.post(
        "/api/v1/payments",
        json={**base_payload, "beneficiary_id": foreign_beneficiary["id"], "idempotency_key": "foreign-beneficiary"},
        headers=auth_headers(sender_token),
    )
    assert foreign.status_code == 404

    with SessionLocal() as db:
        sender = db.query(User).filter(User.email == sender_email).one()
        self_beneficiary = Beneficiary(
            user_id=sender.id,
            beneficiary_reference=sender_email,
            display_name="Self",
            status="ACTIVE",
        )
        db.add(self_beneficiary)
        db.commit()
        db.refresh(self_beneficiary)
        self_beneficiary_id = str(self_beneficiary.id)

    self_payment = client.post(
        "/api/v1/payments",
        json={**base_payload, "beneficiary_id": self_beneficiary_id, "idempotency_key": "self-payment"},
        headers=auth_headers(sender_token),
    )
    assert self_payment.status_code == 400

    no_self_beneficiary = client.post(
        "/api/v1/beneficiaries",
        json={"email": sender_email},
        headers=auth_headers(sender_token),
    )
    assert no_self_beneficiary.status_code == 400
    assert own_beneficiary["id"] != foreign_beneficiary["id"]
    assert other_email != sender_email


def test_inactive_sender_and_insufficient_balance_are_rejected_and_audited() -> None:
    reset_db()
    sender_email, sender_token = register_and_login("inactive_sender")
    receiver_email, _ = register_and_login("inactive_receiver")
    beneficiary = add_beneficiary(sender_token, receiver_email)
    payload = {
        "beneficiary_id": beneficiary["id"],
        "amount": "100.01",
        "currency": "INR",
        "idempotency_key": "insufficient-balance",
    }

    with SessionLocal() as db:
        sender = db.query(User).filter(User.email == sender_email).one()
        account = db.query(Account).filter(Account.user_id == sender.id).one()
        account.balance = Decimal("100.00")
        db.commit()

    insufficient = client.post("/api/v1/payments", json=payload, headers=auth_headers(sender_token))
    assert insufficient.status_code == 409

    with SessionLocal() as db:
        assert db.query(Payment).filter(Payment.idempotency_key == payload["idempotency_key"]).count() == 0
        assert db.query(AuditLog).filter(AuditLog.event_type == "payment_insufficient_balance").count() == 1
        sender = db.query(User).filter(User.email == sender_email).one()
        sender.is_active = False
        db.commit()

    inactive = client.post(
        "/api/v1/payments",
        json={**payload, "amount": "1.00", "idempotency_key": "inactive-sender"},
        headers=auth_headers(sender_token),
    )
    assert inactive.status_code == 401


def test_payment_rejects_client_controlled_fields() -> None:
    reset_db()
    sender_email, sender_token = register_and_login("security_sender")
    receiver_email, _ = register_and_login("security_receiver")
    beneficiary = add_beneficiary(sender_token, receiver_email)
    injection = {
        "beneficiary_id": beneficiary["id"],
        "amount": "1.00",
        "currency": "INR",
        "idempotency_key": "security-injection",
        "sender_user_id": str(uuid.uuid4()),
        "sender_account_id": str(uuid.uuid4()),
        "status": "COMPLETED",
        "risk_score": 0,
        "risk_level": "LOW",
        "policy_decision": "APPROVE",
        "policy_result": {"decision": "APPROVE"},
        "drunix_state": "COMPLETED",
    }
    response = client.post("/api/v1/payments", json=injection, headers=auth_headers(sender_token))
    assert response.status_code == 422
    assert sender_email != receiver_email


def test_payment_response_exposes_assessment_only_to_payment_parties_and_uses_exact_feature_order() -> None:
    reset_db()
    sender_email, sender_token = register_and_login("risk_sender")
    receiver_email, receiver_token = register_and_login("risk_receiver")
    _, unrelated_token = register_and_login("risk_unrelated")
    beneficiary = add_beneficiary(sender_token, receiver_email)
    observed_features: list[dict] = []
    actual_service = RiskAssessmentService()

    class CapturingRiskService:
        def assess(self, features: dict) -> object:
            observed_features.append(features)
            return actual_service.assess(features)

    app.dependency_overrides[get_risk_assessment_service] = lambda: CapturingRiskService()
    try:
        response = client.post(
            "/api/v1/payments",
            json={
                "beneficiary_id": beneficiary["id"],
                "amount": "2500.00",
                "currency": "INR",
                "idempotency_key": "risk-feature-order",
            },
            headers=auth_headers(sender_token),
        )
    finally:
        app.dependency_overrides.pop(get_risk_assessment_service, None)
    assert response.status_code == 201, response.text
    payment = response.json()
    assessment = payment["risk_assessment"]
    assert observed_features and tuple(observed_features[0]) == FEATURE_NAMES
    assert observed_features[0]["is_new_beneficiary"] == 1
    assert observed_features[0]["is_new_device"] == 0
    assert observed_features[0]["is_location_change"] == 0
    assert observed_features[0]["previous_fraud_count"] == 0
    score = Decimal(str(assessment["risk_score"]))
    assert Decimal("0") <= score <= Decimal("1")
    assert assessment["risk_level"] == ("LOW" if score < Decimal("0.30") else "MEDIUM" if score < Decimal("0.70") else "HIGH")
    assert isinstance(assessment["risk_factors"], list)
    assert payment["status"] == "PENDING_RISK"
    assert "policy_decision" not in payment

    receiver_view = client.get(f"/api/v1/payments/{payment['transaction_id']}", headers=auth_headers(receiver_token))
    unrelated_view = client.get(f"/api/v1/payments/{payment['transaction_id']}", headers=auth_headers(unrelated_token))
    assert receiver_view.status_code == 200
    assert receiver_view.json()["risk_assessment"] == assessment
    assert receiver_view.json()["policy_result"] == payment["policy_result"]
    assert unrelated_view.status_code == 404
    with SessionLocal() as db:
        stored_payment = db.query(Payment).filter(Payment.transaction_id == payment["transaction_id"]).one()
        stored_risk = db.query(RiskAssessment).filter(RiskAssessment.payment_id == stored_payment.id).one()
        assert stored_risk.risk_factors == assessment["risk_factors"]
        assert stored_risk.policy_decision is None
    assert sender_email != receiver_email


def test_suspicious_payment_scores_high_after_real_payment_velocity_history() -> None:
    reset_db()
    sender_email, sender_token = register_and_login("velocity_sender")
    routine_receiver_email, _ = register_and_login("velocity_routine_receiver")
    suspicious_receiver_email, _ = register_and_login("velocity_suspicious_receiver")
    routine_beneficiary = add_beneficiary(sender_token, routine_receiver_email)

    routine_scores: list[Decimal] = []
    for index in range(10):
        routine = client.post(
            "/api/v1/payments",
            json={
                "beneficiary_id": routine_beneficiary["id"],
                "amount": "100.00",
                "currency": "INR",
                "idempotency_key": f"routine-risk-{index}",
            },
            headers=auth_headers(sender_token),
        )
        assert routine.status_code == 201, routine.text
        routine_scores.append(Decimal(str(routine.json()["risk_assessment"]["risk_score"])))

    suspicious_beneficiary = add_beneficiary(sender_token, suspicious_receiver_email)
    suspicious = client.post(
        "/api/v1/payments",
        json={
            "beneficiary_id": suspicious_beneficiary["id"],
            "amount": "85000.00",
            "currency": "INR",
            "idempotency_key": "suspicious-risk-history",
        },
        headers=auth_headers(sender_token),
    )
    assert suspicious.status_code == 201, suspicious.text
    result = suspicious.json()
    assert result["risk_assessment"]["risk_level"] == "HIGH"
    assert Decimal(str(result["risk_assessment"]["risk_score"])) > max(routine_scores)
    assert "New beneficiary" in result["risk_assessment"]["risk_factors"]
    assert "High transaction frequency in 24 hours" in result["risk_assessment"]["risk_factors"]
    assert "High transaction velocity in one hour" in result["risk_assessment"]["risk_factors"]
    assert result["status"] == "PENDING_RISK"
    with SessionLocal() as db:
        sender = db.query(User).filter(User.email == sender_email).one()
        account = db.query(Account).filter(Account.user_id == sender.id).one()
        assert account.balance == Decimal("100000.00")
    clear_model_cache()


def test_missing_model_rolls_back_payment_and_assessment(tmp_path) -> None:
    reset_db()
    _, sender_token = register_and_login("risk_failure_sender")
    receiver_email, _ = register_and_login("risk_failure_receiver")
    beneficiary = add_beneficiary(sender_token, receiver_email)
    failing_service = RiskAssessmentService(tmp_path / "missing-model.joblib")
    app.dependency_overrides[get_risk_assessment_service] = lambda: failing_service
    try:
        response = client.post(
            "/api/v1/payments",
            json={
                "beneficiary_id": beneficiary["id"],
                "amount": "10.00",
                "currency": "INR",
                "idempotency_key": "risk-inference-fails",
            },
            headers=auth_headers(sender_token),
        )
    finally:
        app.dependency_overrides.pop(get_risk_assessment_service, None)
        clear_model_cache()
    assert response.status_code == 503
    with SessionLocal() as db:
        assert db.query(Payment).filter(Payment.idempotency_key == "risk-inference-fails").count() == 0
        assert db.query(RiskAssessment).count() == 0
        assert db.query(AuditLog).filter(
            AuditLog.event_type == "payment_risk_assessment",
            AuditLog.event_status == "failed",
        ).count() == 1


def test_real_payment_flow_maps_low_medium_high_to_policy_without_state_or_balance_changes() -> None:
    reset_db()
    sender_email, sender_token = register_and_login("policy_sender")
    low_receiver_email, _ = register_and_login("policy_low_receiver")
    medium_receiver_email, _ = register_and_login("policy_medium_receiver")
    history_receiver_email, _ = register_and_login("policy_history_receiver")
    high_receiver_email, _ = register_and_login("policy_high_receiver")
    starting_balance = client.get("/api/v1/accounts", headers=auth_headers(sender_token)).json()[0]["balance"]

    low_beneficiary = add_beneficiary(sender_token, low_receiver_email)
    low = client.post(
        "/api/v1/payments",
        json={"beneficiary_id": low_beneficiary["id"], "amount": "2500.00", "currency": "INR", "idempotency_key": "policy-low"},
        headers=auth_headers(sender_token),
    )
    assert low.status_code == 201, low.text
    low_result = low.json()
    assert low_result["risk_assessment"]["risk_level"] == "LOW"
    assert low_result["policy_result"]["decision"] == "APPROVE"

    medium_beneficiary = add_beneficiary(sender_token, medium_receiver_email)
    medium = client.post(
        "/api/v1/payments",
        json={"beneficiary_id": medium_beneficiary["id"], "amount": "10000.00", "currency": "INR", "idempotency_key": "policy-medium"},
        headers=auth_headers(sender_token),
    )
    assert medium.status_code == 201, medium.text
    medium_result = medium.json()
    assert medium_result["risk_assessment"]["risk_level"] == "MEDIUM"
    assert medium_result["policy_result"]["decision"] == "VERIFY"

    history_beneficiary = add_beneficiary(sender_token, history_receiver_email)
    for index in range(10):
        history_payment = client.post(
            "/api/v1/payments",
            json={
                "beneficiary_id": history_beneficiary["id"],
                "amount": "100.00",
                "currency": "INR",
                "idempotency_key": f"policy-history-{index}",
            },
            headers=auth_headers(sender_token),
        )
        assert history_payment.status_code == 201, history_payment.text

    high_beneficiary = add_beneficiary(sender_token, high_receiver_email)
    high = client.post(
        "/api/v1/payments",
        json={"beneficiary_id": high_beneficiary["id"], "amount": "85000.00", "currency": "INR", "idempotency_key": "policy-high"},
        headers=auth_headers(sender_token),
    )
    assert high.status_code == 201, high.text
    high_result = high.json()
    assert high_result["risk_assessment"]["risk_level"] == "HIGH"
    assert high_result["policy_result"]["decision"] == "HOLD"

    for result in (low_result, medium_result, high_result):
        assert result["status"] == "PENDING_RISK"
        assert result["policy_result"]["policy_version"] == "1.0"
        assert result["policy_result"]["reason"]
        assert result["policy_result"]["triggered_rules"]
        visible = client.get(
            f"/api/v1/payments/{result['transaction_id']}",
            headers=auth_headers(sender_token),
        )
        assert visible.status_code == 200
        assert visible.json()["policy_result"] == result["policy_result"]

    final_balance = client.get("/api/v1/accounts", headers=auth_headers(sender_token)).json()[0]["balance"]
    assert Decimal(str(final_balance)) == Decimal(str(starting_balance))
    with SessionLocal() as db:
        stored_payments = db.query(Payment).filter(Payment.sender_user_id == db.query(User.id).filter(User.email == sender_email).scalar_subquery()).all()
        decisions = {record.decision for payment in stored_payments for record in [payment.policy_result] if record is not None}
        assert {"APPROVE", "VERIFY", "HOLD"}.issubset(decisions)
        assert all(payment.status == "PENDING_RISK" for payment in stored_payments)
        assert all(payment.risk_assessment.policy_decision is None for payment in stored_payments)


def test_policy_failure_rolls_back_payment_and_risk_assessment() -> None:
    reset_db()
    _, sender_token = register_and_login("policy_failure_sender")
    receiver_email, _ = register_and_login("policy_failure_receiver")
    beneficiary = add_beneficiary(sender_token, receiver_email)

    class FailingPolicyService:
        def evaluate(self, context):
            raise RuntimeError("test policy failure")

    app.dependency_overrides[get_policy_service] = lambda: FailingPolicyService()
    try:
        response = client.post(
            "/api/v1/payments",
            json={
                "beneficiary_id": beneficiary["id"],
                "amount": "10.00",
                "currency": "INR",
                "idempotency_key": "policy-evaluation-fails",
            },
            headers=auth_headers(sender_token),
        )
    finally:
        app.dependency_overrides.pop(get_policy_service, None)
    assert response.status_code == 503
    with SessionLocal() as db:
        assert db.query(Payment).filter(Payment.idempotency_key == "policy-evaluation-fails").count() == 0
        assert db.query(RiskAssessment).count() == 0
        assert db.query(PolicyDecisionRecord).count() == 0
        assert db.query(AuditLog).filter(
            AuditLog.event_type == "payment_policy_evaluation",
            AuditLog.event_status == "failed",
        ).count() == 1


def test_drunix_settings_and_contract_sequence_are_configured_for_the_live_network() -> None:
    assert hasattr(settings, "DRUNIX_MODE")
    assert hasattr(settings, "DRUNIX_NETWORK_PATH")
    assert hasattr(settings, "DRUNIX_CHANNEL")
    assert hasattr(settings, "DRUNIX_CHAINCODE")
    assert settings.DRUNIX_CHANNEL == "mychannel"
    assert settings.DRUNIX_CHAINCODE == "trustpay"

    from app.drunix.service import DrunixPaymentClient

    client = DrunixPaymentClient(network_path="/tmp/test-network", channel="mychannel", chaincode="trustpay")
    expected = {
        "APPROVE": ["CreatePayment", "AssessRisk", "ApprovePayment", "CompletePayment"],
        "VERIFY": ["CreatePayment", "AssessRisk", "RequestVerification"],
        "HOLD": ["CreatePayment", "AssessRisk", "HoldPayment"],
        "REJECT": ["CreatePayment", "AssessRisk", "RejectPayment"],
    }
    assert client._contract_sequence_for("APPROVE") == expected["APPROVE"]
    assert client._contract_sequence_for("VERIFY") == expected["VERIFY"]
    assert client._contract_sequence_for("HOLD") == expected["HOLD"]
    assert client._contract_sequence_for("REJECT") == expected["REJECT"]


def test_drunix_subprocess_prepends_configured_peer_cli_for_invoke_and_query(monkeypatch) -> None:
    from app.drunix.service import DrunixPaymentClient

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.drunix.service.subprocess.run", fake_run)
    client = DrunixPaymentClient(network_path="/mnt/drunix-network/test-network")
    client.enabled = True

    client._invoke("CreatePayment", ["tx-1", "sender", "receiver", "2500", "INR"])
    client._query("GetPayment", ["tx-1"])

    assert len(calls) == 2
    expected_prefix = 'export PATH="/mnt/drunix-network/bin:$PATH" && '
    for command, kwargs in calls:
        assert command[:2] == ["bash", "-lc"]
        assert command[2].startswith(expected_prefix)
        assert kwargs == {"capture_output": True, "text": True, "check": False}
    assert "./network.sh cc invoke" in calls[0][0][2]
    assert "./network.sh cc query" in calls[1][0][2]
