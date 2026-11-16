from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.contracts import MAX_WAKEUP_CHAIN, MAX_WAKEUP_SECONDS, MIN_WAKEUP_SECONDS, RunResult
from backend.database import Database, execute
from backend.tasks import Tasks


def report(run, session_id, **fields):
    return RunResult(
        contract_version="1", task_id=run["task_id"], run_id=run["run_id"],
        generation=run["generation"], session_id=session_id, summary="等待后继续复查", **fields,
    )


def test_wakeup_result_validates_time_and_excludes_human_wait():
    run = {"task_id": uuid4(), "run_id": uuid4(), "generation": 1}
    session_id = uuid4()
    for invalid in (-1, MIN_WAKEUP_SECONDS - 1, MAX_WAKEUP_SECONDS + 1, "10", 5.5, True, None):
        with pytest.raises(ValidationError):
            report(run, session_id, outcome="schedule_wakeup", after_seconds=invalid)
    assert report(run, session_id, outcome="schedule_wakeup", after_seconds=MIN_WAKEUP_SECONDS).after_seconds == 5
    assert report(run, session_id, outcome="schedule_wakeup", after_seconds=MAX_WAKEUP_SECONDS).after_seconds == 604800
    with pytest.raises(ValidationError):
        report(run, session_id, outcome="needs_human", question="请确认？", after_seconds=10)
    with pytest.raises(ValidationError):
        report(run, session_id, outcome="schedule_wakeup", question="请确认？", after_seconds=10)


def test_delayed_wakeup_is_durable_deduplicated_and_bounded(tmp_path, monkeypatch):
    now = 1000.0
    monkeypatch.setattr("backend.tasks.time", lambda: now)
    db = Database(tmp_path / "core.sqlite3")
    db.migrate()
    tasks = Tasks(db, tmp_path, tmp_path, outbox=None)
    task_id = tasks.create("定时续跑", "等待后回访")
    first = tasks.claim(30)
    session_id = uuid4()
    result = report(first, session_id, outcome="schedule_wakeup", after_seconds=10)
    assert tasks.finish(first, result)
    assert not tasks.finish(first, result)
    db.engine.dispose()

    db = Database(tmp_path / "core.sqlite3")
    tasks = Tasks(db, tmp_path, tmp_path, outbox=None)
    detail = tasks.detail(task_id)
    assert detail["status"] == "waiting_timer" and detail["next_wakeup"] == {"due_at": 1010.0}
    assert [event["kind"] for event in detail["events"]] == ["task_created", "agent_wakeup"]
    now = 1009.9
    assert tasks.claim(30) is None

    now = 1010.0
    claimed = tasks.claim(30)
    with db.write() as conn:
        execute(conn, "INSERT INTO workflow_controls VALUES ('sdd-task', 0, :now)", now=now)
    assert tasks.attach_process(claimed, 22, now) is False
    assert tasks.claim(30) is None
    assert tasks.detail(task_id)["next_wakeup"] == {"due_at": now}
    with db.write() as conn:
        execute(conn, "UPDATE workflow_controls SET enabled = 1")
    for depth in range(1, MAX_WAKEUP_CHAIN + 1):
        wake = tasks.claim(30)
        assert wake["chain_depth"] == depth and wake["resume_id"] == str(session_id)
        assert tasks.finish(wake, report(wake, session_id, outcome="schedule_wakeup", after_seconds=10))
        now += 10
    final = tasks.detail(task_id)
    assert final["status"] == "failed" and final["next_wakeup"] is None
    assert f"{MAX_WAKEUP_CHAIN} 次" in final["runs"][-1]["summary"]
    assert tasks.claim(30) is None
    db.engine.dispose()


def test_new_subject_event_and_close_cancel_older_delayed_wakeup(tmp_path, monkeypatch):
    now = 1000.0
    monkeypatch.setattr("backend.tasks.time", lambda: now)
    db = Database(tmp_path / "core.sqlite3")
    db.migrate()
    tasks = Tasks(db, tmp_path, tmp_path, outbox=None)
    workflow = {"id": "test-review"}

    def head(key):
        with db.write() as conn:
            return tasks.submit(conn, "样例评审", key, "mr_head", key, now, workflow=workflow, subject="mr")

    task_id = head("head-1")
    first = tasks.claim(30)
    session_id = uuid4()
    assert tasks.finish(first, report(first, session_id, outcome="schedule_wakeup", after_seconds=10))
    assert head("head-2") == task_id
    assert tasks.detail(task_id)["next_wakeup"] is None
    second = tasks.claim(30)
    assert second["kind"] == "mr_head" and second["resume_id"] == str(session_id)
    assert tasks.finish(second, report(second, session_id, outcome="schedule_wakeup", after_seconds=10))
    tasks.close(task_id, "已完成验收")
    now += 10
    assert head("head-3") == task_id and tasks.claim(30) is None
    assert tasks.detail(task_id)["next_wakeup"] is None
    db.engine.dispose()
