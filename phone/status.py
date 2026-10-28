"""The snapshot ``status()`` returns, and every sentence the app shows. Standard library only.

The app draws what this module says; it adds no wording of its own. Every number is measured: progress from the
engine's per-repo lines of the current pass (``RunState``) against the pass's repo count, the ETA from the recent
rate, the reset time as a clock time, the next cycle in whole days. The progress samples and the data size are taken
by the monitor thread on a timer (``Progress``, ``DataSize``), so ``build`` itself only reads memory and a few marker
files.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from phone.archive import LAYERS
from phone.events import iso_local, redact
from phone.httpgate import TOKEN_HOSTS, WHAT
from phone.runner import STEP_NAMES, marker_ts

SCHEMA = 1
STUCK_AFTER_S = 25 * 60
ETA_WARMUP_S = 20 * 60
FINISHED_FOR_S = 3600
RESTART_NOTE_S = 1800
STEP_ORDER = ("L1", "L0", "L2", "L3g", "L3c", "L4", "LK")
STEP_PHRASE = {
    "L1": "pull requests",
    "L0": "project details",
    "L2": "dependents and releases",
    "L3g": "governance files",
    "L3c": "commits and issues",
    "L4": "contributor history",
    "LK": "package data",
}
WORKSTATION = "Your workstation, only when it asks (link token)"


# ---------------------------------------------------------------------------------------------------------------
# Words and numbers
# ---------------------------------------------------------------------------------------------------------------
def clock_text(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def date_text(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    return f"{d.day} {d:%b}"


def datetime_text(ts: float) -> str:
    return f"{date_text(ts)}, {clock_text(ts)}"


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def span_text(seconds: float, up: bool = True) -> str:
    """A length of time in plain words: 'less than a minute', '38 minutes', '3 hours', '2 days'.

    ``up`` rounds minutes up (time still to wait); ``up=False`` rounds them down (time already gone)."""
    seconds = max(0.0, seconds)
    if seconds < 60:
        return "less than a minute"
    if seconds < 90 * 60:
        return plural(max(1, math.ceil(seconds / 60) if up else int(seconds // 60)), "minute")
    if seconds < 48 * 3600:
        return plural(max(1, round(seconds / 3600)), "hour")
    return plural(max(1, round(seconds / 86400)), "day")


def whole_days(ts: float, now: float) -> int:
    """Whole days from ``now`` to ``ts``, rounded down, never negative."""
    return max(0, int((ts - now) // 86400))


def days_text(n: int) -> str:
    return "today" if n == 0 else f"in {plural(n, 'day')}"


def stopped_note(k: int) -> str:
    """' · K need only the pull-request check' (projects stopped at the black-hole check), or '' when there are none."""
    return f" \u00b7 {k:,} {'needs' if k == 1 else 'need'} only the pull-request check" if k else ""


def eta_words(seconds: float) -> str:
    if seconds < 60:
        return "Less than a minute left in this step"
    if seconds < 3600:
        return f"About {plural(max(1, round(seconds / 60)), 'minute')} left in this step"
    if seconds < 48 * 3600:
        return f"About {plural(max(1, round(seconds / 3600)), 'hour')} left in this step"
    return f"About {plural(max(1, round(seconds / 86400)), 'day')} left in this step"


def looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------------------------------------------
# Measured by the monitor thread
# ---------------------------------------------------------------------------------------------------------------
def count_json(folder: Path) -> int:
    """Envelopes (``*.json`` files) directly in ``folder``; 0 when it does not exist."""
    try:
        with os.scandir(folder) as it:
            return sum(1 for e in it if e.name.endswith(".json") and not e.name.startswith("."))
    except OSError:
        return 0


class Progress:
    """The current pass's checked-repo count against its repo count, and the rate those counts imply."""

    TTL_S = 15.0
    SAMPLE_EVERY_S = 60.0
    KEEP_S = 3 * 3600.0

    def __init__(self, home: Path | str, clock: Callable[[], float]) -> None:
        self.home = Path(home)
        self._clock = clock
        self._lock = threading.Lock()
        self.layer: str | None = None
        self.run = 0
        self.done = 0
        self.total = 0
        self.changed_ts: float | None = None
        self._active_s = 0.0
        self._last_tick: float | None = None
        self._samples: list[tuple[float, int]] = []
        self._counted_at = -1e9
        self._cand_key: tuple[int, int] | None = None
        self._cand_n = 0

    def candidates(self) -> int:
        """Valid lines in ``raw/candidates.jsonl`` (an owner and a name), cached by size and mtime."""
        path = self.home / "raw" / "candidates.jsonl"
        try:
            st = path.stat()
        except OSError:
            return 0
        key = (st.st_mtime_ns, st.st_size)
        if key != self._cand_key:
            n = 0
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(row, dict) and row.get("owner") and row.get("name"):
                            n += 1
            except OSError:
                return self._cand_n
            self._cand_key, self._cand_n = key, n
        return self._cand_n

    def tick(self, layer: str | None, run: int, checked: int, total: int, active: bool, force: bool = False) -> None:
        now = self._clock()
        with self._lock:
            dt = 0.0 if self._last_tick is None else min(max(now - self._last_tick, 0.0), 30.0)
            self._last_tick = now
            if active:
                self._active_s += dt
            if (layer, run) != (self.layer, self.run):
                self.layer, self.run, self.done, self.total = layer, run, 0, 0
                self._samples, self._counted_at = [], -1e9
            if layer is None:
                return
            if not force and now - self._counted_at < self.TTL_S:
                return
            self._counted_at = now
            done, total = checked, total or self.candidates()
            if done > self.done:
                self.changed_ts = now
            self.done, self.total = done, total
            if active and (not self._samples or done != self._samples[-1][1]
                           or self._active_s - self._samples[-1][0] >= self.SAMPLE_EVERY_S):
                self._samples.append((self._active_s, done))
                self._samples = [s for s in self._samples if self._active_s - s[0] <= self.KEEP_S]

    def snapshot(self) -> tuple[int, int]:
        with self._lock:
            return self.done, self.total

    def eta_text(self) -> str:
        with self._lock:
            done, total, samples = self.done, self.total, list(self._samples)
        if total <= 0:
            return ""
        if done >= total:
            return "Finishing this step"
        if len(samples) < 2 or samples[-1][0] - samples[0][0] < ETA_WARMUP_S:
            return "Estimate improves as it runs"
        span = samples[-1][0] - samples[0][0]
        rate = (samples[-1][1] - samples[0][1]) / span
        if rate <= 0:
            return "Not enough progress yet to estimate"
        return eta_words((total - done) / rate)


