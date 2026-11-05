"""Prepare episode -> watched plans. This module cannot execute them."""
import time

from .account_client import AccountReadError, _positive
from .storage import PlanError

SKIPPED_STATES = {2: "已看过", 3: "已抛弃"}


def _shown_number(episode):
    """The number the user knows it by: ep restarts each season. SP and OP have ep 0, so use sort."""
    return episode.get("ep") or episode["sort"]


class ProgressPreview:
    def __init__(self, client, store, clock=time.time):
        self.client, self.store, self.clock = client, store, clock

    def create(self, subject_id, episode_id, ttl_seconds=900):
        _positive(subject_id, "subject_id")
        _positive(episode_id, "episode_id")
        self._check_ttl(ttl_seconds)
        me = self.client.get_me()
        # Both reads must succeed before anything is saved locally.
        subject = self.client.get_my_collection(subject_id)
        episode = self.client.get_my_episode_state(episode_id)
        self._require_active_collection(subject, subject_id)
        if episode["subject_id"] != subject_id or episode["episode_id"] != episode_id:
            raise PlanError("所选章节不属于目标条目，已拒绝创建计划")
        if episode["type"] == 2:
            raise PlanError("该章节已是看过，无需创建相同修改计划")
        now = int(self.clock())
        plan_id = self.store.create(
            account_id=me["id"], subject_id=subject_id, episode_id=episode_id,
            subject_name=subject["name"], episode_name=episode["name"],
            episode_sort=_shown_number(episode), before_type=episode["type"],
            created_at=now, expires_at=now + ttl_seconds,
        )
        return self.store.get_preview(plan_id, account_id=me["id"], now=now)

    def create_up_to(self, subject_id, episode_id, ttl_seconds=900):
        """Plan "watched up to this episode": main episodes only, like the website's button.

        Unwatched and wish-list episodes up to the target change; watched and dropped ones stay.
        """
        _positive(subject_id, "subject_id")
        _positive(episode_id, "episode_id")
        self._check_ttl(ttl_seconds)
        me = self.client.get_me()
        subject = self.client.get_my_collection(subject_id)
        self._require_active_collection(subject, subject_id)
        pages = self.client.get_all_my_episode_states(subject_id, limit=50, max_pages=20, episode_type=0)
        if not pages["complete"]:
            raise PlanError("章节列表没有读完，已拒绝创建计划，避免漏掉或选错章节")
        target = next((row for row in pages["data"] if row["episode_id"] == episode_id), None)
        if target is None:
            raise PlanError("所选章节不是该条目的本篇章节，已拒绝创建计划")
        items, skipped = [], []
        for row in pages["data"]:
            if row["sort"] > target["sort"]:
                continue
            name = row["name_cn"] or row["name"] or ""
            if row["type"] in SKIPPED_STATES:
                skipped.append({"episode_sort": _shown_number(row), "episode_name": name,
                                "reason": SKIPPED_STATES[row["type"]]})
            else:
                items.append({"episode_id": row["episode_id"], "episode_name": name,
                              "episode_sort": _shown_number(row), "before_type": row["type"]})
        if not items:
            raise PlanError("这些章节都已看过或已抛弃，无需创建修改计划")
        now = int(self.clock())
        plan_id = self.store.create_many(
            account_id=me["id"], subject_id=subject_id, subject_name=subject["name"], items=items,
            skipped=skipped, created_at=now, expires_at=now + ttl_seconds,
        )
        return self.store.get_preview(plan_id, account_id=me["id"], now=now)

    def create_change(self, subject_id, episode_ids, target_state, ttl_seconds=900):
        """Plan "change these episodes to target_state". The user writes this (S3-08)."""
        _positive(subject_id,"subject_id")
        if type(target_state) is not int or target_state > 3 or target_state < 0:
            raise ValueError("目标状态不合法")
        if not isinstance(episode_ids,list):
            raise ValueError("章节以列表的形式传输")
        if len(episode_ids) > 50 or len(episode_ids) == 0:
            raise ValueError("一次最多操作50个章节")
        if len(set(episode_ids)) != len(episode_ids):
            raise PlanError("episode_ids 有重复")
        for episode_id in episode_ids:
            _positive(episode_id,"episode_id")
        self._check_ttl(ttl_seconds)
        me = self.client.get_me()
        subject = self.client.get_my_collection(subject_id)
        self._require_active_collection(subject, subject_id)
        pages = self.client.get_all_my_episode_states(subject_id, limit=50, max_pages=20, episode_type=None)
        if not pages["complete"]:
            raise PlanError("章节列表没有读完，已拒绝创建计划，避免漏掉或选错章节")
        items, skipped = [], []
        for episode_id in episode_ids:
            target = next((row for row in pages["data"] if row["episode_id"] == episode_id), None)
            if target is None:
                raise PlanError("所选章节不属于该条目，已拒绝创建计划")
            name = target["name_cn"] or target["name"] or ""
            if target["type"] == target_state:
                skipped.append({"episode_sort": _shown_number(target), "episode_name": name,
                                "reason": "已是目标状态"})
            else:
                items.append({"episode_id": target["episode_id"], "episode_name": name,
                                "episode_sort": _shown_number(target), "before_type": target["type"]})
        if not items:
            raise PlanError("这些章节都已是目标状态，无需创建修改计划")
        now = int(self.clock())
        plan_id = self.store.create_many(
            account_id=me["id"], subject_id=subject_id, subject_name=subject["name"], items=items,
            skipped=skipped, created_at=now, expires_at=now + ttl_seconds, after_type=target_state
        )
        return self.store.get_preview(plan_id, account_id=me["id"], now=now)  

    @staticmethod
    def _check_ttl(ttl_seconds):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise PlanError("预览有效期必须是1到3600秒，默认900秒")

    @staticmethod
    def _require_active_collection(subject, subject_id):
        if subject["subject_id"] != subject_id or subject["type"] not in (1, 2, 3, 4, 5):
            raise AccountReadError("目标条目没有有效收藏记录")
        if subject["type"] == 5:
            raise PlanError("目标条目已抛弃，请先更改为在看")

    def show(self, plan_id):
        return self.store.get_preview(
            plan_id, account_id=self.client.get_me()["id"], now=int(self.clock()),
        )
