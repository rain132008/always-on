import hashlib
import json
from dataclasses import dataclass
from time import time
from typing import Literal, Protocol

from .database import execute, new_id, one, rows
from .workspace import Conflict, Missing


class DeliveryRejected(Exception):
    """Connector 确认远端没有接收，可按退避策略重试。"""


class DeliveryObsolete(Exception):
    """本地结果所依据的外部版本已过期。"""


@dataclass(frozen=True)
class Reconciled:
    status: Literal["found", "absent", "unknown"]
    remote_id: str | None = None


class Connector(Protocol):
    def deliver(self, kind: str, business_key: str, payload: dict) -> str: ...


class ReconciliationConnector(Connector, Protocol):
    def reconcile(self, kind: str, business_key: str, payload: dict) -> Reconciled: ...


def mr_review_key(provider, project_id, mr_iid, head_sha, skill_hash):
    identity = json.dumps([provider, str(project_id), str(mr_iid), head_sha, skill_hash])
    return "mr-review:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


class Outbox:
    def __init__(self, database, connectors: dict[str, Connector]):
        self.db = database
        self.connectors = connectors

    def enqueue(self, connection, connector, kind, business_key, payload):
        if connector not in self.connectors:
            raise ValueError(f"未注册的 Connector {connector}")
        now = time()
        action_id = new_id()
        encoded_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        inserted = execute(connection, """
            INSERT INTO external_actions (id, connector, kind, business_key, payload, status, due_at, updated_at)
            VALUES (:id, :connector, :kind, :key, :payload, 'pending', :now, :now)
            ON CONFLICT (business_key) DO NOTHING
        """, id=action_id, connector=connector, kind=kind, key=business_key, payload=encoded_payload, now=now)
        if inserted.rowcount:
            return action_id
        existing = one(connection, """
            SELECT id, connector, kind, payload FROM external_actions WHERE business_key = :key
        """, key=business_key)
        if (existing["connector"], existing["kind"], existing["payload"]) != (connector, kind, encoded_payload):
            raise Conflict("外部动作业务键已被不同的内容使用。")
        return existing["id"]

    def recover_interrupted(self):
        with self.db.write() as connection:
            for action in rows(connection, "SELECT * FROM external_actions WHERE status = 'sending'"):
                self.update(connection, action, "reconciling", "发送后未记录结果，等待远端核对。")

    def claim(self):
        with self.db.write() as connection:
            action = one(connection, """
                SELECT * FROM external_actions
                WHERE status IN ('pending', 'reconciling') AND due_at <= :now
                ORDER BY due_at, id LIMIT 1
            """, now=time())
            if action is not None and action["status"] == "pending":
                execute(connection, """
                    UPDATE external_actions SET attempts = attempts + 1 WHERE id = :id
                """, id=action["id"])
                self.update(connection, action, "sending", None)
                return action | {"status": "sending", "attempts": action["attempts"] + 1}
            return action

    def tick(self):
        action = self.claim()
        if action is None:
            return
        connector = self.connectors.get(action["connector"])
        inputs = (action["kind"], action["business_key"], json.loads(action["payload"]))
        if action["status"] == "reconciling":
            self.reconcile(action, connector, inputs)
        elif connector is None:
            self.transition(action, "needs_reconcile", "发送 Connector 不可用，请人工核对。")
        else:
            try:
                remote_id = connector.deliver(*inputs)
            except DeliveryObsolete as error:
                self.transition(action, "superseded", str(error))
            except DeliveryRejected:
                self.retry(action, "远端确认未接收。")
            except Exception:
                self.transition(action, "reconciling", "发送结果未知，等待远端核对。")
            else:
                if remote_id:
                    self.transition(action, "sent", None, remote_id=remote_id)
                else:
                    self.transition(action, "reconciling", "未收到远端记录 ID，等待核对。")

    def reconcile(self, action, connector, inputs):
        check = getattr(connector, "reconcile", None)
        if check is None:
            self.transition(action, "needs_reconcile", "Connector 无远端核对能力，请人工核对。")
            return
        try:
            result = check(*inputs)
        except Exception:
            result = Reconciled("unknown")
        if result.status == "found" and result.remote_id:
            self.transition(action, "sent", "远端核对确认已送达。", remote_id=result.remote_id)
        elif result.status == "absent":
            self.retry(action, "远端核对确认未接收。")
        else:
            self.transition(action, "needs_reconcile", "远端结果仍无法确认，请人工核对。")

    def retry(self, action, detail):
        if action["attempts"] >= 3:
            self.transition(action, "failed", f"{detail} 已达三次尝试上限。")
        else:
            self.transition(action, "pending", detail, delay=2 ** action["attempts"])

    def transition(self, action, status, detail, **kwargs):
        with self.db.write() as connection:
            self.update(connection, action, status, detail, **kwargs)

    @staticmethod
    def update(connection, action, status, detail, *, remote_id=None, delay=0):
        now = time()
        updated = execute(connection, """
            UPDATE external_actions SET status = :status, detail = :detail, remote_id = :remote_id,
                updated_at = :now, due_at = :due WHERE id = :id AND status = :old_status
        """, id=action["id"], old_status=action["status"], status=status, detail=detail,
            remote_id=remote_id, now=now, due=now + delay)
        if updated.rowcount != 1:
            raise Conflict("外部动作状态已变化，请刷新后重试。")
        execute(connection, """
            INSERT INTO action_events (action_id, status, detail, created_at) VALUES (:id, :status, :detail, :now)
        """, id=action["id"], status=status, detail=detail, now=now)

    def manual(self, action_id, decision, evidence, remote_id):
        with self.db.write() as connection:
            action = one(connection, "SELECT * FROM external_actions WHERE id = :id", id=action_id)
            if action is None:
                raise Missing("未找到这条外部动作。")
            if action["status"] != "needs_reconcile":
                raise Conflict("该动作已不在待人工核对状态。")
            if decision == "delivered":
                self.update(connection, action, "sent", evidence, remote_id=remote_id)
            else:
                status = "failed" if action["attempts"] >= 3 else "pending"
                self.update(connection, action, status, evidence)

    def list(self):
        with self.db.engine.connect() as connection:
            return rows(connection, """
                SELECT id, connector, kind, business_key, status, attempts, detail, remote_id, updated_at
                FROM external_actions ORDER BY updated_at DESC
            """)