class DataSize:
    """Sizes of the collector folders, measured on a timer in a thread of their own (a full home is ~220k files)."""

    TTL_S = 1800.0

    def __init__(self, home: Path | str, clock: Callable[[], float]) -> None:
        self.home = Path(home)
        self._clock = clock
        self._lock = threading.Lock()
        self.sizes: dict[str, int] = {}
        self.measured_at: float | None = None
        self._thread: threading.Thread | None = None

    @staticmethod
    def _tree(path: Path) -> int:
        total = 0
        stack = [path]
        while stack:
            try:
                with os.scandir(stack.pop()) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
            except OSError:
                continue  # a folder that vanished while it was walked (a layer being staged)
        return total

    def measure(self) -> None:
        raw = self.home / "raw"
        sizes: dict[str, int] = {}
        try:
            with os.scandir(raw) as it:
                names = [e.name for e in it if e.is_dir(follow_symlinks=False)]
        except OSError:
            names = []
        for name in names:
            sizes[name] = self._tree(raw / name)
        with self._lock:
            self.sizes, self.measured_at = sizes, self._clock()

    def maybe_refresh(self, force: bool = False, block: bool = False) -> None:
        with self._lock:
            due = self.measured_at is None or self._clock() - self.measured_at >= self.TTL_S
            busy = self._thread is not None and self._thread.is_alive()
            if not (due or force) or busy:
                return
            self._thread = threading.Thread(target=self.measure, daemon=True, name="porchlamp-size")
            thread = self._thread
        thread.start()
        if block:
            thread.join()

    def data_gb(self) -> float:
        with self._lock:
            return sum(self.sizes.values()) / 1e9

    def ready_gb(self, completed: list[str]) -> float:
        with self._lock:
            wanted = {c for layer in completed for c in LAYERS.get(layer, ())}
            return sum(size for name, size in self.sizes.items() if name in wanted) / 1e9


