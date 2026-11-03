# Bangumi Agent

> **⚠️ 项目状态：开发中的学习项目，尚不能投入正式使用。**
>
> - 已完成：作品搜索/详情/章节的匿名查询、本人身份与收藏的只读查询、单集“标为看过”的预览→人工批准→执行→读回（仅命令行，已做过一次真实修改）、3 个只读/预览 MCP 工具、Android 兼容性实验 APK。
> - 进行中：扩充只读工具集（S3-05）、在 Agent 对话中弹窗批准（S3-06）。**唯一能修改网站数据的是单集进度，且必须由你在命令行对具体预览批准**；MCP 工具不能批准或执行。
> - 尚未开始：独立 Agent、可安装的正式 APK、日志与小组等网站业务。
> - 当前进度以 [PROJECT_STATUS.json](PROJECT_STATUS.json) 为准，完整路线见 [docs/PLAN.md](docs/PLAN.md)。

通过 AI Agent 完成 Bangumi 网站上的各类工作：作品查询、收藏与进度管理，以及日志、小组浏览和讨论等。普通用户的入口是本地 Android APK，自己配置模型 Key 和 Bangumi 凭据；开发者可以通过 MCP 在 Agent 客户端里调用。

本项目为个人项目，与 Bangumi 番组计划官方无关，只通过 Bangumi 公开的 API 访问数据。

## 开发方式

这个项目同时是我学习 AI 应用开发的过程，由 AI 编程助手（Claude Code、Codex）辅助完成，按任务卡推进：

- 每张卡标注类型。**学习卡**的核心逻辑（如分页推进、校验规则、状态转移）由我自己设计和编写，AI 负责讲解、审查和样板代码；**产出卡**以 AI 编写为主，我负责需求和验收。
- 每张卡的学习过程（问过什么、犯过什么错、怎样修正）和验证结果记在 [docs/LOG.md](docs/LOG.md)；个人理解程度单独记录在状态文件的 `learning` 字段，不以“代码能跑”代替。
- 给 AI 助手的工作约定见 [AGENTS.md](AGENTS.md)。

## 从哪里看

1. [PROJECT_STATUS.json](PROJECT_STATUS.json)：任务状态和下一步（`next_task`），唯一状态源。
2. [docs/PLAN.md](docs/PLAN.md)：产品路线、阶段地图、全部任务卡和验收标准。
3. [docs/LOG.md](docs/LOG.md)：按任务记录的学习过程和验证证据。
4. [AGENTS.md](AGENTS.md)：给接手的 AI 助手的工作约定。

## 目录

| 位置 | 内容 |
|---|---|
| `src/bangumi_agent/` | 业务代码：`api_client.py` 匿名查询，`settings.py` + `account_client.py` 本人数据只读，`progress.py` + `storage.py` 进度预览，`mcp_server.py` MCP 工具 |
| `scripts/` | 正式命令行入口 |
| `practice/` | S0–S1 的学习练习脚本 |
| `tests/` | 离线测试 |
| `spike/android-python/` | 手机端（Kotlin/Compose + Chaquopy，名称沿用实验期，S5 时再改） |
| `state/`、`.secrets/` | 本地数据库和 Token 文件，不进 git |

## 环境与测试

```powershell
Set-Location E:\myproject\bangumi-agent
uv sync --locked
.\.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
uv run pyright
```

`uv run pyright` 做类型检查，设置与 VS Code 的 Pylance basic 模式一致。

## MCP 工具

MCP 客户端用 stdio 启动 `.venv\Scripts\python.exe -m bangumi_agent.mcp_server`（直接在终端运行会停在等待协议输入）。当前工具：

- `search_subjects(keyword, limit=10, offset=0)`：匿名搜索作品。
- `get_subject(subject_id)`：匿名查询作品详情。
- `preview_episode_watched(subject_id, episode_id)`：为“某集标为看过”创建本地预览草稿，不修改网站。需要本地 Token。

## 本人数据：Token 文件

把 Bangumi Access Token 单独一行写进 `.secrets/bangumi-token.txt`（不加变量名、引号或 `Bearer`）。该目录不进 git，但文件是明文，不要把 Token 发到聊天、日志或命令行参数里。

脚本默认读这个文件，也可以换一种来源（三选一）：`--prompt-token` 隐藏输入，`--token-file 路径` 指定其他文件，`--use-env` 读环境变量 `BANGUMI_ACCESS_TOKEN`。文件缺失或格式不对时直接报错，不会自动换来源。

## 本人收藏只读查询

```powershell
python scripts/account_readonly.py
```

先显示当前 Token 对应的账号，确认是本人后再读动画在看列表，可以逐页读取，也可以输入条目 ID 查看章节状态。`--me-only` 只查身份。

## 单集进度：预览、批准、执行

为某一集创建“标为看过”的本地草稿，例如《中国奇谭 第二季》第 4 集：

```powershell
python scripts/progress_preview.py create --subject-id 423036 --episode-id 1440795
python scripts/progress_preview.py show PLAN_ID
```

`create` 会重新读取账号、收藏和章节状态（只发 GET 请求），通过检查后在 `state/progress.sqlite3` 保存草稿，默认 15 分钟有效。未收藏的条目、不属于该条目的章节、已看过的章节都会被拒绝。`show` 只核对账号并显示保存时的快照。Token 来源选项和 `--db 路径` 要写在 `create`/`show` 前面。

数据库有两张表：`operation_plans` 存账号、时间、有效期和状态，`plan_items` 存章节及修改前后的状态。可以直接用 SQL 查看：

```powershell
python -m sqlite3 state/progress.sqlite3 "SELECT plan_id, subject_id, episode_id, before_type, after_type FROM plan_items;"
```

确认预览无误后，由你自己在命令行批准并执行：

```powershell
python scripts/progress_preview.py approve PLAN_ID
python scripts/progress_preview.py execute PLAN_ID
```

`execute` 只执行已批准、未过期的计划，执行前会重新读取网站状态，状态变了就拒绝；写入后读回核对。网络中断导致结果不明时，用 `check PLAN_ID` 只读核对，不会自动重试。另有 `status`（查看状态）和 `cancel`（取消）。

## 许可证

[MIT](LICENSE)
