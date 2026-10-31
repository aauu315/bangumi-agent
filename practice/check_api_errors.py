from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from bangumi_agent.api_client import get_subject, BangumiApiError

# 模拟服务器返回 404
with patch(
    "bangumi_agent.api_client.urlopen",
    side_effect=HTTPError(
        "https://api.bgm.tv/v0/subjects/253",
        404,
        "Not Found",
        Message(),
        None,
    ),
):
    try:
        get_subject(253)
    except BangumiApiError as exc:
        print("HTTP 错误已正确报告：", exc)
    else:
        print("未通过：发生 HTTP 错误却没有报告失败")

# 模拟连接失败
with patch(
    "bangumi_agent.api_client.urlopen",
    side_effect=URLError("模拟连接失败"),
):
    try:
        get_subject(253)
    except BangumiApiError as exc:
        print("网络错误已正确报告：", exc)
    else:
        print("未通过：连接失败却没有报告失败")
