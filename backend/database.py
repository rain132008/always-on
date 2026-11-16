from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL


def new_id():
    return str(uuid4())


def execute(connection, statement, **params):
    return connection.execute(text(statement), params)


def one(connection, statement, **params):
    row = execute(connection, statement, **params).mappings().first()
    return dict(row) if row is not None else None


def rows(connection, statement, **params):
    return [dict(row) for row in execute(connection, statement, **params).mappings()]


class Database:
    def __init__(self, path: Path):
        self.engine = create_engine(URL.create("sqlite", database=str(path)))
        event.listen(self.engine, "connect", self.configure_sqlite)

    @staticmethod
    def configure_sqlite(connection, _record):
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")

    def migrate(self):
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode = WAL")
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.write() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    @contextmanager
    def write(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            yield connection
