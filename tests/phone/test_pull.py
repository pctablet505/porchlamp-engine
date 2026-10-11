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
from datetime import datetime, timezone
from pathlib import Path

import pytest

from phone.archive import LAYERS, write_manifest
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
