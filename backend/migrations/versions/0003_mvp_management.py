from alembic import op

revision = "0003_mvp_management"
down_revision = "0002_workflows"


def upgrade():
    op.execute("ALTER TABLE agent_sessions ADD COLUMN name TEXT")
    op.execute("""
        CREATE TABLE workflow_controls (
            workflow_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            updated_at REAL NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE workflow_scans (
            id INTEGER PRIMARY KEY, workflow_id TEXT NOT NULL, trigger TEXT NOT NULL,
            status TEXT NOT NULL, seen INTEGER, reason TEXT, started_at REAL NOT NULL,
            finished_at REAL NOT NULL
        )
    """)
    op.execute("CREATE INDEX workflow_scan_history ON workflow_scans(workflow_id, id DESC)")


def downgrade():
    op.drop_table("workflow_scans")
    op.drop_table("workflow_controls")
    op.drop_column("agent_sessions", "name")
