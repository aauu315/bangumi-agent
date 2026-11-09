"""Model API keys on the development computer: one plain-text file per provider under .secrets/.

.secrets/ is git-ignored. The key is stored unencrypted, like the Bangumi token file; the phone's
encrypted storage is S5-02. Error texts never contain the key.
"""
import re
from pathlib import Path

SECRETS_DIR = Path(__file__).resolve().parents[3] / ".secrets"
MAX_KEY_BYTES = 4096


class MissingKeyError(ValueError):
    pass


def key_path(provider: str, secrets_dir: Path = SECRETS_DIR) -> Path:
    """.secrets/deepseek-api-key.txt, .secrets/anthropic-api-key.txt, ..."""
    if not re.fullmatch(r"[a-z0-9-]+", provider):
        raise ValueError("提供商名只能有小写字母、数字和连字符")
    return secrets_dir / f"{provider}-api-key.txt"


def check_key(key: str) -> str:
    key = key.strip()
    if not key:
        raise MissingKeyError("API Key 为空")
    if len(key.encode("utf-8")) > MAX_KEY_BYTES or not re.fullmatch(r"[\x21-\x7e]+", key):
        raise MissingKeyError("API Key 格式不对：应为一行，不含空格、换行或中文")
    return key


def read_key(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_KEY_BYTES + 1)
    except FileNotFoundError:
        raise MissingKeyError(f"还没有保存 Key：{path.name}，可用 --save-key 保存") from None
    except OSError:
        raise MissingKeyError(f"无法读取 {path.name}，请检查文件权限") from None
    if len(raw) > MAX_KEY_BYTES:
        raise MissingKeyError(f"{path.name} 过大；文件里应只有一行 Key")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        raise MissingKeyError(f"{path.name} 请用 UTF-8 保存") from None
    return check_key(text)


def save_key(path: Path, key: str) -> None:
    """Check, then overwrite the file. The caller asks before replacing an existing key."""
    key = check_key(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key + "\n", encoding="utf-8")
