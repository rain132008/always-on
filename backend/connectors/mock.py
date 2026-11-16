import json
import sqlite3
from contextlib import closing

from ..database import new_id
from ..outbox import Reconciled


class MockRemote:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS received (
                    business_key TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    remote_id TEXT NOT NULL
                )
            """)
            connection.commit()

    def deliver(self, kind, business_key, payload):
        remote_id = new_id()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("""
                INSERT INTO received VALUES (?, ?, ?, ?)
                ON CONFLICT (business_key) DO NOTHING
            """, (business_key, kind, json.dumps(payload, ensure_ascii=False), remote_id))
            return self.lookup(connection, business_key)[0]

    def reconcile(self, kind, business_key, payload):
        with closing(sqlite3.connect(self.path)) as connection:
            found = self.lookup(connection, business_key)
        return Reconciled("found", found[0]) if found else Reconciled("absent")

    @staticmethod
    def lookup(connection, business_key):
        return connection.execute(
            "SELECT remote_id FROM received WHERE business_key = ?", (business_key,),
        ).fetchone()
