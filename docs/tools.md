# MCP 工具清单

设计原则：只返回模型需要的字段；长列表分页；篇幅大的内容（单集简介、短评）默认不返回，由参数打开；Token 只从本机文件读取，不进聊天。

每个参数的含义和范围写在参数自己的说明里（`Annotated[..., Field(description=...)]`），由 SDK 生成到工具表；函数说明只写什么时候用、不该用时改用哪个工具、返回什么。“不适用”只写在容易和相邻工具混淆的地方。

## 只读工具

| 工具 | 用途 | 凭据 | 主要参数 | 返回字段 |
|---|---|---|---|---|
| `search_subjects` | 按关键词找条目 ID | 否 | keyword, limit, offset | id, name, name_cn, type, url |
| `get_subject` | 条目详情 | 否 | subject_id | 名称、类型、简介、日期、集数、评分、排名、图片、url |
| `get_episodes` | 章节列表，把“第几集”换成章节 ID | 否 | subject_id, limit, offset, episode_type=0, include_summary=false | episode_id, sort, ep, type, name, name_cn, airdate, duration；可选 summary |
| `get_calendar` | 某天放送的动画 | 否 | weekday（不传为今天） | weekday, weekday_cn；subject_id, name, name_cn, air_date, score, url |
| `get_subject_characters` | 角色和配音演员 | 否 | subject_id, limit, offset | character_id, name, relation, actors(person_id, name) |
| `get_subject_persons` | 制作人员 | 否 | subject_id, relation, limit, offset | person_id, name, relation, eps |
| `get_subject_relations` | 续作、前传、原作等 | 否 | subject_id | subject_id, name, name_cn, relation, type, url |
| `get_my_collections` | 本人收藏列表 | 是 | collection_type=3, subject_type=2, limit, offset, include_comment=false | subject_id, name, name_cn, ep_status, vol_status, url；可选 comment, rate, tags, private |
| `get_my_episode_states` | 本人每集观看状态 | 是 | subject_id, limit, offset, episode_type=0, include_summary=false | episode_id, type, sort, ep, name, name_cn；可选 summary |

`get_calendar` 用的 `/calendar` 不在官方 v0 文档里，2026-11-05 实测可用，稳定性没有文档保证。

## 本地与写入工具

| 工具 | 用途 | 凭据 | 说明 |
|---|---|---|---|
| `preview_episode_watched` | 建单集标记看过的本地草稿 | 是 | 只写本地 SQLite，不改 Bangumi |
| `execute_episode_plan` | 弹窗确认后执行草稿 | 是 | 只接收 plan_id；只有用户在客户端弹窗确认才写入；Claude 桌面版 Code 页不弹窗 |

## 写入类工具的接口设计（由 S3-07 实现）

待用户决定，见 LOG.md#s3-05。
