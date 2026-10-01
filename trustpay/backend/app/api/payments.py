from __future__ import annotations

import uuid
import logging
from decimal import Decimal
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import get_current_active_user
from app.auth.security import normalize_email
from app.database import get_db
from app.drunix.exceptions import DrunixConflictError
from app.drunix.service import DrunixClientError, DrunixPaymentClient
from app.models.account import Account
from app.models.audit_log import AuditLog
from app.models.beneficiary import Beneficiary
from app.models.payment import Payment
from app.models.policy_decision import PolicyDecisionRecord
from app.models.risk_assessment import RiskAssessment
from app.models.user import User
from app.payments.schemas import PaymentCreateRequest, PaymentResponse
from app.risk.service import (
    RiskAssessmentError,
    RiskAssessmentService,
    build_payment_features,
)
from app.policy import PolicyContext, PolicyEvaluationError, PolicyService

router = APIRouter(prefix="/payments", tags=["payments"])
logger = logging.getLogger(__name__)
_risk_assessment_service = RiskAssessmentService()
_policy_service = PolicyService()
_drunix_client = DrunixPaymentClient()
_DRUNIX_FINAL_PAYMENT_STATUSES = {"COMPLETED", "VERIFICATION_REQUIRED", "HELD", "REJECTED"}


def get_risk_assessment_service() -> RiskAssessmentService:
    return _risk_assessment_service


def get_policy_service() -> PolicyService:
    return _policy_service


def get_drunix_client() -> DrunixPaymentClient:
    return _drunix_client


def _audit(db: Session, user_id: uuid.UUID, event_type: str, event_status: str, details: dict) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            event_type=event_type,
            event_status=event_status,
            details=details,
            source="payments",
        )
    )
    db.flush()


def _reject(
    db: Session,
    user_id: uuid.UUID,
    event_type: str,
    reason: str,
    http_status: int,
    message: str,
) -> NoReturn:
    _audit(db, user_id, event_type, "failed", {"reason": reason})
    db.commit()
    raise HTTPException(status_code=http_status, detail=message)


def _same_request(payment: Payment, beneficiary: Beneficiary, sender: Account, receiver: Account, payload: PaymentCreateRequest) -> bool:
    return (
        payment.beneficiary_id == beneficiary.id
        and payment.sender_account_id == sender.id
        and payment.receiver_account_id == receiver.id
        and payment.amount == payload.amount
        and payment.currency == payload.currency
    )


def _payment_json(payment: Payment) -> dict:
    return PaymentResponse.model_validate(payment).model_dump(mode="json")


def _return_existing(db: Session, current_user: User, payment: Payment) -> JSONResponse:
    _audit(db, current_user.id, "payment_idempotent_replay", "duplicate", {"transaction_id": payment.transaction_id})
    db.commit()
    return JSONResponse(status_code=status.HTTP_200_OK, content=_payment_json(payment))


def _reconcile_existing_payment(
    db: Session,
    payment: Payment,
    drunix_client: DrunixPaymentClient,
    sender_account: Account,
    receiver_account: Account,
) -> None:
    if not drunix_client.is_enabled() or payment.status != "PENDING_RISK":
        return
    try:
        ledger_payment = drunix_client.query_payment(payment.transaction_id)
    except DrunixClientError as exc:
        if "does not exist" in str(exc).lower():
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify the existing payment against DRUNIX",
        ) from exc
    if not isinstance(ledger_payment, dict) or ledger_payment.get("status") not in _DRUNIX_FINAL_PAYMENT_STATUSES:
        return
    try:
        state = {
            "status": "committed",
            "data": ledger_payment,
            "accounts": {
                "sender": drunix_client.query_account(str(payment.sender_account_id)),
                "receiver": drunix_client.query_account(str(payment.receiver_account_id)),
            },
        }
        _apply_drunix_final_status(payment, state, sender_account, receiver_account)
        db.commit()
    except DrunixClientError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reconcile the existing payment with DRUNIX state",
        ) from exc


