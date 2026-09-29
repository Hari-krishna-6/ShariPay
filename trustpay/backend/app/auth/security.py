from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from sqlalchemy.orm import Session

from app.config import settings
from app.models.refresh_token import RefreshToken
from app.models.user import User

ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def password_policy_valid(password: str) -> bool:
    if len(password) < 8:
        return False
    if len(password) > 128:
        return False
    has_upper = any(ch.isupper() for ch in password)
    has_lower = any(ch.islower() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    return has_upper and has_lower and has_digit


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return ph.verify(password_hash, password)
    except Exception:
        return False


def create_access_token(subject: str, role: str, token_id: str | None = None) -> str:
    expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "token_type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if token_id:
        payload["jti"] = token_id
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token_value() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_refresh_token_record(db: Session, user_id: uuid.UUID, family_id: str | None = None) -> tuple[str, RefreshToken]:
    raw = create_refresh_token_value()
    token_hash = hash_refresh_token(raw)
    token_family = family_id or str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    record = RefreshToken(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=token_hash,
        token_family=token_family,
        expires_at=expires_at,
        revoked_at=None,
        replaced_by=None,
    )
    db.add(record)
    db.flush()
    return raw, record


def verify_refresh_token_record(record: RefreshToken, raw_token: str) -> bool:
    if record.revoked_at is not None:
        return False
    if record.expires_at < datetime.now(timezone.utc):
        return False
    return record.token_hash == hash_refresh_token(raw_token)


def revoke_refresh_token(db: Session, record: RefreshToken) -> None:
    if record.revoked_at is None:
        record.revoked_at = datetime.now(timezone.utc)
    db.add(record)
    db.flush()


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == normalize_email(email)).first()


def get_user_by_id(db: Session, user_id: str | uuid.UUID) -> User | None:
    return db.query(User).filter(User.id == str(user_id) if isinstance(user_id, str) else user_id).first()
