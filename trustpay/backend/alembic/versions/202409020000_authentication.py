"""Add authentication fields and refresh tokens for ShariPay users.

Revision ID: 202409020000
Revises: 202409010000
Create Date: 2024-09-02 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "202409020000"
down_revision = "202409010000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("role", sa.String(length=32), server_default="USER", nullable=True))

    op.execute(
        "UPDATE users SET password_hash = 'pending-authentication' WHERE password_hash IS NULL"
    )
    op.execute("UPDATE users SET role = 'USER' WHERE role IS NULL")

    op.alter_column("users", "password_hash", existing_type=sa.String(length=255), nullable=False)
    op.alter_column("users", "role", existing_type=sa.String(length=32), nullable=False, server_default="USER")

    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("token_family", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["replaced_by"], ["refresh_tokens.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False)
    op.create_index(op.f("ix_refresh_tokens_token_family"), "refresh_tokens", ["token_family"], unique=False)
    op.create_index(op.f("ix_refresh_tokens_replaced_by"), "refresh_tokens", ["replaced_by"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_refresh_tokens_replaced_by"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_token_family"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_column("users", "role")
    op.drop_column("users", "password_hash")
