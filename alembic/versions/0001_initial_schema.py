"""initial schema: sessions, messages, agent_runs, tool_calls, cost_logs, pending_confirmations

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-20 00:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("history", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sid", sa.String(64), unique=True, nullable=True),
        sa.Column("sender", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("inbox_page_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_messages_sid", "messages", ["sid"])
    op.create_index("ix_messages_sender", "messages", ["sender"])
    op.create_index("ix_messages_received_at", "messages", ["received_at"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sid", sa.String(64), nullable=True),
        sa.Column("session_key", sa.String(64), nullable=True),
        sa.Column("route", sa.String(32), nullable=False),
        sa.Column("intent", sa.String(64), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("iterations", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_agent_runs_sid", "agent_runs", ["sid"])
    op.create_index("ix_agent_runs_session_key", "agent_runs", ["session_key"])
    op.create_index("ix_agent_runs_started_at", "agent_runs", ["started_at"])

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_run_id", sa.String(36), nullable=True),
        sa.Column("sid", sa.String(64), nullable=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("args", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("result", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_tool_calls_agent_run_id", "tool_calls",
                    ["agent_run_id"])
    op.create_index("ix_tool_calls_name", "tool_calls", ["name"])

    op.create_table(
        "cost_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("sid", sa.String(64), nullable=True),
        sa.Column("route", sa.String(32), nullable=False),
        sa.Column("intent", sa.String(64), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("extra", sa.JSON(), nullable=True),
    )
    op.create_index("ix_cost_logs_ts", "cost_logs", ["ts"])

    op.create_table(
        "pending_confirmations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_key", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )
    op.create_index("ix_pending_confirmations_session_key",
                    "pending_confirmations", ["session_key"])
    op.create_index("ix_pending_confirmations_expires_at",
                    "pending_confirmations", ["expires_at"])
    op.create_index("ix_pending_confirmations_consumed",
                    "pending_confirmations", ["consumed"])


def downgrade() -> None:
    op.drop_table("pending_confirmations")
    op.drop_table("cost_logs")
    op.drop_table("tool_calls")
    op.drop_table("agent_runs")
    op.drop_table("messages")
    op.drop_table("sessions")