# ---------------------------------------------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------------------------------------------
def layer_times(home: Path) -> dict[str, float]:
    """Layer -> time of its ``state/refresh/<L>.complete`` marker."""
    out: dict[str, float] = {}
    for layer in STEP_ORDER:
        ts = marker_ts(home / "state" / "refresh" / f"{layer}.complete")
        if ts is not None:
            out[layer] = ts
    return out


def previous_finish(home: Path) -> float | None:
    """The newest finish time among the retired markers of earlier cycles (``state/refresh-prev``)."""
    prev = home / "state" / "refresh-prev"
    best: float | None = None
    try:
        names = [p for p in prev.iterdir() if p.name.startswith(("LK.complete", "MAIN.done"))]
    except OSError:
        return None
    for path in names:
        ts = marker_ts(path)
        if ts is not None and (best is None or ts > best):
            best = ts
    return best


def has_history(home: Path) -> bool:
    state = home / "state"
    refresh = state / "refresh"
    try:
        if any(refresh.iterdir()):
            return True
    except OSError:
        pass
    return (state / "refresh-prev").is_dir()


# ---------------------------------------------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------------------------------------------
def selected_steps(settings_layers: list[str]) -> list[str]:
    return [layer for layer in STEP_ORDER if layer in settings_layers]


REPEATED_ERRORS = 3
RUN_STATES_ACTIVE = ("working", "waiting", "retrying")


def _alerts(e: Any, state: str, code: str | None, free_gb: float) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    def add(alert_id: str, level: str, title: str, text: str) -> None:
        out.append({"id": alert_id, "level": level, "title": title, "text": text})

    c = e.controls
    s = e.settings.get()
    if not c.token_present and e.needs_token():
        add("no_token", "attention", "No token yet", "Add the GitHub token in Settings.")
    elif c.token_present and c.token_state == "rejected":
        add("token_rejected", "attention", "GitHub rejected the token",
            "Paste a new token in Settings, then tap Test.")
    elif c.token_present and c.token_state == "too_powerful":
        add("token_too_powerful", "attention", "That token is too powerful",
            "Collection did not start. Use a classic token with no scopes.")
    if s["delivery_mode"] != "lan":
        if not s["drop_repo"]:
            add("no_drop_repo", "attention", "No drop-box repo set",
                "Enter your private drop-box repo (owner/name) in Settings under Delivery.")
        elif not e.upload_token_set:
            add("no_upload_token", "attention", "No upload token yet",
                "Add the fine-grained upload token in Settings under Delivery.")
        elif e.upload_token_state == "rejected":
            add("upload_token_rejected", "attention", "GitHub rejected the upload token",
                "Paste a fine-grained upload token with Contents write on the drop-box repo.")
        elif e.upload_token_state == "too_powerful":
            add("upload_token_too_powerful", "attention", "Upload token is too powerful",
                "Use a fine-grained token scoped only to the private drop-box repository.")
        elif e.last_upload_error:
            add("upload_failed", "attention", "Drop-box upload failed", redact(e.last_upload_error))
    if code == "disk_low":
        add("disk_low", "attention", f"Only {free_gb:.1f} GB of space is left",
            "Collection is paused. Free some space, then tap Resume.")
    if e.runstate.failures >= REPEATED_ERRORS:
        add("repeated_errors", "attention", "Repeated errors",
            f"A step stopped with an error {e.runstate.failures} times in a row. "
            "The self-check can show what is wrong.")
    if e.stuck:
        add("stuck", "attention", "No progress for 25 minutes",
            "The collector looks stuck. Tap Resume, or run the self-check.")
    if (state in ("first_run", "attention") and e.no_candidates()) or code == "no_candidates":
        add("no_candidates", "info" if state == "first_run" else "attention", "No repo list yet",
            "Send the list from the workstation, then it starts.")
    return out


