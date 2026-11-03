"""Plain text output: the list table (CL22), the status sections (CL24) and the "Why" line (CL25)."""
import json
from datetime import datetime
from typing import Callable, Optional

COLORS = {"HEALTHY": "32", "PROGRESSING": "33", "DEGRADED": "33", "FAILING": "31", "UNKNOWN": "90"}


def paint(text: str, health: str, color: bool) -> str:
    return f"\033[{COLORS[health]}m{text}\033[0m" if color and health in COLORS else text


def age(created_at: Optional[str], now: float) -> str:
    if not created_at:
        return "-"
    try:
        seconds = int(now - datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return "-"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{max(seconds, 0)}s"


def table(headers: list[str], rows: list[list[str]], painters: Optional[dict[int, Callable[[str], str]]] = None) -> list[str]:
    """Columns padded on plain text, so colour codes never break the alignment."""
    painters = painters or {}
    widths = [max(len(str(c)) for c in col) for col in zip(headers, *rows)]
    def line(cells):
        out = []
        for i, (cell, w) in enumerate(zip(cells, widths)):
            text = str(cell)
            out.append(painters[i](text) + " " * (w - len(text)) if i in painters else text.ljust(w))
        return "  ".join(out).rstrip()
    return [line(headers)] + [line(r) for r in rows]


def as_json(data) -> str:
    return json.dumps(data, indent=2)


def list_table(items: list[dict], now: float, color: bool) -> list[str]:
    if not items:
        return ["No deployments found."]
    items = sorted(items, key=lambda d: (d["namespace"], d["name"]))
    rows = [[d["namespace"], d["name"], d["uid"], f"{d['replicas']['ready']}/{d['replicas']['desired']}",
             d["health"], d["image"], age(d.get("createdAt"), now)] for d in items]
    return table(["NAMESPACE", "NAME", "UID", "READY", "HEALTH", "IMAGE", "AGE"], rows,
                 {4: lambda t: paint(t, t, color)})


def _short(text: Optional[str], limit: int = 120) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def why(d: dict) -> Optional[str]:
    """CL25: the first failing reason, for FAILING and DEGRADED deployments only."""
    if d.get("health") not in ("FAILING", "DEGRADED"):
        return None
    for pod in d.get("pods") or []:
        for c in pod.get("containerStatuses") or []:
            if c.get("state") != "running" and c.get("reason"):
                msg = f" ({_short(c.get('message'))})" if c.get("message") else ""
                return f"pod {pod['name']}, container {c['name']}: {c['reason']}{msg}"
    conds = d.get("conditions") or []
    for cond in sorted(conds, key=lambda c: c.get("reason") != "ProgressDeadlineExceeded"):
        if cond.get("reason") == "ProgressDeadlineExceeded" or (cond.get("status") == "False" and cond.get("reason")):
            msg = f" ({_short(cond.get('message'))})" if cond.get("message") else ""
            return f"condition {cond['type']}: {cond['reason']}{msg}"
    return None


def status_sections(d: dict, now: float, color: bool) -> list[str]:
    r = d["replicas"]
    out = [f"Name:       {d['name']}", f"Namespace:  {d['namespace']}", f"UID:        {d['uid']}",
           f"Image:      {d['image']}", f"Health:     {paint(d['health'], d['health'], color)}",
           f"Age:        {age(d.get('createdAt'), now)}"]
    reason = why(d)
    if reason:
        out += ["", f"Why: {reason}"]
    out += ["", "Replicas", f"  desired      {r['desired']}", f"  ready        {r['ready']}",
            f"  available    {r['available']}", f"  unavailable  {r['unavailable']}", "", "Conditions"]
    conds = d.get("conditions") or []
    if not conds:
        out.append("  (none)")
    for c in conds:
        extra = f": {_short(c.get('message'))}" if c.get("message") else ""
        out.append(f"  {c['type']}={c['status']}  {c.get('reason') or '-'}{extra}")
    pods = d.get("pods") or []
    out += ["", f"Pods ({len(pods)})"]
    if not pods:
        out.append("  (none)")
        return out
    rows = [[p["name"], p["phase"], str(p["restartCount"]), p.get("node") or "-"] for p in pods]
    out += ["  " + ln for ln in table(["NAME", "PHASE", "RESTARTS", "NODE"], rows)]
    for p in pods:
        for c in p.get("containerStatuses") or []:
            detail = f" {c['reason']}" if c.get("reason") else ""
            msg = f" - {_short(c.get('message'))}" if c.get("message") else ""
            out.append(f"    {p['name']} / {c['name']}: {c['state']}{detail}{msg} (ready={str(c['ready']).lower()}, restarts={c['restartCount']})")
    return out
