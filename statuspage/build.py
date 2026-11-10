"""Renders history.json into a static site: index.html, status.json and SVG badges."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from html import escape
from pathlib import Path

DAYS = 90
SPARK_DAYS = 30
TEMPLATE = Path(__file__).with_name("template.html")
NNBSP = "\N{NARROW NO-BREAK SPACE}"  # between number and % (Swiss typography)
DASH = "\N{EN DASH}"
MONTHS = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."]


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
        return DASH
    if value == 1:
        return f"100{NNBSP}%"
    return f"{value * 100:.2f}{NNBSP}%".replace(".", ",")


def fmt_duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{max(minutes, 1)} Min."
    hours, minutes = divmod(minutes, 60)
    return f"{hours} Std. {minutes} Min." if hours < 24 else f"{hours // 24} T. {hours % 24} Std."


def fmt_day(d: date) -> str:
    return f"{d.day}. {MONTHS[d.month - 1]} {d.year}"


def overall(services: dict) -> tuple[str, str, str]:
    """State plus a two-line headline (second line is shown in grey, like on antonybdc.me)."""
    states = [s.get("last", {}).get("up", True) for s in services.values()]
    if all(states):
        return "operational", "Alle Systeme", "laufen."
    if not any(states):
        return "major", "Grosse", "Störung."
    return "partial", "Teilweise", "gestört."


def avg_latency(days: dict, last_n: int, today: date) -> float | None:
    total = n = 0
    for offset in range(last_n):
        d = days.get((today - timedelta(days=offset)).isoformat())
        if d and d.get("latency_n"):
            total += d["latency_sum"]
            n += d["latency_n"]
    return total / n if n else None


def render_bars(days: dict, today: date) -> str:
    out = []
    for offset in range(DAYS - 1, -1, -1):
        day = today - timedelta(days=offset)
        info = days.get(day.isoformat())
        state = day_state(info)
        if info and info["checks"]:
            avg = f" · Ø {round(info['latency_sum'] / info['latency_n'])} ms" if info["latency_n"] else ""
            tip = f"{fmt_day(day)} · {fmt_pct(info['up'] / info['checks'])} erreichbar{avg}"
        else:
            tip = f"{fmt_day(day)} · keine Daten"
        out.append(f'<i class="bar {state}" data-tip="{escape(tip)}"></i>')
    return "".join(out)


def render_spark(days: dict, today: date) -> str:
    """Daily average response time of the last 30 days as a small SVG line."""
    points = []
    for offset in range(SPARK_DAYS - 1, -1, -1):
        d = days.get((today - timedelta(days=offset)).isoformat())
        if d and d.get("latency_n"):
            points.append((SPARK_DAYS - 1 - offset, d["latency_sum"] / d["latency_n"]))
    if len(points) < 2:
        return '<p class="spark-empty">Kurve erscheint ab dem zweiten Messtag.</p>'
    hi = max(v for _, v in points)
    lo = min(v for _, v in points)
    span = (hi - lo) or 1
    w, h = 300, 56

    def xy(i: int, v: float) -> tuple[float, float]:
        return i / (SPARK_DAYS - 1) * w, h - 6 - (v - lo) / span * (h - 14)

    line = " ".join(f"{'M' if k == 0 else 'L'}{x:.1f},{y:.1f}" for k, (x, y) in enumerate(xy(i, v) for i, v in points))
    first_x = xy(*points[0])[0]
    last_x = xy(*points[-1])[0]
    area = f"{line} L{last_x:.1f},{h} L{first_x:.1f},{h} Z"
    return f"""<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img"
      aria-label="Antwortzeit der letzten {SPARK_DAYS} Tage, {round(lo)} bis {round(hi)} ms">
      <path class="area" d="{area}"/><path class="line" d="{line}"/></svg>
      <div class="spark-scale"><span>{round(lo)} ms</span><span>{round(hi)} ms</span></div>"""


def render_service(sid: str, s: dict, today: date) -> str:
    last = s.get("last", {})
    up = last.get("up", True)
    host = s["url"].split("//", 1)[-1].rstrip("/")
    now = f"Online · {last['latency_ms']} ms" if up and last.get("latency_ms") is not None else last.get("reason", "")
    return f"""
      <article class="service {"is-up" if up else "is-down"}" id="{escape(sid)}">
        <header>
          <div class="who">
            <h3>{escape(s["name"])}</h3>
            <a href="{escape(s["url"])}" rel="noopener">{escape(host)} ↗</a>
          </div>
          <div class="pct"><span class="label">Erreichbar · 90 Tage</span>{fmt_pct(uptime(s["days"]))}</div>
        </header>
        <p class="now"><span class="dot" aria-hidden="true"></span>{escape(now)}</p>
        <div class="spark-wrap">
          <span class="label">Antwortzeit · {SPARK_DAYS} Tage</span>{render_spark(s["days"], today)}
        </div>
        <div class="bars" role="img" aria-label="Verfügbarkeit der letzten {DAYS} Tage">
          {render_bars(s["days"], today)}
        </div>
        <footer>
          <span>vor <span class="d90">{DAYS}</span><span class="d45">45</span> Tagen</span><span>heute</span>
        </footer>
      </article>"""


def render_diagram(services: dict) -> str:
    """How the page works: GitHub Actions sends a request to every service, the answer comes back."""
    items = list(services.items())
    n = max(len(items), 1)
    height = max(260, 90 * n)
    src_y = height / 2
    parts = [
        f'<svg class="flow" viewBox="0 0 560 {height}" role="img" aria-labelledby="flow-t">',
        '<title id="flow-t">GitHub Actions ruft alle 10 Minuten jeden Dienst auf und misst die Antwort.</title>',
        f'<g class="node src"><rect x="0" y="{src_y - 34}" width="168" height="68" rx="14"/>',
        f'<text x="18" y="{src_y - 6}" class="t1">GitHub Actions</text>',
        f'<text x="18" y="{src_y + 16}" class="t2">alle 10 Minuten</text></g>',
    ]
    for k, (sid, s) in enumerate(items):
        y = (k + 0.5) * height / n
        last = s.get("last", {})
        up = last.get("up", True)
        cls = "up" if up else "down"
        path = f"M168,{src_y:.0f} C280,{src_y:.0f} 280,{y:.0f} 392,{y:.0f}"
        dur = 2.4 + k * 0.35
        label = f"{last['latency_ms']} ms" if up and last.get("latency_ms") is not None else "keine Antwort"
        parts += [
            f'<path id="p-{escape(sid)}" class="wire {cls}" d="{path}"/>',
            f'<circle class="pkt {cls}" r="4"><animateMotion dur="{dur}s" repeatCount="indefinite" '
            f'keyPoints="0;1;0" keyTimes="0;0.5;1" calcMode="linear">'
            f'<mpath href="#p-{escape(sid)}"/></animateMotion></circle>',
            f'<g class="node dst {cls}"><rect x="392" y="{y - 28:.0f}" width="168" height="56" rx="14"/>',
            f'<circle class="led" cx="412" cy="{y:.0f}" r="5"/>',
            f'<text x="428" y="{y - 3:.0f}" class="t1">{escape(s["name"])}</text>',
            f'<text x="428" y="{y + 16:.0f}" class="t2">{escape(label)}</text></g>',
        ]
    parts.append("</svg>")
    return "".join(parts)


def render_incidents(services: dict, now: datetime) -> str:
    rows = []
    for s in services.values():
        for inc in s.get("incidents", []):
            start = datetime.fromisoformat(inc["start"])
            end = datetime.fromisoformat(inc["end"]) if inc["end"] else None
            rows.append((start, end, s["name"], inc["reason"]))
    if not rows:
        return '<p class="empty-note">Keine Störungen in den letzten 90 Tagen.</p>'
    items = []
    for start, end, name, reason in sorted(rows, key=lambda r: r[0], reverse=True)[:20]:
        dur = fmt_duration(((end or now) - start).total_seconds())
        state = f"behoben nach {dur}" if end else f"seit {dur}"
        items.append(f"""
        <li class="{"open" if end is None else ""}">
          <time datetime="{start.isoformat()}">{fmt_day(start.date())}, {start.strftime("%H:%M")} UTC</time>
          <div><strong>{escape(name)}:</strong> {escape(reason)}<span>{state}</span></div>
        </li>""")
    return f'<ol class="incidents">{"".join(items)}</ol>'


def badge(label: str, value: str, color: str) -> str:
    value = value.replace(NNBSP, "")
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
    state, line1, line2 = overall(services)

    all_days: dict[str, dict] = {}
    for i, s in enumerate(services.values()):
        for d, v in s["days"].items():
            all_days[f"{i}:{d}"] = v
    latency = [x for x in (avg_latency(s["days"], 1, now.date()) for s in services.values()) if x is not None]
    incident_count = sum(len(s.get("incidents", [])) for s in services.values())

    html = TEMPLATE.read_text()
    replacements = {
        "{{STATE}}": state,
        "{{HEADLINE}}": f"{line1} {line2}",
        "{{LINE1}}": escape(line1),
        "{{LINE2}}": escape(line2),
        "{{UPDATED}}": now.isoformat(),
        "{{UPDATED_HUMAN}}": f"{fmt_day(now.date())}, {now.strftime('%H:%M')} UTC",
        "{{UPTIME}}": fmt_pct(uptime(all_days)),
        "{{LATENCY}}": f"{round(sum(latency) / len(latency))} ms" if latency else DASH,
        "{{INCIDENT_COUNT}}": str(incident_count),
        "{{DIAGRAM}}": render_diagram(services) if services else "",
        "{{SERVICES}}": "".join(render_service(k, v, now.date()) for k, v in services.items())
        or '<p class="empty-note">Noch keine Messungen.</p>',
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
