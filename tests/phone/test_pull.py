"""scripts/pull_from_phone.py against an in-process phone/serve.py on loopback."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from pathlib import Path

import pytest

from phone.archive import LAYERS, write_manifest
from phone.auth import sign_request
from phone.serve import make_server
from tests.phone import DUMMY_TOKEN, MARKER_TEXT, make_home

REPO = Path(__file__).resolve().parents[2]
PULL = REPO / "scripts" / "pull_from_phone.py"


@pytest.fixture
def serving(tmp_path):
    """Start a server on a home; yields (home, base_url, token_file). The caller builds the home first."""
    holder = {}

    def start(home: Path):
        server = make_server(home, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        holder["server"] = server
        token_file = tmp_path / "token.txt"
        token_file.write_text(DUMMY_TOKEN + "\n", encoding="utf-8")
        return f"http://127.0.0.1:{server.server_address[1]}", token_file

    yield start
    if "server" in holder:
        holder["server"].shutdown()
        holder["server"].server_close()


def _pull(base, token_file, dest, *extra):
    return subprocess.run([sys.executable, str(PULL), "--url", base, "--token-file", str(token_file),
                           "--dest", str(dest), *extra], capture_output=True, text=True, timeout=120)


def _raw_tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted((root / "raw").rglob("*")) if p.is_file()}


def _plant_archive(home: Path, layer: str, members: dict) -> None:
    """Write a hand-made archive for ``layer`` with the given member name -> bytes, and a matching cache entry."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    blob = buf.getvalue()
    archives = home / "archives"
    archives.mkdir(parents=True, exist_ok=True)
    (archives / f"{layer}.tar.gz").write_bytes(blob)
    meta = {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(), "files": len(members),
            "completed_at": MARKER_TEXT}
    (archives / f"{layer}.meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_pull_end_to_end_three_layers(tmp_path, serving):
    home = make_home(tmp_path / "phone", layers=("L1", "L0", "L2"), files_per=50)
    base, token_file = serving(home)
    dest = tmp_path / "dest"
    proc = _pull(base, token_file, dest)
    assert proc.returncode == 0, proc.stderr
    assert "L1: pulled" in proc.stdout and "L0: pulled" in proc.stdout and "L2: pulled" in proc.stdout
    assert DUMMY_TOKEN not in proc.stdout + proc.stderr
    for layer in ("L1", "L0", "L2"):
        assert (dest / "state" / "refresh" / f"{layer}.complete").read_text().strip() == MARKER_TEXT
    assert _raw_tree(dest) == _raw_tree(home) and len(_raw_tree(dest)) == 50 * sum(len(LAYERS[x]) for x in ("L1", "L0", "L2"))


def test_pull_sha_mismatch_exit_3_extracts_nothing(tmp_path, serving):
    home = make_home(tmp_path / "phone", layers=("L1", "L0"), files_per=5)
    write_manifest(home)
    meta_path = home / "archives" / "L0.meta.json"
    meta = json.loads(meta_path.read_text())
    meta["sha256"] = "0" * 64  # the phone advertises a digest its bytes do not have
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    base, token_file = serving(home)
    dest = tmp_path / "dest"
    proc = _pull(base, token_file, dest)
    assert proc.returncode == 3, proc.stderr
    assert not (dest / "raw").exists()
    assert not (dest / "state" / "pulled").exists()
    assert not (dest / "pull-tmp" / "L0.tar.gz.part").exists()


def test_pull_unsafe_member_exit_4(tmp_path, serving):
    for label, members in (
        ("traversal", {"raw/pr_window/ok.json": b"{}", "raw/pr_window/../evil.json": b"{}"}),
        ("wrong-collector", {"raw/pr_window/ok.json": b"{}", "raw/tree/x.json": b"{}"}),
        ("not-json", {"raw/pr_window/ok.txt": b"{}"}),
    ):
        home = make_home(tmp_path / label / "phone", layers=(), files_per=0)
        (home / "state" / "refresh").mkdir(parents=True, exist_ok=True)
        (home / "state" / "refresh" / "L1.complete").write_text(MARKER_TEXT + "\n", encoding="utf-8")
        _plant_archive(home, "L1", members)
        base, token_file = serving(home)
        dest = tmp_path / label / "dest"
        proc = _pull(base, token_file, dest)
        assert proc.returncode == 4, (label, proc.returncode, proc.stderr)
        assert not (dest / "raw").exists(), label
        assert not (dest / "evil.json").exists()


def test_pull_moves_existing_raw_aside_never_deletes(tmp_path, serving):
    home = make_home(tmp_path / "phone", layers=("L1",), files_per=4)
    base, token_file = serving(home)
    dest = tmp_path / "dest"
    (dest / "raw" / "pr_window").mkdir(parents=True)
    (dest / "raw" / "pr_window" / "old.json").write_text("OLD", encoding="utf-8")
    proc = _pull(base, token_file, dest)
    assert proc.returncode == 0, proc.stderr
    stale = list(dest.glob("raw-stale-*-pull/pr_window/old.json"))
    assert len(stale) == 1 and stale[0].read_text() == "OLD"
    assert _raw_tree(dest) == _raw_tree(home)
    # A set-aside target that already exists is a stop (exit 5), and nothing is moved.
    dest2 = tmp_path / "dest2"
    (dest2 / "raw" / "pr_window").mkdir(parents=True)
    (dest2 / "raw" / "pr_window" / "keep.json").write_text("KEEP", encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (dest2 / f"raw-stale-{stamp}-pull" / "pr_window").mkdir(parents=True)
    proc = _pull(base, token_file, dest2)
    assert proc.returncode == 5, proc.stderr
    assert (dest2 / "raw" / "pr_window" / "keep.json").read_text() == "KEEP"


def test_pull_second_run_downloads_nothing(tmp_path, serving):
    home = make_home(tmp_path / "phone", layers=("L1", "L0"), files_per=3)
    base, token_file = serving(home)
    dest = tmp_path / "dest"
    assert _pull(base, token_file, dest).returncode == 0
    proc = _pull(base, token_file, dest)
    assert proc.returncode == 0, proc.stderr
    assert "L1: unchanged" in proc.stdout and "L0: unchanged" in proc.stdout
    assert "pulled" not in proc.stdout
    assert not list((dest / "pull-tmp").glob("*.part"))


def test_pull_resumes_a_partial_download(tmp_path, serving):
    home = make_home(tmp_path / "phone", layers=("L1",), files_per=20)
    write_manifest(home)
    blob = (home / "archives" / "L1.tar.gz").read_bytes()
    base, token_file = serving(home)
    dest = tmp_path / "dest"
    part = dest / "pull-tmp" / "L1.tar.gz.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(blob[:100])
    proc = _pull(base, token_file, dest)
    assert proc.returncode == 0, proc.stderr
    assert "(resumed at 100)" in proc.stdout
    assert _raw_tree(dest) == _raw_tree(home)


def test_pull_writes_main_done_only_when_all_six_main_layers(tmp_path, serving):
    # Five of the six MAIN layers complete (L3g is not in this home): MAIN.done must not be written.
    home = make_home(tmp_path / "phone", layers=("L1", "L0", "L2", "L3c", "L4"), files_per=2)
    (home / "state" / "refresh" / "MAIN.done").write_text("2026-10-11T04:00:00Z\n", encoding="utf-8")
    base, token_file = serving(home)
    dest = tmp_path / "dest"
    proc = _pull(base, token_file, dest)
    assert proc.returncode == 0, proc.stderr
    assert "MAIN.done written" not in proc.stdout
    assert not (dest / "state" / "refresh" / "MAIN.done").exists()


# ---- reply signatures and signed requests -----------------------------------------------------------------------
class Relay:
    """A man in the middle between the puller and the real phone server, with one deliberate misbehaviour."""

    def __init__(self, upstream_port: int, mode: str, recorded: tuple | None = None, elsewhere: str = ""):
        relay = self
        self.mode, self.recorded, self.elsewhere, self.paths = mode, recorded, elsewhere, []

        class Handler(BaseHTTPRequestHandler):
            def _go(self):
                relay.paths.append(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                if relay.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", relay.elsewhere + self.path)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if relay.mode == "replay" and self.path == "/manifest.json":
                    status, headers, data = relay.recorded
                else:
                    import http.client

                    conn = http.client.HTTPConnection("127.0.0.1", upstream_port, timeout=30)
                    conn.request(self.command, self.path, body=body or None,
                                 headers={k: v for k, v in self.headers.items() if k.lower() != "host"})
                    resp = conn.getresponse()
                    status, headers, data = resp.status, dict(resp.getheaders()), resp.read()
                    conn.close()
                    if relay.mode == "oversize" and self.path.startswith("/archive/"):
                        data += b"X" * 4096  # more bytes than the manifest promised
                    if self.path == "/manifest.json":
                        if relay.mode == "strip-mac":
                            headers.pop("X-Porch-Mac")
                        elif relay.mode == "bad-mac":
                            headers["X-Porch-Mac"] = "0" * 64
                        elif relay.mode == "tamper-body":
                            doc = json.loads(data)
                            for entry in doc["layers"].values():
                                entry["sha256"] = "0" * 64  # advertise a digest the bytes do not have
                            data = json.dumps(doc).encode()
                self.send_response(status)
                for key, value in headers.items():
                    if key.lower() not in ("content-length", "date", "server", "connection", "transfer-encoding"):
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_PUT = _go

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _upstream_port(base: str) -> int:
    return int(base.rsplit(":", 1)[1])


def test_pull_through_a_faithful_relay_works(tmp_path, serving):
    home = make_home(tmp_path / "phone", layers=("L1",), files_per=3)
    base, token_file = serving(home)
    relay = Relay(_upstream_port(base), "pass")
    try:
        proc = _pull(relay.url, token_file, tmp_path / "dest")
    finally:
        relay.close()
    assert proc.returncode == 0, proc.stderr
    assert _raw_tree(tmp_path / "dest") == _raw_tree(home)
    assert "/manifest.json" in relay.paths and "/archive/L1.tar.gz" in relay.paths


@pytest.mark.parametrize("mode", ["strip-mac", "bad-mac", "tamper-body", "replay"])
def test_pull_exits_1_and_extracts_nothing_when_the_manifest_mac_is_missing_or_wrong(tmp_path, serving, mode):
    home = make_home(tmp_path / "phone", layers=("L1", "L0"), files_per=3)
    base, token_file = serving(home)
    recorded = None
    if mode == "replay":  # a genuine, correctly signed manifest reply, but for an earlier request's nonce
        import http.client

        headers = sign_request(DUMMY_TOKEN, "GET", "/manifest.json", b"")
        conn = http.client.HTTPConnection("127.0.0.1", _upstream_port(base), timeout=30)
        conn.request("GET", "/manifest.json", headers=headers)
        resp = conn.getresponse()
        recorded = (resp.status, dict(resp.getheaders()), resp.read())
        conn.close()
        assert "X-Porch-Mac" in recorded[1]
    relay = Relay(_upstream_port(base), mode, recorded=recorded)
    dest = tmp_path / "dest"
    try:
        proc = _pull(relay.url, token_file, dest)
    finally:
        relay.close()
    assert proc.returncode == 1, (mode, proc.returncode, proc.stdout, proc.stderr)
    assert "signature" in proc.stderr
    assert DUMMY_TOKEN not in proc.stdout + proc.stderr
    assert not (dest / "raw").exists() and not (dest / "state").exists() and not (dest / "pull-tmp").exists()
    assert not any(path.startswith("/archive/") for path in relay.paths)  # nothing was even downloaded


def test_pull_does_not_follow_a_redirect_with_its_signed_headers(tmp_path, serving):
    home = make_home(tmp_path / "phone", layers=("L1",), files_per=2)
    base, token_file = serving(home)
    other = Relay(_upstream_port(base), "pass")
    relay = Relay(_upstream_port(base), "redirect", elsewhere=other.url)
    try:
        proc = _pull(relay.url, token_file, tmp_path / "dest")
    finally:
        relay.close()
        other.close()
    assert proc.returncode == 1 and other.paths == []
    assert not (tmp_path / "dest" / "raw").exists()


def test_push_candidates_is_signed_and_validated(tmp_path, serving):
    home = make_home(tmp_path / "phone", layers=("L1",), files_per=1)
    base, token_file = serving(home)
    good = tmp_path / "good.jsonl"
    good.write_bytes(b'{"owner": "octo", "name": "cat", "ecosystem": "npm"}\n')
    cmd = [sys.executable, str(PULL), "--url", base, "--token-file", str(token_file)]
    proc = subprocess.run([*cmd, "--push-candidates", str(good)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert (home / "raw" / "candidates.jsonl").read_bytes() == good.read_bytes()
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b'{"owner": "octo/evil", "name": "cat", "ecosystem": "npm"}\n')
    proc = subprocess.run([*cmd, "--push-candidates", str(bad)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1 and "HTTP 400" in proc.stderr
    assert (home / "raw" / "candidates.jsonl").read_bytes() == good.read_bytes()


def test_pull_with_the_wrong_token_is_exit_1(tmp_path, serving):
    home = make_home(tmp_path / "phone", layers=("L1",), files_per=1)
    base, token_file = serving(home)
    token_file.write_text("w" * 32 + "\n", encoding="utf-8")
    proc = _pull(base, token_file, tmp_path / "dest")
    assert proc.returncode == 1 and "HTTP 401" in proc.stderr
    assert not (tmp_path / "dest" / "raw").exists()


# ---- H. the download stops at the manifest's byte count ------------------------------------------------------------
class _Endless:
    """A response whose body is far longer than promised; counts what the puller actually reads."""

    status = 200

    def __init__(self, total: int):
        self.left, self.read_bytes = total, 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        n = self.left if n is None or n < 0 else min(n, self.left)
        self.left -= n
        self.read_bytes += n
        return b"y" * n


def _load_puller():
    import importlib.util

    spec = importlib.util.spec_from_file_location("pull_from_phone_under_test", PULL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_download_reads_at_most_one_byte_past_the_manifest_count_and_removes_the_part(tmp_path, monkeypatch):
    puller = _load_puller()
    stream = _Endless(total=50 * (1 << 20))
    monkeypatch.setattr(puller, "_request", lambda *a, **kw: stream)
    part = tmp_path / "pull-tmp" / "L1.tar.gz.part"
    with pytest.raises(puller.PullError) as info:
        puller._download("http://x", "t" * 32, "L1", {"bytes": 1000}, part)
    assert info.value.code == 3
    assert stream.read_bytes == 1001  # exactly one byte more than the manifest says, then it stops reading
    assert not part.exists()


def test_download_of_exactly_the_manifest_count_is_accepted(tmp_path, monkeypatch):
    puller = _load_puller()
    stream = _Endless(total=1000)
    monkeypatch.setattr(puller, "_request", lambda *a, **kw: stream)
    part = tmp_path / "pull-tmp" / "L1.tar.gz.part"
    assert puller._download("http://x", "t" * 32, "L1", {"bytes": 1000}, part) is None
    assert part.stat().st_size == 1000 and stream.read_bytes == 1000


def test_pull_exit_3_when_the_phone_sends_more_than_the_manifest_says(tmp_path, serving):
    home = make_home(tmp_path / "phone", layers=("L1",), files_per=3)
    base, token_file = serving(home)
    relay = Relay(_upstream_port(base), "oversize")
    dest = tmp_path / "dest"
    try:
        proc = _pull(relay.url, token_file, dest)
    finally:
        relay.close()
    assert proc.returncode == 3, (proc.returncode, proc.stderr)
    assert not (dest / "raw").exists() and not list((dest / "pull-tmp").glob("*.part"))