def build(e: Any) -> dict[str, Any]:
    """The ``status()`` snapshot of engine ``e`` (see ``phone.entry.Engine``)."""
    now = e.clock()
    s = e.settings.get()
    c = e.controls
    home: Path = e.home
    facts = c.facts()
    steps_sel = selected_steps(s["layers"])
    n_steps = len(steps_sel)
    times = layer_times(home)
    rs = e.runstate
    layer = rs.layer if rs.mode == "running" else None
    idx = steps_sel.index(layer) + 1 if layer in steps_sel else 0
    done, total = e.progress.snapshot() if layer else (0, 0)
    stopped, pruned, failed = (rs.stopped, rs.pruned, rs.failed) if layer else (0, 0, 0)
    pct = max(0, min(100, int(100 * done / total))) if total > 0 else 0
    free_gb = c.free_gb()
    reason = e.current_reason()
    net_type = facts.get("network") if facts.get("network") in ("wifi", "mobile", "none") else "none"
    restart_note = (f" \u00b7 restarted by Android at {clock_text(e.restart_ts)}"
                    if e.restart_ts is not None and now - e.restart_ts <= RESTART_NOTE_S else "")

    due = e.next_due()
    finished_recent = rs.finished_at is not None and now - rs.finished_at <= FINISHED_FOR_S
    last_finished = e.last_finished(times, steps_sel)

    state, code, headline, detail, reason_text = _words(
        e, now=now, reason=reason, layer=layer, idx=idx, n_steps=n_steps, done=done, total=total, stopped=stopped,
        due=due, last_finished=last_finished, finished_recent=finished_recent, net_type=net_type, free_gb=free_gb,
        restart_note=restart_note, facts=facts, s=s)

    budget = e.gate.budget()
    rate = e.gate.rate_state()
    github = {"calls_left": int(budget["remaining"]) if budget else 0,
              "limit": int(budget["limit"]) if budget else 0,
              "resets_in_s": max(0, int(budget["reset"] - now)) if budget else 0,
              "floor": s["rate_floor"],
              "in_use": bool(layer and layer != "LK" and state in RUN_STATES_ACTIVE),
              "known": budget is not None}
    if rate is not None and code == "rate_limit":
        github["resets_in_s"] = max(0, int(rate[1] - now))

    contacts = []
    for row in e.stats.hosts(7):
        host = row["host"]
        contacts.append({
            "host": host, "calls": row["calls"], "bytes": row["bytes"], "calls_7d": row["calls_7d"],
            "bytes_7d": row["bytes_7d"], "token_sent": host in TOKEN_HOSTS and row["token_sent"],
            "what": WHAT.get(host) or (WORKSTATION if looks_like_ip(host) else "Other")})

    completed = [lay for lay in STEP_ORDER if lay in times]
    if state in ("idle", "finished"):
        cycle_steps = [{"id": lay, "name": STEP_NAMES[lay], "state": "done", "pct": 100,
                        "finished": iso_local(times[lay]) if lay in times else None} for lay in steps_sel]
    else:
        mark = {"paused_by_user": "paused", "paused": "paused", "stopped": "stopped",
                "attention": "stopped"}.get(state, "now")
        cycle_steps = []
        for lay in steps_sel:
            if lay == layer:
                cycle_steps.append({"id": lay, "name": STEP_NAMES[lay], "state": mark, "pct": pct, "finished": None})
            elif lay in times:
                cycle_steps.append({"id": lay, "name": STEP_NAMES[lay], "state": "done", "pct": 100,
                                    "finished": iso_local(times[lay])})
            else:
                cycle_steps.append({"id": lay, "name": STEP_NAMES[lay], "state": "later", "pct": 0, "finished": None})

    resume_at: float | None = None
    if code == "slow_network" and reason is not None:
        resume_at = reason.resume_at
    elif code == "rate_limit" and rate is not None:
        resume_at = rate[1]
    override = reason.override if reason is not None and state == "paused" else None

    snapshot = {
        "schema": SCHEMA,
        "now": iso_local(now),
        "state": state,
        "headline": headline,
        "detail": detail,
        "reason": {"code": code, "text": reason_text, "resume_at": iso_local(resume_at) if resume_at else None,
                   "override": override},
        "progress": {"step_index": idx, "step_count": n_steps, "step_name": STEP_NAMES.get(layer or "", ""),
                     "done": done, "total": total, "pct": 100 if state in ("idle", "finished") else pct,
                     "checked": done, "stopped": stopped, "pruned": pruned, "failed": failed,
                     "eta_text": e.progress.eta_text() if state == "working" else "",
                     "last_progress_s": max(0, int(now - e.last_progress_ts()))},
        "cycle": {"started": iso_local(rs.cycle_started) if rs.cycle_started and rs.mode == "running" else None,
                  "last_finished": iso_local(last_finished) if last_finished else None,
                  "next_due": iso_local(due) if due and rs.mode != "running" else None,
                  "interval_days": s["interval_days"], "steps": cycle_steps},
        "github": github,
        "network": {"type": net_type, "mobile_allowed": s["network"] == "wifi_mobile",
                    "bytes_today": e.stats.bytes_today(), "bytes_7d": e.stats.bytes_7d(),
                    "refused_today": sum(e.stats.refused_today().values())},
        "phone": {"battery_pct": facts.get("battery_pct"), "charging": facts.get("charging", False),
                  "temp_c": facts.get("temp_c"), "free_gb": round(free_gb, 1),
                  "data_gb": round(e.datasize.data_gb(), 1)},
        "contacts": contacts,
        "lan": {"mode": s["lan_mode"], "serving": e.lan.serving, "address": e.lan.address,
                "share_until": iso_local(e.lan.share_until) if e.lan.share_until else None,
                "last_fetch": iso_local(e.last_fetch()) if e.last_fetch() else None,
                "ready_steps": len(completed), "ready_gb": round(e.datasize.ready_gb(completed), 1)},
        "delivery": {
            "mode": s["delivery_mode"],
            "drop_repo": s["drop_repo"],
            "upload_token": {
                "set": e.upload_token_set,
                "state": e.upload_token_state if e.upload_token_set else "untested",
                "tested_at": iso_local(e.upload_token_tested_at) if e.upload_token_tested_at else None,
            },
            "uploading": bool(e.uploading),
            "last_upload": iso_local(e.last_upload_at) if e.last_upload_at else None,
            "last_tag": e.last_upload_tag,
            "last_error": redact(e.last_upload_error) if e.last_upload_error else None,
        },
        "tokens": {"github": {"set": c.token_present, "state": c.token_state if c.token_present else "untested",
                              "tested_at": iso_local(c.token_tested_at) if c.token_tested_at else None},
                   "lan": {"set": e.lan_token_set}},
        "alerts": _alerts(e, state, code, free_gb),
        "health": {"last_error": e.last_error(), "retries_today": e.stats.errors_today(),
                   "uptime_s": max(0, int(now - e.start_ts))},
        "device": {"power_save": bool(facts.get("power_save", False)),
                   "battery_optimized": bool(facts.get("battery_optimized", False)),
                   "notifications_enabled": bool(facts.get("notifications_enabled", True)),
                   "data_saver": bool(facts.get("data_saver", False)),
                   "wifi_ip": facts.get("wifi_ip")},        # the phone's own Wi-Fi IPv4 (checked on the way in), or None
    }
    return snapshot


