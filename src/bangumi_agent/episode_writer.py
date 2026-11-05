"""The only Bangumi writes in this project: mark episodes as watched.

Called by ExecutionService.execute after the plan is approved and re-checked; never call it
directly. Endpoints checked against the official v0 spec (one episode 2026-11-03, batch 2026-11-05):
PUT /v0/users/-/collections/-/episodes/{episode_id}, body {"type": 2}, success 204.
PATCH /v0/users/-/collections/{subject_id}/episodes, body {"episode_id": [...], "type": 2}, success 204.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .account_client import AccountReadError, _NoRedirect, _http_error, _positive
from .api_client import BASE_URL, TIMEOUT_SECONDS, USER_AGENT
from .confirmation import WriteRejected, WriteUncertain
from .settings import Settings

WATCHED = 2


class BangumiEpisodeWriter:
    def __init__(self, settings: Settings):
        self.__token = settings.require_token()

    def mark_episode_watched(self, subject_id: int, episode_id: int) -> None:
        _positive(subject_id, "subject_id")
        _positive(episode_id, "episode_id")
        self._send("PUT", f"/v0/users/-/collections/-/episodes/{episode_id}", {"type": WATCHED})

    def mark_episodes_watched(self, subject_id: int, episode_ids: list[int]) -> None:
        _positive(subject_id, "subject_id")
        if not episode_ids:
            raise ValueError("episode_ids不能为空")
        for episode_id in episode_ids:
            _positive(episode_id, "episode_id")
        self._send("PATCH", f"/v0/users/-/collections/{subject_id}/episodes",
                   {"episode_id": episode_ids, "type": WATCHED})

    def _send(self, method: str, path: str, body: dict) -> None:
        request = Request(
            BASE_URL + path, data=json.dumps(body).encode(), method=method,
            headers={"Authorization": "Bearer " + self.__token, "User-Agent": USER_AGENT,
                     "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with build_opener(_NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
                status = response.status
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if 400 <= status < 500:
                # A clear refusal: the site did not apply the change.
                raise WriteRejected(f"{_http_error(status)}（未修改）") from None
            raise WriteUncertain(f"Bangumi HTTP {status}：服务器错误，是否已修改未知") from None
        except AccountReadError:
            raise WriteUncertain("写入请求被重定向，是否已修改未知") from None
        except (URLError, TimeoutError, OSError):
            raise WriteUncertain("连接中断或超时，是否已修改未知") from None
        if status not in (200, 204):
            raise WriteUncertain(f"Bangumi HTTP {status}：非预期回复，是否已修改未知")
