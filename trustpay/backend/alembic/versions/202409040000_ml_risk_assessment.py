"""Link ML risk assessments to application payments.

Revision ID: 202409040000
Revises: 202409030000
Create Date: 2024-09-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "202409040000"
down_revision = "202409030000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "risk_assessments",
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_risk_assessments_payment_id_payments",
        "risk_assessments",
        "payments",
        ["payment_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_risk_assessments_payment_id",
        "risk_assessments",
        ["payment_id"],
    )
    op.add_column(
        "risk_assessments",
        sa.Column(
            "risk_factors",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.alter_column(
        "risk_assessments",
        "risk_score",
        existing_type=sa.Numeric(precision=5, scale=4),
        type_=sa.Numeric(precision=7, scale=6),
        existing_nullable=False,
    )
    op.alter_column(
        "risk_assessments",
        "policy_decision",
        existing_type=sa.String(length=32),
        server_default=None,
        nullable=True,
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute(
        "UPDATE risk_assessments SET policy_decision = 'REVIEW' WHERE policy_decision IS NULL"
    )
    op.alter_column(
        "risk_assessments",
        "policy_decision",
        existing_type=sa.String(length=32),
        server_default="REVIEW",
        nullable=False,
        existing_nullable=True,
    )
    op.alter_column(
        "risk_assessments",
        "risk_score",
        existing_type=sa.Numeric(precision=7, scale=6),
        type_=sa.Numeric(precision=5, scale=4),
        existing_nullable=False,
    )
    op.drop_column("risk_assessments", "risk_factors")
    op.drop_constraint("uq_risk_assessments_payment_id", "risk_assessments", type_="unique")
    op.drop_constraint(
        "fk_risk_assessments_payment_id_payments",
        "risk_assessments",
        type_="foreignkey",
    )
    op.drop_column("risk_assessments", "payment_id")