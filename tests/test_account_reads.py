"""Offline S3-01 tests: fake credentials only; all HTTP transport is mocked."""
import contextlib
import importlib.util
import io
import json
import traceback
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from bangumi_agent.account_client import AccountClient, AccountReadError, _NoRedirect
from bangumi_agent.settings import Settings, MissingTokenError

TOKEN = "offline-fixture-token"
ME = {"id": 42, "username": "fixture-user", "nickname": "测试昵称", "email": "not-returned@example.invalid"}


class Response(io.BytesIO):
    status = 200


def response(data):
    return Response(json.dumps(data, ensure_ascii=False).encode())


def collection_page():
    return {"total": 1, "limit": 20, "offset": 0, "data": [{
        "subject_id": 253, "subject_type": 2, "type": 3, "ep_status": 5,
        "vol_status": 0, "subject": {"id": 253, "name": "Cowboy Bebop", "name_cn": "星际牛仔"},
        "comment": "not-returned", "private": True,
    }]}


def http_error(status):
    """An HTTPError that carries TOKEN in its message, headers and body."""
    headers = Message()
    headers["Authorization"] = TOKEN
    return HTTPError("https://api.bgm.tv/v0/me", status, TOKEN, headers, io.BytesIO(TOKEN.encode()))


def load_account_script(module_name):
    """Load scripts/account_readonly.py as a fresh module under the given name."""
    path = Path(__file__).resolve().parents[1] / "scripts/account_readonly.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def episode_page(eid, offset, total=2):
    return {"total": total, "limit": 1, "offset": offset, "data": [{
        "episode": {"id": eid, "subject_id": 253, "name": "episode", "sort": offset + 1}, "type": 2,
    }]}


