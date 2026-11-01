"""Tests for phone.upload: token scope enforcement, part slicing, draft release upload, resume, and idempotency."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phone import httpgate, upload
from phone.httpgate import Gate
from tests.helpers.fakes import FakeClock, FakeHTTP, loopback_gate_options

pytestmark = pytest.mark.usefixtures("clean_env", "utc")

FINE_PAT = "github_pat_11ABCDEF0123456789abcdef_0123456789abcdefABCDEF0123456789"
CLASSIC_PAT = "ghp_0123456789abcdefABCDEF0123456789abcd"
# Not a `github_pat_` fine-grained token and not a classic one with scopes: the shape whose public-repo push flags still count.
OTHER_TOKEN = "ghu_0123456789abcdefABCDEF0123456789abcd"
DROP_REPO = "pctablet505/porchlamp-drop"


@pytest.fixture
def loopback_gate():
    old = httpgate.install(Gate(**loopback_gate_options()))
    yield
    httpgate.install(old)


def _seed_finished_home(home: Path, payload_bytes: int = 2048) -> None:
    (home / "raw" / "pr_window").mkdir(parents=True, exist_ok=True)
    blob = {"items": ["x" * max(1, payload_bytes)]}
    (home / "raw" / "pr_window" / "acme__widget.json").write_text(json.dumps(blob) + "\n", encoding="utf-8")
    (home / "raw" / "candidates.jsonl").write_text(
        '{"owner":"acme","name":"widget","ecosystem":"pypi"}\n', encoding="utf-8"
    )
    (home / "state" / "refresh").mkdir(parents=True, exist_ok=True)
    (home / "state" / "refresh" / "L1.complete").write_text("2026-10-28T02:00:00Z\n", encoding="utf-8")
    (home / "state" / "refresh" / "MAIN.done").write_text("2026-10-28T02:00:00Z\n", encoding="utf-8")


def test_check_upload_token_enforces_minimal_fine_grained_scope(loopback_gate):
    srv = FakeHTTP()
    mode = ["ok"]

    def responder(req):
        if req.path == "/user":
            if mode[0] == "401":
                return 401, {"Content-Type": "application/json"}, b'{"message":"Bad credentials"}'
            scopes = "repo" if mode[0] == "classic_scopes" else ""
            return 200, {"Content-Type": "application/json", "X-OAuth-Scopes": scopes}, b'{"login":"pctablet505"}'
        if req.path.startswith("/user/repos?visibility=private"):
            if mode[0] == "two_private":
                return 200, {"Content-Type": "application/json"}, json.dumps([
                    {"full_name": DROP_REPO, "private": True, "permissions": {"push": True}},
                    {"full_name": "pctablet505/porchlamp-engine", "private": True, "permissions": {"push": False}},
                ]).encode()
            if mode[0] == "read_only":
                return 200, {"Content-Type": "application/json"}, json.dumps([
                    {"full_name": DROP_REPO, "private": True, "permissions": {"push": False, "pull": True}},
                ]).encode()
            if mode[0] == "not_private":
                return 200, {"Content-Type": "application/json"}, json.dumps([
                    {"full_name": DROP_REPO, "private": False, "permissions": {"push": True}},
                ]).encode()
            return 200, {"Content-Type": "application/json"}, json.dumps([
                {"full_name": DROP_REPO, "private": True, "permissions": {"push": True}},
            ]).encode()
        if req.path.startswith("/user/repos?visibility=public"):
            if mode[0] == "public_push_page2":
                if "page=1" in req.path:
                    return (
                        200,
                        {"Content-Type": "application/json", "Link": '<https://api.github.com/user/repos?page=2>; rel="next"'},
                        json.dumps([{"full_name": "pctablet505/safe", "private": False, "permissions": {"push": False}}]).encode(),
                    )
                return 200, {"Content-Type": "application/json"}, json.dumps([
                    {"full_name": "pctablet505/porchlamp", "private": False, "permissions": {"push": True}},
                ]).encode()
            push_pub = mode[0] == "public_push"
            return 200, {"Content-Type": "application/json"}, json.dumps([
                {"full_name": "pctablet505/porchlamp", "private": False, "permissions": {"push": push_pub}},
            ]).encode()
        if req.path == f"/repos/{DROP_REPO}" and req.method == "GET":
            return 200, {"Content-Type": "application/json"}, json.dumps({
                "full_name": DROP_REPO,
                "private": mode[0] != "flipped_public",
                "fork": mode[0] == "forked_repo",
                "archived": mode[0] == "archived_repo",
            }).encode()
        if req.path == f"/repos/{DROP_REPO}/contents/.github/workflows" and req.method == "GET":
            if mode[0] == "has_workflows":
                return 200, {"Content-Type": "application/json"}, b'[{"name":"evil.yml"}]'
            return 404, {"Content-Type": "application/json"}, b'{"message":"Not Found"}'
        return 404, {}, b""

    srv.responder = responder
    try:
        assert upload.check_upload_token("bad", DROP_REPO, base=srv.url)["state"] == upload.REJECTED
        assert upload.check_upload_token(FINE_PAT, "pctablet505/porchlamp", base=srv.url)["state"] == upload.BAD_REPO

        assert upload.check_upload_token(FINE_PAT, DROP_REPO, base=srv.url)["state"] == upload.ACCEPTED

        mode[0] = "401"
        assert upload.check_upload_token(FINE_PAT, DROP_REPO, base=srv.url)["state"] == upload.REJECTED

        # Over-scoped classic PAT or page-2 public push PAT is flagged TOO_POWERFUL even when drop_repo is empty ("").
        mode[0] = "classic_scopes"
        assert upload.check_upload_token(CLASSIC_PAT, "", base=srv.url)["state"] == upload.TOO_POWERFUL
        assert upload.check_upload_token(CLASSIC_PAT, DROP_REPO, base=srv.url)["state"] == upload.TOO_POWERFUL

        mode[0] = "public_push_page2"
        assert upload.check_upload_token(OTHER_TOKEN, "", base=srv.url)["state"] == upload.TOO_POWERFUL
        assert upload.check_upload_token(OTHER_TOKEN, DROP_REPO, base=srv.url)["state"] == upload.TOO_POWERFUL

        mode[0] = "two_private"
        assert upload.check_upload_token(FINE_PAT, "", base=srv.url)["state"] == upload.TOO_POWERFUL
        assert upload.check_upload_token(FINE_PAT, DROP_REPO, base=srv.url)["state"] == upload.TOO_POWERFUL

        mode[0] = "read_only"
        assert upload.check_upload_token(FINE_PAT, DROP_REPO, base=srv.url)["state"] == upload.REJECTED

        mode[0] = "not_private"
        assert upload.check_upload_token(FINE_PAT, DROP_REPO, base=srv.url)["state"] == upload.REJECTED

        mode[0] = "public_push"
        assert upload.check_upload_token(OTHER_TOKEN, DROP_REPO, base=srv.url)["state"] == upload.TOO_POWERFUL

        mode[0] = "has_workflows"
        assert upload.check_upload_token(FINE_PAT, DROP_REPO, base=srv.url)["state"] == upload.TOO_POWERFUL

        mode[0] = "forked_repo"
        assert upload.check_upload_token(FINE_PAT, DROP_REPO, base=srv.url)["state"] == upload.REJECTED
    finally:
        srv.close()


def test_a_fine_grained_token_is_not_refused_for_the_push_flag_on_public_repositories(loopback_gate):
    """D1-D F5: GitHub does not say whether `permissions.push` on a public repo reflects the token or the owner's role, so
    for a `github_pat_` token that flag is not evidence. The private-repository check still gates it."""
    srv = FakeHTTP()
    public_requests: list[str] = []
    mode = ["ok"]

    def responder(req):
        if req.path == "/user":
            return 200, {"Content-Type": "application/json", "X-OAuth-Scopes": ""}, b'{"login":"pctablet505"}'
        if req.path.startswith("/user/repos?visibility=private"):
            rows = [{"full_name": DROP_REPO, "private": True, "permissions": {"push": True}}]
            if mode[0] == "two_private":
                rows.append({"full_name": "pctablet505/porchlamp-engine", "private": True, "permissions": {"push": True}})
            return 200, {"Content-Type": "application/json"}, json.dumps(rows).encode()
        if req.path.startswith("/user/repos?visibility=public"):
            public_requests.append(req.path)
            return 200, {"Content-Type": "application/json"}, json.dumps([
                {"full_name": "pctablet505/porchlamp", "private": False, "permissions": {"push": True}},
            ]).encode()
        if req.path == f"/repos/{DROP_REPO}" and req.method == "GET":
            return 200, {"Content-Type": "application/json"}, json.dumps({
                "full_name": DROP_REPO, "private": True, "fork": False, "archived": False,
            }).encode()
        if req.path == f"/repos/{DROP_REPO}/contents/.github/workflows" and req.method == "GET":
            return 404, {"Content-Type": "application/json"}, b'{"message":"Not Found"}'
        return 404, {}, b""

    srv.responder = responder
    try:
        # A fine-grained token that lists a public repo as pushable is accepted: only the drop repo is private.
        assert upload.check_upload_token(FINE_PAT, DROP_REPO, base=srv.url)["state"] == upload.ACCEPTED
        # Any other token shape keeps the public-repo push refusal.
        assert upload.check_upload_token(OTHER_TOKEN, DROP_REPO, base=srv.url)["state"] == upload.TOO_POWERFUL
        assert public_requests, "the other-shape token must have been audited against the public list"
        # The gate that stays: a second private repository is refused for the fine-grained token too.
        mode[0] = "two_private"
        assert upload.check_upload_token(FINE_PAT, DROP_REPO, base=srv.url)["state"] == upload.TOO_POWERFUL
    finally:
        srv.close()


def test_split_parts_gives_four_parts_of_at_most_512_mib_for_a_real_sized_archive():
    """D1-C: resume is per asset, so a dropped connection costs one part; 512 MiB parts cap that cost (a 1.63 GB archive is 4 parts)."""
    assert upload.MAX_ASSET_BYTES == 512 * 1024 * 1024
    parts = upload.split_parts(1_630_000_000)
    assert len(parts) == 4
    assert all(length <= 536_870_912 for _suffix, _offset, length in parts)
    assert sum(length for _suffix, _offset, length in parts) == 1_630_000_000
    assert [suffix for suffix, _offset, _length in parts] == [".part00", ".part01", ".part02", ".part03"]
    assert upload.split_parts(536_870_912) == [("", 0, 536_870_912)]


def test_split_parts_handles_single_and_multipart_boundaries():
    assert upload.split_parts(100, 100) == [("", 0, 100)]
    assert upload.split_parts(250, 100) == [
        (".part00", 0, 100),
        (".part01", 100, 100),
        (".part02", 200, 50),
    ]
    with pytest.raises(ValueError):
        upload.split_parts(10, 0)


def test_upload_cycle_splits_large_assets_resumes_partial_draft_and_flips_draft_last(tmp_path, loopback_gate):
    home = tmp_path / "home"
    _seed_finished_home(home, payload_bytes=4096)
    clock = FakeClock()
    srv = FakeHTTP()

    releases: list[dict] = []
    stored_bytes: dict[str, bytes] = {}
    deleted_ids: list[int] = []
    upload_order: list[str] = []
    repo_state = {"private": True, "has_workflows": False}
    next_id = [1]

    def responder(req):
        path = req.path
        if path == f"/repos/{DROP_REPO}" and req.method == "GET":
            return 200, {"Content-Type": "application/json"}, json.dumps({
                "full_name": DROP_REPO,
                "private": repo_state["private"],
                "fork": False,
                "archived": False,
            }).encode()
        if path == f"/repos/{DROP_REPO}/contents/.github/workflows" and req.method == "GET":
            if repo_state["has_workflows"]:
                return 200, {"Content-Type": "application/json"}, b'[{"name":"ci.yml"}]'
            return 404, {"Content-Type": "application/json"}, b'{"message":"Not Found"}'
        if path.startswith(f"/repos/{DROP_REPO}/releases?"):
            return 200, {"Content-Type": "application/json"}, json.dumps(releases).encode()
        if path == f"/repos/{DROP_REPO}/releases" and req.method == "POST":
            body = json.loads(req.body.decode())
            rid = next_id[0]
            next_id[0] += 1
            rel = {"id": rid, "tag_name": body["tag_name"], "draft": True, "assets": []}
            releases.append(rel)
            return 201, {"Content-Type": "application/json"}, json.dumps(rel).encode()
        if path.startswith(f"/repos/{DROP_REPO}/releases/assets/") and req.method == "DELETE":
            aid = int(path.rsplit("/", 1)[1])
            deleted_ids.append(aid)
            for rel in releases:
                rel["assets"] = [a for a in rel["assets"] if a["id"] != aid]
            return 204, {}, b""
        if "/assets?name=" in path and req.method == "POST":
            name = path.split("name=", 1)[1]
            data = req.body
            aid = next_id[0]
            next_id[0] += 1
            stored_bytes[name] = data
            upload_order.append(name)
            asset = {"id": aid, "name": name, "size": len(data), "state": "uploaded"}
            releases[0]["assets"].append(asset)
            return 201, {"Content-Type": "application/json"}, json.dumps(asset).encode()
        if path.startswith(f"/repos/{DROP_REPO}/releases/") and req.method == "PATCH":
            # Every asset including manifest.json must already be uploaded BEFORE draft flips to False.
            assert upload_order[-1] == "manifest.json"
            body = json.loads(req.body.decode())
            releases[0]["draft"] = bool(body["draft"])
            return 200, {"Content-Type": "application/json"}, json.dumps(releases[0]).encode()
        return 404, {}, b""

    srv.responder = responder
    try:
        # Pre-flight abort if drop-box repo was accidentally flipped public or gained a workflow file!
        repo_state["private"] = False
        with pytest.raises(upload.UploadError, match="must be private"):
            upload.upload_cycle(home, FINE_PAT, DROP_REPO, selected_layers=["L1"], api_base=srv.url, upload_base=srv.url)
        assert not releases

        repo_state["private"] = True
        repo_state["has_workflows"] = True
        with pytest.raises(upload.UploadError, match=r"\.github/workflows"):
            upload.upload_cycle(home, FINE_PAT, DROP_REPO, selected_layers=["L1"], api_base=srv.url, upload_base=srv.url)
        assert not releases
        repo_state["has_workflows"] = False

        # Pre-seed archive so we know the deterministic tag, then pre-create a draft with a corrupt partial .part01.
        res1 = upload.upload_cycle(
            home,
            FINE_PAT,
            DROP_REPO,
            selected_layers=["L1"],
            api_base=srv.url,
            upload_base=srv.url,
            clock=clock,
            max_asset_bytes=120,
        )
        assert res1["ok"] is True and res1["skipped"] is False
        assert releases[0]["draft"] is False
        assert upload_order[-1] == "manifest.json"
        drop_manifest = json.loads(stored_bytes["manifest.json"].decode("utf-8"))
        parts = drop_manifest["layers"]["L1"]["parts"]
        assert len(parts) >= 2
        reassembled = b"".join(stored_bytes[p["name"]] for p in parts)
        assert reassembled == (home / "archives" / "L1.tar.gz").read_bytes()

        # Re-running with state/uploaded.json present skips immediately with 0 bytes sent.
        res2 = upload.upload_cycle(
            home,
            FINE_PAT,
            DROP_REPO,
            selected_layers=["L1"],
            api_base=srv.url,
            upload_base=srv.url,
            clock=clock,
            max_asset_bytes=120,
        )
        assert res2["ok"] is True and res2["skipped"] is True and res2["bytes_uploaded"] == 0

        # Simulate interrupted draft resume: remove local uploaded.json, set draft=True, corrupt .part01 size.
        (home / "state" / "uploaded.json").unlink()
        releases[0]["draft"] = True
        part0_name = parts[0]["name"]
        part1_asset = next(a for a in releases[0]["assets"] if a["name"] == parts[1]["name"])
        part1_asset["size"] = 3  # truncated partial upload
        upload_order.clear()

        res3 = upload.upload_cycle(
            home,
            FINE_PAT,
            DROP_REPO,
            selected_layers=["L1"],
            api_base=srv.url,
            upload_base=srv.url,
            clock=clock,
            max_asset_bytes=120,
        )
        assert res3["ok"] is True and res3["skipped"] is False
        assert part0_name not in upload_order  # intact .part00 was reused without re-uploading!
        assert parts[1]["name"] in upload_order and part1_asset["id"] in deleted_ids
        assert releases[0]["draft"] is False
    finally:
        srv.close()
