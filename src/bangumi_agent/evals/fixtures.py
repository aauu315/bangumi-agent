"""Run evaluation tools against frozen data and a temporary plan store."""
import json
from typing import Any

from ..account_client import AccountReadError
from ..agent.messages import ToolError, ToolSpec
from ..progress import ProgressPreview
from .scoring import PREVIEW_TOOLS, ToolCall


def _matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, str):
        return isinstance(actual, str) and expected in actual
    return type(actual) is type(expected) and actual == expected


class FixtureMissing(ToolError):
    pass


class FixtureData:
    def __init__(self, rules: list[dict[str, Any]]):
        self.rules = rules
        self.misses: list[ToolCall] = []

    def lookup(self, name: str, arguments: dict[str, Any]) -> Any:
        for rule in self.rules:
            if rule["tool"] == name and all(
                _matches(value, arguments.get(key)) for key, value in rule.get("when", {}).items()
            ):
                if "error" in rule:
                    raise ToolError(rule["error"])
                return rule["result"]
        self.misses.append(ToolCall(name, arguments))
        raise FixtureMissing("评测夹具没有这次调用的数据")

    def get_me(self) -> dict[str, Any]:
        return {"id": 42}

    def get_my_collection(self, subject_id: int) -> dict[str, Any]:
        return self.lookup("get_my_collection", {"subject_id": subject_id})

    def get_my_episode_state(self, episode_id: int) -> dict[str, Any]:
        # Find ownership in the fixture, without deriving it from the expected preview.
        for rule in self.rules:
            if rule["tool"] != "get_my_episode_states" or "result" not in rule:
                continue
            subject_id = rule["when"]["subject_id"]
            for row in rule["result"]["data"]:
                if row["episode_id"] == episode_id:
                    return {**row, "subject_id": subject_id, "name": row["name_cn"] or row["name"]}
        raise AccountReadError("模拟账号没有该章节的数据")

    def get_all_my_episode_states(self, subject_id: int, limit: int = 50, max_pages: int = 20,
                                  episode_type: int | None = None) -> dict[str, Any]:
        result = self.lookup("get_my_episode_states", {"subject_id": subject_id})
        if episode_type is None:
            return result
        return {**result, "data": [row for row in result["data"] if row["episode_type"] == episode_type]}


class FixtureTools:
    def __init__(self, specs: list[ToolSpec], data: FixtureData, preview: ProgressPreview):
        self._specs = specs
        self.data, self.preview = data, preview

    def specs(self) -> list[ToolSpec]:
        return self._specs

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        if name not in PREVIEW_TOOLS:
            return json.dumps(self.data.lookup(name, arguments), ensure_ascii=False)
        try:
            if name == "preview_episode_change":
                plan = self.preview.create_change(arguments["subject_id"], arguments["episode_ids"],
                                                  arguments["target_state"])
            elif name == "preview_watched_up_to":
                plan = self.preview.create_up_to(arguments["subject_id"], arguments["episode_id"])
            else:
                plan = self.preview.create(arguments["subject_id"], arguments["episode_id"])
        except KeyError as error:
            raise ToolError(f"缺少预览参数 {error}") from None
        except (ValueError, AccountReadError) as error:
            raise ToolError(str(error)) from None
        return json.dumps({**plan, "remote_write_performed": False, "snapshot_only": True},
                          ensure_ascii=False)
