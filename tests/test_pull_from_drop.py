"""Tests for scripts/pull_from_drop.py: round-trip from upload_cycle, multi-part reassembly, redirects, and integrity."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
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

    def blob_responder(req):
        blob_auth_headers.append(req.headers.get("authorization"))
        aid = int(req.path.rsplit("/", 1)[1])
        data = blobs[aid]
        return 200, {"Content-Type": "application/octet-stream"}, data

    def api_responder(req):
        path = req.path
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
            # Redirect to separate blob server so we prove Authorization is stripped on redirect!
            return 302, {"Location": f"{blob_srv.url}/blob/{aid}"}, b""
        if path.startswith(f"/repos/{DROP_REPO}/releases/") and req.method == "PATCH":
            rid = int(path.rsplit("/", 1)[1])
            body = json.loads(req.body.decode())
            rel = next(r for r in releases if r["id"] == rid)
            rel["draft"] = bool(body["draft"])
            return 200, {"Content-Type": "application/json"}, json.dumps(rel).encode()
        return 404, {}, b""

    srv.responder = api_responder
    blob_srv.responder = blob_responder
    return srv, blob_srv, releases, blobs, blob_auth_headers


def test_round_trip_multipart_upload_and_pull_strips_auth_on_redirect_and_tracks_state(tmp_path):
    old_gate = httpgate.install(Gate(**loopback_gate_options()))
    srv, blob_srv, _releases, _blobs, blob_auth_headers = _make_drop_server()
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
        assert pulled["has_new"] is True and pulled["as_of"] == "2026-10-28T03:00:00Z"
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
    finally:
        httpgate.install(old_gate)
        srv.close()
        blob_srv.close()


def test_pull_from_drop_refuses_tampered_sha256_and_unsafe_tar_members(tmp_path):
    srv, blob_srv, releases, blobs, _ = _make_drop_server()
    try:
        # Craft a release containing a path-traversal member ../../evil.json with matching manifest sha256.
        buf = io.BytesIO()
        with (
            gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz,
            tarfile.open(mode="w", fileobj=gz) as tar,
        ):
            payload = b'{"bad": 1}'
            info = tarfile.TarInfo("raw/pr_window/../../evil.json")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        evil_arc = buf.getvalue()
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
            "tag_name": "cycle-2026-10-28-evil00000000",
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
