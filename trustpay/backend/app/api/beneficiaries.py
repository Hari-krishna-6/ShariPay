from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_active_user
from app.auth.security import normalize_email
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.beneficiary import Beneficiary
from app.models.user import User
from app.payments.schemas import BeneficiaryCreateRequest, BeneficiaryResponse

router = APIRouter(prefix="/beneficiaries", tags=["beneficiaries"])


def _audit(db: Session, user_id, event_type: str, event_status: str, details: dict) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            event_type=event_type,
            event_status=event_status,
            details=details,
            source="beneficiaries",
        )
    )
    db.flush()


@router.post("", response_model=BeneficiaryResponse, status_code=status.HTTP_201_CREATED)
def create_beneficiary(
    payload: BeneficiaryCreateRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Beneficiary:
    target_email = normalize_email(str(payload.email))
    receiver = db.query(User).filter(User.email == target_email, User.is_active.is_(True)).first()
    if receiver is None:
        _audit(db, current_user.id, "beneficiary_create", "failed", {"reason": "recipient_unavailable"})
        db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ShariPay user not found")
    if receiver.id == current_user.id:
        _audit(db, current_user.id, "beneficiary_create", "failed", {"reason": "self_beneficiary"})
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot add yourself as a beneficiary")

    existing = (
        db.query(Beneficiary)
        .filter(
            Beneficiary.user_id == current_user.id,
            Beneficiary.beneficiary_reference == target_email,
        )
        .first()
    )
    if existing is not None:
        _audit(db, current_user.id, "beneficiary_create", "duplicate", {"beneficiary_id": str(existing.id)})
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Beneficiary already exists")

    beneficiary = Beneficiary(
        user_id=current_user.id,
        beneficiary_reference=target_email,
        display_name=(payload.display_name or receiver.full_name or receiver.email).strip(),
        status="ACTIVE",
    )
    db.add(beneficiary)
    db.flush()
    _audit(db, current_user.id, "beneficiary_create", "success", {"beneficiary_id": str(beneficiary.id)})
    db.commit()
    db.refresh(beneficiary)
    return beneficiary


@router.get("", response_model=list[BeneficiaryResponse])
def list_beneficiaries(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> list[Beneficiary]:
    return (
        db.query(Beneficiary)
        .filter(Beneficiary.user_id == current_user.id)
        .order_by(Beneficiary.created_at.desc(), Beneficiary.id.asc())
        .all()
    )
