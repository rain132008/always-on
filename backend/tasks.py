import json
from pathlib import Path
from time import time

from .contracts import MAX_WAKEUP_CHAIN
from .database import Database, execute, new_id, one, rows
from .workspace import Conflict, Missing, Workspace

ACTIVE_RUN = """
    SELECT r.*, w.id AS wakeup_id, w.event_id, w.chain_depth, t.status AS task_status, t.workflow_id,
           e.kind AS wake_kind
    FROM runs r JOIN wakeups w ON w.run_id = r.id JOIN tasks t ON t.id = r.task_id
    JOIN events e ON e.id = w.event_id
    JOIN agent_sessions s ON s.id = r.session_id
    WHERE r.id = :run_id AND r.generation = :generation
      AND s.generation = r.generation AND w.status = 'claimed'
"""

SESSION_VIEW = """
    SELECT s.id, s.task_id, s.external_id, s.workspace, COALESCE(s.name, t.title) AS name,
           t.title AS task_title, t.status AS task_status, t.workflow_id, t.updated_at,
           CASE WHEN c.enabled = 0 THEN 0 ELSE 1 END AS workflow_enabled
    FROM agent_sessions s JOIN tasks t ON t.id = s.task_id
    LEFT JOIN workflow_controls c ON c.workflow_id = t.workflow_id
"""

RUN_FIELDS = """
    r.id, r.generation, r.status, r.resume_id, r.pid, r.process_created_at,
    r.summary, r.exit_code, r.created_at, r.finished_at, r.agent_id, r.agent_name, r.runner
"""


def workflow_enabled(connection, workflow_id):
    control = one(connection, "SELECT enabled FROM workflow_controls WHERE workflow_id = :id", id=workflow_id)
    return control is None or bool(control["enabled"])