def _apply_drunix_final_status(
    payment: Payment,
    drunix_state: dict,
    sender_account: Account | None = None,
    receiver_account: Account | None = None,
) -> None:
    if drunix_state.get("status") != "committed":
        raise DrunixClientError("DRUNIX did not confirm a committed payment transaction")
    ledger_payment = drunix_state.get("data")
    final_status = ledger_payment.get("status") if isinstance(ledger_payment, dict) else None
    if final_status not in _DRUNIX_FINAL_PAYMENT_STATUSES:
        raise DrunixClientError("DRUNIX synchronization did not return a terminal payment status")
    if isinstance(ledger_payment, dict):
        expected = {
            "transactionId": payment.transaction_id,
            "senderId": str(payment.sender_user_id),
            "receiverId": str(payment.receiver_user_id),
            "senderAccountId": str(payment.sender_account_id),
            "receiverAccountId": str(payment.receiver_account_id),
            "amountMinor": int(payment.amount * 100),
            "currency": payment.currency,
        }
        for field, value in expected.items():
            if ledger_payment.get(field) != value:
                raise DrunixClientError(f"DRUNIX payment identity mismatch for {field}")
    decision = getattr(getattr(payment, "policy_result", None), "decision", None)
    expected_status = {
        "APPROVE": "COMPLETED",
        "VERIFY": "VERIFICATION_REQUIRED",
        "HOLD": "HELD",
        "REJECT": "REJECTED",
    }.get(decision)
    if expected_status is not None and final_status != expected_status:
        raise DrunixClientError("DRUNIX payment status does not match the recorded policy decision")
    payment.status = final_status
    ledger_accounts = drunix_state.get("accounts")
    if ledger_accounts is not None:
        for role, account_id, account in (
            ("sender", payment.sender_account_id, sender_account),
            ("receiver", payment.receiver_account_id, receiver_account),
        ):
            if account is None:
                raise DrunixClientError(f"DRUNIX {role} account projection is unavailable")
            ledger_account = ledger_accounts.get(role)
            if not isinstance(ledger_account, dict) or ledger_account.get("accountId") != str(account_id):
                raise DrunixClientError(f"DRUNIX {role} account state is missing or mismatched")
            if ledger_account.get("currency") != payment.currency:
                raise DrunixClientError(f"DRUNIX {role} account currency does not match")
            try:
                balance_minor = ledger_account["balanceMinor"]
                if isinstance(balance_minor, bool) or not isinstance(balance_minor, int) or balance_minor < 0:
                    raise ValueError("balanceMinor must be a non-negative integer")
                account.balance = Decimal(balance_minor) / 100
            except (KeyError, ArithmeticError, TypeError, ValueError) as exc:
                raise DrunixClientError(f"DRUNIX {role} account balance is invalid") from exc


