"""The engine's entry points for the Android app (called through Chaquopy by ``app_entry.py``). Standard library only.

Every function of CONTRACT.md lives here: ``start``, ``stop``, ``pause``, ``resume``, ``status``, ``settings_get``,
``settings_set``, ``override``, ``events``, ``export_log``, ``self_check``, ``test_token``, ``share``, ``danger`` and
``update_device``. All but ``start``, ``stop``, ``pause``, ``resume``, ``override``, ``share`` and ``update_device``
return JSON text (``export_log`` returns a path). Python decides everything and writes every sentence; the app only
draws it and reports device facts.

Secrets: ``start(files_dir, github_token, lan_token)`` takes both tokens in memory. They are never written to a file,
a log, an event, an export, an archive, a status or an exception text: every outgoing string passes the redactor that
holds them.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phone import control, httpgate, runner, serve, status as status_mod, tokencheck, upload
from phone.control import Controls, LanManager, Reason, Settings
from phone.events import Redactor, TechLog, Timeline
from phone.httpgate import Gate, Stopped
from phone.netstats import NetStats
from phone.status import DataSize, Progress
from porchlamp.collect import ghcli
from porchlamp.collect import net as collect_net

ENGINE_ROOT = Path(__file__).resolve().parent.parent
STUCK_AFTER_S = status_mod.STUCK_AFTER_S
RUNNER_POLL_S = 30.0
TOKEN_RETRY_S = 60.0
TOKEN_BLOCKED_RETRY_S = 5.0
UPLOAD_RETRY_S = 300.0
HALT_WAIT_S = 20.0
DANGER_ACTIONS = ("reset", "move_data_aside", "remove_tokens")
TOKEN_ENV = ("PORCHLAMP_GH_TOKENS", "GITHUB_TOKEN")   # ghcli reads these ahead of, or in place of, GH_TOKEN
REPO_LINE = re.compile(r"^[\w.-]+/[\w.-]+ \[[^\]]*\]: ")
#: D4: GraphQL points a minute the transport may spend (``PORCHLAMP_GH_PPM``): 4,200 an hour, 15% under this account's
#: busiest measured hour (4,908), leaving the owner's other tools at least 800 of the 5,000.
GH_PPM = 70
#: D4: written in ``state/refresh`` when the transport pins one request at a time (the third GitHub secondary limit in
#: 24 h); while it exists every step runs one project at a time. The next cycle's start retires it.
PIN_FILE = "concurrency.pinned"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clean(token: object) -> str | None:
    if not isinstance(token, str):
        return None
    token = token.strip()
    return token or None


class Engine:
    """Everything one running collector owns. ``entry`` keeps one of these per process."""

    def __init__(self, files_dir: str | Path, *, clock: Callable[[], float] = time.time,
                 free_gb: Callable[[], float] | None = None, gather_main: Callable[[list[str]], int] | None = None,
                 make_server: Callable[..., Any] | None = None, threads: bool = True, tick_s: float = 2.0,
                 gate_options: dict[str, Any] | None = None, api_base: str | None = None,
                 upload_base: str | None = None, lan_port: int = control.LAN_PORT) -> None:
        self.files_dir = Path(files_dir)
        self.home = self.files_dir / "porchlamp"
        self.exports = self.files_dir / "exports"
        self.clock = clock
        self._gather_main = gather_main
        self._threads_enabled = threads
        self._tick_s = tick_s
        self._api_base = api_base
        self._upload_base = upload_base
        for sub in ("raw", "state", "config"):
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        state = self.home / "state"
        self.redactor = Redactor()
        self.techlog = TechLog(state / "log", clock, self.redactor)
        self.timeline = Timeline(state / "events.jsonl", clock, self.redactor)
        self.stats = NetStats(state / "netstats.json", clock)
        self.settings = Settings(self.home / "config" / "settings.json", on_change=self._settings_changed)
        options = dict(gate_options or {})
        options.setdefault("allowed", httpgate.PHONE_HOSTS)      # the phone reaches only what its steps use
        self.controls = Controls(self.settings, clock=clock, state_dir=state, emit=self.event,
                                 free_gb=free_gb or (lambda: runner.shutil_free(self.home)),
                                 on_reason=self._reason_changed,
                                 github_hosts=options.get("token_hosts", frozenset({"api.github.com"})))
        self.gate = Gate(stats=self.stats, controls=self.controls, log=self.log, clock=clock, **options)
        self.gate.on_rate_wait_end = self._rate_wait_ended
        self.runstate = runner.RunState()
        self.progress = Progress(self.home, clock)
        self.datasize = DataSize(self.home, clock)
        self.lan = LanManager(make_server=make_server or self._make_server, settings=self.settings,
                              controls=self.controls, clock=clock, port=lan_port, emit=self.event, log=self.log,
                              persist=lambda ts: self.stats.meta_set("share_until", ts),
                              share_until=self._saved_share())
        self.started = False
        self.stopped = False
        self.stuck = False
        self.restart_ts: float | None = None
        self.start_ts = clock()
        self._stuck_ts = 0.0
        self._work_since: float | None = None
        self._repo_ts = 0.0
        self._idle_event_for: float | None = None
        self._engine_error: dict[str, Any] | None = None
        self._gh_token: str | None = None
        self._lan_token: str | None = None
        self._upload_token: str | None = None
        self.upload_token_state = "untested"
        self.upload_token_tested_at: float | None = None
        self._last_test: tuple[str, str, float] | None = None   # (digest, state, time): never the value
        self._last_upload_test: tuple[str, str, str, float] | None = None  # (digest, repo, state, time)
        self.uploading = False
        self.last_upload_at: float | None = None
        self.last_upload_tag: str | None = None
        self.last_upload_error: str | None = None
        self._auto_upload_tried_at = -1e9
        self._uploaded_main_done: float | None = None
        self._uploaded_repo: str | None = None
        self._upload_lock = threading.Lock()
        self._life = threading.RLock()
        self._shutdown = threading.Event()
        self._cancel = threading.Event()
        self._collector: threading.Thread | None = None
        self._monitor: threading.Thread | None = None
        self._token_worker: threading.Thread | None = None
        self._upload_worker: threading.Thread | None = None
        self._load_uploaded_state()
        if self.settings.load_note:
            self.event("warn", "Settings were reset", self.settings.load_note)

    def _load_uploaded_state(self) -> None:
        saved = upload.read_uploaded_state(self.home)
        if not saved:
            return
        tag = saved.get("tag")
        if isinstance(tag, str) and tag:
            self.last_upload_tag = tag
        stamp = saved.get("uploaded_at")
        if isinstance(stamp, str) and stamp:
            with contextlib.suppress(ValueError):
                self.last_upload_at = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()

    # ---- small shared pieces ------------------------------------------------------------------------------------
    def log(self, text: str) -> None:
        self.techlog.write(text)

    def event(self, level: str, text: str, detail: str = "", kind: str = "progress") -> None:
        self.timeline.add(level, text, detail, kind)
        self.techlog.write(f"event {level}: {text}" + (f" -- {detail}" if detail else ""))

    def _saved_share(self) -> float | None:
        value = self.stats.meta_get("share_until")
        return float(value) if isinstance(value, (int, float)) and value > self.clock() else None

    def needs_token(self) -> bool:
        return any(layer != "LK" for layer in self.settings.value("layers"))

    def no_candidates(self) -> bool:
        return self.progress.candidates() == 0

    def current_reason(self) -> Reason | None:
        return self.controls.current_reason(self.needs_token())

    def collector_alive(self) -> bool:
        return self._collector is not None and self._collector.is_alive()

    def next_due(self) -> float | None:
        return runner.next_due(self.home, self.settings.value("interval_days"))

    def last_fetch(self) -> float | None:
        value = self.stats.meta_get("lan_last_fetch")
        return float(value) if isinstance(value, (int, float)) else None

    def last_progress_ts(self) -> float:
        return max(self.gate.progress_ts, self.progress.changed_ts or 0.0, self._repo_ts)

    def last_finished(self, times: dict[str, float], steps: list[str]) -> float | None:
        if steps and all(layer in times for layer in steps) and (self.home / "state/refresh/MAIN.done").is_file():
            return max(times[layer] for layer in steps)
        return status_mod.previous_finish(self.home)

    def last_error(self) -> dict[str, Any] | None:
        candidates = [e for e in (self.controls.last_error, self._engine_error) if e]
        if not candidates:
            return None
        newest = max(candidates, key=lambda e: e["t"])
        return {"ts": status_mod.iso_local(newest["t"]), "text": self.redactor(newest["text"]),
                "action": self.redactor(newest.get("action", ""))}

    # ---- D4: projects side by side, and the GitHub throttle's settings -----------------------------------------
    def _pin_path(self) -> Path:
        return self.home / "state" / "refresh" / PIN_FILE

    def secondary_hold(self) -> bool:
        """A GitHub hold is in force and GitHub's secondary limit set it, not the hourly limit (the transport's own
        record of its hold, for the status words)."""
        return ghcli.secondary_hold()

    def pinned(self) -> bool:
        """The transport pinned one request at a time in this cycle (the pin file exists)."""
        return self._pin_path().is_file()

    def workers_for(self, layer: str) -> int:
        """Projects ``layer`` fetches side by side: its worker setting (:data:`runner.WORKER_SETTING`), 1 for the
        steps that have none, and 1 for every step while pinned."""
        if self.pinned() or layer not in runner.WORKER_SETTING:
            return 1
        return int(self.settings.value(runner.WORKER_SETTING[layer]))

    def _set_inflight(self) -> None:
        """``PORCHLAMP_GH_INFLIGHT`` 1 (the runner raises it per step, RB5 ruling 2); ``PORCHLAMP_GH_PPM``
        :data:`GH_PPM`."""
        os.environ[ghcli.INFLIGHT_ENV] = "1"
        os.environ[ghcli.PPM_ENV] = str(GH_PPM)

    def _gh_event(self, level: str, text: str) -> None:
        """The GitHub throttle's events (``ghcli.on_event``): ``log`` a technical-log line (the hourly rate, a storm of
        timeouts, the way back), ``warn`` a hold for a secondary limit (timeline), ``err`` the pin (timeline error, the
        pin file, and one request at a time from now on)."""
        if level == "log":
            self.log(text)
            return
        self.event(level, text, "", "network")
        if level == "err":
            path = self._pin_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + "\n",
                            encoding="utf-8")
            self._set_inflight()

    def is_working(self) -> bool:
        return (self.started and not self.stopped and self.runstate.mode == "running"
                and self.runstate.layer is not None and self.current_reason() is None
                and self.gate.rate_state() is None and self.collector_alive())

    # ---- tokens -----------------------------------------------------------------------------------------------------
    def set_tokens(self, github_token: object, lan_token: object, upload_token: object = None) -> None:
        gh, lan, up = _clean(github_token), _clean(lan_token), _clean(upload_token)
        with self._life:
            if gh != self._gh_token:
                old, self._gh_token = self._gh_token, gh
                self.redactor.discard(old)
                self.redactor.add(gh)
                for name in ("GH_TOKEN", *TOKEN_ENV):
                    os.environ.pop(name, None)
                if gh is None:
                    self.controls.set_token(False)
                else:
                    last = self._last_test
                    known = last is not None and last[0] == _digest(gh)
                    self.controls.set_token(True, last[1] if known else "untested", last[2] if known else None)
                    if known and last[1] == "accepted":
                        os.environ["GH_TOKEN"] = gh
                    self.event("info", "GitHub token set" if known else "GitHub token set, not tested yet", "", "network")
                self.gate.kick()
            if lan != self._lan_token:
                old, self._lan_token = self._lan_token, lan
                self.redactor.discard(old)
                self.redactor.add(lan)
                self.lan.set_token(lan)
            if up != self._upload_token:
                old, self._upload_token = self._upload_token, up
                self.redactor.discard(old)
                self.redactor.add(up)
                self.last_upload_error = None
                self._auto_upload_tried_at = -1e9
                if up is None:
                    self.upload_token_state = "untested"
                    self.upload_token_tested_at = None
                else:
                    repo = self.settings.value("drop_repo")
                    last_u = self._last_upload_test
                    known_u = last_u is not None and last_u[0] == _digest(up) and last_u[1] == repo
                    self.upload_token_state = last_u[2] if known_u else "untested"
                    self.upload_token_tested_at = last_u[3] if known_u else None
                    self.event("info", "Upload token set" if known_u else "Upload token set, not tested yet", "", "network")

    @property
    def lan_token_set(self) -> bool:
        return self._lan_token is not None

    @property
    def upload_token_set(self) -> bool:
        return self._upload_token is not None

    def check_token(self, token: str) -> dict[str, Any]:
        """Test ``token`` against GitHub. If it is the token in use, its state and permission to be used follow."""
        result = tokencheck.check(token, base=self._api_base)
        state = result["state"]
        now = self.clock()
        blocked = bool(result.pop("blocked", False))
        result.pop("calls_left", None)
        if state != "unreachable":
            self._last_test = (_digest(token), state, now)
            level = {"accepted": "ok", "rejected": "err", "too_powerful": "err"}[state]
            self.event(level, f"Token tested: {state.replace('_', ' ')}", "", "network")
            with self._life:
                if self._gh_token is not None and _digest(self._gh_token) == _digest(token):
                    self.controls.set_token(True, state, now)
                    if state == "accepted":
                        os.environ["GH_TOKEN"] = token
                    else:
                        os.environ.pop("GH_TOKEN", None)
                    self.gate.kick()
        result["_blocked"] = blocked
        return result

    def check_upload_token(self, token: str, drop_repo: str | None = None) -> dict[str, Any]:
        """Test ``token`` for minimal drop-box scope against GitHub."""
        repo = drop_repo if drop_repo is not None and drop_repo.strip() else self.settings.value("drop_repo")
        result = upload.check_upload_token(token, repo, base=self._api_base)
        state = result["state"]
        now = self.clock()
        blocked = bool(result.pop("blocked", False))
        clean_repo = control.valid_drop_repo(repo) or ""
        if state not in ("unreachable", "bad_repo"):
            self._last_upload_test = (_digest(token), clean_repo, state, now)
            level = {"accepted": "ok", "rejected": "err", "too_powerful": "err"}[state]
            self.event(level, f"Upload token tested: {state.replace('_', ' ')}", "", "network")
            with self._life:
                if (self._upload_token is not None and _digest(self._upload_token) == _digest(token)
                        and clean_repo == self.settings.value("drop_repo")):
                    self.upload_token_state = state
                    self.upload_token_tested_at = now
        result["_blocked"] = blocked
        return result

    def _token_loop(self, cancel: threading.Event) -> None:
        """Test the tokens in use until GitHub has answered; neither collection nor upload uses an unverified token."""
        self.gate.bind_cancel(cancel)
        try:
            while not cancel.is_set() and not self._shutdown.is_set():
                did_work = False
                wait_s = 1.0
                token = self._gh_token
                if token is not None and self.controls.token_state == "untested":
                    did_work = True
                    result = self.check_token(token)
                    if result["state"] == "unreachable":
                        wait_s = max(wait_s, TOKEN_BLOCKED_RETRY_S if result["_blocked"] else TOKEN_RETRY_S)
                up_token = self._upload_token
                s = self.settings.get()
                if (up_token is not None and self.upload_token_state == "untested"
                        and s["delivery_mode"] != "lan" and s["drop_repo"]):
                    did_work = True
                    up_res = self.check_upload_token(up_token, s["drop_repo"])
                    if up_res["state"] == "unreachable":
                        wait_s = max(wait_s, TOKEN_BLOCKED_RETRY_S if up_res["_blocked"] else TOKEN_RETRY_S)
                    elif up_res["state"] == "accepted":
                        self._maybe_start_upload()
                if cancel.wait(wait_s if did_work else 1.0):
                    return
        except Stopped:
            return

    # ---- life cycle ----------------------------------------------------------------------------------------------
    def _route_through_gate(self) -> None:
        """Install this engine's gate and check the collectors really go through it; no token is held before that."""
        previous = httpgate.install(self.gate)
        if collect_net._gate is not httpgate or httpgate.current() is not self.gate:
            httpgate.install(previous)
            raise RuntimeError("the collectors are not routed through the HTTP gate; the engine will not start")

    def start(self, github_token: object, lan_token: object, upload_token: object = None) -> None:
        with self._life:
            self._route_through_gate()
            self.set_tokens(github_token, lan_token, upload_token)
            if self.started and not self.stopped:
                return
            was_stopped = self.stopped
            self.stopped = False
            self.stuck = False
            self.gate.rearm()
            self._shutdown = threading.Event()
            self.started = True
            self.start_ts = self.clock()
            os.environ["PORCHLAMP_GH"] = "http"
            os.environ["PORCHLAMP_ENGINE"] = str(ENGINE_ROOT)
            os.environ["PORCHLAMP_RATE_FLOOR"] = str(self.settings.value("rate_floor"))
            self._set_inflight()
            ghcli.on_event = self._gh_event
            for hook in ("PORCHLAMP_GH_API", "PORCHLAMP_ALLOW_TEST_API", *TOKEN_ENV):
                os.environ.pop(hook, None)   # the API host is fixed, and only the accepted token is used
            marker = self.home / "state" / "running"
            if marker.is_file() and not was_stopped:
                self.restart_ts = self.clock()
                self.event("info", "Restarted by Android",
                           f"The collector was restarted at {status_mod.clock_text(self.restart_ts)}.", "progress")
            marker.write_text(str(int(self.clock())) + "\n", encoding="utf-8")
            self.log("engine started")
            if self._threads_enabled:
                self._spawn_threads()

    def _spawn_threads(self) -> None:
        self._cancel = threading.Event()
        self._monitor = threading.Thread(target=self._monitor_loop, args=(self._shutdown,), daemon=True,
                                         name="porchlamp-monitor")
        self._token_worker = threading.Thread(target=self._token_loop, args=(self._shutdown,), daemon=True,
                                              name="porchlamp-token")
        self._monitor.start()
        self._token_worker.start()
        self._start_collector()

    def _start_collector(self) -> None:
        cancel = self._cancel
        self._collector = threading.Thread(target=self._collector_main, args=(cancel,), daemon=True,
                                           name="porchlamp-collector")
        self._collector.start()

    def restart_collector(self) -> None:
        """Abandon the collector thread (its next request raises ``Stopped``) and start a fresh one."""
        old = self._cancel
        self._cancel = threading.Event()
        old.set()
        self.gate.kick()
        if self._threads_enabled and self.started and not self.stopped:
            self._start_collector()

    def halt_collector(self, wait_s: float = HALT_WAIT_S) -> bool:
        """Stop the collector thread and wait for it. True when it is gone."""
        self._cancel.set()
        self.gate.kick()
        thread = self._collector
        if thread is not None and thread.is_alive():
            thread.join(timeout=wait_s)
        up_thread = self._upload_worker
        if up_thread is not None and up_thread.is_alive():
            up_thread.join(timeout=wait_s)
        return (thread is None or not thread.is_alive()) and (up_thread is None or not up_thread.is_alive())

    def _monitor_loop(self, shutdown: threading.Event) -> None:
        while not shutdown.wait(self._tick_s):
            try:
                self.tick()
            except Exception:  # the monitor must outlive a bug in one tick; the bug is in the log
                self.log("monitor error: " + traceback.format_exc())

    def stop(self) -> None:
        with self._life:
            if not self.started or self.stopped:
                return
            self.stopped = True
            self._shutdown.set()
            self.gate.stop()
            self._cancel.set()
        self.lan.stop_server()
        for thread in (self._collector, self._upload_worker):
            if thread is not None and thread.is_alive():
                thread.join(timeout=5)
        self.stats.flush()
        if ghcli.on_event == self._gh_event:
            ghcli.on_event = None
        (self.home / "state" / "running").unlink(missing_ok=True)
        self.event("info", "Stopped by you", "Progress is saved. Tap Start to carry on.")
        self.log("engine stopped")

    def pause(self) -> None:
        if self.controls.user_paused:
            return
        self.controls.set_user_pause(True)

    def resume(self) -> None:
        self.stuck = False
        self._work_since = None
        self.gate.touch_progress()
        self.controls.set_user_pause(False)
        self.controls.recheck()
        self.gate.kick()

    # ---- the collector thread -----------------------------------------------------------------------------------
    def _sleep(self, seconds: float, cancel: threading.Event) -> None:
        end = time.monotonic() + seconds
        while True:
            if cancel.is_set() or self.gate.stopped:
                raise Stopped("collection was stopped")
            left = end - time.monotonic()
            if left <= 0:
                return
            cancel.wait(min(left, 1.0))

    def _runner_log(self, line: str) -> None:
        if REPO_LINE.match(line):
            self._repo_ts = self.clock()
        self.techlog.write(line)

    def _collector_main(self, cancel: threading.Event) -> None:
        self.gate.bind_cancel(cancel)
        while not cancel.is_set() and not self._shutdown.is_set():
            try:
                main = self._gather_main
                if main is None:
                    main = runner.load_gather(ENGINE_ROOT, self.home).main
                runner.run_forever(
                    self.home, main,
                    every_days=lambda: self.settings.value("interval_days"), poll_s=RUNNER_POLL_S,
                    sleep=lambda s: self._sleep(s, cancel), now=self.clock, log=self._runner_log,
                    layers=lambda: self.settings.value("layers"), state=self.runstate, notify=self._notify,
                    ready=lambda: not self.no_candidates(), workers=self.workers_for)
                return
            except Stopped:
                self.log("collector thread ended")
                return
            except Exception as exc:  # the collector survives its own bugs; the error is told, redacted
                text = self.redactor(f"{type(exc).__name__}: {exc}")
                self.log("collector error: " + self.redactor(traceback.format_exc()))
                self._engine_error = {"t": self.clock(), "text": text, "action": "It tries again in a minute."}
                self.runstate.failures += 1
                self.runstate.last_failure = text
                self.event("err", "The collector hit an error", f"{text} It tries again in a minute.")
                if cancel.wait(60.0):
                    return

    def _notify(self, name: str, **info: Any) -> None:
        steps = status_mod.selected_steps(self.settings.value("layers"))
        layer = info.get("layer")
        where = f"{steps.index(layer) + 1} of {len(steps)}" if layer in steps else ""
        if name == "cycle_start":
            self.controls.cycle_boundary()
            days = self.settings.value("interval_days")
            self.event("info", "Started a new cycle", f"{days} days after the last one finished.")
        elif name == "step_start":
            self.event("ok", f"Started step {where}: {status_mod.STEP_PHRASE.get(layer, str(layer))}")
        elif name == "step_done":
            checked = info.get("checked")
            detail = "" if checked is None else (f"{checked:,} projects checked"
                                                 f"{status_mod.stopped_note(info.get('stopped', 0))}")
            self.event("ok", f"Finished step {where}: {runner.STEP_NAMES.get(layer, str(layer))}", detail)
        elif name == "repair_start":
            self.event("info", "Repairing pull-request records",
                       "It reads again the long discussions that an earlier version of the query cut short.")
        elif name == "repair_done":
            repaired = info.get("repaired") or 0
            self.event("ok", "Pull-request records repaired",
                       f"{repaired:,} projects had records read again." if repaired else "No record needed it.")
        elif name == "cycle_done":
            self.controls.cycle_boundary()
            self.event("ok", "Cycle finished", f"{len(steps)} of {len(steps)} steps. The data is ready to pull.")
            if self.settings.value("delivery_mode") != "lan":
                self._maybe_start_upload()
        elif name == "cycle_fail":
            self.event("err", "A step stopped with an error", f"Code {info.get('rc')}. It tries again in a while.")
        elif name == "no_candidates":
            self.event("warn", "No repo list yet", "It starts by itself once the workstation sends the list.")
        elif name == "idle":
            due = info.get("next_due")
            if due is not None and due != self._idle_event_for:
                self._idle_event_for = due
                self.event("info", f"Idle. The next cycle is due {status_mod.date_text(due)}")
            if self.settings.value("delivery_mode") != "lan":
                self._maybe_start_upload()

    # ---- drop-box upload ----------------------------------------------------------------------------------------
    def _maybe_start_upload(self) -> None:
        if self.stopped or not self.started:
            return
        s = self.settings.get()
        repo = control.valid_drop_repo(s["drop_repo"])
        if s["delivery_mode"] == "lan" or repo is None or self._upload_token is None:
            return
        if self.upload_token_state in ("rejected", "too_powerful"):
            return
        main_done = runner.marker_ts(self.home / "state" / "refresh" / "MAIN.done")
        if main_done is None:
            return
        if self._uploaded_main_done == main_done and self._uploaded_repo == repo:
            return
        now = self.clock()
        if self.last_upload_error is not None and now - self._auto_upload_tried_at < UPLOAD_RETRY_S:
            return
        if self.uploading or (self._upload_worker is not None and self._upload_worker.is_alive()):
            return
        self._auto_upload_tried_at = now
        if self._threads_enabled:
            cancel = self._cancel
            self._upload_worker = threading.Thread(
                target=self._upload_thread_main, args=(cancel,), daemon=True, name="porchlamp-upload"
            )
            self._upload_worker.start()
        else:
            self.run_upload(manual=False)

    def _upload_thread_main(self, cancel: threading.Event) -> None:
        self.gate.bind_cancel(cancel)
        with self._life:
            if self.stopped or cancel.is_set():
                return
        self.run_upload(manual=False)

    def run_upload(self, *, manual: bool = False) -> dict[str, Any]:
        """Run one drop-box upload pass synchronously (used directly in tests and inside ``porchlamp-upload``)."""
        s = self.settings.get()
        repo = control.valid_drop_repo(s["drop_repo"])
        if repo is None:
            return {"ok": False, "detail": "Set a dedicated private drop-box repo in Settings first."}
        token = self._upload_token
        if token is None:
            return {"ok": False, "detail": "Add the upload token in Settings first."}
        main_done = runner.marker_ts(self.home / "state" / "refresh" / "MAIN.done")
        if main_done is None:
            return {"ok": False, "detail": "The current cycle has not finished yet."}
        if not self._upload_lock.acquire(blocking=False):
            return {"ok": True, "uploading": True, "detail": "An upload is already running."}
        try:
            self.uploading = True
            if self.upload_token_state != "accepted":
                chk = self.check_upload_token(token, repo)
                if chk["state"] != "accepted":
                    self.last_upload_error = chk["detail"]
                    return {"ok": False, "detail": chk["detail"]}
            res = upload.upload_cycle(
                self.home,
                token,
                repo,
                selected_layers=s["layers"],
                api_base=self._api_base,
                upload_base=self._upload_base,
                log=self.log,
                clock=self.clock,
                scope="upload",
            )
            self.last_upload_tag = res["tag"]
            self.last_upload_at = self.clock()
            self.last_upload_error = None
            self._uploaded_main_done = main_done
            self._uploaded_repo = repo
            if not res.get("skipped"):
                self.event("ok", "Uploaded cycle to drop-box", f"{res['tag']} -> {repo}", "network")
            detail = (f"Already uploaded {res['tag']}."
                      if res.get("skipped") else f"Uploaded {res['tag']} to {repo}.")
            return {"ok": True, **res, "detail": detail}
        except Stopped:
            return {"ok": False, "detail": "Upload stopped."}
        except httpgate.WouldBlock as exc:
            why = getattr(getattr(exc, "reason", None), "text", None) or "Upload is waiting for device or network rules."
            return {"ok": False, "detail": why}
        except (upload.UploadError, OSError, ValueError) as exc:
            msg = self.redactor(str(exc))
            self.last_upload_error = msg
            self._engine_error = {
                "t": self.clock(),
                "text": f"Drop-box upload failed: {msg}",
                "action": "It tries again automatically, or tap Upload now in Settings.",
            }
            self.event("err", "Drop-box upload failed", msg, "network")
            return {"ok": False, "detail": msg}
        finally:
            self.uploading = False
            self._upload_lock.release()

    # ---- the monitor tick ---------------------------------------------------------------------------------------
    def tick(self) -> None:
        """One pass of the monitor: pause rules, the listener, progress counts, the stuck detector, counters."""
        now = self.clock()
        self.controls.evaluate()
        self.lan.update()
        rs = self.runstate
        layer = rs.layer if rs.mode == "running" else None
        working = self.is_working()
        self.progress.tick(layer, rs.run, rs.checked, rs.total,
                           active=working or self.gate.rate_state() is not None,
                           skipped=rs.pruned + rs.clean)
        self._stuck_check(now, working)
        self.stats.flush(force=False)
        self.datasize.maybe_refresh()

    def _stuck_check(self, now: float, working: bool) -> None:
        if not working:
            self._work_since = None
            return
        if self._work_since is None:
            self._work_since = now
        if self.stuck:
            if self.last_progress_ts() > self._stuck_ts:
                self.stuck = False
                self.event("ok", "Progress is back", "The collector is moving again.")
            return
        if now - max(self._work_since, self.last_progress_ts()) >= STUCK_AFTER_S:
            self.stuck = True
            self._stuck_ts = now
            self.event("warn", "No progress for 25 minutes", "The collector was restarted.")
            self.log("stuck: no progress for 25 minutes; restarting the collector thread once")
            self.restart_collector()

    # ---- callbacks ---------------------------------------------------------------------------------------------------
    def _rate_wait_ended(self, resource: str, seconds: float) -> None:
        self.event("info", f"Waited {status_mod.span_text(seconds)} for GitHub's hourly limit, then carried on", "",
                   "network")

    def _reason_changed(self, old: Reason | None, new: Reason | None) -> None:
        if (old and old.code == "starting") or (new and new.code == "starting"):
            return
        s = self.settings.get()
        facts = self.controls.facts()
        if new is None:
            if old is None:
                return
            if old.code in ("battery", "heat") and s[f"{old.code}_override"]:
                self.event("info", "Carrying on, because Keep going anyway is on.")
                return
            text, kind = {
                "user": ("Resumed", "progress"),
                "battery": (f"Battery is back to {facts.get('battery_pct')}%. Carrying on.", "progress"),
                "heat": ("The battery cooled down. Carrying on.", "progress"),
                "disk_low": ("There is space again. Carrying on.", "progress"),
                "no_wifi": ("Wi-Fi is back. Carrying on." if s["network"] == "wifi" else "The network is back. "
                            "Carrying on.", "network"),
                "slow_network": ("Trying again after the slow network", "network"),
            }.get(old.code, ("Carrying on", "progress"))
            self.event("info", text, "", kind)
            return
        if new.code == "user":
            self.event("info", "Paused by you", "Progress is saved.")
        elif new.code == "battery":
            level = s["battery_pause_pct"]
            self.event("warn", f"Paused: battery is at {facts.get('battery_pct')}%",
                       f"The limit is {level}%. It carries on at {level + 5}%.")
        elif new.code == "heat":
            self.event("warn", f"Paused: the battery is hot ({facts.get('temp_c', 0):.0f} °C)",
                       f"It carries on once it cools below {s['heat_pause_c'] - 3} °C.")
        elif new.code == "disk_low":
            self.event("warn", f"Paused: only {self.controls.free_gb():.1f} GB of space is left",
                       "It resumes by itself above 3 GB.")
        elif new.code == "no_wifi":
            lost = self.controls.wifi_lost_at
            self.event("warn", f"Wi-Fi lost at {status_mod.clock_text(lost)}" if lost else new.text.rstrip("."),
                       new.text, "network")

    def _settings_changed(self, old: dict[str, Any], new: dict[str, Any]) -> None:
        if old["network"] != new["network"]:
            self.event("info", "Mobile data switched on" if new["network"] == "wifi_mobile"
                       else "Mobile data switched off", "", "network")
        for key, word in (("battery_override", "low battery"), ("heat_override", "a hot battery")):
            if old[key] != new[key]:
                self.event("info", f"Keep going anyway turned {'on' if new[key] else 'off'} for {word}",
                           "It lasts until the current cycle ends." if new[key] else "")
        if old["rate_floor"] != new["rate_floor"]:
            os.environ["PORCHLAMP_RATE_FLOOR"] = str(new["rate_floor"])
        if old["lan_mode"] != new["lan_mode"]:
            self.event("info", f"Workstation link set to {new['lan_mode']}", "", "network")
        if old["delivery_mode"] != new["delivery_mode"]:
            self.event("info", f"Delivery mode set to {new['delivery_mode']}", "", "network")
            self._auto_upload_tried_at = -1e9
        if old["drop_repo"] != new["drop_repo"]:
            with self._life:
                self.last_upload_error = None
                self._auto_upload_tried_at = -1e9
                if self._upload_token is not None:
                    last_u = self._last_upload_test
                    known_u = (last_u is not None and last_u[0] == _digest(self._upload_token)
                               and last_u[1] == new["drop_repo"])
                    self.upload_token_state = last_u[2] if known_u else "untested"
                    self.upload_token_tested_at = last_u[3] if known_u else None
            if new["drop_repo"]:
                self.event("info", f"Drop-box repository set to {new['drop_repo']}", "", "network")
        self.controls._dirty = True
        self.gate.kick()
        self.lan.update()
        if new["delivery_mode"] != "lan" and (
            old["delivery_mode"] != new["delivery_mode"] or old["drop_repo"] != new["drop_repo"]
        ):
            self._maybe_start_upload()

    def _on_transfer(self, ip: str, sent: int, received: int, fetched: bool) -> None:
        self.stats.add(ip, "wifi", calls=1, sent=sent, recv=received)
        if fetched:
            self.stats.meta_set("lan_last_fetch", self.clock())

    def _make_server(self, ip: str, port: int, token: str) -> Any:
        if control.valid_bind_ip(ip) != ip:
            raise ValueError("the listener binds only a specific Wi-Fi address")
        try:
            return serve.make_server(self.home, bind=ip, port=port, token=token, on_transfer=self._on_transfer,
                                     log_text=self.techlog.read_text)
        except serve.TokenError as exc:
            raise ValueError(str(exc)) from None

    # ---- the contract functions ---------------------------------------------------------------------------------
    def status(self) -> str:
        return self.redactor(json.dumps(status_mod.build(self)))

    def settings_set(self, text: str) -> str:
        try:
            patch = json.loads(text)
        except (TypeError, ValueError):
            patch = None
        return json.dumps(self.settings.set(patch))

    def override(self, key: str, on: bool) -> None:
        if key in ("battery", "heat"):
            self.settings.set({f"{key}_override": bool(on)})
            self.controls.recheck()

    def events(self, after_id: int, limit: int) -> str:
        return json.dumps(self.timeline.list(int(after_id), int(limit)))

    def export_log(self) -> str:
        return str(self.techlog.export(self.exports))

    def update_device(self, text: str) -> None:
        try:
            facts = json.loads(text)
        except (TypeError, ValueError):
            self.log("update_device: the facts were not readable JSON")
            return
        self.controls.update_device(facts)
        self.lan.update()
        self.gate.kick()

    def test_token(self, token: str) -> str:
        result = self.check_token(token if isinstance(token, str) else "")
        result.pop("_blocked", None)
        return self.redactor(json.dumps(result))

    def test_upload_token(self, token: str, drop_repo: str = "") -> str:
        result = self.check_upload_token(
            token if isinstance(token, str) else "",
            drop_repo if isinstance(drop_repo, str) else "",
        )
        result.pop("_blocked", None)
        return self.redactor(json.dumps(result))

    def upload_now(self) -> str:
        s = self.settings.get()
        repo = control.valid_drop_repo(s["drop_repo"])
        if repo is None:
            return json.dumps({"ok": False, "detail": "Set a dedicated private drop-box repo in Settings first."})
        if self._upload_token is None:
            return json.dumps({"ok": False, "detail": "Add the upload token in Settings first."})
        if not (self.home / "state" / "refresh" / "MAIN.done").is_file():
            return json.dumps({"ok": False, "detail": "The current cycle has not finished yet."})
        if self.uploading or (self._upload_worker is not None and self._upload_worker.is_alive()):
            return json.dumps({"ok": True, "uploading": True, "detail": "An upload is already running."})
        self.last_upload_error = None
        if self._threads_enabled:
            cancel = self._cancel
            self._upload_worker = threading.Thread(
                target=self._upload_thread_main, args=(cancel,), daemon=True, name="porchlamp-upload"
            )
            self._upload_worker.start()
            return json.dumps({"ok": True, "started": True, "detail": "Uploading the finished cycle in the background."})
        return self.redactor(json.dumps(self.run_upload(manual=True)))

    def share(self, minutes: Any = control.SHARE_DEFAULT_MIN) -> None:
        self.lan.share(minutes)

    # ---- the self-check ---------------------------------------------------------------------------------------------
    def self_check(self) -> str:
        t0 = time.monotonic()
        c = self.controls
        s = self.settings.get()
        facts = c.facts()
        now = self.clock()
        checks: list[dict[str, Any]] = []

        def add(check_id: str, title: str, ok: bool, detail: str) -> None:
            checks.append({"id": check_id, "title": title, "ok": ok, "detail": detail})

        # 1. the GitHub token (one free GET /rate_limit)
        if self._gh_token is None:
            add("github", "GitHub token", not self.needs_token(),
                "No GitHub token yet. Add it in Settings." if self.needs_token()
                else "No GitHub token. The selected steps do not need one.")
        elif c.token_state in ("rejected", "too_powerful"):
            add("github", "GitHub token", False,
                "GitHub says the token is " + c.token_state.replace("_", " ") + ". Paste a new one in Settings.")
        else:
            rl = tokencheck.rate_limit(self._gh_token, base=self._api_base)
            if rl.get("blocked"):
                add("github", "GitHub token", False, "Not checked: there is no usable network right now.")
            elif rl.get("status") == 200:
                add("github", "GitHub token", True,
                    f"Accepted. {rl['remaining']:,} calls left this hour, resets at {status_mod.clock_text(rl['reset'])}.")
            elif rl.get("status") == 401:
                c.set_token(True, "rejected", now)
                add("github", "GitHub token", False, "GitHub rejected the token. Paste a new one in Settings.")
            else:
                add("github", "GitHub token", False, "Could not reach GitHub to check it.")
        # 2. the network
        net = facts.get("network")
        if net == "wifi":
            add("network", "Network", True, "Wi-Fi." + (" Mobile data is on." if s["network"] == "wifi_mobile"
                                                         else " Mobile data is off."))
        elif net == "mobile":
            ok = s["network"] == "wifi_mobile" and not facts.get("data_saver")
            add("network", "Network", ok, "Mobile data." if ok else "Mobile data only. It waits until Wi-Fi returns.")
        else:
            add("network", "Network", False, "No network. It waits until Wi-Fi returns.")
        # 3. the workstation link: what the phone side can know
        if not self.lan_token_set:
            if s["delivery_mode"] == "github_drop":
                add("link", "Workstation link", True, "Not needed: delivery is set to GitHub drop-box only.")
            else:
                add("link", "Workstation link", False, "The link token is not set. Add it in Settings.")
        elif self.lan.serving:
            add("link", "Workstation link", True,
                f"Listening on {self.lan.address}. Link token is set. Whether the workstation is on is not checked.")
        elif s["lan_mode"] == "manual":
            add("link", "Workstation link", True,
                "Not sharing right now (manual mode). Tap Share to let the workstation fetch. Link token is set.")
        else:
            add("link", "Workstation link", False, "Not listening: there is no Wi-Fi address yet. Link token is set.")
        # 4. free space
        free = c.free_gb()
        used = self.datasize.data_gb()
        add("space", "Free space", free >= control.DISK_PAUSE_GB,
            f"{free:.0f} GB free. The collector uses {used:.1f} GB." if free >= control.DISK_PAUSE_GB
            else f"Only {free:.1f} GB free. Free some space.")
        # 5. progress
        age = max(0, int(now - self.last_progress_ts()))
        if self.stuck:
            add("progress", "Progress", False,
                f"No progress for {status_mod.span_text(age, up=False)}. It looks stuck.")
        elif self.runstate.mode == "idle":
            due = self.next_due()
            add("progress", "Progress", True, "Idle: nothing to do until "
                + (status_mod.date_text(due) if due else "the next cycle") + ".")
        else:
            add("progress", "Progress", True, f"Last progress {status_mod.span_text(age, up=False)} ago. Not stuck.")
        # 6. battery and heat
        pct, temp = facts.get("battery_pct"), facts.get("temp_c")
        c.evaluate(force=True)
        held = c.power_held
        parts = [f"{pct}%" + (" and charging" if facts.get("charging") else "") if pct is not None else "Battery unknown"]
        if temp is not None:
            parts.append(f"{temp:.0f} °C battery")
        rules = [name for name, key in (("battery", "battery_override"), ("heat", "heat_override")) if not s[key]]
        add("battery", "Battery and heat", not held,
            ", ".join(parts) + ". " + ("Both pause rules are on." if len(rules) == 2 else
                                       "A pause rule is switched off by Keep going anyway."
                                       if rules else "Both pause rules are switched off by Keep going anyway."))
        # 7. drop-box delivery (only when enabled)
        if s["delivery_mode"] != "lan":
            repo = control.valid_drop_repo(s["drop_repo"])
            if repo is None:
                add("delivery", "Drop-box delivery", False, "No private drop-box repo set in Settings.")
            elif self._upload_token is None:
                add("delivery", "Drop-box delivery", False, "No upload token set in Settings.")
            else:
                chk = self.check_upload_token(self._upload_token, repo)
                add("delivery", "Drop-box delivery", chk["state"] == "accepted", chk["detail"])
        return json.dumps({"checks": checks, "all_ok": all(x["ok"] for x in checks),
                           "took_s": round(time.monotonic() - t0, 2)})

    # ---- the danger zone --------------------------------------------------------------------------------------------
    def danger(self, action: str, confirm: str) -> str:
        """Only moves things aside, never deletes. ``confirm`` must equal ``action``."""
        if action not in DANGER_ACTIONS:
            return json.dumps({"ok": False, "detail": "That action is not known."})
        if confirm != action:
            return json.dumps({"ok": False, "detail": "Confirmation did not match."})
        if action == "remove_tokens":
            self._forget_tokens()
            self.event("warn", "Tokens removed", "Add them again in Settings to carry on.")
            return json.dumps({"ok": True, "detail": "All tokens were forgotten. Nothing was deleted."})
        was_running = self.started and not self.stopped and self._threads_enabled
        if was_running and not self.halt_collector():
            return json.dumps({"ok": False, "detail": "The collector could not be stopped in time. "
                                                      "Nothing was moved. Try again in a minute."})
        stamp = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        moved = self._move_aside(action, stamp)
        self.stuck = False
        self.runstate.failures = 0
        self.runstate.last_failure = None
        self.runstate.mode, self.runstate.layer, self.runstate.run = "starting", None, 0
        self.progress.tick(None, 0, 0, 0, False, force=True)
        self.datasize.maybe_refresh(force=True)
        if action == "move_data_aside":
            self.controls.set_user_pause(True)   # nothing starts again by itself; Resume starts a fresh cycle
        if was_running:
            self._cancel = threading.Event()
            self._start_collector()
        self.event("warn", "Reset: progress moved aside" if action == "reset" else "Collected data moved aside",
                   f"Moved to {moved}. Nothing was deleted." if moved else "There was nothing to move.")
        self.gate.kick()
        if not moved:
            return json.dumps({"ok": True, "detail": "There was nothing to move. Nothing was deleted."})
        extra = " Tap Resume to start a fresh cycle." if action == "move_data_aside" else ""
        return json.dumps({"ok": True, "detail": f"Moved aside to {moved}. Nothing was deleted.{extra}"})

    def _move_aside(self, action: str, stamp: str) -> str | None:
        """Rename every collector folder into ``raw-stale-<action>-<stamp>`` and ``state/refresh`` into
        ``state/refresh-prev-<action>-<stamp>``. Returns the data folder's name, or None if nothing moved."""
        raw = self.home / "raw"
        dest = self.home / f"raw-stale-{action}-{stamp}"
        moved_any = False
        for src in sorted(p for p in raw.iterdir() if p.is_dir()) if raw.is_dir() else []:
            dest.mkdir(parents=True, exist_ok=True)
            src.rename(dest / src.name)
            moved_any = True
        markers = self.home / "state" / "refresh"
        prev_dir = self.home / "state" / f"refresh-prev-{action}-{stamp}"
        if markers.is_dir() and any(markers.iterdir()):
            markers.rename(prev_dir)
            markers.mkdir(parents=True, exist_ok=True)
            moved_any = True
        uploaded_state = self.home / "state" / "uploaded.json"
        if uploaded_state.is_file():
            prev_dir.mkdir(parents=True, exist_ok=True)
            uploaded_state.rename(prev_dir / "uploaded.json")
            moved_any = True
        self.last_upload_at = None
        self.last_upload_tag = None
        self.last_upload_error = None
        self._uploaded_main_done = None
        self._uploaded_repo = None
        return dest.name if moved_any else None

    def _forget_tokens(self) -> None:
        with self._life:
            self.redactor.discard(self._gh_token)
            self.redactor.discard(self._lan_token)
            self.redactor.discard(self._upload_token)
            self._gh_token = self._lan_token = self._upload_token = None
            self._last_test = None
            self._last_upload_test = None
            self.upload_token_state = "untested"
            self.upload_token_tested_at = None
            self.last_upload_error = None
            for name in ("GH_TOKEN", *TOKEN_ENV):
                os.environ.pop(name, None)
            self.controls.set_token(False)
        self.lan.set_token(None)
        self.gate.kick()


