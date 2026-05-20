"""agent_runs: agregar plan, safety_level, confirmed_from (Fase F)

Revision ID: 0002_action_plan
Revises: 0001_initial
Create Date: 2026-05-20 00:00:01

"""
import sqlalchemy as sa
from alembic import op

revision = "0002_action_plan"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("plan", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("safety_level", sa.String(16), nullable=True))
        batch.add_column(sa.Column("confirmed_from", sa.String(36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("confirmed_from")
        batch.drop_column("safety_level")
        batch.drop_column("plan")
