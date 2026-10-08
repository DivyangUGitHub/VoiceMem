import json
import logging

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from production.health import readiness
from production.observability import JsonFormatter, Metrics, Observability
from production.security import SecurityMiddleware, Settings

KEY = "k" * 32
AUTH = {"Authorization": f"Bearer {KEY}"}


def build():
    app = FastAPI()
    metrics = Metrics()

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaput")

    @app.get("/metrics")
    def metrics_endpoint():
        return PlainTextResponse(metrics.render())

    legacy = FastAPI()

    @legacy.get("/api/x")
    def x():
        return {}

    app.mount("/backend", legacy)
    wrapped = Observability(SecurityMiddleware(app, Settings(api_keys=(KEY,))), metrics)
    return TestClient(wrapped, raise_server_exceptions=False), metrics


def test_request_id_is_generated_and_returned():
    c, _ = build()
    rid = c.get("/healthz").headers["x-request-id"]
    assert len(rid) == 32 and c.get("/healthz").headers["x-request-id"] != rid


def test_valid_incoming_request_id_is_kept_and_junk_is_replaced():
    c, _ = build()
    assert c.get("/healthz", headers={"X-Request-ID": "trace-1234567890"}).headers["x-request-id"] == "trace-1234567890"
    bad = c.get("/healthz", headers={"X-Request-ID": "a b\tc;<script>"}).headers["x-request-id"]
    assert bad != "a b\tc;<script>" and len(bad) == 32


def test_access_log_has_fields_and_never_the_query_string(caplog):
    c, _ = build()
    with caplog.at_level(logging.INFO, logger="voicemem.access"):
        c.get("/backend/api/x?token=secret", headers=AUTH)
    rec = next(r for r in caplog.records if r.name == "voicemem.access")
    assert rec.fields["status"] == 200 and rec.fields["path"] == "/backend/api/x"
    assert "secret" not in json.dumps(rec.fields)


def test_probe_paths_are_quiet_unless_they_fail(caplog):
    c, _ = build()
    with caplog.at_level(logging.INFO, logger="voicemem.access"):
        c.get("/healthz")
    assert not [r for r in caplog.records if r.name == "voicemem.access"]


def test_rejected_requests_are_logged_with_their_real_status(caplog):
    c, _ = build()
    with caplog.at_level(logging.INFO, logger="voicemem.access"):
        c.get("/backend/api/x")
    assert [r.fields["status"] for r in caplog.records if r.name == "voicemem.access"] == [401]


def test_unhandled_error_is_counted_as_5xx():
    c, m = build()
    assert c.get("/boom").status_code == 500
    assert m.requests.get("5xx") == 1


def test_metrics_endpoint_requires_auth_and_counts_requests():
    c, _ = build()
    assert c.get("/metrics").status_code == 401
    c.get("/healthz")
    body = c.get("/metrics", headers=AUTH).text
    assert 'voicemem_http_requests_total{status="2xx"}' in body
    assert 'voicemem_http_requests_total{status="4xx"}' in body
    assert "voicemem_ws_active 0" in body


def test_json_formatter_emits_valid_json_with_request_id():
    rec = logging.LogRecord("voicemem.app", logging.WARNING, __file__, 1, "hello %s", ("world",), None)
    rec.request_id = "abc"
    rec.fields = {"path": "/x"}
    out = json.loads(JsonFormatter().format(rec))
    assert out["msg"] == "hello world" and out["request_id"] == "abc" and out["path"] == "/x" and out["level"] == "warning"


def test_readiness_reports_each_failing_check(tmp_path):
    frontend = tmp_path / "out"
    ok, checks = readiness(frontend, tmp_path / "mem", env={})
    assert not ok and checks["frontend"] != "ok" and checks["llm_credentials"] != "ok" and checks["memory_storage"] == "ok"

    frontend.mkdir()
    (frontend / "index.html").write_text("x")
    ok, checks = readiness(frontend, tmp_path / "mem", env={"OPENAI_API_KEY": "sk-test"})
    assert ok and set(checks.values()) == {"ok"}


def test_readiness_flags_unwritable_storage(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    ok, checks = readiness(tmp_path, blocker / "mem", env={"OPENAI_API_KEY": "x"})
    assert not ok and "not writable" in checks["memory_storage"]
