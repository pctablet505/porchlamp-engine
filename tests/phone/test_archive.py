"""phone/archive.py: deterministic archives, marker gating, manifest contents."""

from __future__ import annotations

import pytest

from phone.archive import build_archive, completed_layers, write_manifest
from tests.phone import MARKER_TEXT, make_home


def test_archive_is_deterministic(tmp_path):
    home = make_home(tmp_path / "a", layers=("L1", "L0"))
    first = build_archive(home, "L1")
    (home / "archives" / "L1.tar.gz").unlink()
    (home / "archives" / "L1.meta.json").unlink()
    second = build_archive(home, "L1")
    assert first["sha256"] == second["sha256"]
    assert first["bytes"] == second["bytes"]
    # The same files in a different home (other path, other mtimes) give the same bytes.
    other = make_home(tmp_path / "b", layers=("L1", "L0"))
    for p in (other / "raw").rglob("*.json"):
        p.touch()
    assert build_archive(other, "L1")["sha256"] == first["sha256"]


def test_archive_skips_a_layer_without_its_marker(tmp_path):
    home = make_home(tmp_path, layers=("L1", "L0"))
    # L2 has envelopes on disk but no .complete marker: it must never be archived or listed.
    (home / "raw" / "repo_object").mkdir(parents=True)
    (home / "raw" / "repo_object" / "partial.json").write_text("{}", encoding="utf-8")
    manifest = write_manifest(home)
    assert list(manifest["layers"]) == ["L1", "L0"]
    assert not (home / "archives" / "L2.tar.gz").exists()
    with pytest.raises(FileNotFoundError):
        build_archive(home, "L2")


def test_manifest_lists_only_completed_layers(tmp_path):
    home = make_home(tmp_path, layers=("L4", "L1"), files_per=3)
    assert completed_layers(home) == ["L1", "L4"]  # LAYERS order, not marker-creation order
    manifest = write_manifest(home)
    assert list(manifest["layers"]) == ["L1", "L4"]
    assert manifest["candidates_sha256"] is None
    entry = manifest["layers"]["L4"]
    assert entry["archive"] == "L4.tar.gz" and entry["files"] == 3 and entry["completed_at"] == MARKER_TEXT
    (home / "raw" / "candidates.jsonl").write_text('{"repo": "a/b"}\n', encoding="utf-8")
    import hashlib

    expected = hashlib.sha256((home / "raw" / "candidates.jsonl").read_bytes()).hexdigest()
    assert write_manifest(home)["candidates_sha256"] == expected


def test_manifest_main_done_follows_marker(tmp_path):
    home = make_home(tmp_path, layers=("L1",), files_per=2)
    assert write_manifest(home)["main_done"] is None
    done = home / "state" / "refresh" / "MAIN.done"
    done.write_text("2026-10-11T04:00:00Z\n", encoding="utf-8")
    manifest = write_manifest(home)
    assert manifest["main_done"] == "2026-10-11T04:00:00Z"
    assert (home / "archives" / "manifest.json").is_file()


# ---- concurrent rebuilds ------------------------------------------------------------------------------------------
def test_eight_concurrent_gets_during_a_forced_rebuild_never_fail(tmp_path, monkeypatch):
    import http.client
    import threading
    import time

    from phone import archive
    from phone.auth import sign_request
    from phone.serve import make_server
    from tests.phone import DUMMY_TOKEN

    home = make_home(tmp_path / "home", layers=("L1",), files_per=30)
    builds = []
    real = archive._write_archive

    def slow(out, files):  # widen the race window so the unlocked code cannot slip through by luck
        builds.append(1)
        time.sleep(0.05)
        real(out, files)

    monkeypatch.setattr(archive, "_write_archive", slow)
    server = make_server(home, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    failures: list = []

    def get(target, barrier):
        barrier.wait()
        headers = sign_request(DUMMY_TOKEN, "GET", target, b"")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            conn.request("GET", target, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            if resp.status != 200 or not data:
                failures.append((target, resp.status))
        except Exception as exc:  # the test must see every kind of failure, not stop at the first
            failures.append((target, type(exc).__name__))
        finally:
            conn.close()

    try:
        for _ in range(25):
            (home / "archives" / "L1.tar.gz").unlink(missing_ok=True)  # force a rebuild
            barrier = threading.Barrier(8)
            targets = ["/manifest.json", "/archive/L1.tar.gz"] * 4
            threads = [threading.Thread(target=get, args=(t, barrier)) for t in targets]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
            deadline = time.monotonic() + 10
            while server.active and time.monotonic() < deadline:
                time.sleep(0.01)
    finally:
        server.shutdown()
        server.server_close()
    assert failures == []
    assert len(builds) == 25  # one rebuild per forced round, not one per concurrent request
    assert not list((home / "archives").glob("*.tmp"))


def test_temp_files_are_unique_and_none_are_left_behind(tmp_path):
    home = make_home(tmp_path / "home", layers=("L1", "L0"), files_per=4)
    write_manifest(home)
    leftovers = [p.name for p in (home / "archives").iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    names = set()
    from phone import archive

    for _ in range(5):
        raw, tmp = archive._temp_file(home / "archives", "L1.tar.gz")
        raw.close()
        names.add(tmp.name)
        tmp.unlink()
    assert len(names) == 5 and all(n.startswith("L1.tar.gz.") for n in names)
