from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth.schemas import (
    AuthErrorResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.auth.security import (
    create_access_token,
    create_refresh_token_record,
    decode_access_token,
    get_user_by_email,
    get_user_by_id,
    hash_password,
    hash_refresh_token,
    normalize_email,
    password_policy_valid,
    revoke_refresh_token,
    verify_password,
    verify_refresh_token_record,
)
from app.config import settings
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.account import Account
from app.models.refresh_token import RefreshToken
from app.models.user import User

router = APIRouter(tags=["auth"])
security = HTTPBearer(auto_error=False)


def _log_event(db: Session, user_id: uuid.UUID | None, event_type: str, event_status: str, details: dict | None = None) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            event_type=event_type,
            event_status=event_status,
            details=details or {},
            source="auth",
        )
    )
    db.flush()


async def get_token_payload(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_access_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc
    if payload.get("token_type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    return payload


async def get_current_user(
    payload: dict = Depends(get_token_payload),
    db: Session = Depends(get_db),
) -> User:
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    user = db.query(User).filter(User.id == uuid.UUID(str(user_id))).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")
    return user


async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")
    return current_user


async def require_admin(current_user: User = Depends(get_current_active_user)) -> User:
    if current_user.role != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


@router.post("/auth/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> User:
    normalized_email = normalize_email(payload.email)
    if not password_policy_valid(payload.password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters and include upper, lower, and numeric characters")
    if db.query(User).filter(User.email == normalized_email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = User(
        email=normalized_email,
        full_name="",
        password_hash=hash_password(payload.password),
        role="USER",
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(
        Account(
            user_id=user.id,
            account_reference=f"TPA-{uuid.uuid4().hex.upper()}",
            currency="INR",
            balance=Decimal("100000.00"),
            status="ACTIVE",
            is_active=True,
        )
    )
    _log_event(db, user.id, "registration", "success", {"email": user.email})
    _log_event(db, user.id, "simulated_account_create", "success", {"currency": "INR", "demo_balance": "100000.00"})
    db.commit()
    return user


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    email = normalize_email(payload.email)
    user = get_user_by_email(db, email)
    if not user or not verify_password(payload.password, user.password_hash):
        _log_event(db, None, "login", "failed", {"email": email})
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        _log_event(db, user.id, "login", "failed", {"email": user.email, "reason": "inactive"})
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    token_id = str(uuid.uuid4())
    access_token = create_access_token(str(user.id), user.role, token_id)
    refresh_raw, refresh_record = create_refresh_token_record(db, user.id, family_id=str(uuid.uuid4()))
    db.commit()
    _log_event(db, user.id, "login", "success", {"email": user.email, "token_family": refresh_record.token_family})
    db.commit()
    return {
        "access_token": access_token,
        "refresh_token": refresh_raw,
        "token_type": "bearer",
    }


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh_token(payload: RefreshRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    raw = payload.refresh_token.strip()
    token_hash = hash_refresh_token(raw)
    record = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if not record:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if record.revoked_at is not None:
        family_tokens = db.query(RefreshToken).filter(RefreshToken.token_family == record.token_family).all()
        for family_record in family_tokens:
            if family_record.revoked_at is None:
                family_record.revoked_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked")
    if record.expires_at < datetime.now(timezone.utc):
        revoke_refresh_token(db, record)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user or not user.is_active:
        revoke_refresh_token(db, record)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")

    old_record = record
    revoke_refresh_token(db, old_record)
    new_raw, new_record = create_refresh_token_record(db, user.id, family_id=old_record.token_family)
    old_record.replaced_by = new_record.id
    access_token = create_access_token(str(user.id), user.role, str(uuid.uuid4()))
    db.commit()
    _log_event(db, user.id, "refresh", "success", {"token_family": old_record.token_family})
    db.commit()
    return {
        "access_token": access_token,
        "refresh_token": new_raw,
        "token_type": "bearer",
    }


@router.post("/auth/logout")
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    raw = payload.refresh_token.strip()
    token_hash = hash_refresh_token(raw)
    record = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if record is not None:
        revoke_refresh_token(db, record)
        db.commit()
        _log_event(db, record.user_id, "logout", "success", {"token_family": record.token_family})
        db.commit()
        return {"status": "ok", "detail": "logged out"}
    return {"status": "ok", "detail": "logged out"}


@router.get("/auth/me", response_model=UserResponse)
def current_user_me(current_user: User = Depends(get_current_active_user)) -> User:
    return current_user


@router.get("/auth/admin-check")
def admin_check(current_user: User = Depends(require_admin)) -> dict[str, str]:
    return {"status": "ok", "role": current_user.role}
