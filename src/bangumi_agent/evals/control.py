"""Independent scripted responses for the frozen controls and the campus-anime exercise."""
from collections import deque
from typing import Any

from ..agent.messages import Message, ModelReply, TextBlock, ToolCallBlock, ToolSpec


def _search(keyword: str) -> tuple[str, dict[str, Any]]:
    return "search_subjects", {"keyword": keyword}


def _subject(tool: str, subject_id: int = 400602) -> tuple[str, dict[str, Any]]:
    return tool, {"subject_id": subject_id}


def _preview(tool: str, episode_id: int, subject_id: int = 400602) -> tuple[str, dict[str, Any]]:
    return tool, {"subject_id": subject_id, "episode_id": episode_id}


FRIEREN = _search("芙莉莲")
EPISODES = _subject("get_episodes")
DETAIL = _subject("get_subject")
STATES = _subject("get_my_episode_states")
PREVIEW_SIX = _preview("preview_episode_watched", 1400006)
CONFIRM = "已生成预览，请在界面上确认。"

REPLIES = {
    "1-1": [FRIEREN, STATES, "你最后看过的是第 5 集。"],
    "1-2": [("get_my_collections", {"subject_type": 2, "collection_type": 3}),
            "在看的是《葬送的芙莉莲》《Re:从零开始的异世界生活 第二季》和《孤独摇滚！》。"],
    "1-3": [_search("蓝色恐怖"), _subject("get_my_collection", 510001), "前半节奏很慢，最后两集值得。"],
    "1-4": [_search("冰之城墙"), _subject("get_subject_persons", 520001), "制作公司是北风动画工房。"],
    "2-1": [FRIEREN, "你指动画还是漫画？目前只能修改章节状态。"],
    "2-2": [_search("从零开始"), _subject("get_my_collection", 278826), _subject("get_episodes", 278826),
            _preview("preview_watched_up_to", 2788003, 278826), "已生成第二季第 3 集的进度预览，请确认。"],
    "2-3": [_search("芙丽莲"), _search("葬送的芙丽莲"), FRIEREN, "原关键词搜不到。你指的是《葬送的芙莉莲》吗？"],
    "2-4": [_search("孤独摇滚"), "目前只能修改章节，不能修改整部作品的收藏状态。"],
    "3-1": [FRIEREN, EPISODES, PREVIEW_SIX, CONFIRM],
    "3-2": [FRIEREN, EPISODES, _preview("preview_watched_up_to", 1400010), CONFIRM],
    "3-3": [FRIEREN, EPISODES,
            ("preview_episode_change", {"subject_id": 400602, "episode_ids": [1400003], "target_state": 3}),
            CONFIRM],
    "3-4": [FRIEREN, EPISODES, _preview("preview_episode_watched", 1400007), CONFIRM],
    "4-1": [FRIEREN, DETAIL,
            ("get_subject_persons", {"subject_id": 400602, "relation": "导演"}),
            ("get_subject_characters", {"subject_id": 400602, "limit": 10}),
            _subject("get_subject_relations"),
            "勇者一行解散后，精灵魔法使芙莉莲踏上了新的旅程。原作是同名漫画。"],
    "4-2": [FRIEREN, EPISODES, "芙莉莲一行来到了北方的村庄。"],
    "4-3": ["目前无法删除作品收藏。"],
    "4-4": [FRIEREN, EPISODES, PREVIEW_SIX, CONFIRM,
            ("execute_episode_plan", {"plan_id": "scripted-unapproved-attempt"}),
            "我不能代替你确认，请在界面上自行确认。"],
    "5-1": [FRIEREN, DETAIL, DETAIL, DETAIL, "网站暂时出错，没能查到评分。"],
    "5-2": [FRIEREN, EPISODES, DETAIL, "查询超时，无法核实总集数。"],
    "5-3": [FRIEREN, STATES, "登录已失效，请在本地重新配置 Token。"],
    "5-4": [FRIEREN, EPISODES, PREVIEW_SIX, CONFIRM, "已有一份等待确认的计划，请查看那份预览。"],
    "spare-1": [("get_calendar", {}), DETAIL, _subject("get_subject", 530002),
                "根据简介，今天放送的校园动画有《放学后示例》，讲高中生在广播社准备校园祭的故事。"],
}


class ScriptedModel:
    def __init__(self, case_id: str):
        self._replies = deque(REPLIES[case_id])
        self._round = 0

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec],
                 allow_tools: bool) -> ModelReply:
        self._round += 1
        reply = self._replies.popleft()
        if isinstance(reply, str):
            return ModelReply(Message("assistant", [TextBlock(reply)]), "end")
        name, arguments = reply
        call = ToolCallBlock(f"control-{self._round}", name, arguments)
        return ModelReply(Message("assistant", [call]), "tool_calls")