@router.post("", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
def create_payment(
    payload: PaymentCreateRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    risk_service: RiskAssessmentService = Depends(get_risk_assessment_service),
    policy_service: PolicyService = Depends(get_policy_service),
    drunix_client: DrunixPaymentClient = Depends(get_drunix_client),
) -> Payment | JSONResponse:
    if payload.currency != "INR":
        _reject(db, current_user.id, "payment_validation", "unsupported_currency", status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported currency")

    beneficiary = (
        db.query(Beneficiary)
        .filter(
            Beneficiary.id == payload.beneficiary_id,
            Beneficiary.user_id == current_user.id,
            Beneficiary.status == "ACTIVE",
        )
        .first()
    )
    if beneficiary is None:
        _reject(db, current_user.id, "payment_validation", "beneficiary_unavailable", status.HTTP_404_NOT_FOUND, "Beneficiary not found")

    receiver_user = (
        db.query(User)
        .filter(User.email == normalize_email(beneficiary.beneficiary_reference), User.is_active.is_(True))
        .first()
    )
    if receiver_user is None:
        _reject(db, current_user.id, "payment_validation", "recipient_unavailable", status.HTTP_404_NOT_FOUND, "Beneficiary not found")
    if receiver_user.id == current_user.id:
        _reject(db, current_user.id, "payment_validation", "self_payment", status.HTTP_400_BAD_REQUEST, "Self-payment is not allowed")

    sender_account = (
        db.query(Account)
        .filter(
            Account.user_id == current_user.id,
            Account.currency == payload.currency,
            Account.is_active.is_(True),
            Account.status == "ACTIVE",
        )
        .order_by(Account.created_at.asc(), Account.id.asc())
        .first()
    )
    if sender_account is None:
        _reject(db, current_user.id, "payment_validation", "sender_account_unavailable", status.HTTP_409_CONFLICT, "No active simulated sender account")

    receiver_account = (
        db.query(Account)
        .filter(
            Account.user_id == receiver_user.id,
            Account.currency == payload.currency,
            Account.is_active.is_(True),
            Account.status == "ACTIVE",
        )
        .order_by(Account.created_at.asc(), Account.id.asc())
        .first()
    )
    if receiver_account is None:
        _reject(db, current_user.id, "payment_validation", "receiver_account_unavailable", status.HTTP_409_CONFLICT, "Beneficiary has no active account for this currency")
    if sender_account.id == receiver_account.id:
        _reject(db, current_user.id, "payment_validation", "same_account", status.HTTP_400_BAD_REQUEST, "Sender and receiver accounts must differ")

    existing = (
        db.query(Payment)
        .filter(
            Payment.sender_user_id == current_user.id,
            Payment.idempotency_key == payload.idempotency_key,
        )
        .first()
    )
    if existing is not None:
        if _same_request(existing, beneficiary, sender_account, receiver_account, payload):
            _reconcile_existing_payment(db, existing, drunix_client, sender_account, receiver_account)
            return _return_existing(db, current_user, existing)
        _reject(db, current_user.id, "payment_idempotency_conflict", "key_reused_with_different_data", status.HTTP_409_CONFLICT, "Idempotency key was already used with different payment data")

    if sender_account.balance < payload.amount:
        _reject(db, current_user.id, "payment_insufficient_balance", "insufficient_simulated_balance", status.HTTP_409_CONFLICT, "Insufficient simulated balance")

    payment = Payment(
        transaction_id=f"TPAY-{uuid.uuid4().hex.upper()}",
        sender_user_id=current_user.id,
        receiver_user_id=receiver_user.id,
        sender_account_id=sender_account.id,
        receiver_account_id=receiver_account.id,
        beneficiary_id=beneficiary.id,
        amount=payload.amount.quantize(Decimal("0.01")),
        currency=payload.currency,
        status="PENDING_RISK",
        idempotency_key=payload.idempotency_key,
    )
    db.add(payment)
    try:
        db.flush()
        features = build_payment_features(db, payment, beneficiary, sender_account)
        risk_result = risk_service.assess(features)
        assessment = RiskAssessment(
            payment_id=payment.id,
            user_id=current_user.id,
            transaction_reference=payment.transaction_id,
            risk_score=risk_result.risk_score,
            risk_level=risk_result.risk_level,
            policy_decision=None,
            model_version=risk_result.model_version,
            risk_factors=risk_result.risk_factors,
        )
        db.add(assessment)
        payment.risk_assessment = assessment
        policy_context = PolicyContext(
                risk_score=assessment.risk_score,
                risk_level=assessment.risk_level,
                risk_factors=tuple(assessment.risk_factors),
                amount=payment.amount,
                sender_balance=sender_account.balance,
                sender_user_id=str(current_user.id),
                receiver_user_id=str(receiver_user.id),
                sender_user_active=current_user.is_active,
                sender_account_active=sender_account.is_active and sender_account.status == "ACTIVE",
                receiver_user_active=receiver_user.is_active,
                receiver_account_active=receiver_account.is_active and receiver_account.status == "ACTIVE",
                beneficiary_active=beneficiary.status == "ACTIVE",
                payment_status=payment.status,
                failed_attempts_24h=int(features["failed_attempts_24h"]),
            )
        try:
            policy_result = policy_service.evaluate(policy_context)
        except PolicyEvaluationError:
            raise
        except Exception as exc:
            raise PolicyEvaluationError("Policy engine evaluation failed") from exc
        policy_record = PolicyDecisionRecord(
            payment_id=payment.id,
            decision=policy_result.decision,
            reason=policy_result.reason,
            policy_version=policy_result.policy_version,
            triggered_rules=list(policy_result.triggered_rules),
        )
        db.add(policy_record)
        payment.policy_result = policy_record
        _audit(
            db,
            current_user.id,
            "payment_create",
            "success",
            {"amount": str(payment.amount), "currency": payment.currency},
        )
        _audit(
            db,
            current_user.id,
            "payment_risk_assessment",
            "success",
            {"transaction_id": payment.transaction_id, "risk_level": risk_result.risk_level},
        )
        _audit(
            db,
            current_user.id,
            "payment_policy_evaluation",
            "success",
            {"transaction_id": payment.transaction_id, "decision": policy_result.decision},
        )
        db.commit()
    except (RiskAssessmentError, PolicyEvaluationError) as exc:
        db.rollback()
        logger.exception("Payment assessment failed; payment transaction rolled back")
        event_type = (
            "payment_risk_assessment"
            if isinstance(exc, RiskAssessmentError)
            else "payment_policy_evaluation"
        )
        try:
            _audit(db, current_user.id, event_type, "failed", {"reason": "assessment_unavailable"})
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Could not persist payment assessment failure audit event")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payment assessment is currently unavailable",
        ) from exc
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(Payment)
            .filter(
                Payment.sender_user_id == current_user.id,
                Payment.idempotency_key == payload.idempotency_key,
            )
            .first()
        )
        if existing is not None and _same_request(existing, beneficiary, sender_account, receiver_account, payload):
            return _return_existing(db, current_user, existing)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment could not be created")

    if drunix_client.is_enabled():
        try:
            drunix_state = drunix_client.sync_payment(
                payment,
                risk_result,
                policy_result,
                sender_account,
                receiver_account,
            )
            if drunix_state is None:
                raise DrunixClientError("DRUNIX did not confirm the payment commit")
            _apply_drunix_final_status(payment, drunix_state, sender_account, receiver_account)
            _audit(
                db,
                current_user.id,
                "payment_drunix_sync",
                "success",
                {"transaction_id": payment.transaction_id, "state": drunix_state.get("status")},
            )
            db.commit()
        except DrunixConflictError as exc:
            db.rollback()
            payment.status = "FAILED"
            db.add(payment)
            _audit(
                db,
                current_user.id,
                "payment_drunix_conflict",
                "failed",
                {"transaction_id": payment.transaction_id, "reason": str(exc)},
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment conflicted with a concurrent DRUNIX ledger update; retry with a new idempotency key",
            ) from exc
        except DrunixClientError as exc:
            db.rollback()
            logger.exception("Payment commit could not be confirmed by DRUNIX")
            _audit(
                db,
                current_user.id,
                "payment_drunix_sync",
                "failed",
                {"transaction_id": payment.transaction_id, "reason": str(exc)},
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Payment remains pending because its DRUNIX commit could not be confirmed",
            ) from exc

    db.refresh(payment)
    return payment


