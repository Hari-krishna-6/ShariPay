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


def get_risk_assessment_service() -> RiskAssessmentService:
    return _risk_assessment_service


def get_policy_service() -> PolicyService:
    return _policy_service


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


@router.post("", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
def create_payment(
    payload: PaymentCreateRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    risk_service: RiskAssessmentService = Depends(get_risk_assessment_service),
    policy_service: PolicyService = Depends(get_policy_service),
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
