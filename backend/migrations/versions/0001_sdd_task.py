from alembic import op

revision = "0001_sdd_task"
down_revision = None

SCHEMA = (
    """CREATE TABLE tasks (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL,
        status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
    )""",
    """CREATE TABLE agent_sessions (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
        external_id TEXT UNIQUE, workspace TEXT NOT NULL, workspace_id TEXT NOT NULL,
        generation INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id),
        kind TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE, summary TEXT NOT NULL,
        created_at REAL NOT NULL
    )""",
    """CREATE TABLE wakeups (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
        event_id INTEGER NOT NULL UNIQUE REFERENCES events(id), status TEXT NOT NULL,
        due_at REAL NOT NULL, lease_until REAL,
        run_id TEXT UNIQUE REFERENCES runs(id)
    )""",
    """CREATE TABLE runs (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
        session_id TEXT NOT NULL REFERENCES agent_sessions(id),
        generation INTEGER NOT NULL, status TEXT NOT NULL, workspace TEXT NOT NULL,
        workspace_id TEXT NOT NULL, resume_id TEXT, pid INTEGER, process_created_at REAL,
        created_at REAL NOT NULL, finished_at REAL, exit_code INTEGER,
        summary TEXT, log_path TEXT, UNIQUE(session_id, generation)
    )""",
    """CREATE UNIQUE INDEX one_active_run ON runs(session_id)
        WHERE status IN ('running', 'recovering', 'recovery_required')""",
    """CREATE TABLE human_requests (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
        run_id TEXT NOT NULL UNIQUE REFERENCES runs(id), question TEXT NOT NULL,
        answer TEXT, answered_at REAL, created_at REAL NOT NULL
    )""",
    """CREATE TABLE external_actions (
        id TEXT PRIMARY KEY, connector TEXT NOT NULL, kind TEXT NOT NULL,
        business_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, due_at REAL NOT NULL, remote_id TEXT,
        detail TEXT, updated_at REAL NOT NULL
    )""",
    """CREATE TABLE action_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action_id TEXT NOT NULL REFERENCES external_actions(id),
        status TEXT NOT NULL, detail TEXT, created_at REAL NOT NULL
    )""",
    "CREATE INDEX due_wakeups ON wakeups(status, due_at)",
    "CREATE INDEX due_external_actions ON external_actions(status, due_at)",
)


def upgrade():
    for statement in SCHEMA:
        op.execute(statement)


def downgrade():
    for table in (
        "action_events", "external_actions", "human_requests", "wakeups",
        "runs", "events", "agent_sessions", "tasks",
    ):
        op.drop_table(table)
