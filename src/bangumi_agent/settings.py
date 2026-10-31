"""Local credential input. No .env file is loaded automatically."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


class MissingTokenError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    access_token: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls):
        # Only used when a caller explicitly chooses this input method.
        return cls(access_token=os.environ.get("BANGUMI_ACCESS_TOKEN", ""))

    @classmethod
    def from_token_file(cls, path):
        """Read an explicitly selected local file; never print its contents."""
        try:
            with Path(path).open("rb") as stream:
                raw = stream.read(8193)
        except OSError:
            raise MissingTokenError("无法读取Token文件，请检查文件路径和权限") from None
        if len(raw) > 8192:
            raise MissingTokenError("Token文件过大；文件应只包含一行Token")
        try:
            token = raw.decode("utf-8-sig").strip()
        except UnicodeError:
            raise MissingTokenError("Token文件请使用UTF-8文本格式保存") from None
        settings = cls(access_token=token)
        settings.require_token()
        return settings

    def require_token(self):
        value = self.access_token
        if not isinstance(value, str) or not value:
            raise MissingTokenError("缺少Bangumi Token；公共查询仍可使用")
        if len(value) > 4096 or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", value):
            raise MissingTokenError("Token格式不正确；不要包含空白、换行或Bearer前缀")
        return value
