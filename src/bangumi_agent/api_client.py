"""Anonymous, read-only Bangumi HTTP requests. No MCP dependency lives here."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://api.bgm.tv"
USER_AGENT = "aauu315-bangumi-agent/0.1 (https://github.com/aauu315)"
TIMEOUT_SECONDS = 20


class BangumiApiError(Exception):
    """A request failed or Bangumi returned an unexpected response."""


def _request_json(path: str, *, body: dict | None = None) -> dict:
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        BASE_URL + path,
        data=payload,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise BangumiApiError(f"Bangumi 返回 HTTP {response.status}")
            result = json.load(response)
    except HTTPError as exc:
        raise BangumiApiError(f"Bangumi 返回 HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise BangumiApiError("连接 Bangumi 失败或超时") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BangumiApiError("Bangumi 返回的内容不是有效 JSON") from exc
    if not isinstance(result, dict):
        raise BangumiApiError("Bangumi 返回的数据格式不符合预期")
    return result


def search_subjects(keyword: str, limit: int = 10, offset: int = 0) -> dict:
    """按关键词搜索公开的 Bangumi 条目，返回一页候选。

    适用于用户提供作品名称或关键词、尚不知道条目 ID 的情况。
    keyword：搜索关键词，不能是空白。
    limit：本次最多返回多少条，范围 1～20，默认 10。
    offset：跳过的记录数，必须非负，默认 0；不是页码。

    返回 total、limit、offset 和 data。
    data 中每项包含 id、name、name_cn、type、url。
    搜索可能返回同名作品及不同类型条目，应结合名称和类型
    确认目标；无法确定时询问用户，再用 get_subject 查询详情。
    本工具只读，无需账户凭据，不修改收藏。
    """
    if not isinstance(keyword, str) or not keyword.strip():
        raise ValueError("keyword 不能为空")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("limit 必须是 1 到 20 的整数")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset 必须是非负整数")
    path = "/v0/search/subjects?" + urlencode({"limit": limit, "offset": offset})
    result = _request_json(path, body={"keyword": keyword.strip()})
    data = result.get("data")
    if not isinstance(data, list) or type(result.get("total")) is not int:
        raise BangumiApiError("搜索响应缺少 data 或 total")
    subjects = []
    for item in data:
        if not isinstance(item, dict) or type(item.get("id")) is not int:
            raise BangumiApiError("搜索响应包含无效条目")
        subjects.append({
            "id": item["id"],
            "name": item.get("name"),
            "name_cn": item.get("name_cn"),
            "type": item.get("type"),
            "url": f"https://bgm.tv/subject/{item['id']}",
        })
    return {"total": result["total"], "limit": limit, "offset": offset, "data": subjects}


def get_subject(subject_id: int) -> dict:
    """按 Bangumi 条目 ID 查询公开详情。

    subject_id：正整数条目 ID，应来自搜索结果或明确的条目链接，
    不能使用章节 ID、集数或搜索结果的数组下标代替。

    返回名称、类型、简介、日期、集数、评分、图片和条目链接等字段；
    部分字段可能为空。
    eps 和 total_episodes 是作品信息，不是本人的观看进度。
    本工具只读，无需账户凭据，不查询或修改个人收藏。
    条目不存在或请求失败时返回工具错误，不能当作查询成功。
    """
    if type(subject_id) is not int or subject_id <= 0:
        raise ValueError("subject_id 必须是正整数")
    item = _request_json(f"/v0/subjects/{subject_id}")
    if item.get("id") != subject_id or not isinstance(item.get("name"), str) or not item["name"]:
        raise BangumiApiError("详情响应中的条目 ID 或名称不符合预期")
    fields = ("id", "name", "name_cn", "type", "summary", "date", "eps", "total_episodes", "rating", "rank", "images")
    return {**{key: item.get(key) for key in fields}, "url": f"https://bgm.tv/subject/{subject_id}"}


def get_episodes(subject_id: int, limit: int = 40, offset: int = 0, episode_type:int|None = None) -> dict:
    if type(subject_id) is not int or subject_id <= 0:
                raise ValueError("subject_id 必须是非负整数")
    if type(limit) is not int or not 1 <= limit <= 40:
            raise ValueError("limit 必须是 1 到 40 的整数")
    if type(offset) is not int or offset < 0:
            raise ValueError("offset 必须是非负整数")
    params = {
    "subject_id": subject_id,
    "limit": limit,
    "offset": offset,
    }

    if episode_type is not None:
        if type(episode_type) is not int or episode_type not in range(7):
            raise ValueError("episode_type 必须是 0 到 6 的整数")
        params["type"] = episode_type

    path = "/v0/episodes?" + urlencode(params)
    result = _request_json(path)
    data = result.get("data")
    total = result.get("total")
    if not isinstance(data, list):
            raise BangumiApiError("章节响应缺少 data 或 total")
    if not isinstance(total, int) or total < 0:
            raise BangumiApiError("total不存在或小于零")
    for item in data:
            if not isinstance(item, dict) or type(item.get("id")) is not int:
                raise BangumiApiError("搜索响应包含无效条目")
    return {"total": total, "limit": limit, "offset": offset, "data": data}

def get_all_episodes(
    subject_id: int,
    episode_type: int | None = None,
    limit: int = 20,
    max_pages: int = 5,
) -> dict:
    if type(max_pages) is not int or max_pages <= 0:
        raise ValueError("max_pages 必须是正整数")

    offset = 0
    items = []
    total = None
    complete = False
    stop_reason = "page_limit"
    error = None

    for _ in range(max_pages):
        try:
            result = get_episodes(
                subject_id,
                limit=limit,
                offset=offset,
                episode_type=episode_type,
            )
        except BangumiApiError as exc:
            stop_reason = "request_failed"
            error = str(exc)
            break

        page_total = result["total"]
        current = result["data"]
        total = page_total

        items.extend(current)
        offset += len(current)

        if offset >= page_total:
            complete = True
            stop_reason = "completed"
            break

        if not current:
            stop_reason = "unexpected_empty_page"
            break

    return {
        "data": items,
        "total": total,
        "complete": complete,
        "next_offset": None if complete else offset,
        "stop_reason": stop_reason,
        "error": error,
    }

