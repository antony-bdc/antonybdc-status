"""Runs the checks and updates history.json. Standard library only."""

from __future__ import annotations

import json
import socket
import ssl
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

HISTORY_DAYS = 90
USER_AGENT = "status-page-checker/1.0 (+https://github.com/businessantonybdc-crypto/status-page)"


@dataclass
class Result:
    up: bool
    latency_ms: int | None
    status: int | None
    reason: str
    cert_expires: str | None = None


def load_services(path: Path) -> list[dict]:
    services = tomllib.loads(path.read_text()).get("service", [])
    ids = [s["id"] for s in services]
    if len(ids) != len(set(ids)):
        raise ValueError("service ids must be unique")
    return services


def cert_expiry(url: str, timeout: float) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None
    host, port = parsed.hostname, parsed.port or 443
    ctx = ssl.create_default_context()
    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as sock,
            ctx.wrap_socket(sock, server_hostname=host) as tls,
        ):
            not_after = tls.getpeercert()["notAfter"]
    except (OSError, ssl.SSLError, KeyError):
        return None
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), UTC).isoformat()


def check(service: dict) -> Result:
    url = service["url"]
    timeout = float(service.get("timeout", 15))
    expect = int(service.get("expect_status", 200))
    keyword = service.get("keyword")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(512_000)
            status = resp.status
    except urllib.error.HTTPError as exc:
        status, body = exc.code, b""
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return Result(False, None, None, f"connection failed: {reason}")
    latency = round((time.perf_counter() - start) * 1000)

    if status != expect:
        return Result(False, latency, status, f"HTTP {status}, expected {expect}")
    if keyword and keyword.encode() not in body:
        return Result(False, latency, status, f'keyword "{keyword}" not found')
    return Result(True, latency, status, "OK", cert_expiry(url, timeout))


def record(history: dict, service: dict, result: Result, now: datetime) -> None:
    """Adds one check result: daily aggregate, last result and incident tracking."""
    entry = history.setdefault("services", {}).setdefault(service["id"], {"days": {}, "incidents": []})
    entry["name"] = service["name"]
    entry["url"] = service["url"]

    day = entry["days"].setdefault(now.date().isoformat(), {"checks": 0, "up": 0, "latency_sum": 0, "latency_n": 0})
    day["checks"] += 1
    day["up"] += int(result.up)
    if result.latency_ms is not None and result.up:
        day["latency_sum"] += result.latency_ms
        day["latency_n"] += 1

    open_incident = next((i for i in entry["incidents"] if i.get("end") is None), None)
    if not result.up and open_incident is None:
        entry["incidents"].append({"start": now.isoformat(), "end": None, "reason": result.reason})
    elif result.up and open_incident is not None:
        open_incident["end"] = now.isoformat()

    entry["last"] = {
        "time": now.isoformat(),
        "up": result.up,
        "latency_ms": result.latency_ms,
        "status": result.status,
        "reason": result.reason,
        "cert_expires": result.cert_expires,
    }

    # Keep the file small: 90 days of aggregates, incidents from the same period.
    cutoff = (now - timedelta(days=HISTORY_DAYS)).date().isoformat()
    entry["days"] = {d: v for d, v in entry["days"].items() if d >= cutoff}
    entry["incidents"] = [i for i in entry["incidents"] if i["end"] is None or i["end"][:10] >= cutoff]


def prune_removed(history: dict, services: list[dict]) -> None:
    keep = {s["id"] for s in services}
    history["services"] = {k: v for k, v in history.get("services", {}).items() if k in keep}


def main(config: Path, history_file: Path) -> int:
    services = load_services(config)
    history = json.loads(history_file.read_text()) if history_file.exists() else {}
    prune_removed(history, services)
    now = datetime.now(UTC).replace(microsecond=0)
    down = 0
    for service in services:
        result = check(service)
        record(history, service, result, now)
        down += not result.up
        mark = "UP  " if result.up else "DOWN"
        print(f"{mark} {service['id']:<20} {result.latency_ms or '-':>6} ms  {result.reason}")
    history["updated"] = now.isoformat()
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(json.dumps(history, indent=1, sort_keys=True))
    # Exit 0 even when something is down: an outage is data, not a failed workflow.
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(
        main(
            Path(sys.argv[1] if len(sys.argv) > 1 else "sites.toml"),
            Path(sys.argv[2] if len(sys.argv) > 2 else "data/history.json"),
        )
    )
