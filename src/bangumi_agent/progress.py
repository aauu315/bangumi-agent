"""Prepare a single episode -> watched plan. This module cannot execute it."""
import time

from .account_client import AccountReadError, _positive
from .storage import PlanError


class ProgressPreview:
    def __init__(self, client, store, clock=time.time):
        self.client, self.store, self.clock = client, store, clock

    def create(self, subject_id, episode_id, ttl_seconds=900):
        _positive(subject_id, "subject_id")
        _positive(episode_id, "episode_id")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise PlanError("预览有效期必须是1到3600秒，默认900秒")
        me = self.client.get_me()
        # Both reads must succeed before anything is saved locally.
        subject = self.client.get_my_collection(subject_id)
        episode = self.client.get_my_episode_state(episode_id)
        if subject["subject_id"] != subject_id or subject["type"] not in (1, 2, 3, 4, 5):
            raise AccountReadError("目标条目没有有效收藏记录")
        if episode["subject_id"] != subject_id or episode["episode_id"] != episode_id:
            raise PlanError("所选章节不属于目标条目，已拒绝创建计划")
        if episode["type"] == 2:
            raise PlanError("该章节已是看过，无需创建相同修改计划")
        now = int(self.clock())
        plan_id = self.store.create(
            account_id=me["id"], subject_id=subject_id, episode_id=episode_id,
            subject_name=subject["name"], episode_name=episode["name"],
            episode_sort=episode["sort"], before_type=episode["type"],
            created_at=now, expires_at=now + ttl_seconds,
        )
        return self.store.get_preview(plan_id, account_id=me["id"], now=now)

    def show(self, plan_id):
        return self.store.get_preview(
            plan_id, account_id=self.client.get_me()["id"], now=int(self.clock()),
        )
