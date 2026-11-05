"""Authenticated GET-only account reads; no writes, cookies or MCP dependency."""
import json
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .api_client import BASE_URL, USER_AGENT, TIMEOUT_SECONDS
from .settings import Settings

MAX_BODY = 4 * 1024 * 1024


class AccountReadError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AccountReadError("账号接口发生重定向，已停止请求")


def _http_error(status):
    if 500 <= status < 600:
        return AccountReadError(f"Bangumi HTTP {status}：服务器暂时出错，请稍后重试", status=status)
    hint = {
        401: "Token无效或已过期",
        403: "当前凭据无权访问",
        404: "资源不存在、未收藏或当前不可见，不能仅凭404判定资源已删除",
        429: "请求受限，请稍后自行重试",
    }.get(status, "账号接口请求未成功")
    return AccountReadError(f"Bangumi HTTP {status}：{hint}", status=status)


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name}必须是正整数")


def _pagination(limit, offset):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit必须为1到50的整数（本客户端上限）")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset必须为非负整数")


class AccountClient:
    def __init__(self, settings: Settings):
        self.__token = settings.require_token()
        self.__identity: dict[str, Any] | None = None

    def _redact(self, value):
        if isinstance(value, str):
            return value.replace(self.__token, "[REDACTED]")
        if isinstance(value, list):
            return [self._redact(v) for v in value]
        if isinstance(value, dict):
            return {k: self._redact(v) for k, v in value.items()}
        return value

    def _get(self, path):
        # Internal paths only. All public methods below are GET-only.
        if not path.startswith("/v0/") or "#" in path:
            raise ValueError("无效账号接口路径")
        request = Request(BASE_URL + path, method="GET", headers={
            "Authorization": "Bearer " + self.__token,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        try:
            with build_opener(_NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise _http_error(response.status)
                raw = response.read(MAX_BODY + 1)
                if len(raw) > MAX_BODY:
                    raise AccountReadError("账号响应超过大小上限")
                data = json.loads(raw)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            raise _http_error(status) from None
        except (URLError, TimeoutError, OSError):
            raise AccountReadError("连接Bangumi失败或超时；未自动重试") from None
        except (UnicodeError, ValueError):
            raise AccountReadError("账号接口未返回有效JSON") from None
        if not isinstance(data, dict):
            raise AccountReadError("账号响应必须是JSON对象")
        return data

    def get_me(self) -> dict[str, Any]:
        """Bind this client's identity from the current token, not a supplied username."""
        if self.__identity is None:
            data = self._get("/v0/me")
            uid, username = data.get("id"), data.get("username")
            if type(uid) is not int or uid <= 0 or not isinstance(username, str) or not username:
                raise AccountReadError("身份响应缺少有效ID或用户名")
            if self.__token in username:
                raise AccountReadError("身份响应异常，已停止后续读取")
            # Do not return email, registration details or a raw server payload.
            # username was checked above, so only nickname still needs redacting.
            self.__identity = {"id": uid, "username": username,
                               "nickname": self._redact(data.get("nickname", ""))}
        return dict(self.__identity)

    def _page(self, data, limit, offset, convert):
        total, rows = data.get("total"), data.get("data")
        if type(total) is not int or total < 0 or not isinstance(rows, list) or len(rows) > limit:
            raise AccountReadError("分页响应缺少有效total/data，或超过请求上限")
        if data.get("offset", offset) != offset or data.get("limit", limit) != limit:
            raise AccountReadError("分页响应与请求的limit/offset不一致")
        items = [convert(row) for row in rows]
        next_offset = offset + len(items)
        complete = next_offset >= total
        if not items and not complete:
            raise AccountReadError("分页提前返回空页，不能当作已读取全部数据")
        return {"total": total, "limit": limit, "offset": offset,
                "data": self._redact(items), "complete": complete,
                "next_offset": None if complete else next_offset}

    def get_my_collections(self, limit=20, offset=0, subject_type=2, collection_type=3,
                           include_comment=False):
        """One page of this token owner's collections; type=3 means watching."""
        _pagination(limit, offset)
        if type(subject_type) is not int or subject_type not in (1, 2, 3, 4, 6):
            raise ValueError("subject_type不是有效条目类型")
        if type(collection_type) is not int or collection_type not in (1, 2, 3, 4, 5):
            raise ValueError("collection_type必须是1到5")
        identity = self.get_me()
        username = quote(identity["username"], safe="")
        query = urlencode({"subject_type": subject_type, "type": collection_type,
                           "limit": limit, "offset": offset})
        data = self._get(f"/v0/users/{username}/collections?{query}")

        def convert(row):
            if not isinstance(row, dict):
                raise AccountReadError("收藏列表包含无效记录")
            sid = row.get("subject_id")
            if type(sid) is not int or sid <= 0:
                raise AccountReadError("收藏记录缺少条目ID")
            if row.get("type") != collection_type or row.get("subject_type") != subject_type:
                raise AccountReadError("收藏记录类型与请求筛选不一致")
            subject = row.get("subject")
            if subject is not None and not isinstance(subject, dict):
                raise AccountReadError("收藏条目信息格式错误")
            subject = subject or {}
            if subject.get("id", sid) != sid:
                raise AccountReadError("收藏条目ID不一致")
            item = {"subject_id": sid, "subject_type": subject_type, "type": collection_type,
                    "name": subject.get("name"), "name_cn": subject.get("name_cn"),
                    "ep_status": row.get("ep_status"), "vol_status": row.get("vol_status"),
                    "url": f"https://bgm.tv/subject/{sid}"}
            if include_comment:
                item.update(comment=row.get("comment"), rate=row.get("rate"),
                            tags=row.get("tags"), private=row.get("private"))
            return item

        return self._page(data, limit, offset, convert)

    def get_my_collection(self, subject_id) -> dict[str, Any]:
        """Read one existing subject collection belonging to the token owner."""
        _positive(subject_id, "subject_id")
        username = quote(self.get_me()["username"], safe="")
        row = self._get(f"/v0/users/{username}/collections/{subject_id}")
        if (type(row.get("subject_id")) is not int or row["subject_id"] != subject_id
                or type(row.get("type")) is not int or row["type"] not in (1, 2, 3, 4, 5)):
            raise AccountReadError("未获得目标条目的有效收藏记录")
        subject = row.get("subject") or {}
        if not isinstance(subject, dict) or subject.get("id", subject_id) != subject_id:
            raise AccountReadError("收藏条目ID不一致")
        return cast(dict[str, Any], self._redact({
            "subject_id": subject_id, "type": row["type"],
            "name": subject.get("name_cn") or subject.get("name") or "",
            "ep_status": row.get("ep_status"), "vol_status": row.get("vol_status"),
            "comment": row.get("comment"), "rate": row.get("rate"),
            "tags": row.get("tags"), "private": row.get("private"),
            "url": f"https://bgm.tv/subject/{subject_id}",
        }))

    def get_my_episode_state(self, episode_id) -> dict[str, Any]:
        """Read one episode and its subject ID, without changing its state."""
        _positive(episode_id, "episode_id")
        self.get_me()
        row = self._get(f"/v0/users/-/collections/-/episodes/{episode_id}")
        episode, state = row.get("episode"), row.get("type")
        if (not isinstance(episode, dict) or type(episode.get("id")) is not int
                or episode["id"] != episode_id or type(episode.get("subject_id")) is not int
                or episode["subject_id"] <= 0 or type(state) is not int or state not in (0, 1, 2, 3)):
            raise AccountReadError("章节身份、所属条目或收藏状态无效")
        return {"episode_id": episode_id, "subject_id": episode["subject_id"],
                "type": state, "sort": episode.get("sort"),
                "name": self._redact(episode.get("name_cn") or episode.get("name") or "")}

    def get_my_episode_states(self, subject_id, limit=40, offset=0, episode_type=None,
                              include_summary=False):
        """One page of actual episode IDs and this token owner's collection states."""
        _positive(subject_id, "subject_id")
        _pagination(limit, offset)
        if episode_type is not None and (type(episode_type) is not int or episode_type not in range(7)):
            raise ValueError("episode_type必须是0到6的整数")
        self.get_me()
        params = {"limit": limit, "offset": offset}
        if episode_type is not None:
            params["episode_type"] = episode_type
        query = urlencode(params)
        data = self._get(f"/v0/users/-/collections/{subject_id}/episodes?{query}")

        def convert(row):
            if not isinstance(row, dict) or not isinstance(row.get("episode"), dict):
                raise AccountReadError("章节收藏记录格式错误")
            episode, state = row["episode"], row.get("type")
            eid = episode.get("id")
            if type(eid) is not int or eid <= 0 or type(state) is not int or state not in (0, 1, 2, 3):
                raise AccountReadError("章节ID或收藏状态无效")
            if episode.get("subject_id", subject_id) != subject_id:
                raise AccountReadError("章节不属于请求条目")
            item = {"episode_id": eid, "type": state, "name": episode.get("name"),
                    "name_cn": episode.get("name_cn"), "sort": episode.get("sort"),
                    "ep": episode.get("ep"), "episode_type": episode.get("type")}
            if include_summary:
                item["summary"] = episode.get("desc")
            return item

        return self._page(data, limit, offset, convert)

    def get_all_my_episode_states(self, subject_id, limit=40, max_pages=5, episode_type=None) -> dict[str, Any]:
        _positive(subject_id, "subject_id")
        _pagination(limit, 0)
        if type(max_pages) is not int or not 1 <= max_pages <= 20:
            raise ValueError("max_pages必须为1到20的整数")
        rows, offset, total = [], 0, None
        for _ in range(max_pages):
            try:
                page = self.get_my_episode_states(subject_id, limit, offset, episode_type)
            except AccountReadError as exc:
                if not rows:
                    raise
                return {"data": rows, "total": total, "complete": False,
                        "next_offset": offset, "stop_reason": "request_failed", "error": str(exc)}
            rows.extend(page["data"])
            total = page["total"]
            if page["complete"]:
                return {"data": rows, "total": total, "complete": True,
                        "next_offset": None, "stop_reason": "completed", "error": None}
            offset = page["next_offset"]
        return {"data": rows, "total": total, "complete": False,
                "next_offset": offset, "stop_reason": "page_limit", "error": None}
