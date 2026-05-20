"""agent_runs.async_state (Fase H)

Revision ID: 0003_async_state
Revises: 0002_action_plan
Create Date: 2026-05-20 00:00:02

"""
import sqlalchemy as sa
from alembic import op

revision = "0003_async_state"
down_revision = "0002_action_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("async_state", sa.String(16), nullable=True))
    op.create_index("ix_agent_runs_async_state", "agent_runs", ["async_state"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_async_state", table_name="agent_runs")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("async_state")
