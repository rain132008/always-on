from alembic import op

revision = "0005_agent_catalog"
down_revision = "0004_agent_wakeups"


def upgrade():
    op.execute("""
        CREATE TABLE agents (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, prompt TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE workflow_agents (
            workflow_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id)
        )
    """)
    op.execute("ALTER TABLE runs ADD COLUMN agent_id TEXT")
    op.execute("ALTER TABLE runs ADD COLUMN agent_name TEXT")


def downgrade():
    op.drop_column("runs", "agent_name")
    op.drop_column("runs", "agent_id")
    op.drop_table("workflow_agents")
    op.drop_table("agents")