class Tasks:
    def __init__(self, database: Database, root: Path, log_root: Path, outbox, result_actions=None):
        self.db = database
        self.root = root
        self.log_root = log_root
        self.outbox = outbox
        self.result_actions = result_actions

    def create(self, title, prompt, *, snapshot=None):
        now = time()
        with self.db.write() as connection:
            if not workflow_enabled(connection, "sdd-task"):
                raise Conflict("SDD 通用任务工作流已停用。")
            return self.submit(connection, title, prompt, "task_created", f"created:{new_id()}", now,
                               workflow=snapshot(connection) if snapshot else None)

    def submit(self, connection, title, prompt, kind, event_key, now, *, workflow=None, subject=None):
        existing = one(connection, "SELECT task_id FROM events WHERE dedupe_key = :key", key=event_key)
        if existing:
            return existing["task_id"]
        workflow_id = workflow["id"] if workflow else "sdd-task"
        task = one(connection, """
            SELECT id, status FROM tasks WHERE workflow_id = :workflow AND subject_key = :subject
        """, workflow=workflow_id, subject=subject) if subject else None
        if task is None:
            workspace = Workspace.inspect(self.root)
            task_id = new_id()
            execute(connection, """
                INSERT INTO tasks (id, title, prompt, status, created_at, updated_at, workflow_id, subject_key)
                VALUES (:id, :title, :prompt, 'ready', :now, :now, :workflow, :subject)
            """, id=task_id, title=title, prompt=prompt, now=now, workflow=workflow_id, subject=subject)
            execute(connection, """
                INSERT INTO agent_sessions (id, task_id, workspace, workspace_id)
                VALUES (:id, :task, :path, :identity)
            """, id=new_id(), task=task_id, path=workspace.path, identity=workspace.identity)
        else:
            task_id = task["id"]
            if task["status"] == "closed":
                return task_id
            # 同一主题尚未领取的事件只保留最新一项。运行及人工回复继续使用原事件快照。
            execute(connection, """
                UPDATE wakeups SET status = 'superseded'
                WHERE task_id = :task AND status = 'pending'
                  AND event_id IN (
                      SELECT id FROM events WHERE task_id = :task AND kind IN (:kind, 'agent_wakeup')
                  )
            """, task=task_id, kind=kind)
            execute(connection, """
                UPDATE tasks SET title = :title, prompt = :prompt, updated_at = :now WHERE id = :id
            """, title=title, prompt=prompt, now=now, id=task_id)
            if task["status"] not in {"running", "waiting_human", "recovery_required"}:
                self.set_status(connection, task_id, "ready", now)
        self.schedule(connection, task_id, kind, event_key, prompt, now, workflow=workflow)
        return task_id

    def reply(self, request_id, answer):
        now = time()
        with self.db.write() as connection:
            request = one(connection, """
                SELECT h.*, t.status, t.workflow_id, e.context_json FROM human_requests h JOIN tasks t ON t.id = h.task_id
                JOIN wakeups w ON w.run_id = h.run_id JOIN events e ON e.id = w.event_id
                WHERE h.id = :id
            """, id=request_id)
            if request is None:
                raise Missing("未找到这条待办。")
            if request["answer"] is not None:
                if request["answer"] == answer:
                    return request["task_id"]
                raise Conflict("这条待办已有回复。")
            if request["status"] != "waiting_human":
                raise Conflict("任务当前不接受人工回复。")
            if not workflow_enabled(connection, request["workflow_id"]):
                raise Conflict("关联工作流已停用，请启用后再回复。")
            execute(connection, """
                UPDATE human_requests SET answer = :answer, answered_at = :now WHERE id = :id
            """, id=request_id, answer=answer, now=now)
            workflow = json.loads(request["context_json"]) if request["context_json"] else None
            self.schedule(connection, request["task_id"], "human_replied", f"reply:{request_id}", answer, now,
                          workflow=workflow)
            self.set_status(connection, request["task_id"], "ready", now)
            return request["task_id"]

    @staticmethod
    def schedule(connection, task_id, kind, key, summary, now, *, workflow=None, due_at=None, chain_depth=0):
        event_id = Tasks.record_event(connection, task_id, kind, key, summary, now, workflow)
        execute(connection, """
            INSERT INTO wakeups (id, task_id, event_id, status, due_at, chain_depth)
            VALUES (:id, :task, :event, 'pending', :due, :depth)
        """, id=new_id(), task=task_id, event=event_id, due=now if due_at is None else due_at, depth=chain_depth)

    @staticmethod
    def record_event(connection, task_id, kind, key, summary, now, workflow=None):
        return execute(connection, """
            INSERT INTO events (task_id, kind, dedupe_key, summary, created_at, context_json)
            VALUES (:task, :kind, :key, :summary, :now, :context)
        """, task=task_id, kind=kind, key=key, summary=summary, now=now,
            context=json.dumps(workflow, ensure_ascii=False) if workflow else None).lastrowid

    def close(self, task_id, reason):
        now = time()
        with self.db.write() as connection:
            task = one(connection, "SELECT status FROM tasks WHERE id = :id", id=task_id)
            if task is None:
                raise Missing("未找到这项任务。")
            if task["status"] == "closed":
                return
            self.set_status(connection, task_id, "closed", now)
            self.record_event(connection, task_id, "task_closed", f"closed:{task_id}", reason or "手动关闭任务。", now)
            execute(connection, """
                UPDATE wakeups SET status = 'cancelled', lease_until = NULL
                WHERE task_id = :task AND status = 'pending'
            """, task=task_id)
            unstarted = one(connection, """
                SELECT r.id, w.id AS wakeup_id FROM runs r JOIN wakeups w ON w.run_id = r.id
                WHERE r.task_id = :task AND w.status = 'claimed' AND r.pid IS NULL
            """, task=task_id)
            if unstarted:
                self.cancel_claimed(connection, unstarted, now)

    @staticmethod
    def cancel_claimed(connection, active, now, exit_code=None):
        execute(connection, """
            UPDATE runs SET status = 'cancelled', summary = '任务已关闭，本轮结束。',
                finished_at = :now, exit_code = :exit WHERE id = :run
        """, now=now, exit=exit_code, run=active["id"])
        execute(connection, """
            UPDATE wakeups SET status = 'cancelled', lease_until = NULL WHERE id = :id
        """, id=active["wakeup_id"])

    def list_sessions(self):
        with self.db.engine.connect() as connection:
            return rows(connection, SESSION_VIEW + " ORDER BY t.updated_at DESC, s.id")

    def session_detail(self, session_id):
        with self.db.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN")
            session = one(connection, SESSION_VIEW + " WHERE s.id = :id", id=session_id)
            if session is None:
                raise Missing("未找到这条会话。")
            session["runs"] = rows(connection, f"""
                SELECT {RUN_FIELDS} FROM runs r WHERE r.session_id = :id ORDER BY r.generation DESC
            """, id=session_id)
            return session

    def rename_session(self, session_id, name):
        with self.db.write() as connection:
            updated = execute(connection, "UPDATE agent_sessions SET name = :name WHERE id = :id",
                              id=session_id, name=name)
            if not updated.rowcount:
                raise Missing("未找到这条会话。")

    def resume_session(self, session_id, instruction):
        now = time()
        with self.db.write() as connection:
            session = one(connection, """
                SELECT s.*, t.status AS task_status, t.workflow_id
                FROM agent_sessions s JOIN tasks t ON t.id = s.task_id WHERE s.id = :id
            """, id=session_id)
            if session is None:
                raise Missing("未找到这条会话。")
            if session["task_status"] == "closed":
                raise Conflict("任务已关闭，会话只能查看。")
            if not workflow_enabled(connection, session["workflow_id"]):
                raise Conflict("关联工作流已停用，请启用后再继续会话。")
            if session["task_status"] not in {"completed", "failed"} or not session["external_id"]:
                raise Conflict("只有已有会话 ID 且当前无待办或运行的任务可以手动继续。")
            self.verify_workspace(session)
            latest = one(connection, """
                SELECT e.context_json FROM runs r JOIN wakeups w ON w.run_id = r.id
                JOIN events e ON e.id = w.event_id
                WHERE r.session_id = :id ORDER BY r.generation DESC LIMIT 1
            """, id=session_id)
            snapshot = json.loads(latest["context_json"]) if latest and latest["context_json"] else None
            if snapshot:
                snapshot["manual_follow_up"] = True
            self.schedule(connection, session["task_id"], "manual_resume",
                          f"manual-resume:{session_id}:{session['generation']}", instruction, now, workflow=snapshot)
            self.set_status(connection, session["task_id"], "ready", now)
            return session["task_id"]

    @staticmethod
    def set_status(connection, task, status, now):
        execute(connection, """
            UPDATE tasks SET status = :status, updated_at = :now WHERE id = :task
        """, task=task, status=status, now=now)

    def claim(self, timeout, *, legacy_runner="mock"):
        now = time()
        with self.db.write() as connection:
            wakeup = one(connection, """
                SELECT w.id AS wakeup_id, w.task_id, w.chain_depth, t.title, t.prompt, t.subject_key,
                       e.kind, e.summary, e.context_json,
                       s.id AS session_id, s.external_id AS resume_id, s.generation,
                       s.workspace, s.workspace_id
                FROM wakeups w JOIN tasks t ON t.id = w.task_id
                JOIN events e ON e.id = w.event_id
                JOIN agent_sessions s ON s.task_id = w.task_id
                WHERE w.status = 'pending' AND w.due_at <= :now
                  AND (t.status = 'ready' OR (t.status = 'waiting_timer' AND e.kind = 'agent_wakeup'))
                  AND NOT EXISTS (SELECT 1 FROM workflow_controls c WHERE c.workflow_id = t.workflow_id
                                  AND c.enabled = 0)
                ORDER BY CASE WHEN e.kind = 'human_replied' THEN 0 ELSE 1 END, w.due_at, e.id LIMIT 1
            """, now=now)
            if wakeup is None:
                return None
            run_id = new_id()
            generation = wakeup["generation"] + 1
            log_path = str(self.log_root / f"{run_id}.log")
            workflow = json.loads(wakeup["context_json"]) if wakeup["context_json"] else None
            agent = (workflow or {}).get("agent") or {}
            runner = (workflow or {}).get("runner", legacy_runner)
            execute(connection, """
                UPDATE agent_sessions SET generation = :generation WHERE id = :session_id
            """, **(wakeup | {"generation": generation}))
            execute(connection, """
                INSERT INTO runs (
                    id, task_id, session_id, generation, status, workspace, workspace_id,
                    resume_id, created_at, log_path, agent_id, agent_name, runner
                ) VALUES (
                    :run_id, :task_id, :session_id, :generation, 'running', :workspace,
                    :workspace_id, :resume_id, :now, :log_path, :agent_id, :agent_name, :runner
                )
            """, **(wakeup | dict(run_id=run_id, generation=generation, now=now, log_path=log_path,
                                 agent_id=agent.get("id"), agent_name=agent.get("name"), runner=runner)))
            execute(connection, """
                UPDATE wakeups SET status = 'claimed', run_id = :id, lease_until = :lease
                WHERE id = :wakeup
            """, id=run_id, lease=now + timeout, wakeup=wakeup["wakeup_id"])
            self.set_status(connection, wakeup["task_id"], "running", now)
            return wakeup | dict(run_id=run_id, generation=generation, log_path=log_path,
                                 workflow=workflow, runner=runner)

    def attach_process(self, run, pid, created_at):
        with self.db.write() as connection:
            active = self.active(connection, run)
            if active is None or active["pid"] is not None or active["task_status"] == "closed":
                raise Conflict("运行已失效，无法启动进程。")
            superseded = active["wake_kind"] == "agent_wakeup" and self.has_newer_subject_wakeup(connection, active)
            paused = active["wake_kind"] == "agent_wakeup" and not workflow_enabled(connection, active["workflow_id"])
            if superseded or paused:
                now = time()
                execute(connection, """
                    UPDATE runs SET status = :status, summary = :summary,
                        finished_at = :now WHERE id = :run
                """, status="superseded" if superseded else "interrupted",
                    summary="已有更新的任务输入，跳过本次预约。" if superseded else "关联工作流已停用，预约唤醒保留。",
                    now=now, run=run["run_id"])
                execute(connection, """
                    UPDATE wakeups SET status = :status, lease_until = NULL,
                        run_id = CASE WHEN :skip THEN run_id ELSE NULL END WHERE id = :wake
                """, status="superseded" if superseded else "pending", skip=superseded, wake=active["wakeup_id"])
                self.set_status(connection, active["task_id"], "ready" if superseded else "waiting_timer", now)
                return False
            execute(connection, """
                UPDATE runs SET pid = :pid, process_created_at = :created WHERE id = :run
            """, pid=pid, created=created_at, run=run["run_id"])
            return True

    @staticmethod
    def active(connection, run, status="running"):
        return one(connection, ACTIVE_RUN + " AND r.status = :status", run_id=run["run_id"],
                   generation=run["generation"], status=status)

    @staticmethod
    def has_newer_subject_wakeup(connection, active):
        return one(connection, """
            SELECT w.id FROM wakeups w JOIN events newer ON newer.id = w.event_id
            JOIN events current ON current.id = :event
            JOIN tasks t ON t.id = current.task_id
            WHERE w.task_id = current.task_id AND t.subject_key IS NOT NULL
              AND w.status = 'pending' AND newer.id > current.id
              AND (newer.kind = current.kind OR current.kind = 'agent_wakeup')
            LIMIT 1
        """, event=active["event_id"]) is not None

    def finish(self, run, result=None, *, error=None, exit_code=None):
        with self.db.write() as connection:
            now = time()
            active = self.active(connection, run)
            if active is None:
                return False
            if active["task_status"] == "closed":
                self.cancel_claimed(connection, active, now, exit_code)
                return True
            if result is not None:
                result_session = str(result.session_id)
                if (
                    str(result.run_id) != run["run_id"]
                    or result.generation != run["generation"]
                    or str(result.task_id) != run["task_id"]
                    or (run["resume_id"] and result_session != run["resume_id"])
                ):
                    return False
                owner = one(connection, """
                    SELECT id FROM agent_sessions WHERE external_id = :id
                """, id=result_session)
                if owner and owner["id"] != run["session_id"]:
                    return False
                execute(connection, """
                    UPDATE agent_sessions SET external_id = :external_id WHERE id = :session_id
                """, external_id=result_session, session_id=run["session_id"])
            status = result.outcome if result else "failed"
            summary = result.summary if result else error
            superseded = self.has_newer_subject_wakeup(connection, active)
            if superseded:
                status = "superseded"
                summary = "已发现同一主题的新版本，本轮已跳过。"
            elif status == "schedule_wakeup" and active["chain_depth"] >= MAX_WAKEUP_CHAIN:
                status = "failed"
                summary = f"已达到连续自动唤醒上限（{MAX_WAKEUP_CHAIN} 次）。可以从会话中心人工继续。"
            elif status == "schedule_wakeup":
                self.schedule(
                    connection, run["task_id"], "agent_wakeup", f"agent-wakeup:{run['run_id']}",
                    result.summary, now, workflow=run.get("workflow"), due_at=now + result.after_seconds,
                    chain_depth=active["chain_depth"] + 1,
                )
            elif (
                result is not None and self.result_actions and run.get("workflow")
                and not run["workflow"].get("manual_follow_up")
            ):
                self.result_actions.apply(connection, run, result, now)
            execute(connection, """
                UPDATE runs SET status = :status, summary = :summary, exit_code = :exit,
                    finished_at = :now WHERE id = :id
            """, status=status, summary=summary, exit=exit_code, now=now, id=run["run_id"])
            execute(connection, """
                UPDATE wakeups SET status = :status, lease_until = NULL WHERE id = :id
            """, status="superseded" if superseded else "done", id=run["wakeup_id"])
            if status == "needs_human":
                request_id = new_id()
                execute(connection, """
                    INSERT INTO human_requests (id, task_id, run_id, question, created_at)
                    VALUES (:id, :task, :run, :question, :now)
                """, id=request_id, task=run["task_id"], run=run["run_id"], question=result.question, now=now)
                self.outbox.enqueue(
                    connection, "mock-notify", "human_request", f"human-request:{request_id}",
                    {"task_id": run["task_id"], "request_id": request_id},
                )
                status = "waiting_human"
            elif status == "schedule_wakeup":
                status = "waiting_timer"
            elif one(connection, """
                SELECT id FROM wakeups WHERE task_id = :task AND status = 'pending' LIMIT 1
            """, task=run["task_id"]):
                status = "ready"
            self.set_status(connection, run["task_id"], status, now)
            return True

    def unfinished(self):
        with self.db.engine.connect() as connection:
            return rows(connection, """
                SELECT id AS run_id, generation, task_id, pid, process_created_at
                FROM runs WHERE status = 'running'
            """)

    def require_recovery(self, run, reason, *, process_exited=False):
        with self.db.write() as connection:
            active = self.active(connection, run)
            if active is not None:
                if active["task_status"] == "closed" and (process_exited or active["pid"] is None):
                    self.cancel_claimed(connection, active, time())
                    return
                execute(connection, """
                    UPDATE runs SET status = 'recovery_required', summary = :reason WHERE id = :run
                """, reason=reason, run=run["run_id"])
                if active["task_status"] != "closed":
                    self.set_status(connection, active["task_id"], "recovery_required", time())

    def recovery_run(self, run_id):
        with self.db.engine.connect() as connection:
            run = one(connection, """
                SELECT id AS run_id, task_id, generation, pid, process_created_at, runner
                FROM runs WHERE id = :id AND status = 'recovery_required'
            """, id=run_id)
        if run is None:
            raise Conflict("这次运行已不在待恢复状态。")
        return run

    def recover(self, run, *, retry):
        now = time()
        with self.db.write() as connection:
            active = self.active(connection, run, "recovery_required")
            if active is None:
                raise Conflict("这次运行的恢复状态已变化，请刷新后重试。")
            if active["task_status"] == "closed":
                if retry:
                    raise Conflict("任务已关闭，不能重新排队。")
                self.cancel_claimed(connection, active, now)
                return
            if retry:
                if not workflow_enabled(connection, active["workflow_id"]):
                    raise Conflict("关联工作流已停用，请启用后再重试。")
                self.verify_workspace(active)
                execute(connection, """
                    UPDATE runs SET status = 'interrupted', finished_at = :now WHERE id = :run
                """, now=now, run=run["run_id"])
                if self.has_newer_subject_wakeup(connection, active):
                    execute(connection, """
                        UPDATE wakeups SET status = 'superseded', lease_until = NULL WHERE id = :id
                    """, id=active["wakeup_id"])
                else:
                    execute(connection, """
                        UPDATE wakeups SET status = 'pending', lease_until = NULL, run_id = NULL,
                            due_at = :now WHERE id = :id
                    """, now=now, id=active["wakeup_id"])
                self.set_status(connection, active["task_id"], "ready", now)
            else:
                execute(connection, """
                    UPDATE runs SET summary = :message WHERE id = :run
                """, message="已确认旧进程退出。检查工作区后可重新排队。", run=run["run_id"])

    def verify_workspace(self, run):
        Workspace(run["workspace"], run["workspace_id"]).verify(self.root)

    def list(self):
        with self.db.engine.connect() as connection:
            return rows(connection, """
                WITH latest_output AS (
                    SELECT task_id, MAX(generation) AS generation
                    FROM runs WHERE summary IS NOT NULL GROUP BY task_id
                ), pending_request AS (
                    SELECT task_id, question,
                           ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY created_at, id) AS position
                    FROM human_requests WHERE answer IS NULL
                ), planned_wakeup AS (
                    SELECT w.task_id, MIN(w.due_at) AS due_at
                    FROM wakeups w JOIN events e ON e.id = w.event_id
                    WHERE w.status = 'pending' AND e.kind = 'agent_wakeup' GROUP BY w.task_id
                )
                SELECT t.id, t.title, t.status, t.updated_at, t.workflow_id,
                       COALESCE(c.enabled, 1) AS workflow_enabled,
                       substr(r.summary, 1, 420) AS latest_summary,
                       substr(h.question, 1, 420) AS pending_question,
                       w.due_at AS next_wakeup_at
                FROM tasks t
                LEFT JOIN workflow_controls c ON c.workflow_id = t.workflow_id
                LEFT JOIN latest_output lo ON lo.task_id = t.id
                LEFT JOIN runs r ON r.task_id = lo.task_id AND r.generation = lo.generation
                LEFT JOIN pending_request h ON h.task_id = t.id AND h.position = 1 AND t.status = 'waiting_human'
                LEFT JOIN planned_wakeup w ON w.task_id = t.id
                ORDER BY t.created_at DESC
            """)

    def detail(self, task_id):
        with self.db.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN")
            task = one(connection, "SELECT * FROM tasks WHERE id = :id", id=task_id)
            if task is None:
                raise Missing("未找到这项任务。")
            task["workflow_enabled"] = workflow_enabled(connection, task["workflow_id"])
            task["next_wakeup"] = one(connection, """
                SELECT w.due_at FROM wakeups w JOIN events e ON e.id = w.event_id
                WHERE w.task_id = :id AND w.status = 'pending' AND e.kind = 'agent_wakeup'
                ORDER BY w.due_at LIMIT 1
            """, id=task_id)
            task["session"] = one(connection, """
                SELECT s.id, s.external_id, s.workspace, COALESCE(s.name, t.title) AS name
                FROM agent_sessions s JOIN tasks t ON t.id = s.task_id WHERE s.task_id = :id
            """, id=task_id)
            task["runs"] = rows(connection, f"""
                SELECT {RUN_FIELDS} FROM runs r WHERE r.task_id = :id ORDER BY r.generation
            """, id=task_id)
            task["events"] = rows(connection, """
                SELECT id, kind, summary, created_at FROM events WHERE task_id = :id ORDER BY id
            """, id=task_id)
            task["requests"] = rows(connection, """
                SELECT id, question, answer, created_at, answered_at FROM human_requests
                WHERE task_id = :id ORDER BY created_at
            """, id=task_id)
            task["artifacts"] = [
                item | {"content": json.loads(item["content"])} for item in rows(connection, """
                    SELECT id, run_id, kind, content, created_at FROM task_artifacts
                    WHERE task_id = :id ORDER BY created_at
                """, id=task_id)
            ]
            return task

    def read_log(self, run_id):
        with self.db.engine.connect() as connection:
            run = one(connection, "SELECT log_path FROM runs WHERE id = :id", id=run_id)
        if run is None:
            raise Missing("未找到这次运行。")
        expected = self.log_root / f"{run_id}.log"
        if run["log_path"] != str(expected) or not expected.is_file():
            raise Missing("这次运行还没有日志。")
        return expected.read_text(encoding="utf-8")