Words = tuple[str, "str | None", str, str, str]


def _words(e: Any, *, now: float, reason: Any, layer: str | None, idx: int, n_steps: int, done: int, total: int,
           stopped: int, due: float | None, last_finished: float | None, finished_recent: bool, net_type: str,
           free_gb: float, restart_note: str, facts: dict[str, Any], s: dict[str, Any]) -> Words:
    """``(state, reason code, headline, detail, reason text)`` for the moment."""
    c = e.controls
    rs = e.runstate
    rate = e.gate.rate_state()

    if e.stopped:
        return ("stopped", "user", "Stopped",
                "Collection is stopped and nothing is using the network. Progress is saved. "
                "Tap Start when you want it to carry on.", "Stopped by you.")
    if not e.started:
        return ("starting", None, "Starting up", "Getting ready. This takes a few seconds.", "")
    needs_token = e.needs_token()
    if not has_history(e.home) and (e.no_candidates() or (needs_token and not c.token_present)):
        if needs_token and not c.token_present:
            link = "" if (e.lan_token_set or s["delivery_mode"] == "github_drop") else " and the link token"
            return ("first_run", "token_missing", "Welcome to the collector",
                    f"Nothing is collecting yet. Add the GitHub token{link} to begin.", "No token yet.")
        return ("first_run", None, "No repo list yet",
                "The tokens are set, but the workstation has not sent the list of projects. "
                "It starts by itself once the list arrives.", "")
    if reason is not None:
        if reason.state == "paused_by_user":
            return ("paused_by_user", "user", "Paused by you",
                    "Progress is saved. Nothing is using the network. Tap Resume to carry on.", reason.text)
        if reason.state == "paused":
            return _paused_words(reason, s, facts, free_gb)
        if reason.state == "starting":
            if reason.code == "token_untested":
                return ("starting", None, "Checking the GitHub token",
                        "It checks that the token can see public data only. This takes a few seconds.", "")
            return ("starting", None, "Starting up", "Getting ready. This takes a few seconds.", "")
        if reason.state == "waiting":
            return _waiting_words(reason, c, s)
        if reason.state == "retrying":
            left = max(0.0, (reason.resume_at or now) - now)
            return ("retrying", "slow_network", "The network is slow",
                    f"{plural(s['timeout_streak'], 'request')} in a row got no answer from the network. "
                    f"Trying again in {span_text(left)}.", reason.text)
        if reason.state == "attention":
            return _token_words(reason)
    if e.stuck:
        return ("attention", "stuck", "No progress for 25 minutes",
                "The collector was restarted. If this happens again you get an alert. "
                "Tap Resume, or run the self-check.", "No progress for 25 minutes.")
    if rs.failures >= REPEATED_ERRORS:
        why = f" ({redact(rs.last_failure)})" if rs.last_failure else ""
        return ("attention", "repeated_errors", "A step keeps failing",
                f"It stopped with an error {rs.failures} times in a row{why}. It tries again soon. "
                "The self-check can show what is wrong.", "A step keeps failing.")
    if rs.mode == "no_candidates":
        return ("attention", "no_candidates", "No repo list yet",
                "The workstation has not sent the list of projects. It starts by itself once the list arrives.",
                "No repo list yet.")
    if rate is not None:
        left = max(0.0, rate[1] - now)
        budget = e.gate.budget()
        calls = (f"Only {int(budget['remaining']):,} calls are left, and it keeps {s['rate_floor']:,} for you. "
                 if budget else "")
        return ("waiting", "rate_limit", "Waiting for GitHub's hourly limit",
                f"{calls}It carries on by itself at {clock_text(rate[1])}, in {span_text(left)}.",
                "Waiting for GitHub's hourly limit.")
    if rs.mode == "idle":
        if finished_recent:
            if s["delivery_mode"] == "lan":
                done_detail = "The data is ready to pull from the workstation."
            elif e.uploading:
                done_detail = "Uploading the finished cycle to the private drop-box."
            elif e.last_upload_tag and not e.last_upload_error:
                done_detail = (f"Uploaded {e.last_upload_tag} to {s['drop_repo']}."
                               if s["drop_repo"] else "Uploaded to the private drop-box.")
            else:
                done_detail = "The data is ready to upload to the private drop-box."
            return ("finished", None, f"Cycle finished: {n_steps} of {n_steps} steps", done_detail, "")
        if due is not None:
            last = f"Last cycle finished {datetime_text(last_finished)}. " if last_finished else ""
            return ("idle", None, f"Idle, next cycle due {date_text(due)}",
                    f"{last}Next cycle starts {date_text(due)} ({days_text(whole_days(due, now))}). "
                    "Nothing is running, so nothing is using the network.", "")
    if layer is None or rs.mode != "running":
        return ("starting", None, "Starting up", "Getting ready. This takes a few seconds.", "")
    pass_note = f" \u00b7 pass {rs.run}" if rs.run >= 2 else ""
    mobile_note = " \u00b7 using mobile data" if net_type == "mobile" else ""
    count = f" \u00b7 {done:,} of {total:,} projects checked{stopped_note(stopped)}" if total else ""
    return ("working", None, f"Collecting {STEP_PHRASE[layer]}",
            f"Step {idx} of {n_steps}{count}{pass_note}{mobile_note}{restart_note}", "")


