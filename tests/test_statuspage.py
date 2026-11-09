import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from statuspage import build, check

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
SVC = {"id": "web", "name": "Web", "url": "http://x"}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        code = {"/ok": 200, "/missing": 404}.get(self.path, 200)
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b"hello homelab")

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_check_up(server):
    r = check.check({"url": f"{server}/ok"})
    assert r.up and r.status == 200 and r.latency_ms is not None


def test_check_wrong_status(server):
    r = check.check({"url": f"{server}/missing"})
    assert not r.up and r.reason == "HTTP 404, expected 200"


def test_check_keyword(server):
    assert check.check({"url": f"{server}/ok", "keyword": "homelab"}).up
    assert not check.check({"url": f"{server}/ok", "keyword": "nope"}).up


def test_check_connection_refused():
    r = check.check({"url": "http://127.0.0.1:1/", "timeout": 2})
    assert not r.up and r.reason.startswith("connection failed")


def test_incident_opens_and_closes():
    h = {}
    check.record(h, SVC, check.Result(True, 50, 200, "OK"), NOW)
    check.record(h, SVC, check.Result(False, None, None, "connection failed"), NOW + timedelta(minutes=10))
    check.record(h, SVC, check.Result(False, None, None, "connection failed"), NOW + timedelta(minutes=20))
    inc = h["services"]["web"]["incidents"]
    assert len(inc) == 1 and inc[0]["end"] is None
    check.record(h, SVC, check.Result(True, 60, 200, "OK"), NOW + timedelta(minutes=30))
    assert inc[0]["end"] == (NOW + timedelta(minutes=30)).isoformat()
    day = h["services"]["web"]["days"]["2026-11-09"]
    assert day == {"checks": 4, "up": 2, "latency_sum": 110, "latency_n": 2}


def test_history_is_trimmed_to_90_days():
    h = {}
    for d in range(100):
        check.record(h, SVC, check.Result(True, 10, 200, "OK"), NOW + timedelta(days=d))
    assert len(h["services"]["web"]["days"]) == 91


def test_removed_services_are_pruned():
    h = {"services": {"old": {}, "web": {}}}
    check.prune_removed(h, [SVC])
    assert list(h["services"]) == ["web"]


def test_build_renders_page_api_and_badges(tmp_path):
    h = {}
    check.record(h, SVC, check.Result(True, 42, 200, "OK"), NOW)
    check.record(
        h,
        {"id": "mc", "name": "Mine<craft>", "url": "https://mc"},
        check.Result(False, None, None, "connection failed"),
        NOW,
    )
    h["updated"] = NOW.isoformat()
    (tmp_path / "history.json").write_text(json.dumps(h))
    out = tmp_path / "site"
    build.build(tmp_path / "history.json", out, "https://github.com/x/status-page")

    html = (out / "index.html").read_text()
    assert "Partial outage" in html and 'class="is-partial"' in html
    assert "Mine&lt;craft&gt;" in html  # names are escaped
    assert html.count('class="bar ') == 180  # 90 bars per service
    assert "{{" not in html  # every placeholder replaced
    api = json.loads((out / "status.json").read_text())
    assert api["services"]["web"]["uptime_90d"] == 1 and api["services"]["mc"]["up"] is False
    assert (out / "badge" / "web.svg").read_text().startswith("<svg")
    assert (out / "assets" / "inter-tight-latin-600-normal.woff2").exists()


@pytest.mark.parametrize(("up", "checks", "state"), [(10, 10, "ok"), (96, 100, "partial"), (5, 10, "down")])
def test_day_state(up, checks, state):
    assert build.day_state({"checks": checks, "up": up}) == state
