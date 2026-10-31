"""Offline S3-01 tests: fake credentials only; all HTTP transport is mocked."""
import contextlib
import importlib.util
import io
import json
import os
import traceback
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from bangumi_agent.account_client import AccountClient, AccountReadError, _NoRedirect
from bangumi_agent.settings import Settings, MissingTokenError
from bangumi_agent.api_client import get_subject

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
                    AccountClient(Settings(access_token=token))
                opener.assert_not_called()

    def test_settings_repr_and_explicit_environment(self):
        self.assertNotIn(TOKEN, repr(Settings(access_token=TOKEN)))
        with patch.dict(os.environ, {"BANGUMI_ACCESS_TOKEN": TOKEN}, clear=True):
            self.assertEqual(Settings.from_environment().require_token(), TOKEN)

    def test_no_token_public_query_still_works(self):
        with patch.dict(os.environ, {}, clear=True), patch("bangumi_agent.api_client._request_json", return_value={"id": 253, "name": "fixture"}):
            self.assertEqual(get_subject(253)["id"], 253)

    def test_me_projection_and_identity_binding(self):
        client = self.client()
        with patch("bangumi_agent.account_client.build_opener") as opener:
            opener.return_value.open.side_effect = [response(ME), response(collection_page())]
            me = client.get_me()
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

    def test_http_error_semantics_and_no_secret_in_traceback(self):
        for status in (401, 403, 404):
            error = HTTPError("https://api.bgm.tv/v0/me", status, TOKEN, {"Authorization": TOKEN}, io.BytesIO(TOKEN.encode()))
            with self.subTest(status=status), patch("bangumi_agent.account_client.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(AccountReadError) as caught:
                    self.client().get_me()
                self.assertEqual(caught.exception.status, status)
                rendered = "".join(traceback.format_exception(caught.exception))
                self.assertNotIn(TOKEN, rendered)
                if status == 404:
                    self.assertIn("不可见", str(caught.exception))

    def test_network_failure_not_empty_success_or_secret_echo(self):
        with patch("bangumi_agent.account_client.build_opener") as opener:
            opener.return_value.open.side_effect = URLError(TOKEN)
            with self.assertRaises(AccountReadError) as caught:
                self.client().get_me()
            self.assertNotIn(TOKEN, "".join(traceback.format_exception(caught.exception)))

    def test_redirect_is_rejected(self):
        with self.assertRaises(AccountReadError):
            _NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid")

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
        spec = importlib.util.spec_from_file_location("account_readonly_entry", Path(__file__).resolve().parents[1] / "scripts/account_readonly.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch("sys.argv", ["account_readonly.py", "--prompt-token"]), patch.object(module.getpass, "getpass", return_value=TOKEN), patch.object(module.AccountClient, "get_me", return_value=ME), patch.object(module.AccountClient, "get_my_collections") as collections, patch("builtins.input", return_value="n"), contextlib.redirect_stdout(io.StringIO()) as output:
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

    def test_cli_explicit_default_token_file_does_not_prompt_or_read_environment(self):
        spec = importlib.util.spec_from_file_location("account_file_entry", Path(__file__).resolve().parents[1] / "scripts/account_readonly.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch("sys.argv", ["account_readonly.py", "--token-file", "--me-only"]), patch.object(module.Settings, "from_token_file", return_value=Settings(access_token=TOKEN)) as from_file, patch.object(module.Settings, "from_environment") as from_env, patch.object(module.getpass, "getpass") as prompt, patch.object(module.AccountClient, "get_me", return_value=ME), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(module.main(), 0)
            from_file.assert_called_once_with(Path(__file__).resolve().parents[1] / ".secrets/bangumi-token.txt")
            from_env.assert_not_called()
            prompt.assert_not_called()
            self.assertNotIn(TOKEN, output.getvalue())

    def test_cli_token_sources_are_mutually_exclusive(self):
        spec = importlib.util.spec_from_file_location("account_source_entry", Path(__file__).resolve().parents[1] / "scripts/account_readonly.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch("sys.argv", ["account_readonly.py", "--use-env", "--token-file"]), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            module.main()
        self.assertEqual(caught.exception.code, 2)



    def test_cli_no_source_option_defaults_to_project_file(self):
        spec = importlib.util.spec_from_file_location("account_default_entry", Path(__file__).resolve().parents[1] / "scripts/account_readonly.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch("sys.argv", ["account_readonly.py", "--me-only"]), patch.object(module.Settings, "from_token_file", return_value=Settings(access_token=TOKEN)) as from_file, patch.object(module.Settings, "from_environment") as from_env, patch.object(module.getpass, "getpass") as prompt, patch.object(module.AccountClient, "get_me", return_value=ME), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(), 0)
            from_file.assert_called_once_with(module.DEFAULT_TOKEN_FILE)
            from_env.assert_not_called()
            prompt.assert_not_called()

    def test_cli_explicit_prompt_never_reads_file(self):
        spec = importlib.util.spec_from_file_location("account_prompt_entry", Path(__file__).resolve().parents[1] / "scripts/account_readonly.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch("sys.argv", ["account_readonly.py", "--prompt-token", "--me-only"]), patch.object(module.Settings, "from_token_file") as from_file, patch.object(module.Settings, "from_environment") as from_env, patch.object(module.getpass, "getpass", return_value=TOKEN) as prompt, patch.object(module.AccountClient, "get_me", return_value=ME), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(module.main(), 0)
            prompt.assert_called_once()
            from_file.assert_not_called()
            from_env.assert_not_called()
            self.assertNotIn(TOKEN, output.getvalue())

    def test_cli_missing_default_file_does_not_fall_back_or_request(self):
        spec = importlib.util.spec_from_file_location("account_missing_entry", Path(__file__).resolve().parents[1] / "scripts/account_readonly.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch("sys.argv", ["account_readonly.py", "--me-only"]), patch.object(module.Settings, "from_token_file", side_effect=MissingTokenError("missing fixture file")), patch.object(module.getpass, "getpass") as prompt, patch.object(module.AccountClient, "get_me") as get_me, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(), 1)
            prompt.assert_not_called()
            get_me.assert_not_called()



if __name__ == "__main__":
    unittest.main()
