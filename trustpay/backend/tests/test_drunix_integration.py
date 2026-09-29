from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.payments import _apply_drunix_final_status
from app.database import engine
from app.drunix.exceptions import DrunixClientError
from app.drunix.service import DrunixPaymentClient
from app.models.account import Account
from app.models.beneficiary import Beneficiary
from app.models.payment import PAYMENT_STATUSES, Payment
from app.models.user import User
from app.payments.schemas import PaymentResponse


def test_query_payment_history_parses_snapshots_after_wrapper_logs() -> None:
    transaction_id = "TPAY-TEST-HISTORY"
    history = [
        {"transactionId": transaction_id, "sequence": 1, "status": "CREATED"},
        {"transactionId": transaction_id, "sequence": 2, "status": "RISK_ASSESSED"},
        {"transactionId": transaction_id, "sequence": 3, "status": "APPROVED"},
        {"transactionId": transaction_id, "sequence": 4, "status": "COMPLETED"},
    ]
    raw_output = (
        "\x1b[0;34mUsing docker and docker-compose\x1b[0m\n"
        "\x1b[0;34mQuerying on peer0.org1 on channel 'mychannel'...\x1b[0m\n"
        f"{json.dumps(history)}\n"
        "\x1b[0;32mQuery successful on peer0.org1 on channel 'mychannel'\x1b[0m\n"
    )
    client = DrunixPaymentClient(network_path="/mnt/drunix-network/test-network")
    client._query = lambda function_name, args: client._decode_response(raw_output)

    assert client.query_payment_history(transaction_id) == history


def test_successful_drunix_sync_applies_terminal_status_to_payment() -> None:
    payment = SimpleNamespace(status="PENDING_RISK")

    _apply_drunix_final_status(payment, {"status": "synced", "data": {"status": "COMPLETED"}})

    assert payment.status == "COMPLETED"


def test_drunix_sync_rejects_missing_or_nonterminal_status() -> None:
    payment = SimpleNamespace(status="PENDING_RISK")

    with pytest.raises(DrunixClientError, match="terminal payment status"):
        _apply_drunix_final_status(payment, {"status": "synced", "data": {"status": "RISK_ASSESSED"}})


@pytest.mark.parametrize("status", PAYMENT_STATUSES)
def test_payment_response_accepts_application_status(status: str) -> None:
    now = datetime.now(timezone.utc)
    response = PaymentResponse.model_validate(
        {
            "transaction_id": "TPAY-TEST-STATUS",
            "sender_user_id": uuid4(),
            "receiver_user_id": uuid4(),
            "amount": Decimal("2500.00"),
            "currency": "INR",
            "status": status,
            "risk_assessment": None,
            "policy_result": None,
            "created_at": now,
            "updated_at": now,
        }
    )

    assert response.status == status


def test_postgres_payment_status_check_includes_all_application_states() -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("This diagnostic requires the configured PostgreSQL schema")

    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection)
        try:
            constraints = inspect(connection).get_check_constraints("payments")
            status_check = next(
                (item for item in constraints if item["name"] == "ck_payments_application_status"),
                None,
            )
            assert status_check is not None
            for payment_status in PAYMENT_STATUSES:
                assert payment_status in status_check["sqltext"]

            sender = User(email=f"payment-status-sender-{uuid4()}@example.test", password_hash="test")
            receiver = User(email=f"payment-status-receiver-{uuid4()}@example.test", password_hash="test")
            session.add_all([sender, receiver])
            session.flush()
            sender_account = Account(
                user_id=sender.id,
                account_reference=f"STATUS-SENDER-{uuid4().hex}",
                currency="INR",
                balance=Decimal("1000.00"),
                status="ACTIVE",
                is_active=True,
            )
            receiver_account = Account(
                user_id=receiver.id,
                account_reference=f"STATUS-RECEIVER-{uuid4().hex}",
                currency="INR",
                balance=Decimal("1000.00"),
                status="ACTIVE",
                is_active=True,
            )
            beneficiary = Beneficiary(
                user_id=sender.id,
                beneficiary_reference=receiver.email,
                display_name=receiver.email,
                status="ACTIVE",
            )
            session.add_all([sender_account, receiver_account, beneficiary])
            session.flush()

            for index, payment_status in enumerate(PAYMENT_STATUSES):
                session.add(
                    Payment(
                        transaction_id=f"TPAY-STATUS-{uuid4().hex}",
                        sender_user_id=sender.id,
                        receiver_user_id=receiver.id,
                        sender_account_id=sender_account.id,
                        receiver_account_id=receiver_account.id,
                        beneficiary_id=beneficiary.id,
                        amount=Decimal("1.00"),
                        currency="INR",
                        status=payment_status,
                        idempotency_key=f"status-{index}-{uuid4().hex}",
                    )
                )
            session.flush()
            persisted = session.query(Payment.status).filter(Payment.sender_user_id == sender.id).all()
            assert {status for (status,) in persisted} == set(PAYMENT_STATUSES)

            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(
                        Payment(
                            transaction_id=f"TPAY-STATUS-INVALID-{uuid4().hex}",
                            sender_user_id=sender.id,
                            receiver_user_id=receiver.id,
                            sender_account_id=sender_account.id,
                            receiver_account_id=receiver_account.id,
                            beneficiary_id=beneficiary.id,
                            amount=Decimal("1.00"),
                            currency="INR",
                            status="ARBITRARY_INVALID_STATUS",
                            idempotency_key=f"status-invalid-{uuid4().hex}",
                        )
                    )
                    session.flush()
        finally:
            session.close()
            transaction.rollback()


def test_payment_status_constraint_accepts_all_application_states_and_rejects_unknown() -> None:
    test_engine = create_engine("sqlite:///:memory:")
    try:
        Payment.__table__.create(test_engine)
        with test_engine.begin() as connection:
            for index, payment_status in enumerate(PAYMENT_STATUSES):
                connection.execute(
                    Payment.__table__.insert().values(
                        id=uuid4(),
                        transaction_id=f"TPAY-STATUS-{index}",
                        sender_user_id=uuid4(),
                        receiver_user_id=uuid4(),
                        sender_account_id=uuid4(),
                        receiver_account_id=uuid4(),
                        beneficiary_id=uuid4(),
                        amount=Decimal("1.00"),
                        currency="INR",
                        status=payment_status,
                        idempotency_key=f"status-{index}",
                    )
                )

            persisted = connection.execute(select(Payment.status)).scalars().all()
            assert set(persisted) == set(PAYMENT_STATUSES)

            with pytest.raises(IntegrityError):
                connection.execute(
                    Payment.__table__.insert().values(
                        id=uuid4(),
                        transaction_id="TPAY-STATUS-INVALID",
                        sender_user_id=uuid4(),
                        receiver_user_id=uuid4(),
                        sender_account_id=uuid4(),
                        receiver_account_id=uuid4(),
                        beneficiary_id=uuid4(),
                        amount=Decimal("1.00"),
                        currency="INR",
                        status="ARBITRARY_INVALID_STATUS",
                        idempotency_key="status-invalid",
                    )
                )
    finally:
        test_engine.dispose()