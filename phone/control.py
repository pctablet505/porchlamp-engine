"""Settings, pause rules and the mode of the LAN listener. Standard library only.

* ``Settings``: validated, persisted in ``config/settings.json`` (no secrets); invalid input changes nothing and names
  the field.
* ``Controls``: the user's pause, the device facts the app pushes, and the rules that hold the collector back: battery
  (resume at level + 5), battery temperature (resume 3 degrees below), disk (below 2 GB, resume above 3 GB), mobile
  data only when allowed (never with Data Saver), a streak of timeouts, and the GitHub token's state. Each rule has its
  override where the owner asked for one. The HTTP gate asks ``block_reason`` before every request.
* ``LanManager``: the LAN listener. Manual by default: no listener unless ``share`` was called (it stops by itself)
  or the owner chose auto. It binds only the Wi-Fi address the app reports, follows that address as it appears,
  changes and disappears, and never listens on anything else.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import math
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LAYER_IDS: tuple[str, ...] = ("L1", "L0", "L2", "L3g", "L3c", "L4", "LK")
RESERVED_REPO_NAMES: frozenset[str] = frozenset({
    "porchlamp",
    "porchlamp-engine",
    "porchlamp-phone",
    "contributor-value-index",
})
_REPO_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
DEFAULTS: dict[str, Any] = {
    "network": "wifi",
    "battery_pause_pct": 20,
    "battery_override": False,
    "heat_pause_c": 40,
    "heat_override": False,
    "timeout_streak": 5,
    "interval_days": 28,
    "layers": list(LAYER_IDS),
    "rate_floor": 500,
    "lan_mode": "manual",
    "delivery_mode": "lan",
    "drop_repo": "",
    # D5 (2026-11-02 dry run): at 3, two GitHub secondary limits in 12 minutes and 1.06-1.36 times as fast; default 1,
    # with a supervised step-up to 2 by a settings edit (D5-dryrun.md). L0, L3g and L3c at up to 2 once the phone's REST
    # call counts allow it (default 1).
    "l4_workers": 1,
    "github_workers": 1,
    # L4 starts from the PR list L1 read this cycle (#89 Q1); off reads the whole 365-d window, as before.
    "l4_reuse_l1": True,
}
INT_RANGES: dict[str, tuple[int, int]] = {
    "battery_pause_pct": (5, 50),
    "heat_pause_c": (35, 50),
    "timeout_streak": (2, 20),
    "interval_days": (7, 90),
    "rate_floor": (100, 2000),
    "l4_workers": (1, 3),
    "github_workers": (1, 2),
}
BOOLS = ("battery_override", "heat_override", "l4_reuse_l1")
ENUMS: dict[str, tuple[tuple[str, ...], str]] = {
    "network": (("wifi", "wifi_mobile"), "Pick Wi-Fi only, or Wi-Fi and mobile data."),
    "lan_mode": (("auto", "manual"), "Pick Auto or Manual."),
    "delivery_mode": (("lan", "github_drop", "both"), "Pick LAN only, GitHub drop-box, or Both."),
}

BATTERY_RESUME_MARGIN = 5       # resume at pause level + 5 points
HEAT_RESUME_MARGIN = 3          # resume 3 degrees below the heat level
DISK_PAUSE_GB = 2.0
DISK_RESUME_GB = 3.0
RETRY_WAIT_S = 600              # wait after a streak of timeouts
AUTH_REJECT_STREAK = 3          # refused tries in a row before the token counts as rejected
EVAL_EVERY_S = 1.0
SHARE_DEFAULT_MIN = 30
SHARE_MAX_MIN = 240
LAN_PORT = 8765
LAN_RETRY_S = 30.0


def valid_drop_repo(value: Any) -> str | None:
    """Return trimmed ``owner/name`` when ``value`` names an allowed drop-box repository, else ``None``."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if "/" not in cleaned or cleaned.count("/") != 1:
        return None
    owner, name = cleaned.split("/", 1)
    if owner in ("", ".", "..") or name in ("", ".", ".."):
        return None
    if not (_REPO_SEGMENT_RE.fullmatch(owner) and _REPO_SEGMENT_RE.fullmatch(name)):
        return None
    if name.lower() in RESERVED_REPO_NAMES:
        return None
    return f"{owner}/{name}"


