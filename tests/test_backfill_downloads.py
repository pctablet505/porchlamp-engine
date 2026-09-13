"""Tests for scripts/backfill_downloads.py (W1-8 downloads coverage).

A fake pypistats server (local http.server implementing
``/api/packages/<name>/recent``) supplies every downloads value in this
file; the throttled-package case returns pypistats' real 429 shape
(HTTP 200 + HTML body) so "null is not done" is exercised against the
parser, not a stub. No downloads value is fabricated (R-4).
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses resolves the module via sys.modules
    spec.loader.exec_module(mod)
    return mod


def _load_backfill():
    return _load("backfill_downloads", SCRIPTS / "backfill_downloads.py")


# --------------------------------------------------------------------------
# fake pypistats server
# --------------------------------------------------------------------------

class _PypistatsHandler(BaseHTTPRequestHandler):
    """Serves /api/packages/<name>/recent with a per-response delay.

    Server attributes (set by the test): ``values`` (name -> last_month),
    ``modes`` (name -> "html429" | "missing"), ``response_delay`` seconds,
    ``seen`` (completed responses: [(name, timestamp), ...]).
    """

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, code: int, body: bytes) -> bool:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return False
        return True

    def do_GET(self) -> None:
        try:
            m = re.fullmatch(r"/api/packages/([^/]+)/recent", self.path)
            if m is None:
                self._send(404, b'{"error": "not found"}')
                return
            name = m.group(1)
            time.sleep(self.server.response_delay)
            mode = self.server.modes.get(name, "value")
            if mode == "html429":  # pypistats throttling: 200 + HTML body
                ok = self._send(200, b'<a href="/api/#etiquette">429 RATE LIMIT EXCEEDED</a>')
            elif mode == "missing":
                ok = self._send(404, b'{"error": "package not found"}')
            else:
                body = json.dumps({"data": {"last_day": 1, "last_week": 7,
                                            "last_month": self.server.values[name]},
                                  "package": name,
                                  "type": "recent_downloads"}).encode()
                ok = self._send(200, body)
            if ok:
                self.server.seen.append((name, time.time()))
        except (BrokenPipeError, ConnectionResetError):
            pass


def _start_server(values: dict, modes: Optional[dict] = None, delay: float = 0.0):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PypistatsHandler)
    server.response_delay = delay
    server.modes = modes or {}
    server.values = values
    server.seen = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def monthly_of(data_dir: Path, name: str) -> Optional[int]:
    p = data_dir / "raw" / "downloads" / f"pypi__{name}.json"
    if not p.is_file():
        return None
    try:
        return (json.loads(p.read_text(encoding="utf-8")).get("data") or {}).get("monthly")
    except json.JSONDecodeError:
        return None


def make_data_dir(data_dir: Path, names: list[str],
                  done: Optional[dict] = None) -> Path:
    done = done or {}
    dl = data_dir / "raw" / "downloads"
    dl.mkdir(parents=True, exist_ok=True)
    for name in names:
        env = {
            "fetched_at": "2026-09-12T00:00:00+00:00",
            "source": "pypistats-deferred",
            "http_status": 200, "etag": None, "sha256": "0" * 64,
            "collector_version": "gather/1.0",
            "data": {"monthly": done.get(name)},
        }
        (dl / f"pypi__{name}.json").write_text(json.dumps(env), encoding="utf-8")
    return data_dir


# --------------------------------------------------------------------------
# runner: resume after kill (acceptance 1)
# --------------------------------------------------------------------------

def test_runner_resumes_after_kill(tmp_path):
    names = sorted(f"pkg{i:02d}" for i in range(6))
    values = {n: 100 + 10 * i for i, n in enumerate(names)}
    server = _start_server(values, delay=0.5)  # 0.5 s hold -> kill lands mid-request
    try:
        data_dir = tmp_path / "data"
        make_data_dir(data_dir, names)
        cmd = [sys.executable, str(SCRIPTS / "backfill_downloads.py"),
               "--data-dir", str(data_dir),
               "--base-url", f"http://127.0.0.1:{server.server_address[1]}",
               "--interval", "0.05"]
        log1 = tmp_path / "run1.log"
        with open(log1, "w") as lf:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
            try:
                deadline = time.time() + 60
                while time.time() < deadline:
                    if sum(1 for n in names if monthly_of(data_dir, n) is not None) == 3:
                        break
                    time.sleep(0.02)
                proc.kill()  # SIGKILL mid-run
                proc.wait(timeout=10)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=10)
        # exact counts after the kill: 3 done, 3 nulls still undone on disk
        done1 = sorted(n for n in names if monthly_of(data_dir, n) is not None)
        assert done1 == names[:3]
        assert [monthly_of(data_dir, n) for n in names[3:]] == [None] * 3
        first_run = list(server.seen)
        assert [n for n, _ in first_run] == names[:3]  # no 4th response completed

        proc2 = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        assert proc2.returncode == 0, proc2.stdout[-2000:]
        # resumed exactly where it stopped: the 3 nulls fetched, done ones not
        for n in names:
            assert monthly_of(data_dir, n) == values[n]
        second_run = [n for n, _ in server.seen[len(first_run):]]
        assert second_run == names[3:]
        assert len(server.seen) == 6  # 6 packages, 6 completed responses, no refetch
    finally:
        server.shutdown()


# --------------------------------------------------------------------------
# runner: rate respected (acceptance 2) + ETA
# --------------------------------------------------------------------------

def test_rate_respected_and_eta_printed(tmp_path, capsys):
    names = [f"b{i}" for i in range(4)]
    values = {n: 1 for n in names}
    server = _start_server(values, delay=0.0)  # instant replies: spacing is pure throttle
    try:
        data_dir = tmp_path / "data"
        make_data_dir(data_dir, names)
        backfill = _load_backfill()
        report = backfill.backfill(
            data_dir, base_url=f"http://127.0.0.1:{server.server_address[1]}",
            min_interval=0.4,
        )
        assert report.fetched == 4 and report.still_null == 0
        ts = [t for _, t in server.seen]
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        assert all(g >= 0.35 for g in gaps), f"inter-request gaps {gaps} < 0.4 s interval"
        assert all(n == f"b{i}" for i, (n, _) in enumerate(server.seen))
        out = capsys.readouterr().out
        assert "eta" in out.lower()  # ETA is reported, not just counted
        assert "measured" in out
    finally:
        server.shutdown()


def test_default_interval_is_4_per_minute():
    backfill = _load_backfill()
    assert backfill.DEFAULT_MIN_INTERVAL == 15.0
    assert round(60.0 / backfill.DEFAULT_MIN_INTERVAL, 1) == 4.0


# --------------------------------------------------------------------------
# runner: nulls are not done (R-4: no fabricated downloads)
# --------------------------------------------------------------------------

def test_nulls_stay_undone_and_are_refetched(tmp_path):
    values = {"goodpkg": 77, "throttledpkg": None}
    server = _start_server(values, modes={"throttledpkg": "html429"})
    try:
        data_dir = tmp_path / "data"
        make_data_dir(data_dir, ["goodpkg", "throttledpkg"])
        backfill = _load_backfill()
        r1 = backfill.backfill(data_dir,
                               base_url=f"http://127.0.0.1:{server.server_address[1]}",
                               min_interval=0.0)
        assert monthly_of(data_dir, "goodpkg") == 77
        assert monthly_of(data_dir, "throttledpkg") is None  # null stays null
        assert r1.fetched == 1 and r1.still_null == 1
        server.modes = {}
        server.values["throttledpkg"] = 78
        r2 = backfill.backfill(data_dir,
                               base_url=f"http://127.0.0.1:{server.server_address[1]}",
                               min_interval=0.0)
        assert monthly_of(data_dir, "throttledpkg") == 78
        assert r2.fetched == 1  # only the null was refetched
        assert [n for n, _ in server.seen] == ["goodpkg", "throttledpkg", "throttledpkg"]
    finally:
        server.shutdown()


def test_done_not_refetched_and_404_not_fabricated(tmp_path):
    values = {"knownpkg": 5}
    server = _start_server(values, modes={"ghostpkg": "missing"})
    try:
        data_dir = tmp_path / "data"
        make_data_dir(data_dir, ["ghostpkg", "knownpkg"], done={"knownpkg": 42})
        backfill = _load_backfill()
        r = backfill.backfill(data_dir,
                              base_url=f"http://127.0.0.1:{server.server_address[1]}",
                              min_interval=0.0)
        assert monthly_of(data_dir, "knownpkg") == 42  # existing value untouched
        assert "knownpkg" not in [n for n, _ in server.seen]  # done -> skipped
        assert monthly_of(data_dir, "ghostpkg") is None  # 404 is not a value
        assert r.fetched == 0 and r.still_null == 1 and r.already_done == 1
    finally:
        server.shutdown()


# --------------------------------------------------------------------------
# BigQuery probe (acceptance 4): no credentials -> UNDECIDED / U-ENV
# --------------------------------------------------------------------------

def test_probe_bigquery_no_credentials_on_this_machine():
    backfill = _load_backfill()
    res = backfill.probe_bigquery()
    assert res.status == "UNDECIDED"
    assert res.reason_code == "U-ENV"
    assert res.label() == "UNDECIDED / U-ENV"


def test_probe_bigquery_reachable_report():
    backfill = _load_backfill()

    class FakeClient:
        def list_datasets(self, project, max_results=None):
            assert project == backfill.PUBLIC_DATASET_PROJECT
            return iter([{"dataset_id": "pypi"}] * 3)

    res = backfill.probe_bigquery(client=FakeClient())
    assert res.status == "OK" and res.reason_code is None
    assert "3 datasets" in res.detail


def test_probe_bigquery_query_failure_undecided():
    backfill = _load_backfill()

    class Boom:
        def list_datasets(self, *a, **k):
            raise RuntimeError("quota denied")

    res = backfill.probe_bigquery(client=Boom())
    assert res.status == "UNDECIDED" and res.reason_code == "U-QUERY"
    assert "quota denied" in res.detail


def test_cli_probe_bigquery_output(tmp_path):
    out = subprocess.run([sys.executable, str(SCRIPTS / "backfill_downloads.py"),
                          "--data-dir", str(tmp_path), "--probe-bigquery"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "UNDECIDED / U-ENV" in out.stdout


# --------------------------------------------------------------------------
# ecosystem capability map (packet: go dependents-only, maven unmeasured,
# crates 90-day/3 kept, npm as is)
# --------------------------------------------------------------------------

def test_downloads_status_per_ecosystem():
    backfill = _load_backfill()
    f = backfill.downloads_status
    assert f("pypi", True) == "measured"
    assert f("pypi", False) == "pending"
    assert f("npm", False) == "pending"
    assert f("crates", False) == "pending"
    assert f("go", False) == "unmeasured"
    assert f("maven", False) == "unmeasured"
    assert f("systems", False) == "unmeasured"


# --------------------------------------------------------------------------
# gather_repo hook (append-only): downloads null -> pending/unmeasured
# --------------------------------------------------------------------------

gather = _load("gather_w18", SCRIPTS / "gather.py")


def _seed_all_but_downloads(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    key = "acme__widget"
    # W1-9/W1-5/W1-2/W1-3/W1-7/W1-6 postdate this file and are not under
    # test here; marking them done avoids live check-run/file-body/commits/
    # deps.dev/ecosyste.ms/graphql/gh api calls for "acme/widget" below
    # (Blocker B4 round 2).
    for col in ("github_meta", "pr_events", "pr_reviews", "contributors",
                "commits_180d", "actions_runs", "depsdev",
                "scorecard", "tree", "repo_object", "governance",
                "commits_365d", "dependents", "pr_window",
                "releases", "issues_counts", "runs"):
        p = raw / col / f"{key}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"fetched_at": "2026-09-12T00:00:00+00:00",
                                 "source": "test",
                                 "data": {"default_branch": "main"}}),
                     encoding="utf-8")
    # W1-1's per-label gfi collector treats a bare/legacy-shaped envelope as
    # needing re-gather (needs_gather); the new shape's metadata-first
    # record is what actually marks it done.
    gfi_p = raw / "gfi" / f"{key}.json"
    gfi_p.parent.mkdir(parents=True, exist_ok=True)
    gfi_p.write_text(json.dumps({"fetched_at": "2026-09-12T00:00:00+00:00",
                                 "source": "test",
                                 "data": [{"gfi_newcomer_v2": True,
                                           "labels_matched": [], "queries": [],
                                           "truncated": False}]}),
                     encoding="utf-8")


def test_gather_pypi_null_is_pending_not_fail(tmp_path, monkeypatch, block_live_network):
    _seed_all_but_downloads(tmp_path)
    monkeypatch.setattr(gather, "DATA", tmp_path / "raw")
    monkeypatch.setattr(gather.time, "sleep", lambda s: None)
    monkeypatch.setattr(gather, "_get_json", lambda url, headers=None: None)  # throttled
    st = gather.gather_repo("acme", "widget", "pypi")
    assert st["downloads"] == "pending"
    assert st["github_meta"] == st["repo_object"] == "skip"  # untouched paths
    assert monthly_of(tmp_path, "widget") is None  # null envelope kept


def test_gather_maven_is_unmeasured(tmp_path, monkeypatch, block_live_network):
    _seed_all_but_downloads(tmp_path)
    monkeypatch.setattr(gather, "DATA", tmp_path / "raw")
    monkeypatch.setattr(gather.time, "sleep", lambda s: None)
    st = gather.gather_repo("acme", "widget", "maven")
    assert st["downloads"] == "unmeasured"


def test_gather_pypi_ok_path_unchanged(tmp_path, monkeypatch, block_live_network):
    """Over-correction guard: a successful pypistats answer stays "ok"."""
    _seed_all_but_downloads(tmp_path)
    monkeypatch.setattr(gather, "DATA", tmp_path / "raw")
    monkeypatch.setattr(gather.time, "sleep", lambda s: None)
    monkeypatch.setattr(gather, "_get_json",
                        lambda url, headers=None: {"data": {"last_month": 42}})
    st = gather.gather_repo("acme", "widget", "pypi")
    assert st["downloads"] == "ok"
    assert monthly_of(tmp_path, "widget") == 42


# --------------------------------------------------------------------------
# derive_repo hook (append-only): downloads_status in the bundle
# --------------------------------------------------------------------------

derive = _load("derive_w18", SCRIPTS / "derive.py")


def _seed_downloads(tmp_path: Path, eco: str, monthly) -> None:
    p = tmp_path / "raw" / "downloads" / f"{eco}__widget.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": "2026-09-12T00:00:00+00:00",
                             "source": "pypistats", "data": {"monthly": monthly}}),
                 encoding="utf-8")


def test_derive_pypi_pending(tmp_path):
    _seed_downloads(tmp_path, "pypi", None)
    out = derive.derive_repo("acme", "widget", derive.Ecosystem.PYPI, tmp_path, now=NOW)
    assert out["meta"]["downloads_status"] == "pending"
    assert out["confidence"]["downloads_status"] == "derived"
    assert out["meta"]["monthly_downloads"] == 0  # existing path unchanged
    assert out["confidence"]["monthly_downloads"] == "missing"


def test_derive_pypi_measured(tmp_path):
    _seed_downloads(tmp_path, "pypi", 5000)
    out = derive.derive_repo("acme", "widget", derive.Ecosystem.PYPI, tmp_path, now=NOW)
    assert out["meta"]["downloads_status"] == "measured"
    assert out["provenance"]["downloads_status"]["source"].endswith(
        "downloads/pypi__widget.json")
    assert out["meta"]["monthly_downloads"] == 5000


def test_derive_maven_unmeasured(tmp_path):
    out = derive.derive_repo("acme", "widget", derive.Ecosystem.MAVEN, tmp_path, now=NOW)
    assert out["meta"]["downloads_status"] == "unmeasured"
    assert out["provenance"]["downloads_status"]["source"] is None