# ---------------------------------------------------------------------------------------------------------------
# The module-level functions the app calls
# ---------------------------------------------------------------------------------------------------------------
_ENGINE: Engine | None = None
_PENDING_DEVICE: str | None = None
_LOCK = threading.Lock()


def engine() -> Engine | None:
    return _ENGINE


def _cold_status() -> str:
    return json.dumps({
        "schema": 1, "now": status_mod.iso_local(time.time()), "state": "starting", "headline": "Starting up",
        "detail": "Getting ready. This takes a few seconds.",
        "reason": {"code": None, "text": "", "resume_at": None, "override": None},
        "progress": {"step_index": 0, "step_count": 7, "step_name": "", "done": 0, "total": 0, "pct": 0,
                     "checked": 0, "stopped": 0, "pruned": 0, "failed": 0, "eta_text": "", "last_progress_s": 0},
        "cycle": {"started": None, "last_finished": None, "next_due": None, "interval_days": 28, "steps": []},
        "github": {"calls_left": 0, "limit": 0, "resets_in_s": 0, "floor": 500, "in_use": False},
        "network": {"type": "none", "mobile_allowed": False, "bytes_today": {"wifi": 0, "mobile": 0},
                    "bytes_7d": []},
        "phone": {"battery_pct": None, "charging": False, "temp_c": None, "free_gb": 0.0, "data_gb": 0.0},
        "contacts": [], "lan": {"mode": "manual", "serving": False, "address": None, "share_until": None,
                                "last_fetch": None, "ready_steps": 0, "ready_gb": 0.0},
        "delivery": {"mode": "lan", "drop_repo": "",
                     "upload_token": {"set": False, "state": "untested", "tested_at": None},
                     "uploading": False, "last_upload": None, "last_tag": None, "last_error": None},
        "tokens": {"github": {"set": False, "state": "untested", "tested_at": None}, "lan": {"set": False}},
        "alerts": [], "health": {"last_error": None, "retries_today": 0, "uptime_s": 0},
        "device": {"power_save": False, "battery_optimized": False, "notifications_enabled": True,
                   "data_saver": False, "wifi_ip": None}})


