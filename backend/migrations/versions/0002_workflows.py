from alembic import op

revision = "0002_workflows"
down_revision = "0001_sdd_task"


def upgrade():
    for statement in (
        "ALTER TABLE tasks ADD COLUMN workflow_id TEXT NOT NULL DEFAULT 'sdd-task'",
        "ALTER TABLE tasks ADD COLUMN subject_key TEXT",
        "ALTER TABLE events ADD COLUMN context_json TEXT",
        "CREATE UNIQUE INDEX task_subject ON tasks(workflow_id, subject_key) WHERE subject_key IS NOT NULL",
        """CREATE TABLE workflow_cursors (
            workflow_id TEXT NOT NULL, stream TEXT NOT NULL, cursor TEXT NOT NULL,
            PRIMARY KEY (workflow_id, stream)
        )""",
        """CREATE TABLE workflow_ticks (
            workflow_id TEXT PRIMARY KEY, slot REAL NOT NULL
        )""",
        """CREATE TABLE task_artifacts (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
            run_id TEXT NOT NULL REFERENCES runs(id), kind TEXT NOT NULL,
            content TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(run_id, kind)
        )""",
    ):
        op.execute(statement)


def downgrade():
    for table in ("task_artifacts", "workflow_ticks", "workflow_cursors"):
        op.drop_table(table)
    op.drop_index("task_subject", table_name="tasks")
    op.drop_column("events", "context_json")
    op.drop_column("tasks", "subject_key")
    op.drop_column("tasks", "workflow_id")
