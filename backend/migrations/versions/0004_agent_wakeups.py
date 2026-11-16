from alembic import op

revision = "0004_agent_wakeups"
down_revision = "0003_mvp_management"


def upgrade():
    op.execute("ALTER TABLE wakeups ADD COLUMN chain_depth INTEGER NOT NULL DEFAULT 0")


def downgrade():
    op.drop_column("wakeups", "chain_depth")
