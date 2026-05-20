"""sessions: source + last_message_at (Fase I)

Revision ID: 0004_session_source
Revises: 0003_async_state
Create Date: 2026-05-20 00:00:03

"""
import sqlalchemy as sa
from alembic import op

revision = "0004_session_source"
down_revision = "0003_async_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("source", sa.String(16), nullable=True))
        batch.add_column(sa.Column("last_message_at",
                                   sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_sessions_source", "sessions", ["source"])


def downgrade() -> None:
    op.drop_index("ix_sessions_source", table_name="sessions")
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("last_message_at")
        batch.drop_column("source")
