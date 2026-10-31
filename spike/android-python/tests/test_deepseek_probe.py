"""Offline protocol/transport tests. Never use a real API key or network."""
import copy
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src/main/python"))
import deepseek_probe as probe


def completion():
    return {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{
        "id": "fixture-call", "type": "function",
        "function": {"name": "get_subject", "arguments": '{"subject_id":253}'},
    }]}}]}


def sse_fixture(done=True, finish="tool_calls"):
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": "中文"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "fixture-call", "type": "function", "function": {"name": "get_subject", "arguments": '{"subject_'}}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'id":253}'}}]}, "finish_reason": finish}]},
        {"choices": [], "usage": {"total_tokens": 20}},
    ]
    text = ": keepalive\n\n" + "".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in chunks)
    if done:
        text += "data: [DONE]\n\n"
    return text.encode("utf-8")


class Response(io.BytesIO):
    status = 200


class ProbeTests(unittest.TestCase):
    def test_nonstream_normalization(self):
        self.assertEqual(probe._parse_json_response(completion()), {
            "call_id": "fixture-call", "tool_name": "get_subject", "arguments": {"subject_id": 253},
        })

    def test_stream_fragment_join_unicode_and_usage_event(self):
        self.assertEqual(probe._parse_stream(io.BytesIO(sse_fixture())),
                         probe._parse_json_response(completion()))

    def test_multiline_sse_event(self):
        self.assertEqual(list(probe._sse_events(io.BytesIO(b'data: {\ndata: "a": 1}\n\n'))), ['{\n"a": 1}'])

    def test_incomplete_stream_rejected(self):
        for data in (sse_fixture(done=False), sse_fixture(finish="length"), b"data: invalid\n\ndata: [DONE]\n\n"):
            with self.subTest(data_size=len(data)), self.assertRaises(probe.ProbeError):
                probe._parse_stream(io.BytesIO(data))

    def test_untrusted_tool_fields_rejected(self):
        for field, value in (("arguments", '{"subject_id":true}'),
                             ("arguments", '{"subject_id":254}'),
                             ("arguments", '{"subject_id":253,"extra":1}'),
                             ("arguments", '{'), ("name", "unknown_tool")):
            data = completion()
            data["choices"][0]["message"]["tool_calls"][0]["function"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(probe.ProbeError):
                probe._parse_json_response(data)

    def test_missing_call_id_and_multiple_calls_rejected(self):
        for mode in ("missing_id", "multiple", "length"):
            data = completion()
            calls = data["choices"][0]["message"]["tool_calls"]
            if mode == "missing_id":
                calls[0]["id"] = ""
            elif mode == "multiple":
                calls.append(copy.deepcopy(calls[0]))
            else:
                data["choices"][0]["finish_reason"] = "length"
            with self.subTest(mode=mode), self.assertRaises(probe.ProbeError):
                probe._parse_json_response(data)

    def test_one_request_json_and_stream_never_execute_tool(self):
        for stream in (False, True):
            response = Response(sse_fixture() if stream else json.dumps(completion()).encode())
            with self.subTest(stream=stream), patch.object(probe, "build_opener") as factory:
                factory.return_value.open.return_value = response
                result = probe.run_deepseek_probe("offline-dummy-key", stream)
                self.assertIn("get_subject", result)
                self.assertNotIn("offline-dummy-key", result)
                factory.return_value.open.assert_called_once()
                request = factory.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url, probe.ENDPOINT)
                payload = json.loads(request.data)
                self.assertEqual(payload["stream"], stream)
                self.assertEqual(payload["max_tokens"], 128)
                self.assertEqual(payload["thinking"], {"type": "disabled"})
                self.assertNotIn("offline-dummy-key", request.data.decode())

    def test_http_and_network_errors_do_not_echo_credentials_or_body(self):
        for error in (HTTPError(probe.ENDPOINT, 401, "offline-dummy-key", {}, io.BytesIO(b"PRIVATE_BODY")),
                      URLError("offline-dummy-key PRIVATE_BODY")):
            with patch.object(probe, "build_opener") as factory:
                factory.return_value.open.side_effect = error
                with self.assertRaises(probe.ProbeError) as caught:
                    probe.run_deepseek_probe("offline-dummy-key")
                self.assertNotIn("offline-dummy-key", str(caught.exception))
                self.assertNotIn("PRIVATE_BODY", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)

    def test_bad_key_rejected_before_transport(self):
        for key in ("", "a\nb", "a\0b", "中文", "a b"):
            with self.subTest(key_length=len(key)), patch.object(probe, "build_opener") as factory:
                with self.assertRaises(probe.ProbeError):
                    probe.run_deepseek_probe(key)
                factory.assert_not_called()

    def test_redirect_rejected(self):
        with self.assertRaises(probe.ProbeError):
            probe._NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid")


if __name__ == "__main__":
    unittest.main()
