"""Allow terminal DRUNIX lifecycle states in application payments.

Revision ID: 202609300000
Revises: 202409050000
Create Date: 2026-09-30 00:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "202609300000"
down_revision = "202409050000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_payments_application_status", "payments", type_="check")
    op.create_check_constraint(
        "ck_payments_application_status",
        "payments",
        "status IN ('CREATED', 'PENDING_RISK', 'COMPLETED', 'FAILED', 'CANCELLED', "
        "'REJECTED', 'VERIFICATION_REQUIRED', 'HELD')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_payments_application_status", "payments", type_="check")
    op.create_check_constraint(
        "ck_payments_application_status",
        "payments",
        "status IN ('CREATED', 'PENDING_RISK', 'FAILED', 'CANCELLED')",
    )