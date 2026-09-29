"""Persist deterministic policy decisions separately from ML assessments.

Revision ID: 202409050000
Revises: 202409040000
Create Date: 2024-09-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "202409050000"
down_revision = "202409040000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("triggered_rules", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint(
            "decision IN ('APPROVE', 'HOLD', 'VERIFY', 'REJECT')",
            name="ck_policy_decisions_decision",
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id", name="uq_policy_decisions_payment_id"),
    )


def downgrade() -> None:
    op.drop_table("policy_decisions")