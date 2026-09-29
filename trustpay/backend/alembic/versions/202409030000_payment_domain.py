"""Add simulated accounts active state and application payment domain.

Revision ID: 202409030000
Revises: 202409020000
Create Date: 2024-09-03 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "202409030000"
down_revision = "202409020000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.execute("UPDATE accounts SET is_active = (status = 'ACTIVE')")

    op.drop_constraint(
        "uq_beneficiaries_beneficiary_reference",
        "beneficiaries",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_beneficiaries_user_reference",
        "beneficiaries",
        ["user_id", "beneficiary_reference"],
    )

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("sender_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receiver_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receiver_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("beneficiary_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CREATED"),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        sa.CheckConstraint(
            "status IN ('CREATED', 'PENDING_RISK', 'FAILED', 'CANCELLED')",
            name="ck_payments_application_status",
        ),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["receiver_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sender_account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["receiver_account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["beneficiary_id"], ["beneficiaries.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transaction_id", name="uq_payments_transaction_id"),
        sa.UniqueConstraint("sender_user_id", "idempotency_key", name="uq_payments_sender_idempotency"),
    )
    op.create_index(op.f("ix_payments_sender_user_id"), "payments", ["sender_user_id"], unique=False)
    op.create_index(op.f("ix_payments_receiver_user_id"), "payments", ["receiver_user_id"], unique=False)
    op.create_index(op.f("ix_payments_sender_account_id"), "payments", ["sender_account_id"], unique=False)
    op.create_index(op.f("ix_payments_receiver_account_id"), "payments", ["receiver_account_id"], unique=False)
    op.create_index(op.f("ix_payments_beneficiary_id"), "payments", ["beneficiary_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_payments_beneficiary_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_receiver_account_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_sender_account_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_receiver_user_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_sender_user_id"), table_name="payments")
    op.drop_table("payments")

    op.drop_constraint("uq_beneficiaries_user_reference", "beneficiaries", type_="unique")
    op.create_unique_constraint(
        "uq_beneficiaries_beneficiary_reference",
        "beneficiaries",
        ["beneficiary_reference"],
    )
    op.drop_column("accounts", "is_active")
