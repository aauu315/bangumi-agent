"""Local SQLite plans and their status history. No credentials or HTTP requests belong here."""
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4


class PlanError(ValueError):
    pass


SCHEMA_VERSION = 1
STATUSES = ("draft", "approved", "cancelled", "expired", "rejected",
            "executing", "verified", "failed", "unknown")
# The only allowed moves; see LOG.md#s3-03 for the state diagram.
TRANSITIONS = {
    "draft": {"approved", "cancelled", "expired"},
    "approved": {"cancelled", "expired", "rejected", "executing"},
    "executing": {"verified", "failed", "unknown"},
    "unknown": {"verified", "failed"},
}

PLANS_TABLE = f"""
CREATE TABLE operation_plans (
    plan_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL CHECK(account_id > 0),
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL CHECK(expires_at > created_at),
    status TEXT NOT NULL CHECK(status IN ({", ".join(f"'{s}'" for s in STATUSES)})),
    updated_at INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""
ITEMS_TABLE = """
CREATE TABLE plan_items (
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
# Version 0 is the S3-02 layout: status could only be 'draft'. SQLite cannot ALTER a
# CHECK, so copy into a new table and rename it, keeping every existing draft.
MIGRATE_0_TO_1 = f"""
BEGIN;
{PLANS_TABLE.replace("CREATE TABLE operation_plans", "CREATE TABLE operation_plans_v1")}
INSERT INTO operation_plans_v1 (plan_id, account_id, created_at, expires_at, status, updated_at)
    SELECT plan_id, account_id, created_at, expires_at, status, created_at FROM operation_plans;
DROP TABLE operation_plans;
ALTER TABLE operation_plans_v1 RENAME TO operation_plans;
PRAGMA user_version = 1;
COMMIT;
"""

PLAN_QUERY = (
    "SELECT p.*, i.subject_id, i.episode_id, i.subject_name, i.episode_name, "
    "i.episode_sort, i.before_type, i.after_type "
    "FROM operation_plans p JOIN plan_items i USING(plan_id) WHERE p.plan_id = ?"
)


class PlanStore:
    def __init__(self, path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        self._ensure_schema(connection)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version > SCHEMA_VERSION:
            raise PlanError("本地数据库版本比当前程序新，请更新程序")
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'operation_plans'"
        ).fetchone()
        if exists:
            # Foreign keys stay off while the parent table is swapped.
            connection.executescript(MIGRATE_0_TO_1)
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise PlanError("数据库迁移后外键检查失败")
        else:
            connection.executescript(
                f"BEGIN;{PLANS_TABLE}{ITEMS_TABLE}PRAGMA user_version = {SCHEMA_VERSION};COMMIT;"
            )

    def _open_existing(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise PlanError("本地计划数据库不存在，请先创建预览")
        return self._connect()

    def create(self, *, account_id, subject_id, episode_id, subject_name,
               episode_name, episode_sort, before_type, created_at, expires_at) -> str:
        """Atomically save a draft and exactly one proposed episode change."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        plan_id = uuid4().hex
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO operation_plans (plan_id, account_id, created_at, expires_at, "
                    "status, updated_at) VALUES (?, ?, ?, ?, 'draft', ?)",
                    (plan_id, account_id, created_at, expires_at, created_at),
                )
                connection.execute(
                    "INSERT INTO plan_items VALUES (?, ?, ?, ?, ?, ?, ?, 2)",
                    (plan_id, subject_id, episode_id, subject_name, episode_name,
                     str(episode_sort), before_type),
                )
        return plan_id

    def get_plan(self, plan_id, *, account_id) -> dict[str, Any]:
        """Any status, but only for the account that owns the plan."""
        with closing(self._open_existing()) as connection:
            row = connection.execute(PLAN_QUERY, (plan_id,)).fetchone()
        if row is None:
            raise PlanError("找不到该计划")
        if type(account_id) is not int or row["account_id"] != account_id:
            raise PlanError("计划不属于当前凭据对应的账号")
        return dict(row)

    def get_preview(self, plan_id, *, account_id, now) -> dict[str, Any]:
        """Validate the owner and expiry of a persisted snapshot, not live state."""
        plan = self.get_plan(plan_id, account_id=account_id)
        if now >= plan["expires_at"]:
            raise PlanError("计划已过期；历史记录仍保留，请重新读取网站状态并创建预览")
        if plan["status"] != "draft":
            raise PlanError("该记录不是可预览的草稿")
        return plan

    def transition(self, plan_id, *, account_id, from_status, to_status, now, detail="") -> bool:
        """Move one plan only if it is still in from_status.

        The WHERE clause makes this a compare-and-set: when two callers race, exactly one
        UPDATE matches and the other gets False. That is the single-use lock for execution.
        """
        if to_status not in TRANSITIONS.get(from_status, set()):
            raise PlanError(f"不允许的状态转换：{from_status} → {to_status}")
        with closing(self._open_existing()) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE operation_plans SET status = ?, updated_at = ?, detail = ? "
                    "WHERE plan_id = ? AND account_id = ? AND status = ?",
                    (to_status, now, detail, plan_id, account_id, from_status),
                )
        return cursor.rowcount == 1
