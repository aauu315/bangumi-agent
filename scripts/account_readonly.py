"""Local learning entry: token file by default, optional hidden input, GET-only reads."""
import argparse
import getpass
import sys
import warnings
from pathlib import Path

# Run from a checkout without depending on the caller's working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bangumi_agent.account_client import AccountClient, AccountReadError
from bangumi_agent.settings import Settings, MissingTokenError

DEFAULT_TOKEN_FILE = Path(__file__).resolve().parents[1] / ".secrets" / "bangumi-token.txt"

def main():
    parser = argparse.ArgumentParser(description="Bangumi本人收藏只读验证；Token不作为命令行参数")
    parser.add_argument("--me-only", action="store_true", help="只查询当前凭据对应身份")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--prompt-token", action="store_true", help="本次改用隐藏输入Token，不读取本地文件")
    sources.add_argument("--use-env", action="store_true", help="明确选择读取BANGUMI_ACCESS_TOKEN环境变量")
    sources.add_argument(
        "--token-file", type=Path, nargs="?",
        const=DEFAULT_TOKEN_FILE,
        help="指定Token文件；默认读取项目.secrets/bangumi-token.txt",
    )
    args = parser.parse_args()
    try:
        if args.use_env:
            settings = Settings.from_environment()
        elif args.prompt_token:
            # Fail closed if this terminal cannot hide input; never fall back to visible token entry.
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                token = getpass.getpass("请在本地输入Bangumi Access Token（输入不显示）：")
            settings = Settings(access_token=token)
            token = None
        else:
            settings = Settings.from_token_file(args.token_file or DEFAULT_TOKEN_FILE)
        client = AccountClient(settings)
        settings = None
        me = client.get_me()
        print(f"账号ID：{me['id']}\n用户名：{me['username']}\n昵称：{me['nickname']}")
        if args.me_only:
            return 0
        if input("请对照官网确认这是本人账号，是请输入 y；其他输入退出：").strip().lower() != "y":
            print("已退出，未读取收藏。")
            return 0
        offset = 0
        while True:
            page = client.get_my_collections(offset=offset)
            print(f"\n动画在看：官网API报告共{page['total']}条；本页{len(page['data'])}条，offset={offset}")
            for row in page["data"]:
                name = row.get("name_cn") or row.get("name") or "条目信息不可见"
                print(f"ID {row['subject_id']} | {name} | 已记录进度 {row['ep_status']}")
            if page["complete"]:
                print("收藏分页已读到末页。")
                break
            if input("还有后续记录。输入 n 读下一页；其他输入停止：").strip().lower() != "n":
                print("已停止，以上不是完整收藏列表。")
                break
            offset = page["next_offset"]
        raw = input("可选：输入一个已收藏的条目ID查询章节状态；直接回车结束：").strip()
        if raw:
            if not raw.isdecimal() or int(raw) <= 0:
                print("条目ID必须是正整数，未发起章节请求。")
                return 1
            episodes = client.get_all_my_episode_states(int(raw))
            labels = {0: "未收藏", 1: "想看", 2: "看过", 3: "抛弃"}
            for row in episodes["data"]:
                name = row.get("name_cn") or row.get("name") or ""
                print(f"章节ID {row['episode_id']} | 序号 {row['sort']} | {name} | {labels[row['type']]}")
            print(f"章节读取完整：{episodes['complete']}；停止原因：{episodes['stop_reason']}")
            if episodes["error"]:
                print(episodes["error"])
        print("本轮只读验证结束；请与官网核对，未修改收藏。")
        return 0
    except (MissingTokenError, AccountReadError) as exc:
        print(f"未完成：{exc}")
        return 1
    except getpass.GetPassWarning:
        print("终端无法隐藏输入，已停止。请用本机PowerShell运行。")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\n已退出。")
        return 1
    except Exception:
        # No raw traceback: it may contain credential-bearing request objects.
        print("只读验证遇到未处理错误；已隐藏原始异常，请先运行离线测试检查。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