def start(files_dir: str, github_token: str | None = None, lan_token: str | None = None,
          upload_token: str | None = None) -> None:
    """Start the engine threads once (idempotent). A repeated call with other tokens swaps the tokens in memory."""
    global _ENGINE, _PENDING_DEVICE
    with _LOCK:
        if _ENGINE is None or not runner.same_path(_ENGINE.files_dir, files_dir):
            if _ENGINE is not None:
                _ENGINE.stop()
            _ENGINE = Engine(files_dir)
        eng, pending, _PENDING_DEVICE = _ENGINE, _PENDING_DEVICE, None
    eng.start(github_token, lan_token, upload_token)
    if pending is not None:
        eng.update_device(pending)


def stop() -> None:
    if _ENGINE is not None:
        _ENGINE.stop()


def pause() -> None:
    if _ENGINE is not None:
        _ENGINE.pause()


def resume() -> None:
    if _ENGINE is not None:
        _ENGINE.resume()


def status() -> str:
    return _ENGINE.status() if _ENGINE is not None else _cold_status()


def settings_get() -> str:
    return json.dumps(_ENGINE.settings.get() if _ENGINE is not None else control.DEFAULTS)


def settings_set(text: str) -> str:
    if _ENGINE is None:
        return json.dumps({"ok": False, "errors": {"settings": "The engine has not started yet."},
                           "settings": control.DEFAULTS})
    return _ENGINE.settings_set(text)


