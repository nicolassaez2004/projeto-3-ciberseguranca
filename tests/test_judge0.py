import base64

import pytest
import requests

from app.services.judge0 import Judge0Client, Judge0Error, decoded_output


class Response:
    def __init__(self, data, status=200, raw=None):
        import json
        self.data = raw if raw is not None else json.dumps(data).encode()
        self.status_code = status
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def iter_content(self, size): yield self.data


@pytest.fixture
def executor():
    return Judge0Client({"JUDGE0_URL": "https://runner.example.test", "JUDGE0_API_KEY": "private-key", "JUDGE0_RAPIDAPI_HOST": "runner.example.test"})


def test_adapter_sends_base64_limits_and_no_network(monkeypatch, executor):
    seen = []
    def request(method, url, **kwargs):
        seen.append((method, url, kwargs))
        if url.endswith("/languages"):
            return Response([{"id": 71, "name": "Python (3.8.1)"}, {"id": 100, "name": "Python (3.12.5)"}])
        return Response([{"token": "12345678-test-token"}], 201)
    monkeypatch.setattr(requests, "request", request)
    tokens = executor.create_batch("python", "print('Olá')", [{"stdin": "ç\n", "expected_output": "Olá\n"}], {"cpu_time_limit": 2, "memory_limit_kb": 128000})
    assert tokens == ["12345678-test-token"]
    sent = seen[-1][2]
    assert sent["headers"]["X-RapidAPI-Key"] == "private-key"
    assert sent["params"] == {"base64_encoded": "true"}
    payload = sent["json"]["submissions"][0]
    assert payload["language_id"] == 100
    assert payload["enable_network"] is False
    assert payload["cpu_time_limit"] == 2
    assert payload["memory_limit"] == 128000
    assert base64.b64decode(payload["source_code"]).decode() == "print('Olá')"
    assert base64.b64decode(payload["expected_output"]).decode() == "Olá\n"
    assert sent["allow_redirects"] is False
    assert sent["timeout"] == (4, 10)


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_upstream_error_bodies_are_not_exposed(monkeypatch, executor, status):
    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: Response({"error": "SECRET-KEY-AND-TESTS"}, status))
    with pytest.raises(Judge0Error) as error:
        executor.languages()
    assert "SECRET" not in str(error.value)


def test_timeout_becomes_retryable_service_error(monkeypatch, executor):
    def request(*args, **kwargs): raise requests.Timeout("sensitive URL")
    monkeypatch.setattr(requests, "request", request)
    with pytest.raises(Judge0Error) as error:
        executor.languages()
    assert error.value.status == 503
    assert "sensitive" not in str(error.value)


def test_reordered_results_match_original_test_order(monkeypatch, executor):
    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: Response({"submissions": [
        {"token": "second", "status": {"id": 4}}, {"token": "first", "status": {"id": 3}},
    ]}))
    assert [r["token"] for r in executor.get_batch(["first", "second"])] == ["first", "second"]


def test_missing_or_duplicated_results_are_not_graded(monkeypatch, executor):
    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: Response({"submissions": [{"token": "one", "status": {"id": 3}}]}))
    with pytest.raises(Judge0Error): executor.get_batch(["one", "two"])


def test_partial_batch_acceptance_is_not_used(monkeypatch, executor):
    executor._language_cache = {"python": {"id": 71}}
    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: Response([{"token": "12345678-first"}, {"error": "private data"}]))
    with pytest.raises(Judge0Error):
        executor.create_batch("python", "print(0)", [{"stdin": ""}, {"stdin": ""}], {"cpu_time_limit": 2, "memory_limit_kb": 128000})


def test_non_https_remote_executor_is_rejected():
    with pytest.raises(Judge0Error):
        Judge0Client({"JUDGE0_URL": "http://remote.example.test"}).languages()


def test_private_output_is_not_requested_for_grading(monkeypatch, executor):
    seen = []
    def request(*args, **kwargs):
        seen.append(kwargs["params"]["fields"])
        return Response({"submissions": [{"token": "one", "status": {"id": 3}}]})
    monkeypatch.setattr(requests, "request", request)
    executor.get_batch(["one"])
    executor.get_batch(["one"], include_output=True)
    assert seen[0] == "token,status,time,memory"
    assert "stdout" in seen[1]


@pytest.mark.parametrize("encoded", [
    "aGVsbG8sIEp1ZGdlMAo=",
    "aGVsbG8sIEp1ZGdlMAo=\n",
    "aGVsbG8sIEp1ZGdlMAo=\r\n",
    " \taGVsbG8sIEp1\nZGdlMAo=\r\n",
])
def test_output_accepts_base64_transport_whitespace(encoded):
    assert decoded_output(encoded) == "hello, Judge0\n"


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_output_preserves_unicode_and_program_line_breaks(line_ending):
    output = "Análise de autenticação\n192.0.2.10\n" * 8
    encoded = base64.encodebytes(output.encode("utf-8")).decode("ascii")
    assert decoded_output(encoded.replace("\n", line_ending)) == output


@pytest.mark.parametrize("encoded", ["!!!", "aGVsbG8sIEp1ZGdlMAo=#", "\u00a0aGVsbG8sIEp1ZGdlMAo="])
def test_output_rejects_invalid_base64(encoded):
    assert decoded_output(encoded) == "[Saída não disponível]"


@pytest.mark.parametrize("value", [None, "", False, 42])
def test_absent_output_is_empty(value):
    assert decoded_output(value) == ""


def test_output_keeps_its_display_limit_after_decoding():
    encoded = base64.encodebytes(("á" * 16001).encode("utf-8")).decode("ascii")
    assert decoded_output(encoded) == "á" * 16000


def test_output_replaces_invalid_utf8_without_losing_the_rest():
    assert decoded_output(base64.encodebytes(b"before\xffafter\n").decode("ascii")) == "before\ufffdafter\n"
