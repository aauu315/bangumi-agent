"""The only Bangumi write in this project: mark one episode as watched.

Called by ExecutionService.execute after the plan is approved and re-checked; never call it
directly. Endpoint checked against the official v0 spec on 2026-11-03:
PUT /v0/users/-/collections/-/episodes/{episode_id}, body {"type": 2}, success 204.
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
        request = Request(
            f"{BASE_URL}/v0/users/-/collections/-/episodes/{episode_id}",
            data=json.dumps({"type": WATCHED}).encode(), method="PUT",
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
