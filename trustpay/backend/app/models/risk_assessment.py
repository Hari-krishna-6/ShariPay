from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (UniqueConstraint("payment_id", name="uq_risk_assessments_payment_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    transaction_reference: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    risk_score: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    policy_decision: Mapped[str] = mapped_column(String(32), nullable=True)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False, default="sharipay-ml-v1")
    features_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True)
    risk_factors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user: Mapped["User"] = relationship(back_populates="risk_assessments")
    payment: Mapped["Payment"] = relationship(back_populates="risk_assessment")