class AccountTests(unittest.TestCase):
    def client(self):
        return AccountClient(Settings(access_token=TOKEN))

    def test_missing_or_bad_token_fails_before_http(self):
        for token in ("", None, "a b", "a\nb", "中文"):
            with self.subTest(length=len(token) if isinstance(token, str) else None), patch("bangumi_agent.account_client.build_opener") as opener:
                with self.assertRaises(MissingTokenError):
                    # None is a deliberately wrong type here.
                    AccountClient(Settings(access_token=token))  # pyright: ignore[reportArgumentType]
                opener.assert_not_called()

    def test_me_projection_and_identity_binding(self):
        client = self.client()
        with patch("bangumi_agent.account_client.build_opener") as opener:
            opener.return_value.open.side_effect = [response(ME), response(collection_page())]
            me = client.get_me()
            # Redirects are refused, so the token is never forwarded to another host.
            self.assertIsInstance(opener.call_args.args[0], _NoRedirect)
            self.assertEqual(set(me), {"id", "username", "nickname"})
            me["username"] = "someone-else"
            page = client.get_my_collections()
            self.assertEqual(page["data"][0]["subject_id"], 253)
            self.assertNotIn("comment", page["data"][0])
            self.assertTrue(page["complete"])
            self.assertIsNone(page["next_offset"])
            calls = opener.return_value.open.call_args_list
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0].args[0].full_url, "https://api.bgm.tv/v0/me")
            self.assertIn("/v0/users/fixture-user/collections?", calls[1].args[0].full_url)
            for call in calls:
                request = call.args[0]
                self.assertEqual(request.get_method(), "GET")
                self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
                self.assertNotIn(TOKEN, request.full_url)
                self.assertIsNone(request.data)

    def test_request_failures_become_safe_errors(self):
        # (exception raised by the transport, expected status, expected hint)
        cases = [
            (http_error(401), 401, "Token无效"),
            (http_error(403), 403, "无权访问"),
            (http_error(404), 404, "不可见"),
            (http_error(500), 500, "服务器暂时出错"),
            (http_error(587), 587, "服务器暂时出错"),
            (http_error(600), 600, "账号接口请求未成功"),
            (URLError(TOKEN), None, "连接Bangumi失败"),
        ]
        for error, status, hint in cases:
            with self.subTest(error=type(error).__name__, status=status), \
                 patch("bangumi_agent.account_client.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(AccountReadError) as caught:
                    self.client().get_me()
                self.assertEqual(caught.exception.status, status)
                self.assertIn(hint, str(caught.exception))
                # The token was put in the error's message, headers and body; none may leak.
                self.assertNotIn(TOKEN, "".join(traceback.format_exception(caught.exception)))

    def test_bad_identity_prevents_collection_read(self):
        with patch("bangumi_agent.account_client.build_opener") as opener:
            opener.return_value.open.return_value = response({"username": "missing-id"})
            with self.assertRaises(AccountReadError):
                self.client().get_my_collections()
            opener.return_value.open.assert_called_once()

    def test_response_echo_is_redacted(self):
        with patch("bangumi_agent.account_client.build_opener") as opener:
            opener.return_value.open.return_value = response({**ME, "nickname": TOKEN})
            result = self.client().get_me()
            self.assertEqual(result["nickname"], "[REDACTED]")
            self.assertNotIn(TOKEN, repr(result))

    def test_collection_empty_and_partial_page(self):
        client = self.client()
        client.get_me = lambda: dict(ME)
        for total, complete in ((0, True), (2, False)):
            page = collection_page()
            page["total"] = total
            if total == 0:
                page["data"] = []
            with self.subTest(total=total), patch.object(client, "_get", return_value=page):
                result = client.get_my_collections()
                self.assertEqual(result["complete"], complete)
                self.assertEqual(result["next_offset"], None if complete else 1)

    def test_bad_pages_are_rejected(self):
        client = self.client()
        client.get_me = lambda: dict(ME)
        for page in ({"total": 2, "data": []}, {"total": True, "data": []}, {"total": 0, "data": "bad"}):
            with self.subTest(page=page), patch.object(client, "_get", return_value=page), self.assertRaises(AccountReadError):
                client.get_my_collections()

    def test_episode_pagination_uses_real_ids(self):
        client = self.client()
        client.get_me = lambda: dict(ME)
        with patch.object(client, "_get", side_effect=[episode_page(9001, 0), episode_page(9008, 1)]) as get:
            result = client.get_all_my_episode_states(253, limit=1)
            self.assertTrue(result["complete"])
            self.assertEqual([e["episode_id"] for e in result["data"]], [9001, 9008])
            self.assertEqual(get.call_args_list[1].args[0], "/v0/users/-/collections/253/episodes?limit=1&offset=1")

    def test_episode_page_limit_and_midway_failure_are_incomplete(self):
        client = self.client()
        client.get_me = lambda: dict(ME)
        with patch.object(client, "_get", return_value=episode_page(9001, 0)):
            result = client.get_all_my_episode_states(253, limit=1, max_pages=1)
            self.assertFalse(result["complete"])
            self.assertEqual(result["stop_reason"], "page_limit")
            self.assertEqual(result["next_offset"], 1)
        with patch.object(client, "_get", side_effect=[episode_page(9001, 0), AccountReadError("fixture failure", status=403)]):
            result = client.get_all_my_episode_states(253, limit=1)
            self.assertFalse(result["complete"])
            self.assertEqual(result["stop_reason"], "request_failed")
            self.assertEqual(len(result["data"]), 1)

    def test_bad_parameters_make_no_http_request(self):
        client = self.client()
        with patch.object(client, "_get") as get:
            for call in (lambda: client.get_my_collections(offset=-1),
                         lambda: client.get_my_collections(subject_type=True),
                         lambda: client.get_my_episode_states(0),
                         lambda: client.get_all_my_episode_states(253, max_pages=0)):
                with self.assertRaises(ValueError):
                    call()
            get.assert_not_called()

    def test_cli_identity_rejection_stops_before_collections(self):
        module = load_account_script("account_readonly_entry")
        with patch("sys.argv", ["account_readonly.py", "--prompt-token"]), \
             patch.object(module.getpass, "getpass", return_value=TOKEN), \
             patch.object(module.AccountClient, "get_me", return_value=ME), \
             patch.object(module.AccountClient, "get_my_collections") as collections, \
             patch("builtins.input", return_value="n"), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(module.main(), 0)
            collections.assert_not_called()
            self.assertNotIn(TOKEN, output.getvalue())

    def test_token_file_utf8_bom_and_trailing_newline(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture-token.txt"
            path.write_bytes(b"\xef\xbb\xbf" + TOKEN.encode() + b"\r\n")
            settings = Settings.from_token_file(path)
            self.assertEqual(settings.require_token(), TOKEN)
            self.assertNotIn(TOKEN, repr(settings))

    def test_token_file_errors_never_echo_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture-token.txt"
            with self.assertRaises(MissingTokenError):
                Settings.from_token_file(path)
            for raw in (b"", (TOKEN + "\nsecond-line").encode(), b"\xff" + TOKEN.encode(), b"a" * 8193):
                path.write_bytes(raw)
                with self.subTest(size=len(raw)), self.assertRaises(MissingTokenError) as caught:
                    Settings.from_token_file(path)
                self.assertNotIn(TOKEN, "".join(traceback.format_exception(caught.exception)))

    def test_cli_reads_only_the_chosen_token_source(self):
        default_file = Path(__file__).resolve().parents[1] / ".secrets/bangumi-token.txt"
        # (extra argv, which source should be used)
        cases = [([], "file"), (["--token-file"], "file"), (["--prompt-token"], "prompt")]
        for extra, source in cases:
            module = load_account_script("account_source_entry")
            with self.subTest(argv=extra), patch("sys.argv", ["account_readonly.py", *extra, "--me-only"]), \
                 patch.object(module.Settings, "from_token_file", return_value=Settings(access_token=TOKEN)) as from_file, \
                 patch.object(module.Settings, "from_environment") as from_env, \
                 patch.object(module.getpass, "getpass", return_value=TOKEN) as prompt, \
                 patch.object(module.AccountClient, "get_me", return_value=ME), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(module.main(), 0)
                if source == "file":
                    from_file.assert_called_once_with(default_file)
                    prompt.assert_not_called()
                else:
                    prompt.assert_called_once()
                    from_file.assert_not_called()
                from_env.assert_not_called()
                self.assertNotIn(TOKEN, output.getvalue())

    def test_cli_missing_default_file_does_not_fall_back_or_request(self):
        module = load_account_script("account_missing_entry")
        with patch("sys.argv", ["account_readonly.py", "--me-only"]), \
             patch.object(module.Settings, "from_token_file", side_effect=MissingTokenError("missing fixture file")), \
             patch.object(module.getpass, "getpass") as prompt, \
             patch.object(module.AccountClient, "get_me") as get_me, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(), 1)
            prompt.assert_not_called()
            get_me.assert_not_called()


if __name__ == "__main__":
    unittest.main()
