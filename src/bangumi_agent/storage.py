"""Local SQLite plans and their status history. No credentials or HTTP requests belong here."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4


class PlanError(ValueError):
    pass


SCHEMA_VERSION = 2
STATUSES = ("draft", "approved", "cancelled", "expired", "rejected",
            "executing", "verified", "failed", "unknown", "partial")
# The only allowed moves; see LOG.md#s3-03 and LOG.md#s3-07 for the state diagram.
TRANSITIONS = {
    "draft": {"approved", "cancelled", "expired"},
    "approved": {"cancelled", "expired", "rejected", "executing"},
    "executing": {"verified", "failed", "unknown", "partial"},
    "unknown": {"verified", "failed", "partial"},
}
# What a read-back found for one episode of an executed plan.
ITEM_RESULTS = ("applied", "not_applied", "changed_elsewhere")

PLANS_TABLE = f"""
CREATE TABLE operation_plans_new (
    plan_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL CHECK(account_id > 0),
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL CHECK(expires_at > created_at),
    status TEXT NOT NULL CHECK(status IN ({", ".join(f"'{s}'" for s in STATUSES)})),
    updated_at INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    skipped TEXT NOT NULL DEFAULT '[]'
);
"""
ITEMS_TABLE = f"""
CREATE TABLE plan_items_new (
    plan_id TEXT NOT NULL REFERENCES operation_plans(plan_id),
    subject_id INTEGER NOT NULL CHECK(subject_id > 0),
    episode_id INTEGER NOT NULL CHECK(episode_id > 0),
    subject_name TEXT NOT NULL,
    episode_name TEXT NOT NULL,
    episode_sort TEXT NOT NULL,
    before_type INTEGER NOT NULL CHECK(before_type IN (0, 1, 3)),
    after_type INTEGER NOT NULL CHECK(after_type = 2),
    result TEXT CHECK(result IS NULL OR result IN ({", ".join(f"'{r}'" for r in ITEM_RESULTS)})),
    PRIMARY KEY (plan_id, episode_id)
);
"""
RENAME_NEW_TABLES = """
ALTER TABLE operation_plans_new RENAME TO operation_plans;
ALTER TABLE plan_items_new RENAME TO plan_items;
"""
CREATE_CURRENT = (f"BEGIN;{PLANS_TABLE}{ITEMS_TABLE}{RENAME_NEW_TABLES}"
                  f"PRAGMA user_version = {SCHEMA_VERSION};COMMIT;")


def _migration(updated_at: str, detail: str) -> str:
    """SQLite cannot ALTER a CHECK or a primary key, so copy into new tables, keeping every plan."""
    return f"""
BEGIN;
{PLANS_TABLE}
INSERT INTO operation_plans_new (plan_id, account_id, created_at, expires_at, status, updated_at, detail)
    SELECT plan_id, account_id, created_at, expires_at, status, {updated_at}, {detail} FROM operation_plans;
{ITEMS_TABLE}
INSERT INTO plan_items_new (plan_id, subject_id, episode_id, subject_name, episode_name,
                            episode_sort, before_type, after_type)
    SELECT plan_id, subject_id, episode_id, subject_name, episode_name,
           episode_sort, before_type, after_type FROM plan_items;
DROP TABLE plan_items;
DROP TABLE operation_plans;
{RENAME_NEW_TABLES}
PRAGMA user_version = {SCHEMA_VERSION};
COMMIT;
"""


# Version 0 is the S3-02 layout: status could only be 'draft', no updated_at or detail.
# Version 1 is the S3-03 layout: one episode per plan, no 'partial' status.
MIGRATIONS = {0: _migration("created_at", "''"), 1: _migration("updated_at", "detail")}


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
        if not exists:
            connection.executescript(CREATE_CURRENT)
            return
        # Foreign keys stay off while the tables are swapped.
        connection.executescript(MIGRATIONS[version])
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise PlanError("数据库迁移后外键检查失败")

    def _open_existing(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise PlanError("本地计划数据库不存在，请先创建预览")
        return self._connect()

    def create(self, *, account_id, subject_id, episode_id, subject_name,
               episode_name, episode_sort, before_type, created_at, expires_at) -> str:
        """Atomically save a draft and exactly one proposed episode change."""
        return self.create_many(
            account_id=account_id, subject_id=subject_id, subject_name=subject_name,
            items=[{"episode_id": episode_id, "episode_name": episode_name,
                    "episode_sort": episode_sort, "before_type": before_type}],
            created_at=created_at, expires_at=expires_at,
        )

    def create_many(self, *, account_id, subject_id, subject_name, items, created_at, expires_at,
                    skipped=()) -> str:
        """Atomically save a draft and every proposed change; skipped is only shown to the user."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        plan_id = uuid4().hex
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO operation_plans (plan_id, account_id, created_at, expires_at, "
                    "status, updated_at, skipped) VALUES (?, ?, ?, ?, 'draft', ?, ?)",
                    (plan_id, account_id, created_at, expires_at, created_at,
                     json.dumps(list(skipped), ensure_ascii=False)),
                )
                connection.executemany(
                    "INSERT INTO plan_items (plan_id, subject_id, episode_id, subject_name, "
                    "episode_name, episode_sort, before_type, after_type) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 2)",
                    [(plan_id, subject_id, item["episode_id"], subject_name, item["episode_name"],
                      str(item["episode_sort"]), item["before_type"]) for item in items],
                )
        return plan_id

    def get_plan(self, plan_id, *, account_id) -> dict[str, Any]:
        """Any status, but only for the account that owns the plan.

        items holds every episode. Top-level episode fields repeat the first item, which is
        all a single-episode plan has.
        """
        with closing(self._open_existing()) as connection:
            row = connection.execute("SELECT * FROM operation_plans WHERE plan_id = ?", (plan_id,)).fetchone()
            rows = connection.execute(
                "SELECT subject_id, subject_name, episode_id, episode_name, episode_sort, before_type, "
                "after_type, result FROM plan_items WHERE plan_id = ? ORDER BY rowid", (plan_id,),
            ).fetchall()
        if row is None or not rows:
            raise PlanError("找不到该计划")
        if type(account_id) is not int or row["account_id"] != account_id:
            raise PlanError("计划不属于当前凭据对应的账号")
        return {**dict(rows[0]), **dict(row), "skipped": json.loads(row["skipped"]),
                "items": [dict(item) for item in rows]}

    def get_preview(self, plan_id, *, account_id, now) -> dict[str, Any]:
        """Validate the owner and expiry of a persisted snapshot, not live state."""
        plan = self.get_plan(plan_id, account_id=account_id)
        if now >= plan["expires_at"]:
            raise PlanError("计划已过期；历史记录仍保留，请重新读取网站状态并创建预览")
        if plan["status"] != "draft":
            raise PlanError("该记录不是可预览的草稿")
        return plan

    def transition(self, plan_id, *, account_id, from_status, to_status, now, detail="",
                   item_results=None) -> bool:
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
                if cursor.rowcount == 1 and item_results:
                    connection.executemany(
                        "UPDATE plan_items SET result = ? WHERE plan_id = ? AND episode_id = ?",
                        [(result, plan_id, episode_id) for episode_id, result in item_results.items()],
                    )
        return cursor.rowcount == 1
