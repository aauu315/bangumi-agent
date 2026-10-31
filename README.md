# Bangumi Agent

目标：让用户通过 AI Agent 完成 Bangumi 网站上的各类工作，包括作品查询、收藏与进度管理，以及日志、小组浏览和讨论等网站业务。日志与小组等属于核心产品范围；现阶段的公开 API 查询和进度闭环是分阶段起点，不是完整产品边界。具体功能逐项核对、实现和验收，不承诺尚未验证的网站全功能覆盖。

普通用户主入口仍为本地 Android APK，按需配置自己的模型 Key 与 Bangumi 凭据，无需终端或部署作者服务器。访问层按业务需要使用公开 API、网站内部 API，必要时评估网页适配；公开 API 的覆盖范围不决定产品范围。开发者可用 MCP；FastAPI、数据库与 LangGraph 工作流是可选服务端/完整作品集路线。完整范围与待补规划见 docs/PLAN.md。

## 从哪里看

1. [PROJECT_STATUS.json](PROJECT_STATUS.json)：**唯一**任务状态、`next_task` 与下一步。
2. [docs/PLAN.md](docs/PLAN.md)：产品路线、阶段地图、全部任务卡、技术取舍和验收标准。按 `next_task` 找当前卡。
3. [docs/LOG.md](docs/LOG.md)：历史真实验证、开发流水；新证据按任务 ID 追加。
4. [AGENTS.md](AGENTS.md)：给接手 Agent 的工作边界。旧文档原稿在 `work/history/planning-before-consolidation-20261017/`，不作为当前状态源。

当前代码已有匿名搜索/详情业务层、本人收藏只读客户端和本地章节预览；MCP提供两个公开查询及一个预览工具。实际验证范围见 LOG，下一张任务卡及其个人理解状态以 `PROJECT_STATUS.json` 为准。已有用于兼容性验证的 Android 实验 APK，尚非完整产品；LangGraph、FastAPI 和服务端数据库尚未实现，本地SQLite仅保存预览草稿。

## 开发者：已实现的只读 MCP

`search_subjects(keyword, limit=10, offset=0)` 和 `get_subject(subject_id)` 使用匿名公开请求，不修改收藏。HTTP 业务层在 `src/bangumi_agent/api_client.py`，stdio 入口在 `src/bangumi_agent/mcp_server.py`。实际客户端证据见 LOG.md#s1-04，当前状态以 PROJECT_STATUS.json 为准。

在项目根目录用 uv 按锁文件同步唯一环境 `.venv`，激活后运行现有测试：

```powershell
Set-Location E:\myproject\bangumi-agent
uv sync --locked
.\.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
```

MCP 客户端通过 stdio 启动 `.venv\Scripts\python.exe -m bangumi_agent.mcp_server`；直接在终端运行会等待协议输入。激活后用 `python` 运行练习，例如 `python practice/prcts.py 638497`。

目录约定：`src/bangumi_agent/` 是业务代码；`scripts/` 只放正式命令行入口；`practice/` 放 S0–S1 的学习练习脚本；`spike/android-python/` 是手机端（名称沿用实验期，S5 时再改）。`.venv` 已安装项目包及依赖；旧的 `.venv-mcp` 已在验证后删除，历史日志中的路径仅用于追溯。

## 开发练习：本人收藏只读验证

默认读取本机项目文件`.secrets/bangumi-token.txt`（UTF-8，只放Token本身一行，不加变量名、引号或Bearer前缀）。路径基于项目目录，不依赖当前工作目录：

```powershell
.\.venv\Scripts\python.exe scripts/account_readonly.py
```

本次需要手动隐藏输入时，加`--prompt-token`：

```powershell
.\.venv\Scripts\python.exe scripts/account_readonly.py --prompt-token
```

核对账号是本人后继续读取动画在看列表；支持逐页读取和可选本人章节状态查询，不修改收藏。可加`--me-only`仅查身份。文件缺失、为空或格式错误会停止并提示，不会自动切换到其他凭据来源。

也可使用`--token-file 其他路径`选择另一文件，或显式`--use-env`读取BANGUMI_ACCESS_TOKEN环境变量。这三个输入选项互斥；不自动寻找或读取旧.env。`.env.example`仅为空配置示例。

`.secrets/`已在.gitignore中排除，但仍是明文文件；不要把Token发到聊天、日志、示例文件或命令行参数中。脚本只读取，不会改写已保存的Token。真实账户验收以PROJECT_STATUS.json和LOG为准。

## 开发练习：章节修改预览（不执行）

例如为用户选定的《中国奇谭 第二季》第4集《小雪》创建本地草稿：

```powershell
py scripts/progress_preview.py create --subject-id 423036 --episode-id 1440795
```

默认使用同一本地Token文件，重新读取当前账号、条目收藏和章节归属/状态，仅发送GET请求。未收藏条目、跨条目章节、已看过章节会拒绝；成功才在`state/progress.sqlite3`保存单章节草稿。有效期默认900秒（15分钟）；无批准或执行入口，不修改官网收藏。此命令的真实账号验收仍待用户执行。

输出计划ID后，可在新进程中查询未过期草稿（替换PLAN_ID）：

```powershell
py scripts/progress_preview.py show PLAN_ID
```

show只用/me核对账号，其余是本地创建时快照；不重新查询章节，不代表实时状态。过期草稿拒绝作为有效预览，但历史行仍留在数据库。`--prompt-token`、`--token-file 路径`、`--use-env`和`--db 路径`放在create/show前面。

`operation_plans`存账号、创建时间、过期时间与draft状态；`plan_items`存单个章节的before/after。外键关联两表，计划和条目在一个事务保存；参数化SQL绑定值。数据库CHECK约束限制after_type=2（看过），当前阶段只允许草稿。ID是网站标识，不是集数；数据库本身不能判断网站章节归属，归属由只读请求核对。

SQL查询练习（Python自带SQLite，无需另装数据库服务）：

```powershell
py -m sqlite3 state/progress.sqlite3 "SELECT plan_id, subject_id, episode_id, before_type, after_type FROM plan_items;"
```

注册的MCP预览工具为`preview_episode_watched(subject_id, episode_id)`，使用相同服务及数据库，客户端需重新加载工具列表。它不会批准或执行草稿。CLI能用不代表MCP客户端已完成真实验收。
