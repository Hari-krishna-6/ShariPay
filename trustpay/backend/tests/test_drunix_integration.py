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
from app.config import settings
from app.database import engine
from app.drunix.exceptions import DrunixClientError, DrunixConflictError
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
    payment = SimpleNamespace(
        status="PENDING_RISK",
        transaction_id="TPAY-TEST-STATUS",
        sender_user_id="sender-1",
        receiver_user_id="receiver-1",
        sender_account_id="sender-account-1",
        receiver_account_id="receiver-account-1",
        amount=Decimal("2500.00"),
        currency="INR",
        policy_result=SimpleNamespace(decision="APPROVE"),
    )
    state = {
        "status": "committed",
        "data": {
            "transactionId": "TPAY-TEST-STATUS",
            "senderId": "sender-1",
            "receiverId": "receiver-1",
            "senderAccountId": "sender-account-1",
            "receiverAccountId": "receiver-account-1",
            "amountMinor": 250000,
            "currency": "INR",
            "status": "COMPLETED",
        },
    }

    _apply_drunix_final_status(payment, state)

    assert payment.status == "COMPLETED"


def test_successful_drunix_sync_projects_ledger_account_balances() -> None:
    payment = SimpleNamespace(
        status="PENDING_RISK",
        transaction_id="TPAY-TEST-STATUS",
        sender_user_id="sender-1",
        receiver_user_id="receiver-1",
        sender_account_id="sender-account-1",
        receiver_account_id="receiver-account-1",
        amount=Decimal("2500.00"),
        currency="INR",
        policy_result=SimpleNamespace(decision="APPROVE"),
    )
    sender = SimpleNamespace(id="sender-account-1", balance=Decimal("10000.00"))
    receiver = SimpleNamespace(id="receiver-account-1", balance=Decimal("10000.00"))
    state = {
        "status": "committed",
        "data": {
            "transactionId": "TPAY-TEST-STATUS",
            "senderId": "sender-1",
            "receiverId": "receiver-1",
            "senderAccountId": "sender-account-1",
            "receiverAccountId": "receiver-account-1",
            "amountMinor": 250000,
            "currency": "INR",
            "status": "COMPLETED",
        },
        "accounts": {
            "sender": {"accountId": "sender-account-1", "balanceMinor": 750000, "currency": "INR"},
            "receiver": {"accountId": "receiver-account-1", "balanceMinor": 1250000, "currency": "INR"},
        },
    }

    _apply_drunix_final_status(payment, state, sender, receiver)

    assert sender.balance == Decimal("7500")
    assert receiver.balance == Decimal("12500")


def test_drunix_sync_rejects_missing_or_nonterminal_status() -> None:
    payment = SimpleNamespace(status="PENDING_RISK")

    with pytest.raises(DrunixClientError, match="terminal payment status"):
        _apply_drunix_final_status(payment, {"status": "committed", "data": {"status": "RISK_ASSESSED"}})


def test_drunix_submission_is_one_atomic_minor_unit_invocation() -> None:
    client = DrunixPaymentClient(network_path="/tmp/test-network")
    client.enabled = True
    calls = []
    client._invoke = lambda function_name, args: calls.append((function_name, args))
    client.query_payment = lambda transaction_id: {"status": "COMPLETED"}
    client.query_account = lambda account_id: {"accountId": account_id, "balanceMinor": 0, "currency": "INR"}

    result = client.submit_payment(
        transaction_id="TPAY-MINOR-UNIT",
        sender_user_id="sender-1",
        receiver_user_id="receiver-1",
        sender_account_id="account-1",
        receiver_account_id="account-2",
        amount=Decimal("25.50"),
        sender_balance=Decimal("100.00"),
        receiver_balance=Decimal("10.25"),
        currency="INR",
        risk_score=0.12,
        risk_level="LOW",
        policy_decision="APPROVE",
    )

    assert len(calls) == 1
    assert calls[0][0] == "SubmitPayment"
    assert calls[0][1][5:8] == ["2550", "10000", "1025"]
    assert result["status"] == "committed"


def test_drunix_explicit_mvcc_failure_is_a_conflict() -> None:
    client = DrunixPaymentClient(network_path="/tmp/test-network")
    client.enabled = True

    class FailedCommand:
        returncode = 1
        stdout = ""
        stderr = "transaction invalidated: MVCC_READ_CONFLICT"

    client._run_bash = lambda command: FailedCommand()
    with pytest.raises(DrunixConflictError, match="concurrently"):
        client._invoke("SubmitPayment", ["tx"])


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
            assert {payment_status for (payment_status,) in persisted} == set(PAYMENT_STATUSES)

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


def test_drunix_settings_are_configured_for_the_live_network() -> None:
    assert hasattr(settings, "DRUNIX_MODE")
    assert hasattr(settings, "DRUNIX_NETWORK_PATH")
    assert hasattr(settings, "DRUNIX_CHANNEL")
    assert hasattr(settings, "DRUNIX_CHAINCODE")
    assert settings.DRUNIX_CHANNEL == "mychannel"
    assert settings.DRUNIX_CHAINCODE == "trustpay"


def test_drunix_subprocess_prepends_configured_peer_cli_for_invoke_and_query(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.drunix.service.subprocess.run", fake_run)
    client = DrunixPaymentClient(network_path="/mnt/drunix-network/test-network")
    client.enabled = True

    client._invoke("SubmitPayment", ["tx-1"])
    client._query("GetPayment", ["tx-1"])

    assert len(calls) == 2
    expected_prefix = 'export PATH="/mnt/drunix-network/bin:$PATH" && '
    for command, kwargs in calls:
        assert command[:2] == ["bash", "-lc"]
        assert command[2].startswith(expected_prefix)
        assert kwargs == {"capture_output": True, "text": True, "check": False}
    assert "./network.sh cc invoke" in calls[0][0][2]
    assert "./network.sh cc query" in calls[1][0][2]