def override(key: str, on: bool) -> None:
    if _ENGINE is not None:
        _ENGINE.override(key, on)


def events(after_id: int = 0, limit: int = 100) -> str:
    return _ENGINE.events(after_id, limit) if _ENGINE is not None else "[]"


def export_log() -> str:
    if _ENGINE is None:
        raise RuntimeError("start() has not been called")
    return _ENGINE.export_log()


def self_check() -> str:
    if _ENGINE is None:
        raise RuntimeError("start() has not been called")
    return _ENGINE.self_check()


def test_token(github_token: str) -> str:
    if _ENGINE is None:
        raise RuntimeError("start() has not been called")
    return _ENGINE.test_token(github_token)


def test_upload_token(upload_token: str, drop_repo: str = "") -> str:
    if _ENGINE is None:
        raise RuntimeError("start() has not been called")
    return _ENGINE.test_upload_token(upload_token, drop_repo)


def upload_now() -> str:
    if _ENGINE is None:
        return json.dumps({"ok": False, "detail": "The engine has not started yet."})
    return _ENGINE.upload_now()


def share(minutes: int = control.SHARE_DEFAULT_MIN) -> None:
    if _ENGINE is not None:
        _ENGINE.share(minutes)


def danger(action: str, confirm: str) -> str:
    if _ENGINE is None:
        return json.dumps({"ok": False, "detail": "The engine has not started yet."})
    return _ENGINE.danger(action, confirm)


def update_device(text: str) -> None:
    global _PENDING_DEVICE
    if _ENGINE is None:
        _PENDING_DEVICE = text
        return
    _ENGINE.update_device(text)