@router.get("", response_model=list[PaymentResponse])
def list_payments(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> list[Payment]:
    return (
        db.query(Payment)
        .filter(or_(Payment.sender_user_id == current_user.id, Payment.receiver_user_id == current_user.id))
        .order_by(Payment.created_at.desc(), Payment.id.asc())
        .all()
    )


@router.get("/{transaction_id}/drunix")
def get_payment_drunix(
    transaction_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    drunix_client: DrunixPaymentClient = Depends(get_drunix_client),
) -> dict:
    payment = (
        db.query(Payment)
        .filter(
            Payment.transaction_id == transaction_id,
            or_(Payment.sender_user_id == current_user.id, Payment.receiver_user_id == current_user.id),
        )
        .first()
    )
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if not drunix_client.is_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="DRUNIX integration is disabled")
    return drunix_client.query_payment(transaction_id)


@router.get("/{transaction_id}/drunix/history")
def get_payment_drunix_history(
    transaction_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    drunix_client: DrunixPaymentClient = Depends(get_drunix_client),
) -> dict:
    payment = (
        db.query(Payment)
        .filter(
            Payment.transaction_id == transaction_id,
            or_(Payment.sender_user_id == current_user.id, Payment.receiver_user_id == current_user.id),
        )
        .first()
    )
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if not drunix_client.is_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="DRUNIX integration is disabled")
    return {"transaction_id": transaction_id, "history": drunix_client.query_payment_history(transaction_id)}


@router.get("/{transaction_id}", response_model=PaymentResponse)
def get_payment(
    transaction_id: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Payment:
    payment = (
        db.query(Payment)
        .filter(
            Payment.transaction_id == transaction_id,
            or_(Payment.sender_user_id == current_user.id, Payment.receiver_user_id == current_user.id),
        )
        .first()
    )
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return payment