# ---------------------------------------------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------------------------------------------
def _atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _as_int(value: Any) -> int | None:
    """An int, or a float with no fraction, from JSON; booleans and strings are not numbers here."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value == int(value):
        return int(value)
    return None


def validate(patch: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """``(clean values, errors)`` for the fields in ``patch``; a field in ``errors`` is not in the clean values."""
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, value in patch.items():
        if key not in DEFAULTS:
            errors[str(key)] = "Unknown setting."
        elif key in INT_RANGES:
            low, high = INT_RANGES[key]
            number = _as_int(value)
            if number is None or not low <= number <= high:
                errors[key] = f"Pick a number from {low} to {high}."
            else:
                clean[key] = number
        elif key in BOOLS:
            if isinstance(value, bool):
                clean[key] = value
            else:
                errors[key] = "Pick on or off."
        elif key in ENUMS:
            choices, message = ENUMS[key]
            if isinstance(value, str) and value in choices:
                clean[key] = value
            else:
                errors[key] = message
        elif key == "layers":
            if (isinstance(value, list) and value and all(isinstance(v, str) for v in value)
                    and all(v in LAYER_IDS for v in value) and len(set(value)) == len(value)):
                clean[key] = [layer for layer in LAYER_IDS if layer in value]
            else:
                errors[key] = "Pick at least one step."
        elif key == "drop_repo":
            if isinstance(value, str) and value.strip() == "":
                clean[key] = ""
            else:
                repo = valid_drop_repo(value)
                if repo is not None:
                    clean[key] = repo
                else:
                    errors[key] = "Enter a dedicated private repo as owner/name (never porchlamp or engine repos)."
    return clean, errors


class Settings:
    """The settings store. ``on_change(old, new)`` runs after a successful change, outside the lock."""

    def __init__(self, path: Path | str, on_change: Callable[[dict[str, Any], dict[str, Any]], object] | None = None
                 ) -> None:
        self.path = Path(path)
        self.on_change = on_change
        self._lock = threading.Lock()
        self._values: dict[str, Any] = json.loads(json.dumps(DEFAULTS))
        self.load_note: str | None = None
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            raw = None
        if not isinstance(raw, dict):
            self.load_note = "The settings file could not be read; the defaults are used."
            with contextlib.suppress(OSError):   # moved aside, never deleted
                self.path.rename(self.path.with_name(f"{self.path.name}.bad-{int(time.time())}"))
            return
        clean, errors = validate(raw)
        self._values.update(clean)
        if errors:
            self.load_note = "Some saved settings were not valid and were reset: " + ", ".join(sorted(errors)) + "."

    def get(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._values))

    def value(self, key: str) -> Any:
        with self._lock:
            return self._values[key]

    def set(self, patch: Any) -> dict[str, Any]:
        """Apply ``patch`` (a dict) all-or-nothing: ``{"ok", "errors", "settings"}``."""
        if not isinstance(patch, dict):
            return {"ok": False, "errors": {"settings": "The settings were not readable."},
                    "settings": self.get()}
        clean, errors = validate(patch)
        if errors:
            return {"ok": False, "errors": errors, "settings": self.get()}
        with self._lock:
            old = json.loads(json.dumps(self._values))
            self._values.update(clean)
            new = json.loads(json.dumps(self._values))
            if new != old:
                _atomic_write(self.path, (json.dumps(new, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        if new != old and self.on_change is not None:
            self.on_change(old, new)
        return {"ok": True, "errors": {}, "settings": new}


# ---------------------------------------------------------------------------------------------------------------
# Pause rules
# ---------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Reason:
    """Why the collector is held back, as facts (the words are built in ``phone.status``)."""

    code: str                       # user|disk_low|heat|battery|no_wifi|slow_network|starting|token_*
    state: str                      # paused_by_user|paused|waiting|retrying|starting|attention
    text: str                       # one short plain sentence
    resume_at: float | None = None
    override: str | None = None     # "battery" or "heat": the switch that lifts this pause


TOKEN_REASONS = {
    "missing": ("token_missing", "There is no GitHub token."),
    "rejected": ("token_rejected", "GitHub rejected the token."),
    "too_powerful": ("token_too_powerful", "That token can reach more than public data, so it is not used."),
    "untested": ("token_untested", "Checking the GitHub token."),
}


def valid_bind_ip(value: Any) -> str | None:
    """The IPv4 address text when ``value`` is a usable interface address; never a wildcard or multicast."""
    if not isinstance(value, str):
        return None
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if ip.version != 4 or ip.is_unspecified or ip.is_multicast or ip == ipaddress.ip_address("255.255.255.255"):
        return None
    return str(ip)


class Controls:
    """Device facts, the user's pause, the pause rules and the token's state. Thread-safe."""

    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.time,
                 free_gb: Callable[[], float] = lambda: 1e9, state_dir: Path | str | None = None,
                 emit: Callable[..., object] | None = None,
                 on_reason: Callable[[Reason | None, Reason | None], object] | None = None,
                 github_hosts: frozenset[str] | set[str] = frozenset({"api.github.com"})) -> None:
        self.settings = settings
        self.github_hosts = frozenset(github_hosts)   # the hosts that need the accepted token
        self._clock = clock
        self._free_gb = free_gb
        self._state_dir = Path(state_dir) if state_dir else None
        self._emit = emit
        self.on_reason = on_reason
        self._lock = threading.RLock()
        self.device: dict[str, Any] = {}
        self.device_known = False
        self.wifi_lost_at: float | None = None
        self._batt = self._heat = self._disk = False
        self._user = bool(self._state_dir and (self._state_dir / "user_pause").is_file())
        self._streak = 0
        self._retry_until: float | None = None
        self._auth_fail = 0
        self.token_present = False
        self.token_state = "untested"      # untested | accepted | rejected | too_powerful
        self.token_tested_at: float | None = None
        self._reason: Reason | None = None
        self._last_eval = -1e9
        self._dirty = True
        self.last_error: dict[str, Any] | None = None

    # ---- events -------------------------------------------------------------------------------------------------
    def _say(self, level: str, text: str, detail: str = "", kind: str = "progress") -> None:
        if self._emit is not None:
            self._emit(level, text, detail, kind)

    # ---- device facts -----------------------------------------------------------------------------------------
    def update_device(self, facts: Any) -> None:
        """Take the facts the app pushed; a field of the wrong type is ignored, the rest are kept."""
        if not isinstance(facts, dict):
            return
        with self._lock:
            before = self.device.get("network")
            for key in ("battery_pct",):
                number = _as_int(facts.get(key))
                if number is not None:
                    self.device[key] = number
            temp = facts.get("temp_c")
            if isinstance(temp, (int, float)) and not isinstance(temp, bool) and math.isfinite(temp):
                self.device["temp_c"] = float(temp)
            for key in ("charging", "data_saver", "power_save", "battery_optimized", "notifications_enabled"):
                if isinstance(facts.get(key), bool):
                    self.device[key] = facts[key]
            if facts.get("network") in ("wifi", "mobile", "none"):
                self.device["network"] = facts["network"]
            if "wifi_ip" in facts:
                self.device["wifi_ip"] = valid_bind_ip(facts["wifi_ip"])
            self.device_known = "network" in self.device
            after = self.device.get("network")
            if before == "wifi" and after in ("none", "mobile"):
                self.wifi_lost_at = self._clock()
            elif after == "wifi":
                self.wifi_lost_at = None
            self._dirty = True
        self.evaluate(force=True)

    def facts(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.device)

    def network_type(self) -> str:
        with self._lock:
            return "mobile" if self.device.get("network") == "mobile" else "wifi"

    def free_gb(self) -> float:
        return float(self._free_gb())

    # ---- the user's pause --------------------------------------------------------------------------------------
    @property
    def user_paused(self) -> bool:
        return self._user

    def _write_user(self, on: bool) -> None:
        if self._state_dir is None:
            return
        marker = self._state_dir / "user_pause"
        if on:
            _atomic_write(marker, (str(int(self._clock())) + "\n").encode("utf-8"))
        else:
            marker.unlink(missing_ok=True)

    def set_user_pause(self, on: bool) -> None:
        with self._lock:
            if self._user == on:
                return
            self._user = on
            self._write_user(on)
            self._dirty = True
        self.evaluate(force=True)

    def recheck(self) -> Reason | None:
        """Look at the device and the disk again now (the Resume button)."""
        return self.evaluate(force=True)

    # ---- overrides ---------------------------------------------------------------------------------------------
    def cycle_boundary(self) -> None:
        """A cycle started or ended: both overrides go back to off."""
        patch = {k: False for k in ("battery_override", "heat_override") if self.settings.value(k)}
        if patch:
            self.settings.set(patch)  # its on_change handler writes the "turned off" events
        self._dirty = True

    # ---- the token's state ---------------------------------------------------------------------------------------
    def set_token(self, present: bool, state: str = "untested", tested_at: float | None = None) -> None:
        with self._lock:
            self.token_present = present
            self.token_state = state if present else "untested"
            self.token_tested_at = tested_at if present else None
            self._auth_fail = 0
            self._dirty = True

    def _token_reason(self, needs: bool) -> Reason | None:
        if not needs:
            return None
        if not self.token_present:
            key = "missing"
        elif self.token_state == "accepted":
            return None
        else:
            key = self.token_state if self.token_state in TOKEN_REASONS else "untested"
        code, text = TOKEN_REASONS[key]
        return Reason(code, "starting" if key == "untested" else "attention", text)

    # ---- rules ----------------------------------------------------------------------------------------------------
    def evaluate(self, force: bool = False) -> Reason | None:
        """Update the latches and return the first reason that applies (pause and wait rules, not the token)."""
        now = self._clock()
        fire: tuple[Reason | None, Reason | None] | None = None
        with self._lock:
            if not force and not self._dirty and now - self._last_eval < EVAL_EVERY_S:
                return self._reason
            self._last_eval = now
            self._dirty = False
            reason = self._compute(now)
            if (reason.code if reason else None) != (self._reason.code if self._reason else None):
                fire = (self._reason, reason)
            self._reason = reason
        if fire is not None and self.on_reason is not None:
            self.on_reason(*fire)
        return reason

    def _compute(self, now: float) -> Reason | None:
        s = self.settings.get()
        d = self.device
        # battery
        level = s["battery_pause_pct"]
        pct = d.get("battery_pct")
        if s["battery_override"]:
            self._batt = False
        elif pct is not None and pct >= 0:
            if pct < level:
                self._batt = True
            elif pct >= level + BATTERY_RESUME_MARGIN:
                self._batt = False
        # heat
        limit = s["heat_pause_c"]
        temp = d.get("temp_c")
        if s["heat_override"]:
            self._heat = False
        elif temp is not None:
            if temp > limit:
                self._heat = True
            elif temp < limit - HEAT_RESUME_MARGIN:
                self._heat = False
        # disk
        free = self.free_gb()
        if free < DISK_PAUSE_GB:
            self._disk = True
        elif free > DISK_RESUME_GB:
            self._disk = False
        # slow-network wait
        if self._retry_until is not None and now >= self._retry_until:
            self._retry_until = None
            self._streak = 0
            self._say("info", "Trying again after the slow network", "", "network")
        if self._user:
            return Reason("user", "paused_by_user", "Paused by you.")
        if self._disk:
            return Reason("disk_low", "paused", f"Only {free:.1f} GB of space is left.")
        if self._heat:
            return Reason("heat", "paused", f"The battery is hot ({temp:.0f} °C).", None, "heat")
        if self._batt:
            return Reason("battery", "paused", f"Battery is at {pct}%.", None, "battery")
        offline = self._network_reason(s, d)
        if offline is not None:
            return offline
        if self._retry_until is not None:
            return Reason("slow_network", "retrying", "The network is slow.", self._retry_until)
        return None

    def _network_reason(self, s: dict[str, Any], d: dict[str, Any]) -> Reason | None:
        """The network rule alone: no facts yet, no network, or mobile data that is not allowed."""
        if not self.device_known:
            return Reason("starting", "starting", "Getting ready.")
        net = d.get("network")
        if net == "none":
            wording = "Waiting for a network." if s["network"] == "wifi_mobile" else "Waiting for Wi-Fi."
            return Reason("no_wifi", "waiting", wording)
        if net == "mobile" and (s["network"] != "wifi_mobile" or d.get("data_saver")):
            wording = "Data Saver is on, so mobile data is not used." if s["network"] == "wifi_mobile" \
                else "Waiting for Wi-Fi."
            return Reason("no_wifi", "waiting", wording)
        return None

    def current_reason(self, needs_token: bool = True) -> Reason | None:
        """What the status screen shows: the pause and wait rules first, then the token."""
        return self.evaluate() or self._token_reason(needs_token)

    def block_reason(self, host: str, scope: str = "collect") -> Reason | None:
        """The reason a request to ``host`` must wait, or None. ``upload`` obeys pause and device rules without the
        collector token; owner acts (token test, self-check) wait only for the network rule."""
        reason = self.evaluate()
        if scope == "upload":
            return reason
        if scope != "collect":
            with self._lock:
                return self._network_reason(self.settings.get(), self.device)
        if reason is not None:
            return reason
        if host in self.github_hosts:
            return self._token_reason(True)
        return None

    # ---- results the gate reports ---------------------------------------------------------------------------------
    def on_response(self, host: str, status: int, authed: bool) -> None:
        with self._lock:
            if status < 500:
                self._streak = 0
            if host in self.github_hosts and authed:
                if status == 401:
                    self._auth_fail += 1
                    reject = self._auth_fail >= AUTH_REJECT_STREAK and self.token_state != "rejected"
                    if reject:
                        self.token_state = "rejected"
                        self.token_tested_at = self._clock()
                        self.last_error = {"t": self._clock(), "text": "GitHub answered “Bad credentials” "
                                           "(status 401)", "action": "It paused itself after three refused tries."}
                        self._dirty = True
                    else:
                        reject = False
                elif status < 400:
                    self._auth_fail = 0
                    reject = False
                else:
                    reject = False
            else:
                reject = False
        if reject:
            self._say("err", "GitHub rejected the token", "Three tries in a row were refused (status 401).", "network")
            self.evaluate(force=True)

    def on_failure(self, host: str, no_answer: bool) -> None:
        """A request failed. One that never got an answer (``no_answer``: a timeout, an unresolved name, a refused or
        dropped connection, an unreachable network) counts toward the slow-network wait; any other failure does not."""
        if not no_answer:
            return
        started = False
        with self._lock:
            self._streak += 1
            limit = self.settings.value("timeout_streak")
            if self._streak >= limit and self._retry_until is None:
                self._retry_until = self._clock() + RETRY_WAIT_S
                self.last_error = {"t": self._clock(), "text": f"{self._streak} requests in a row got no answer from the network",
                                   "action": f"It waits {RETRY_WAIT_S // 60} minutes, then tries again."}
                self._dirty = True
                started = True
        if started:
            self._say("warn", "The network is slow", f"{self._streak} requests in a row got no answer from the network. "
                      f"Trying again in {RETRY_WAIT_S // 60} minutes.", "network")
            self.evaluate(force=True)

    @property
    def timeout_streak(self) -> int:
        return self._streak

    @property
    def power_held(self) -> bool:
        """True while the battery-level or the battery-heat rule holds the collector (whatever else does too)."""
        return self._batt or self._heat


# ---------------------------------------------------------------------------------------------------------------
# The LAN listener
# ---------------------------------------------------------------------------------------------------------------
class LanManager:
    """Starts and stops the LAN server as the rules say. It binds only the Wi-Fi address the app reported."""

    def __init__(self, *, make_server: Callable[[str, int, str], Any], settings: Settings, controls: Controls,
                 clock: Callable[[], float] = time.time, port: int = LAN_PORT,
                 emit: Callable[..., object] | None = None, log: Callable[[str], object] | None = None,
                 persist: Callable[[float | None], object] | None = None,
                 share_until: float | None = None) -> None:
        self._make_server = make_server
        self._settings = settings
        self._controls = controls
        self._clock = clock
        self.port = port
        self._emit = emit
        self._log = log
        self._persist = persist
        self._lock = threading.RLock()
        self._token: str | None = None
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._bound: str | None = None
        self._share_until: float | None = share_until
        self._failed_at = -1e9
        self.last_error: str | None = None

    def set_token(self, token: str | None) -> None:
        with self._lock:
            changed = token != self._token
            self._token = token
        if changed:
            self.stop_server()  # the new link token takes effect at the next evaluate
            self.update()

    # ---- state -----------------------------------------------------------------------------------------------------
    @property
    def serving(self) -> bool:
        return self._server is not None

    @property
    def address(self) -> str | None:
        server = self._server
        return f"{self._bound}:{server.server_address[1]}" if server is not None and self._bound else None

    @property
    def share_until(self) -> float | None:
        return self._share_until

    def _say(self, level: str, text: str, detail: str = "") -> None:
        if self._emit is not None:
            self._emit(level, text, detail, "network")

    def _note(self, text: str) -> None:
        if self._log is not None:
            self._log(text)

    # ---- share ---------------------------------------------------------------------------------------------------------
    def share(self, minutes: Any = SHARE_DEFAULT_MIN) -> None:
        number = _as_int(minutes)
        if number is None or number <= 0:
            number = SHARE_DEFAULT_MIN
        number = min(number, SHARE_MAX_MIN)
        with self._lock:
            self._share_until = self._clock() + number * 60
        if self._persist is not None:
            self._persist(self._share_until)
        self._say("info", "Sharing with the workstation started", f"For {number} minutes.")
        self.update()

    def _desired_ip(self) -> str | None:
        if not self._token:
            return None
        facts = self._controls.facts()
        ip = valid_bind_ip(facts.get("wifi_ip"))
        if facts.get("network") != "wifi" or ip is None:
            return None
        if self._settings.value("lan_mode") == "auto":
            return ip
        if self._share_until is not None and self._clock() < self._share_until:
            return ip
        return None

    def update(self) -> None:
        """Make the listener match the rules now: start, stop, or move to a new Wi-Fi address."""
        with self._lock:
            now = self._clock()
            expired = self._share_until is not None and now >= self._share_until
            if expired:
                self._share_until = None
                if self._persist is not None:
                    self._persist(None)
                if self._server is not None or self._settings.value("lan_mode") == "manual":
                    self._say("info", "Sharing with the workstation stopped", "The time was up.")
            want = self._desired_ip()
            if self._server is not None and want != self._bound:
                self.stop_server()
            if self._server is None and want is not None and now - self._failed_at >= LAN_RETRY_S:
                self._start_server(want)

    def _start_server(self, ip: str) -> None:
        try:
            server = self._make_server(ip, self.port, self._token or "")
        except (OSError, ValueError) as exc:
            self._failed_at = self._clock()
            self.last_error = type(exc).__name__
            self._note(f"LAN listener could not start on {ip}: {type(exc).__name__}")
            self._say("warn", "Could not start sharing", "The Wi-Fi address could not be used.")
            return
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True,
                                  name="porchlamp-lan")
        thread.start()
        self._server, self._thread, self._bound = server, thread, ip
        self.last_error = None
        self._note(f"LAN listener started on {ip}:{self.port}")

    def stop_server(self) -> None:
        with self._lock:
            server, thread, bound = self._server, self._thread, self._bound
            self._server = self._thread = self._bound = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        self._note(f"LAN listener stopped on {bound}:{self.port}")
