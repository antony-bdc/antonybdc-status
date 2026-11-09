"""Renders history.json into a static site: index.html, status.json and SVG badges."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from html import escape
from pathlib import Path

DAYS = 90
TEMPLATE = Path(__file__).with_name("template.html")


def day_state(d: dict | None) -> str:
    if not d or not d["checks"]:
        return "empty"
    ratio = d["up"] / d["checks"]
    if ratio == 1:
        return "ok"
    return "partial" if ratio >= 0.95 else "down"


def uptime(days: dict) -> float | None:
    checks = sum(d["checks"] for d in days.values())
    return None if not checks else sum(d["up"] for d in days.values()) / checks


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "\u2013"  # en dash
    return "100%" if value == 1 else f"{value * 100:.2f}%"


def fmt_duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{max(minutes, 1)} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours < 24 else f"{hours // 24} d {hours % 24} h"


def overall(services: dict) -> tuple[str, str]:
    states = [s.get("last", {}).get("up", True) for s in services.values()]
    if all(states):
        return "operational", "All systems operational"
    if not any(states):
        return "major", "Major outage"
    return "partial", "Partial outage"


def render_bars(days: dict, today: date) -> str:
    out = []
    for offset in range(DAYS - 1, -1, -1):
        d = (today - timedelta(days=offset)).isoformat()
        info = days.get(d)
        state = day_state(info)
        if info and info["checks"]:
            avg = f" · {round(info['latency_sum'] / info['latency_n'])} ms" if info["latency_n"] else ""
            tip = f"{d} · {fmt_pct(info['up'] / info['checks'])}{avg}"
        else:
            tip = f"{d} · no data"
        out.append(f'<i class="bar {state}" data-tip="{escape(tip)}"></i>')
    return "".join(out)


def render_service(sid: str, s: dict, today: date) -> str:
    last = s.get("last", {})
    up = last.get("up", True)
    host = s["url"].split("//", 1)[-1].split("/", 1)[0]
    latency = (
        f"{last['latency_ms']} ms" if up and last.get("latency_ms") is not None else escape(last.get("reason", ""))
    )
    return f"""
      <article class="service {"is-up" if up else "is-down"}" id="{escape(sid)}">
        <header>
          <div class="who">
            <span class="dot" aria-hidden="true"></span>
            <h3>{escape(s["name"])}</h3>
            <a href="{escape(s["url"])}" rel="noopener">{escape(host)}</a>
          </div>
          <div class="nums">
            <span class="now">{latency}</span>
            <span class="pct">{fmt_pct(uptime(s["days"]))}</span>
          </div>
        </header>
        <div class="bars" role="img" aria-label="Uptime of the last {DAYS} days">{render_bars(s["days"], today)}</div>
        <footer>
          <span><span class="d90">{DAYS}</span><span class="d45">45</span> days ago</span><span>today</span>
        </footer>
      </article>"""


def render_incidents(services: dict, now: datetime) -> str:
    rows = []
    for s in services.values():
        for inc in s.get("incidents", []):
            start = datetime.fromisoformat(inc["start"])
            end = datetime.fromisoformat(inc["end"]) if inc["end"] else None
            rows.append((start, end, s["name"], inc["reason"]))
    if not rows:
        return '<p class="empty-note">No incidents in the last 90 days.</p>'
    items = []
    for start, end, name, reason in sorted(rows, key=lambda r: r[0], reverse=True)[:20]:
        dur = fmt_duration(((end or now) - start).total_seconds())
        state = f"resolved after {dur}" if end else f"ongoing for {dur}"
        items.append(f"""
        <li class="{"open" if end is None else ""}">
          <time datetime="{start.isoformat()}">{start.strftime("%d %b %Y, %H:%M")} UTC</time>
          <div><strong>{escape(name)}:</strong> {escape(reason)}<span>{state}</span></div>
        </li>""")
    return f'<ol class="incidents">{"".join(items)}</ol>'


def badge(label: str, value: str, color: str) -> str:
    lw, vw = 7 * len(label) + 12, 7 * len(value) + 12
    aria = f"{escape(label)}: {escape(value)}"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{lw + vw}" height="20" role="img" aria-label="{aria}">
<rect width="{lw}" height="20" fill="#111"/><rect x="{lw}" width="{vw}" height="20" fill="{color}"/>
<g fill="#fff" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="11" text-anchor="middle">
<text x="{lw / 2}" y="14">{escape(label)}</text><text x="{lw + vw / 2}" y="14">{escape(value)}</text></g></svg>"""


def build(history_file: Path, out: Path, repo_url: str = "") -> None:
    history = json.loads(history_file.read_text()) if history_file.exists() else {"services": {}}
    services = history.get("services", {})
    now = datetime.fromisoformat(history["updated"]) if "updated" in history else datetime.now(UTC)
    state, headline = overall(services)

    html = TEMPLATE.read_text()
    replacements = {
        "{{STATE}}": state,
        "{{HEADLINE}}": headline,
        "{{UPDATED}}": now.isoformat(),
        "{{UPDATED_HUMAN}}": now.strftime("%d %b %Y, %H:%M UTC"),
        "{{SERVICES}}": "".join(render_service(k, v, now.date()) for k, v in services.items())
        or '<p class="empty-note">No checks yet.</p>',
        "{{INCIDENTS}}": render_incidents(services, now),
        "{{REPO_URL}}": escape(repo_url or "#"),
        "{{COUNT}}": str(len(services)),
    }
    for key, value in replacements.items():
        html = html.replace(key, value)

    html = "\n".join(line.rstrip() for line in html.splitlines()) + "\n"

    if out.exists():
        shutil.rmtree(out)
    (out / "badge").mkdir(parents=True)
    (out / "index.html").write_text(html)
    shutil.copytree(TEMPLATE.with_name("assets"), out / "assets")
    (out / ".nojekyll").write_text("")

    api = {"updated": now.isoformat(), "status": state, "services": {}}
    for sid, s in services.items():
        last = s.get("last", {})
        u = uptime(s["days"])
        api["services"][sid] = {
            "name": s["name"],
            "url": s["url"],
            "up": last.get("up"),
            "latency_ms": last.get("latency_ms"),
            "uptime_90d": u,
            "cert_expires": last.get("cert_expires"),
        }
        color = "#1f9d55" if last.get("up", True) else "#e5484d"
        (out / "badge" / f"{sid}.svg").write_text(badge(s["name"].lower(), fmt_pct(u), color))
    (out / "status.json").write_text(json.dumps(api, indent=2))


if __name__ == "__main__":
    import os
    import sys

    build(
        Path(sys.argv[1] if len(sys.argv) > 1 else "data/history.json"),
        Path(sys.argv[2] if len(sys.argv) > 2 else "site"),
        os.environ.get("REPO_URL", ""),
    )
