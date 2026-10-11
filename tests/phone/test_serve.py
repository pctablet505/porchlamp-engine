"""phone/serve.py: token gate, routes, Range, name checks, atomic PUTs, and no writes outside HOME."""

from __future__ import annotations

import http.client
import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from phone.serve import CANDIDATES_CAP, make_server
from tests.phone import DUMMY_TOKEN, make_home

REPO = Path(__file__).resolve().parents[2]
AUTH = {"Authorization": f"Bearer {DUMMY_TOKEN}"}


@pytest.fixture
def served(tmp_path):
    home = make_home(tmp_path / "home", layers=("L1", "L0"), files_per=5)
    server = make_server(home, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _req(server, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=20)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
    finally:
        conn.close()


def _tree(root: Path) -> dict:
    """path -> (size, mode, content hash) for every file under root."""
    import hashlib

    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = (p.stat().st_size, stat.S_IMODE(p.stat().st_mode),
                                             hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def test_serve_refuses_start_without_token_or_short_token(tmp_path):
    for label, token in (("missing", None), ("short", "x" * 23)):
        home = make_home(tmp_path / label, layers=("L1",), files_per=1, token=token)
        proc = subprocess.run(
            [sys.executable, "-m", "phone.serve", "--home", str(home), "--port", "0", "--bind", "127.0.0.1"],
            cwd=REPO, capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 2, (label, proc.returncode, proc.stderr)
        assert DUMMY_TOKEN not in proc.stderr + proc.stdout


def test_serve_401_without_or_with_wrong_token(served):
    for headers in ({}, {"Authorization": "Bearer " + "x" * 32}, {"Authorization": "Basic " + DUMMY_TOKEN},
                    {"Authorization": DUMMY_TOKEN}):
        status, resp, body = _req(served, "GET", "/status.json", headers)
        assert status == 401, headers
        assert body == b""
        assert resp.get("www-authenticate") == "Bearer"
    status, _, _ = _req(served, "PUT", "/config/github_token", {"Authorization": "Bearer nope"}, b"x")
    assert status == 401
    assert not (served.home / "config" / "github_token").exists()


def test_serve_200_with_token(served):
    status, _, body = _req(served, "GET", "/status.json", AUTH)
    assert status == 200
    st = json.loads(body)
    assert st["completed_layers"] == ["L1", "L0"]
    assert st["files"]["pr_window"] == 5 and st["lan_token_set"] is True and st["main_done"] is None
    status, _, body = _req(served, "GET", "/manifest.json", AUTH)
    assert status == 200 and sorted(json.loads(body)["layers"]) == ["L0", "L1"]
    assert _req(served, "GET", "/nope", AUTH)[0] == 404
    assert _req(served, "POST", "/status.json", AUTH, b"")[0] == 405


def test_serve_archive_range_resumes_at_offset(served):
    status, _, full = _req(served, "GET", "/archive/L1.tar.gz", AUTH)
    assert status == 200 and len(full) > 100
    assert full == (served.home / "archives" / "L1.tar.gz").read_bytes()
    size = len(full)
    status, resp, tail = _req(served, "GET", "/archive/L1.tar.gz", {**AUTH, "Range": "bytes=40-"})
    assert status == 206
    assert resp["content-range"] == f"bytes 40-{size - 1}/{size}"
    assert resp["content-length"] == str(size - 40)
    assert tail == full[40:]
    assert _req(served, "GET", "/archive/L1.tar.gz", {**AUTH, "Range": f"bytes={size}-"})[0] == 416


def test_serve_archive_name_traversal_404(served):
    # Stray archives on disk that are not in the manifest (never listed): the manifest is the gate.
    for stray in ("L9.tar.gz", "L2.tar.gz"):
        (served.home / "archives").mkdir(exist_ok=True)
        (served.home / "archives" / stray).write_bytes(b"stray")
    bad = ["/archive/L9.tar.gz", "/archive/L2.tar.gz", "/archive/..%2fL1.tar.gz", "/archive/L1.tar.gz/../x", "/archive/l1.tar.gz", "/archive/L3G.tar.gz",
           "/archive/L9.tar.gz", "/archive/L1.tar.gz.bak", "/archive/../status.json", "/archive/L1.tar.gz%0a"]
    for path in bad:
        status, _, body = _req(served, "GET", path, AUTH)
        assert status == 404, path
        assert b"tree" not in body


def test_serve_archive_serves_lower_case_layer_names(tmp_path):
    home = make_home(tmp_path / "home", layers=("L3g", "L3c"), files_per=2)
    server = make_server(home, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for name in ("L3g.tar.gz", "L3c.tar.gz"):
            status, _, body = _req(server, "GET", "/archive/" + name, AUTH)
            assert status == 200, name
            assert body == (home / "archives" / name).read_bytes()
    finally:
        server.shutdown()
        server.server_close()


def test_serve_put_candidates_atomic_and_413_over_cap(served):
    target = served.home / "raw" / "candidates.jsonl"
    body = b'{"repo": "a/b"}\n' * 10
    assert _req(served, "PUT", "/config/candidates.jsonl", AUTH, body)[0] == 204
    assert target.read_bytes() == body
    assert not (served.home / "raw" / "candidates.jsonl.tmp").exists()
    exact = b"c" * CANDIDATES_CAP
    assert _req(served, "PUT", "/config/candidates.jsonl", AUTH, exact)[0] == 204
    assert target.stat().st_size == CANDIDATES_CAP
    # One byte over the cap: 413 from the headers alone, and the file keeps its previous content.
    conn = http.client.HTTPConnection("127.0.0.1", served.server_address[1], timeout=20)
    conn.putrequest("PUT", "/config/candidates.jsonl")
    conn.putheader("Authorization", f"Bearer {DUMMY_TOKEN}")
    conn.putheader("Content-Length", str(CANDIDATES_CAP + 1))
    conn.endheaders()
    assert conn.getresponse().status == 413
    conn.close()
    assert target.stat().st_size == CANDIDATES_CAP
    assert not (served.home / "raw" / "candidates.jsonl.tmp").exists()


def test_serve_put_github_token_mode_0600(served):
    token_file = served.home / "config" / "github_token"
    assert _req(served, "PUT", "/config/github_token", AUTH, b"g" * 40)[0] == 204
    assert token_file.read_bytes() == b"g" * 40
    assert stat.S_IMODE(os.stat(token_file).st_mode) == 0o600
    assert _req(served, "PUT", "/config/github_token", AUTH, b"g" * 257)[0] == 413
    assert token_file.read_bytes() == b"g" * 40


def test_serve_never_writes_outside_home(tmp_path):
    home = make_home(tmp_path / "home", layers=("L1", "L0"), files_per=3)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    server = make_server(home, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        before_all = _tree(tmp_path)
        before_home = _tree(home)
        paths = ["/archive/..%2fx", "/archive/../../outside/keep.txt", "/config/../../outside/x",
                 "/manifest.json", "/status.json", "/archive/L1.tar.gz"]
        for path in paths:
            _req(server, "GET", path, AUTH)
        _req(server, "PUT", "/config/../../outside/x", AUTH, b"x")
        _req(server, "PUT", "/config/github_token", AUTH, b"g" * 40)
        after_all = _tree(tmp_path)
        after_home = _tree(home)
    finally:
        server.shutdown()
        server.server_close()
    outside_changes = {p for p in set(before_all) | set(after_all)
                       if not p.startswith("home/") and before_all.get(p) != after_all.get(p)}
    assert outside_changes == set()
    changed = {p for p in set(before_home) | set(after_home) if before_home.get(p) != after_home.get(p)}
    assert changed <= {"archives/L1.tar.gz", "archives/L1.meta.json", "archives/L0.tar.gz", "archives/L0.meta.json",
                       "archives/manifest.json", "config/github_token"}, changed
    assert "config/github_token" in changed
