"""第一步：无令牌、只读请求一个公开 Bangumi 条目。仅使用 Python 标准库。"""
import argparse
import json
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subject_id", nargs="?", type=int, default=253)
    args = parser.parse_args()
    if args.subject_id <= 0:
        parser.error("subject_id 必须为正整数")
    url = f"https://api.bgm.tv/v0/subjects/{args.subject_id}"
    request = Request(url, method="GET", headers={
        "User-Agent": "aauu315-bangumi-agent/0.1 (https://github.com/aauu315)",
        "Accept": "application/json",
    })
    # 没有 Authorization，不读取环境变量或令牌；超时后不自动重试。
    try:
        with urlopen(request, timeout=20) as response:
            status = response.status
            data = json.load(response)
        if status != 200 or not isinstance(data, dict) or data.get("id") != args.subject_id:
            raise ValueError("响应状态或条目 ID 不符合预期")
        if not isinstance(data.get("name"), str) or not data["name"]:
            raise ValueError("响应缺少作品名称")
    except HTTPError as exc:
        print(f"HTTP_ERROR: {exc.code}; 请求未成功，不要把空结果当成成功。", file=sys.stderr)
        return 1
    except (URLError, TimeoutError, OSError) as exc:
        print(f"NETWORK_ERROR: {type(exc).__name__}; 请检查网络或证书，不要关闭 TLS 校验。", file=sys.stderr)
        return 1
    except (ValueError, TypeError) as exc:
        print(f"RESPONSE_ERROR: {exc}", file=sys.stderr)
        return 1
    result = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "GET", "url": url, "authenticated": False,
        "http_status": status,
        "subject": {key: data.get(key) for key in ("id", "name", "name_cn", "type", "eps", "total_episodes", "summary")},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


import io

if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
