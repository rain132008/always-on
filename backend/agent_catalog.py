from time import time

from .database import execute, new_id, one, rows
from .workspace import Missing

BUILTIN_AGENTS = {
    "sdd-default": {
        "name": "通用任务 Agent",
        "description": "推进日常 SDD 任务和工程交接。",
        "prompt": "拆解任务目标并按已有约束推进。依据当前信息决定直接完成、询问人工或预约复查。",
    },
    "review": {
        "name": "代码评审 Agent",
        "description": "检查变更中的缺陷和回归风险。",
        "prompt": "优先检查可复现的缺陷、边界和回归风险。说明证据、影响和最小修复方向，区分已确认问题和待验证假设。",
    },
    "document": {
        "name": "文档维护 Agent",
        "description": "把有依据的工程变更整理成文档建议。",
        "prompt": "核对工程变更与现有说明的差异。写明应维护的文档、依据和建议内容。事实不足时提出具体问题。",
    },
}


class AgentCatalog:
    def __init__(self, database):
        self.db = database
        with self.db.write() as connection:
            for agent_id, fields in BUILTIN_AGENTS.items():
                execute(connection, """
                    INSERT INTO agents (id, name, description, prompt, updated_at)
                    VALUES (:id, :name, :description, :prompt, :now)
                    ON CONFLICT (id) DO NOTHING
                """, id=agent_id, now=time(), **fields)

    @staticmethod
    def effective(connection, workflow_id, default_agent):
        return one(connection, """
            SELECT a.id, a.name, a.prompt FROM agents a
            WHERE a.id = COALESCE(
                (SELECT agent_id FROM workflow_agents WHERE workflow_id = :workflow), :default_agent
            )
        """, workflow=workflow_id, default_agent=default_agent)

    def list(self):
        with self.db.engine.connect() as connection:
            return rows(connection, """
                SELECT id, name, description, prompt, updated_at FROM agents ORDER BY rowid
            """)

    def save(self, fields, agent_id=None):
        with self.db.write() as connection:
            params = fields.model_dump() | {"now": time()}
            if agent_id is None:
                agent_id = new_id()
                execute(connection, """
                    INSERT INTO agents (id, name, description, prompt, updated_at)
                    VALUES (:id, :name, :description, :prompt, :now)
                """, id=agent_id, **params)
            elif not execute(connection, """
                UPDATE agents SET name = :name, description = :description, prompt = :prompt,
                    updated_at = :now WHERE id = :id
            """, id=agent_id, **params).rowcount:
                raise Missing("未找到该 Agent。")
        return agent_id

    def bind(self, workflow_id, agent_id):
        with self.db.write() as connection:
            if not one(connection, "SELECT id FROM agents WHERE id = :id", id=agent_id):
                raise Missing("未找到该 Agent。")
            execute(connection, """
                INSERT INTO workflow_agents (workflow_id, agent_id) VALUES (:workflow, :agent)
                ON CONFLICT (workflow_id) DO UPDATE SET agent_id = excluded.agent_id
            """, workflow=workflow_id, agent=agent_id)
