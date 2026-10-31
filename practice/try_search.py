from bangumi_agent.api_client import search_subjects, get_episodes, BangumiApiError

result = search_subjects("星际牛仔", limit=2, offset=0)

print("匹配总数：", result["total"])

for subject in result["data"]:
    print(subject["id"], subject["name"], subject["name_cn"])

    try:
        episodes_result = get_episodes(subject["id"])
    except BangumiApiError as exc:
        print("这部作品的章节查询失败：", exc)
        continue

    print(episodes_result["total"])
    for episode in episodes_result["data"]:
        print(episode["id"], episode["ep"], episode["name_cn"])