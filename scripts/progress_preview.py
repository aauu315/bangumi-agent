"""Create, approve or cancel local plans. No remote write command is provided yet (S3-04)."""
import argparse
import getpass
import sqlite3
import sys
import warnings
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bangumi_agent.account_client import AccountClient, AccountReadError
from bangumi_agent.settings import Settings, MissingTokenError
from bangumi_agent.storage import PlanStore, PlanError
from bangumi_agent.progress import ProgressPreview
from bangumi_agent.confirmation import ExecutionService, SimulatedWriter

STATUS_TEXT = {
    "draft": "草稿，未批准", "approved": "已批准，尚未执行", "cancelled": "已取消",
    "expired": "已过期", "rejected": "执行前检查未通过，已拒绝", "executing": "执行中或执行被中断",
    "verified": "已执行并读回核实", "failed": "未生效", "unknown": "结果未知，需要只读核对",
}


def main():
    parser = argparse.ArgumentParser(description="章节看过计划：只读Bangumi，保存本地草稿")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--prompt-token", action="store_true", help="改用隐藏输入")
    sources.add_argument("--use-env", action="store_true", help="使用BANGUMI_ACCESS_TOKEN")
    sources.add_argument("--token-file", type=Path, default=ROOT / ".secrets/bangumi-token.txt")
    parser.add_argument("--db", type=Path, default=ROOT / "state/progress.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="从网站重新读取状态，创建草稿")
    create.add_argument("--subject-id", type=int, required=True)
    create.add_argument("--episode-id", type=int, required=True)
    create.add_argument("--ttl-seconds", type=int, default=900)
    show = commands.add_parser("show", help="验证当前账号并查看未过期草稿快照")
    show.add_argument("plan_id")
    for name, text in (("status", "查看任意状态的计划"), ("approve", "批准草稿（只记录在本地，不修改网站）"),
                       ("cancel", "取消草稿或已批准的计划")):
        commands.add_parser(name, help=text).add_argument("plan_id")
    args = parser.parse_args()
    try:
        if args.prompt_token:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                settings = Settings(access_token=getpass.getpass("本地输入Token（不显示）："))
        elif args.use_env:
            settings = Settings.from_environment()
        else:
            settings = Settings.from_token_file(args.token_file)
        client, store = AccountClient(settings), PlanStore(args.db)
        settings = None
        preview = ProgressPreview(client, store)
        # Writer is never called by approve/cancel/status; execution arrives with S3-04.
        confirm = ExecutionService(client, store, SimulatedWriter())
        plan = {
            "create": lambda: preview.create(args.subject_id, args.episode_id, args.ttl_seconds),
            "show": lambda: preview.show(args.plan_id),
            "status": lambda: confirm.store.get_plan(args.plan_id, account_id=client.get_me()["id"]),
            "approve": lambda: confirm.approve(args.plan_id),
            "cancel": lambda: confirm.cancel(args.plan_id),
        }[args.command]()
        labels = {0: "未收藏", 1: "想看", 2: "看过", 3: "抛弃"}
        print(f"计划ID：{plan['plan_id']}\n账号ID：{plan['account_id']}")
        print(f"条目：{plan['subject_name']}（{plan['subject_id']}）")
        print(f"章节：第{plan['episode_sort']}集 {plan['episode_name']}（{plan['episode_id']}）")
        print(f"拟修改：{labels[plan['before_type']]} → {labels[plan['after_type']]}")
        print(f"状态：{plan['status']}（{STATUS_TEXT[plan['status']]}）；过期时间：{datetime.fromtimestamp(plan['expires_at']).astimezone().isoformat(timespec='seconds')}")
        print(f"本地数据库：{args.db.resolve()}")
        if plan.get("detail"):
            print(f"说明：{plan['detail']}")
        print("以上修改前状态是创建时的快照；本命令没有修改Bangumi收藏。")
        return 0
    except (ValueError, AccountReadError) as exc:
        print(f"未完成：{exc}")
    except (sqlite3.Error, OSError):
        print("本地数据库无法读取或保存，请检查路径、权限和数据库格式。")
    except getpass.GetPassWarning:
        print("终端无法隐藏输入，已停止。")
    except (KeyboardInterrupt, EOFError):
        print("已退出。")
    except Exception:
        print("预览失败；已隐藏原始异常，避免输出凭据。请运行离线测试检查。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
