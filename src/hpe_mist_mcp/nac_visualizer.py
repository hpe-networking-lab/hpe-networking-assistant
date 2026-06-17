"""NAC (Access Assurance) visualizer — aggregates NAC data into a self-contained
HTML dashboard. No external dependencies: charts are rendered as inline CSS bars
so the saved .html opens anywhere, offline.
"""

from __future__ import annotations

import html
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .mist_client import MistClient


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _is_failure(event: Dict[str, Any]) -> bool:
    t = str(event.get("type", "")).upper()
    return any(k in t for k in ("DENY", "DENIED", "FAIL", "REJECT", "ERROR"))


def _first(d: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if d.get(k) is not None:
            return d.get(k)
    return None


def build_nac_overview(
    client: MistClient, org_id: str, org_name: Optional[str] = None, duration: str = "1d",
) -> Dict[str, Any]:
    """Aggregate NAC clients and events into counts for the dashboard."""
    clients = client.search_nac_clients(org_id, duration=duration, limit=1000)
    events = client.search_nac_events(org_id, duration=duration, limit=1000)

    by_type = Counter(str(_first(c, "type") or "unknown") for c in clients)
    by_auth = Counter(str(_first(c, "auth_type") or "unknown") for c in clients)
    by_status = Counter(str(_first(c, "last_status", "status") or "unknown") for c in clients)
    event_types = Counter(str(e.get("type") or "unknown") for e in events)

    failures = [e for e in events if _is_failure(e)]
    fail_users = Counter(str(e.get("username") or "unknown") for e in failures)
    fail_rules = Counter(
        str(_first(e, "nacrule_name", "last_nacrule_name") or "unknown") for e in failures
    )

    return {
        "generated_at": _now(),
        "org_id": org_id,
        "org_name": org_name,
        "duration": duration,
        "client_count": len(clients),
        "event_count": len(events),
        "failure_count": len(failures),
        "by_client_type": by_type.most_common(),
        "by_auth_type": by_auth.most_common(),
        "by_status": by_status.most_common(),
        "by_event_type": event_types.most_common(10),
        "top_failing_users": fail_users.most_common(10),
        "top_failing_rules": fail_rules.most_common(10),
    }


def _bars(items: Sequence[Tuple[str, int]], fail: bool = False) -> str:
    if not items:
        return '<p class="muted">No data.</p>'
    top = max((v for _, v in items), default=1) or 1
    cls = "bar fail" if fail else "bar"
    rows = []
    for label, value in items:
        pct = max(2, round(100 * value / top))
        rows.append(
            f'<div class="bar-row"><div class="bar-label" title="{html.escape(str(label))}">'
            f'{html.escape(str(label))}</div>'
            f'<div class="{cls}" style="width:{pct}%"></div>'
            f'<div class="bar-val">{value}</div></div>'
        )
    return "\n".join(rows)


def render_nac_dashboard_html(data: Dict[str, Any]) -> str:
    org = html.escape(str(data.get("org_name") or data.get("org_id")))
    ev = data["event_count"]
    fails = data["failure_count"]
    success_rate = f"{round(100 * (ev - fails) / ev)}%" if ev else "n/a"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NAC Dashboard — {org}</title>
<style>
  body {{ font-family: system-ui, "Segoe UI", Arial, sans-serif; margin: 28px; color: #1e2327; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 15px; margin: 26px 0 8px; }}
  .muted {{ color: #888; font-size: 12px; }}
  .cards {{ display: flex; gap: 14px; flex-wrap: wrap; margin: 18px 0; }}
  .card {{ border: 1px solid #e2e2e2; border-radius: 10px; padding: 14px 20px; min-width: 130px; }}
  .card .n {{ font-size: 28px; font-weight: 700; }}
  .card .l {{ color: #666; font-size: 12px; margin-top: 2px; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; margin: 5px 0; }}
  .bar-label {{ width: 200px; font-size: 13px; text-align: right; overflow: hidden;
                text-overflow: ellipsis; white-space: nowrap; }}
  .bar {{ height: 16px; background: #3a7afe; border-radius: 4px; min-width: 2px; }}
  .bar.fail {{ background: #e2483d; }}
  .bar-val {{ font-size: 12px; color: #444; }}
</style></head>
<body>
  <h1>NAC Dashboard — {org}</h1>
  <div class="muted">Generated {html.escape(data['generated_at'])} · window {html.escape(str(data['duration']))}</div>
  <div class="cards">
    <div class="card"><div class="n">{data['client_count']}</div><div class="l">NAC clients</div></div>
    <div class="card"><div class="n">{ev}</div><div class="l">Auth events</div></div>
    <div class="card"><div class="n">{fails}</div><div class="l">Failures</div></div>
    <div class="card"><div class="n">{success_rate}</div><div class="l">Success rate</div></div>
  </div>

  <h2>Authentication types</h2>
  {_bars(data['by_auth_type'])}

  <h2>Client types</h2>
  {_bars(data['by_client_type'])}

  <h2>Client status</h2>
  {_bars(data['by_status'])}

  <h2>Event types</h2>
  {_bars(data['by_event_type'])}

  <h2>Top failing users</h2>
  {_bars(data['top_failing_users'], fail=True)}

  <h2>Top failing auth rules</h2>
  {_bars(data['top_failing_rules'], fail=True)}
</body></html>"""
