from alembic import op

revision = "0006_run_runner"
down_revision = "0005_agent_catalog"


def upgrade():
    op.execute("ALTER TABLE runs ADD COLUMN runner TEXT")


def downgrade():
    op.drop_column("runs", "runner")
