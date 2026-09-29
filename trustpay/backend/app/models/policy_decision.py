from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PolicyDecisionRecord(Base):
    __tablename__ = "policy_decisions"
    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_policy_decisions_payment_id"),
        CheckConstraint(
            "decision IN ('APPROVE', 'HOLD', 'VERIFY', 'REJECT')",
            name="ck_policy_decisions_decision",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    triggered_rules: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    payment: Mapped["Payment"] = relationship(back_populates="policy_result")