def _paused_words(reason: Any, s: dict[str, Any], facts: dict[str, Any], free_gb: float) -> Words:
    if reason.code == "battery":
        level = s["battery_pause_pct"]
        return ("paused", "battery", f"Battery is at {facts.get('battery_pct')}%",
                f"The limit is {level}%. It carries on at {level + 5}%.", reason.text)
    if reason.code == "heat":
        limit = s["heat_pause_c"]
        temp = facts.get("temp_c", 0.0)
        return ("paused", "heat", f"The phone is hot ({temp:.0f} \u00b0C)",
                f"Battery temperature is {temp:.0f} \u00b0C. It carries on once it cools below {limit - 3} \u00b0C.",
                reason.text)
    return ("paused", "disk_low", f"Only {free_gb:.1f} GB of space is left",
            "Collection is paused so nothing gets cut off. It resumes by itself above 3 GB. "
            "Free some space, then tap Resume.", reason.text)


def _waiting_words(reason: Any, c: Any, s: dict[str, Any]) -> Words:
    both = s["network"] == "wifi_mobile"
    if reason.text.startswith("Data Saver"):
        return ("waiting", "no_wifi", "Waiting for Wi-Fi",
                "Data Saver is on, so mobile data is not used. It carries on when Wi-Fi returns.", reason.text)
    lost = c.wifi_lost_at
    if lost is not None:
        if both:
            return ("waiting", "no_wifi", f"Network lost at {clock_text(lost)}",
                    f"The network was lost at {clock_text(lost)}, so it waits. It carries on when a network returns.",
                    reason.text)
        return ("waiting", "no_wifi", f"Wi-Fi lost at {clock_text(lost)}",
                f"Wi-Fi was lost at {clock_text(lost)} and mobile data is off, so it waits. "
                "It carries on when Wi-Fi returns.", reason.text)
    if both:
        return ("waiting", "no_wifi", "Waiting for a network",
                "There is no network. It carries on when one returns.", reason.text)
    return ("waiting", "no_wifi", "Waiting for Wi-Fi",
            "Mobile data is off, so it waits. It carries on when Wi-Fi returns.", reason.text)


def _token_words(reason: Any) -> Words:
    if reason.code == "token_missing":
        return ("attention", "token_missing", "No GitHub token",
                "Collection is waiting for a token. Add it in Settings. Nothing was lost.", reason.text)
    if reason.code == "token_too_powerful":
        return ("attention", "token_rejected", "That token is too powerful",
                "The token can reach more than public data, so it was refused. Make a token with no scopes.",
                reason.text)
    return ("attention", "token_rejected", "GitHub rejected the token",
            "Collection is paused. Nothing was lost: all progress is saved, and it carries on from here "
            "once the token works.", reason.text)
