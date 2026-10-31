"""Local SQLite preview snapshots. No credentials or HTTP requests belong here."""
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4


class PlanError(ValueError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS operation_plans (
    plan_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL CHECK(account_id > 0),
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL CHECK(expires_at > created_at),
    status TEXT NOT NULL CHECK(status = 'draft')
);
CREATE TABLE IF NOT EXISTS plan_items (
    plan_id TEXT PRIMARY KEY REFERENCES operation_plans(plan_id),
    subject_id INTEGER NOT NULL CHECK(subject_id > 0),
    episode_id INTEGER NOT NULL CHECK(episode_id > 0),
    subject_name TEXT NOT NULL,
    episode_name TEXT NOT NULL,
    episode_sort TEXT NOT NULL,
    before_type INTEGER NOT NULL CHECK(before_type IN (0, 1, 3)),
    after_type INTEGER NOT NULL CHECK(after_type = 2)
);
"""


class PlanStore:
    def __init__(self, path):
        self.path = Path(path)

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def create(self, *, account_id, subject_id, episode_id, subject_name,
               episode_name, episode_sort, before_type, created_at, expires_at):
        """Atomically save a draft and exactly one proposed episode change."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        plan_id = uuid4().hex
        with closing(self._connect()) as connection:
            connection.executescript(SCHEMA)
            with connection:
                connection.execute(
                    "INSERT INTO operation_plans VALUES (?, ?, ?, ?, 'draft')",
                    (plan_id, account_id, created_at, expires_at),
                )
                connection.execute(
                    "INSERT INTO plan_items VALUES (?, ?, ?, ?, ?, ?, ?, 2)",
                    (plan_id, subject_id, episode_id, subject_name, episode_name,
                     str(episode_sort), before_type),
                )
        return plan_id

    def get_preview(self, plan_id, *, account_id, now):
        """Validate the owner and expiry of a persisted snapshot, not live state."""
        if not self.path.is_file():
            raise PlanError("本地计划数据库不存在，请先创建预览")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT p.*, i.subject_id, i.episode_id, i.subject_name, "
                "i.episode_name, i.episode_sort, i.before_type, i.after_type "
                "FROM operation_plans p JOIN plan_items i USING(plan_id) WHERE p.plan_id = ?",
                (plan_id,),
            ).fetchone()
        if row is None:
            raise PlanError("找不到该计划")
        if type(account_id) is not int or row["account_id"] != account_id:
            raise PlanError("计划不属于当前凭据对应的账号")
        if now >= row["expires_at"]:
            raise PlanError("计划已过期；历史记录仍保留，请重新读取网站状态并创建预览")
        if row["status"] != "draft":
            raise PlanError("该记录不是可预览的草稿")
        return dict(row)
