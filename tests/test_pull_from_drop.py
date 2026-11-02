"""Tests for scripts/pull_from_drop.py: round-trip from upload_cycle, multi-part reassembly, redirects, and integrity."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import tarfile
from pathlib import Path

from phone import httpgate, upload
from phone.httpgate import Gate
from tests.helpers.fakes import FakeClock, FakeHTTP, loopback_gate_options

ROOT = Path(__file__).resolve().parent.parent
DROP_REPO = "pctablet505/porchlamp-drop"
FINE_PAT = "github_pat_11ABCDEF0123456789abcdef_0123456789abcdefABCDEF0123456789"
READ_PAT = "github_pat_11READER0123456789abcdef_0123456789abcdefABCDEF0123456789"


def _load_pull_from_drop():
    spec = importlib.util.spec_from_file_location("pull_from_drop", ROOT / "scripts" / "pull_from_drop.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pull_mod = _load_pull_from_drop()


def _make_drop_server():
    srv = FakeHTTP()
    blob_srv = FakeHTTP()
    releases: list[dict] = []
    blobs: dict[int, bytes] = {}
    blob_auth_headers: list[str | None] = []
    next_id = [10]

    repo_flags = {"private": True, "has_workflows": False, "redirect_url": None}

    def blob_responder(req):
        blob_auth_headers.append(req.headers.get("authorization"))
        aid = int(req.path.rsplit("/", 1)[1])
        data = blobs[aid]
        return 200, {"Content-Type": "application/octet-stream"}, data

    def api_responder(req):
        path = req.path
        if path == f"/repos/{DROP_REPO}" and req.method == "GET":
            return 200, {"Content-Type": "application/json"}, json.dumps({
                "full_name": DROP_REPO,
                "private": repo_flags["private"],
                "fork": False,
                "archived": False,
            }).encode()
        if path == f"/repos/{DROP_REPO}/contents/.github/workflows" and req.method == "GET":
            if repo_flags["has_workflows"]:
                return 200, {"Content-Type": "application/json"}, b'[{"name":"ci.yml"}]'
            return 404, {"Content-Type": "application/json"}, b'{"message":"Not Found"}'
        if path.startswith(f"/repos/{DROP_REPO}/releases?"):
            return 200, {"Content-Type": "application/json"}, json.dumps(list(reversed(releases))).encode()
        if path.startswith(f"/repos/{DROP_REPO}/releases/tags/"):
            tag = path.rsplit("/", 1)[1]
            rel = next((r for r in releases if r["tag_name"] == tag), None)
            if rel is None:
                return 404, {"Content-Type": "application/json"}, b'{"message":"not found"}'
            return 200, {"Content-Type": "application/json"}, json.dumps(rel).encode()
        if path == f"/repos/{DROP_REPO}/releases" and req.method == "POST":
            body = json.loads(req.body.decode())
            rid = next_id[0]
            next_id[0] += 1
            rel = {
                "id": rid,
                "tag_name": body["tag_name"],
                "draft": bool(body.get("draft", True)),
                "prerelease": False,
                "assets": [],
            }
            releases.append(rel)
            return 201, {"Content-Type": "application/json"}, json.dumps(rel).encode()
        if "/assets?name=" in path and req.method == "POST":
            rid = int(path.split("/releases/", 1)[1].split("/assets", 1)[0])
            name = path.split("name=", 1)[1]
            data = req.body
            aid = next_id[0]
            next_id[0] += 1
            blobs[aid] = data
            rel = next(r for r in releases if r["id"] == rid)
            asset = {"id": aid, "name": name, "size": len(data), "state": "uploaded"}
            rel["assets"].append(asset)
            return 201, {"Content-Type": "application/json"}, json.dumps(asset).encode()
        if path.startswith(f"/repos/{DROP_REPO}/releases/assets/") and req.method == "DELETE":
            aid = int(path.rsplit("/", 1)[1])
            blobs.pop(aid, None)
            for rel in releases:
                rel["assets"] = [a for a in rel["assets"] if a["id"] != aid]
            return 204, {}, b""
        if path.startswith(f"/repos/{DROP_REPO}/releases/assets/") and req.method == "GET":
            aid = int(path.rsplit("/", 1)[1])
            target_url = repo_flags["redirect_url"] or f"{blob_srv.url}/blob/{aid}"
            return 302, {"Location": target_url}, b""
        if path.startswith(f"/repos/{DROP_REPO}/releases/") and req.method == "PATCH":
            rid = int(path.rsplit("/", 1)[1])
            body = json.loads(req.body.decode())
            rel = next(r for r in releases if r["id"] == rid)
            rel["draft"] = bool(body["draft"])
            return 200, {"Content-Type": "application/json"}, json.dumps(rel).encode()
        return 404, {}, b""

    srv.responder = api_responder
    blob_srv.responder = blob_responder
    return srv, blob_srv, releases, blobs, blob_auth_headers, repo_flags


def test_round_trip_multipart_upload_and_pull_strips_auth_on_redirect_and_tracks_state(tmp_path):
    old_gate = httpgate.install(Gate(**loopback_gate_options()))
    srv, blob_srv, _releases, _blobs, blob_auth_headers, _flags = _make_drop_server()
    try:
        phone_home = tmp_path / "phone"
        (phone_home / "raw" / "pr_window").mkdir(parents=True)
        env_text = json.dumps({"repo": "acme/widget", "items": ["p" * 2048]}) + "\n"
        (phone_home / "raw" / "pr_window" / "acme__widget.json").write_text(env_text, encoding="utf-8")
        cand_text = '{"owner":"acme","name":"widget","ecosystem":"pypi"}\n'
        (phone_home / "raw" / "candidates.jsonl").write_text(cand_text, encoding="utf-8")
        (phone_home / "state" / "refresh").mkdir(parents=True)
        (phone_home / "state" / "refresh" / "L1.complete").write_text("2026-10-28T03:00:00Z\n", encoding="utf-8")
        (phone_home / "state" / "refresh" / "MAIN.done").write_text("2026-10-28T03:00:00Z\n", encoding="utf-8")

        up_res = upload.upload_cycle(
            phone_home,
            FINE_PAT,
            DROP_REPO,
            selected_layers=["L1"],
            api_base=srv.url,
            upload_base=srv.url,
            clock=FakeClock(),
            max_asset_bytes=110,  # force multi-part .part00, .part01, ...
        )
        assert up_res["ok"] is True

        dest = tmp_path / "workstation"
        token_file = tmp_path / "read.token"
        token_file.write_text(READ_PAT + "\n", encoding="utf-8")
        status_out = tmp_path / "status.json"

        # 1. --check-only sees the new release without extracting anything.
        rc = pull_mod.main([
            "--repo", DROP_REPO,
            "--dest", str(dest),
            "--token-file", str(token_file),
            "--api-base", srv.url,
            "--check-only",
            "--status-out", str(status_out),
        ])
        assert rc == 0
        chk = json.loads(status_out.read_text(encoding="utf-8"))
        assert chk["has_new"] is True and chk["tag"] == up_res["tag"]
        assert not (dest / "raw").exists()

        # 2. Full pull reassembles .partNN slices, verifies SHA-256, extracts raw envelopes, and strips Auth on 302.
        rc = pull_mod.main([
            "--repo", DROP_REPO,
            "--dest", str(dest),
            "--token-file", str(token_file),
            "--api-base", srv.url,
            "--status-out", str(status_out),
        ])
        assert rc == 0
        pulled = json.loads(status_out.read_text(encoding="utf-8"))
        assert pulled["has_new"] is True and pulled["main_done"] == "2026-10-28T03:00:00Z"
        # D2-A: the puller reports MAIN.done as main_done only; an `as_of` key would invite the build to take it as the as_of.
        assert "as_of" not in pulled
        cached = json.loads((dest / "state" / "pulled" / "release.json").read_text(encoding="utf-8"))
        assert "as_of" not in cached and cached["main_done"] == "2026-10-28T03:00:00Z"
        assert (dest / "raw" / "pr_window" / "acme__widget.json").read_text(encoding="utf-8") == env_text
        assert (dest / "raw" / "candidates.jsonl").read_text(encoding="utf-8") == cand_text
        assert (dest / "state" / "refresh" / "L1.complete").read_text(encoding="utf-8").strip() == "2026-10-28T03:00:00Z"
        assert not (dest / "pull-tmp").exists()
        assert blob_auth_headers and all(h is None for h in blob_auth_headers)

        # 3. Subsequent --check-only sees already_pulled -> has_new == False.
        rc = pull_mod.main([
            "--repo", DROP_REPO,
            "--dest", str(dest),
            "--token-file", str(token_file),
            "--api-base", srv.url,
            "--check-only",
            "--status-out", str(status_out),
        ])
        assert rc == 0
        chk2 = json.loads(status_out.read_text(encoding="utf-8"))
        assert chk2["has_new"] is False and chk2["reason"] == "already_pulled"
        assert "as_of" not in chk2 and chk2["main_done"] == "2026-10-28T03:00:00Z"
    finally:
        httpgate.install(old_gate)
        srv.close()
        blob_srv.close()


def _pack_tar_gz(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with (
        gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz,
        tarfile.open(mode="w", fileobj=gz) as tar,
    ):
        for member_name, payload in entries:
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def test_pull_from_drop_refuses_tampered_sha256_and_unsafe_tar_members(tmp_path):
    srv, blob_srv, releases, blobs, _, _ = _make_drop_server()
    try:
        evil_arc = _pack_tar_gz([("raw/pr_window/../../evil.json", b'{"bad": 1}')])
        evil_sha = hashlib.sha256(evil_arc).hexdigest()
        manifest = {
            "generated_at": "2026-10-28T04:00:00Z",
            "main_done": "2026-10-28T04:00:00Z",
            "layers": {
                "L1": {
                    "archive": "L1.tar.gz",
                    "bytes": len(evil_arc),
                    "sha256": evil_sha,
                    "completed_at": "2026-10-28T04:00:00Z",
                }
            },
            "candidates_sha256": None,
        }
        manifest_bytes = json.dumps(manifest).encode("utf-8")
        blobs[1] = evil_arc
        blobs[2] = manifest_bytes
        releases.append({
            "id": 99,
            "tag_name": "cycle-2026-10-28-bad000000000",
            "draft": False,
            "prerelease": False,
            "assets": [
                {"id": 1, "name": "L1.tar.gz", "size": len(evil_arc), "state": "uploaded"},
                {"id": 2, "name": "manifest.json", "size": len(manifest_bytes), "state": "uploaded"},
            ],
        })

        dest = tmp_path / "dest"
        token_file = tmp_path / "tok"
        token_file.write_text(READ_PAT + "\n", encoding="utf-8")

        # 1. Unsafe tar member exits with code 4 and extracts nothing.
        rc = pull_mod.main([
            "--repo", DROP_REPO,
            "--dest", str(dest),
            "--token-file", str(token_file),
            "--api-base", srv.url,
        ])
        assert rc == 4
        assert not (tmp_path / "evil.json").exists() and not (dest / "raw").exists()

        # 2. Tampered archive bytes exit with code 3 before tar inspection.
        blobs[1] = evil_arc[:-1] + bytes([evil_arc[-1] ^ 0xFF])
        rc = pull_mod.main([
            "--repo", DROP_REPO,
            "--dest", str(dest),
            "--token-file", str(token_file),
            "--api-base", srv.url,
        ])
        assert rc == 3
    finally:
        srv.close()
        blob_srv.close()


def test_pull_from_drop_adversarial_gzip_bomb_ssrf_injection_and_repo_safety(tmp_path):
    srv, blob_srv, releases, blobs, _, flags = _make_drop_server()
    try:
        token_file = tmp_path / "tok"
        token_file.write_text(READ_PAT + "\n", encoding="utf-8")
        dest = tmp_path / "dest"

        # 1. Refuse untrusted --api-base before sending token anywhere.
        for bad_api in ("http://evil.invalid", "https://evil.invalid", "https://user@api.github.com"):
            assert pull_mod.main(["--repo", DROP_REPO, "--dest", str(dest), "--token-file", str(token_file), "--api-base", bad_api]) == 1

        # 2. Refuse malformed / newline-injected --tag.
        for bad_tag in ("cycle-2026-10-28-0123456789ab\npwn=1", "cycle---help", "cycle-2026-10-28-NOTHEX000000"):
            assert pull_mod.main([
                "--repo", DROP_REPO, "--dest", str(dest), "--token-file", str(token_file),
                "--api-base", srv.url, "--tag", bad_tag,
            ]) == 1

        # 3. Craft a valid-SHA256 >2 MiB highly-compressible JSON gzip bomb (>200x ratio) -> blocked with exit code 4!
        bomb_payload = b'{"z":"' + (b"A" * (2 * 1024 * 1024)) + b'"}'
        bomb_arc = _pack_tar_gz([("raw/pr_window/acme__bomb.json", bomb_payload)])
        assert len(bomb_payload) > len(bomb_arc) * pull_mod.MAX_COMPRESSION_RATIO
        bomb_sha = hashlib.sha256(bomb_arc).hexdigest()
        manifest = {
            "generated_at": "2026-10-28T04:00:00Z",
            "main_done": "2026-10-28T04:00:00Z",
            "layers": {
                "L1": {
                    "archive": "L1.tar.gz",
                    "bytes": len(bomb_arc),
                    "sha256": bomb_sha,
                    "completed_at": "2026-10-28T04:00:00Z",
                }
            },
            "candidates_sha256": None,
        }
        mbytes = json.dumps(manifest).encode("utf-8")
        blobs[1] = bomb_arc
        blobs[2] = mbytes
        releases.append({
            "id": 100,
            "tag_name": "cycle-2026-10-28-b00b00000001",
            "draft": False,
            "prerelease": False,
            "assets": [
                {"id": 1, "name": "L1.tar.gz", "size": len(bomb_arc), "state": "uploaded"},
                {"id": 2, "name": "manifest.json", "size": len(mbytes), "state": "uploaded"},
            ],
        })
        assert pull_mod.main(["--repo", DROP_REPO, "--dest", str(dest), "--token-file", str(token_file), "--api-base", srv.url]) == 4
        assert not (dest / "raw").exists()

        # 4. Hidden dotfile or non-dict JSON envelope inside tar -> blocked with exit code 4!
        for bad_member, bad_json in [
            ("raw/pr_window/.hidden.json", b'{"ok": 1}'),
            ("raw/pr_window/acme__arr.json", b"[1, 2, 3]"),
            ("raw/pr_window/acme__corrupt.json", b"{not json"),
        ]:
            arc = _pack_tar_gz([(bad_member, bad_json)])
            manifest["layers"]["L1"]["bytes"] = len(arc)
            manifest["layers"]["L1"]["sha256"] = hashlib.sha256(arc).hexdigest()
            mb = json.dumps(manifest).encode("utf-8")
            blobs[1] = arc
            blobs[2] = mb
            releases[0]["assets"][0]["size"] = len(arc)
            releases[0]["assets"][1]["size"] = len(mb)
            assert pull_mod.main(["--repo", DROP_REPO, "--dest", str(dest), "--token-file", str(token_file), "--api-base", srv.url]) == 4
            assert not (dest / "raw").exists()

        # 5. Newline injection in manifest main_done -> blocked with exit code 1!
        good_arc = _pack_tar_gz([("raw/pr_window/acme__ok.json", b'{"ok": true}')])
        bad_manifest = {
            **manifest,
            "main_done": "2026-10-28T04:00:00Z\nGITHUB_OUTPUT_PWN=1",
            "layers": {
                "L1": {
                    "archive": "L1.tar.gz",
                    "bytes": len(good_arc),
                    "sha256": hashlib.sha256(good_arc).hexdigest(),
                    "completed_at": "2026-10-28T04:00:00Z",
                }
            },
        }
        mb = json.dumps(bad_manifest).encode("utf-8")
        blobs[1] = good_arc
        blobs[2] = mb
        releases[0]["assets"][0]["size"] = len(good_arc)
        releases[0]["assets"][1]["size"] = len(mb)
        assert pull_mod.main(["--repo", DROP_REPO, "--dest", str(dest), "--token-file", str(token_file), "--api-base", srv.url]) == 1

        # 6. Asset redirect to untrusted external HTTPS host -> blocked with exit code 1!
        good_manifest = {**bad_manifest, "main_done": "2026-10-28T04:00:00Z"}
        mb_ok = json.dumps(good_manifest).encode("utf-8")
        blobs[2] = mb_ok
        releases[0]["assets"][1]["size"] = len(mb_ok)
        flags["redirect_url"] = "https://attacker.invalid/steal-asset"
        assert pull_mod.main(["--repo", DROP_REPO, "--dest", str(dest), "--token-file", str(token_file), "--api-base", srv.url]) == 1
        flags["redirect_url"] = None

        # 7. Public drop-box or workflow-containing drop-box -> blocked with exit code 1 before reading releases!
        flags["private"] = False
        assert pull_mod.main(["--repo", DROP_REPO, "--dest", str(dest), "--token-file", str(token_file), "--api-base", srv.url]) == 1
        flags["private"] = True
        flags["has_workflows"] = True
        assert pull_mod.main(["--repo", DROP_REPO, "--dest", str(dest), "--token-file", str(token_file), "--api-base", srv.url]) == 1
    finally:
        srv.close()
        blob_srv.close()


# The largest real envelope and layer in the 2026-10-27 phone data: raw/pr_window/DIYgod__RSSHub.json and the whole
# pr_window layer. The caps exist to stop a bomb, never to refuse a real cycle.
REAL_MAX_MEMBER_BYTES = 135_616_732
REAL_L1_UNCOMPRESSED_BYTES = 10_924_894_684
REAL_L1_COMPRESSED_BYTES = 1_900_000_000


def _pack_random_tar_gz(path: Path, member: str, size: int) -> None:
    block = os.urandom(1 << 20)
    with tarfile.open(path, "w:gz", compresslevel=1) as tar:
        info = tarfile.TarInfo(member)
        info.size = size
        tar.addfile(info, _RepeatedBlock(block, size))


class _RepeatedBlock:
    def __init__(self, block: bytes, size: int) -> None:
        self._block, self._left = block, size

    def read(self, n: int = -1) -> bytes:
        n = self._left if n is None or n < 0 else min(n, self._left)
        out = (self._block * (n // len(self._block) + 1))[:n]
        self._left -= len(out)
        return out


def test_a_real_sized_envelope_and_layer_are_inside_the_caps(tmp_path):
    arc = tmp_path / "L1.tar.gz"
    _pack_random_tar_gz(arc, "raw/pr_window/acme__big.json", 70 * 1024 * 1024)  # over the old 64 MiB cap
    members = pull_mod._check_members(arc, "L1")
    assert [m.name for m in members] == ["raw/pr_window/acme__big.json"]

    assert pull_mod.MAX_MEMBER_BYTES >= REAL_MAX_MEMBER_BYTES * 2
    assert pull_mod._max_uncompressed_for_archive(REAL_L1_COMPRESSED_BYTES) >= REAL_L1_UNCOMPRESSED_BYTES * 3 // 2


def _release_with_candidates(releases: list, blobs: dict, cand_bytes: bytes) -> None:
    """One finished release holding only a candidates.jsonl (no layers), the way a hostile drop could carry one."""
    manifest = {
        "generated_at": "2026-10-28T04:00:00Z",
        "main_done": "2026-10-28T04:00:00Z",
        "layers": {},
        "candidates_sha256": hashlib.sha256(cand_bytes).hexdigest(),
        "candidates_bytes": len(cand_bytes),
    }
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    blobs[1], blobs[2] = cand_bytes, manifest_bytes
    releases[:] = [{
        "id": 101,
        "tag_name": "cycle-2026-10-28-ca4d1d000001",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"id": 1, "name": "candidates.jsonl", "size": len(cand_bytes), "state": "uploaded"},
            {"id": 2, "name": "manifest.json", "size": len(manifest_bytes), "state": "uploaded"},
        ],
    }]


def test_a_candidate_row_that_is_not_a_path_segment_or_has_a_bad_ecosystem_exits_4_and_writes_nothing(tmp_path):
    """D1 F1 on the drop door (and RB3 U1): owner and name become file names in derive, the ecosystem becomes one in
    gather. A row that could climb out of the data root must be refused before `raw/candidates.jsonl` exists."""
    srv, blob_srv, releases, blobs, _, _ = _make_drop_server()
    try:
        token_file = tmp_path / "tok"
        token_file.write_text(READ_PAT + "\n", encoding="utf-8")
        hostile = [
            {"owner": "../x", "name": "y", "ecosystem": "npm"},
            {"owner": "ok", "name": "../../../e2", "ecosystem": "npm"},
            {"owner": "a/b", "name": "y", "ecosystem": "npm"},
            {"owner": "a\\b", "name": "y", "ecosystem": "npm"},
            {"owner": "ok", "name": "..", "ecosystem": "npm"},
            {"owner": ".", "name": "y", "ecosystem": "npm"},
            {"owner": "ok", "name": "y\x00z", "ecosystem": "npm"},
            {"owner": "ok", "name": "y\nz", "ecosystem": "npm"},
            {"owner": "a", "name": "b", "ecosystem": "../../e"},
            {"owner": "a", "name": "b", "ecosystem": "/abs"},
            {"owner": "a", "name": "b", "ecosystem": ""},
            {"owner": "a", "name": "b", "ecosystem": "n p m"},
            {"owner": "a", "name": "b", "ecosystem": 7},
            {"owner": "a", "name": "b"},
        ]
        for index, row in enumerate(hostile):
            good = json.dumps({"owner": "acme", "name": "widget", "ecosystem": "pypi"})
            _release_with_candidates(releases, blobs, (good + "\n" + json.dumps(row) + "\n").encode("utf-8"))
            dest = tmp_path / f"dest-hostile-{index}"
            rc = pull_mod.main([
                "--repo", DROP_REPO, "--dest", str(dest), "--token-file", str(token_file), "--api-base", srv.url,
            ])
            assert rc == 4, row
            assert not (dest / "raw" / "candidates.jsonl").exists(), row
            assert not (dest / "raw").exists(), row

        # The control: the real candidate `981011512/--` (valid_name refuses it, a path segment accepts it) and every
        # real ecosystem pass, so the refusals above come from the rows and not from the harness.
        real = [
            {"owner": "981011512", "name": "--", "ecosystem": "npm"},
            *({"owner": "acme", "name": f"w-{eco}", "ecosystem": eco} for eco in ("crates", "go", "maven", "npm", "pypi", "systems")),
        ]
        text = "".join(json.dumps(row) + "\n" for row in real)
        _release_with_candidates(releases, blobs, text.encode("utf-8"))
        dest = tmp_path / "dest-real"
        rc = pull_mod.main([
            "--repo", DROP_REPO, "--dest", str(dest), "--token-file", str(token_file), "--api-base", srv.url,
        ])
        assert rc == 0
        assert (dest / "raw" / "candidates.jsonl").read_text(encoding="utf-8") == text
    finally:
        srv.close()
        blob_srv.close()


def _release_with_l4(releases: list, blobs: dict, envelope: bytes) -> None:
    arc = _pack_tar_gz([("raw/author_history/acme__one.json", envelope)])
    manifest = json.dumps({
        "generated_at": "2026-10-28T04:00:00Z",
        "main_done": "2026-10-28T04:00:00Z",
        "layers": {"L4": {"archive": "L4.tar.gz", "bytes": len(arc), "sha256": hashlib.sha256(arc).hexdigest(),
                          "completed_at": "2026-10-28T04:00:00Z"}},
        "candidates_sha256": None,
    }).encode("utf-8")
    blobs[1], blobs[2] = arc, manifest
    releases.append({
        "id": 77, "tag_name": "cycle-2026-10-28-a40000000000", "draft": False, "prerelease": False,
        "assets": [{"id": 1, "name": "L4.tar.gz", "size": len(arc), "state": "uploaded"},
                   {"id": 2, "name": "manifest.json", "size": len(manifest), "state": "uploaded"}],
    })


def test_a_pull_of_l4_fills_the_user_cache_and_a_cache_failure_does_not_fail_the_pull(tmp_path, capsys):
    srv, blob_srv, releases, blobs, _, _ = _make_drop_server()
    try:
        _release_with_l4(releases, blobs, json.dumps({"data": {"author_records": [
            {"login": "alice", "account_created_at": "2018-01-01T00:00:00Z", "public_event_count": 12,
             "eligibility_unmeasured": False, "eligibility_observed_at": "2026-10-01T00:00:00+00:00"},
            5,
        ]}}).encode("utf-8"))
        token_file = tmp_path / "tok"
        token_file.write_text(READ_PAT + "\n", encoding="utf-8")
        args = ["--repo", DROP_REPO, "--token-file", str(token_file), "--api-base", srv.url]

        dest = tmp_path / "dest"
        assert pull_mod.main([*args, "--dest", str(dest)]) == 0
        conn = sqlite3.connect(f"file:{dest / 'state' / 'users.sqlite'}?mode=ro&immutable=1", uri=True)
        assert conn.execute("SELECT login, public_event_count FROM users").fetchall() == [("alice", 12)]
        conn.close()
        assert "user_cache: 1 logins from L4" in capsys.readouterr().out

        broken = tmp_path / "broken"
        (broken / "state" / "users.sqlite").mkdir(parents=True)  # sqlite3 cannot open a folder
        (broken / "raw" / "author_history").mkdir(parents=True)  # something to set aside
        assert pull_mod.main([*args, "--dest", str(broken)]) == 0
        assert "user_cache: not updated" in capsys.readouterr().out
        assert (broken / "state" / "pulled" / "L4.sha256").is_file()
        assert (broken / "raw" / "author_history" / "acme__one.json").is_file()
        assert pull_mod.main([*args, "--dest", str(broken), "--layers", "L4"]) == 0  # not exit 5
        assert "L4: unchanged" in capsys.readouterr().out
    finally:
        srv.close()
        blob_srv.close()
