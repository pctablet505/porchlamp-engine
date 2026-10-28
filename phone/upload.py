"""Upload a finished phone cycle to a dedicated zero-code private GitHub drop-box repository. Standard library only.

``check_upload_token(token, drop_repo)`` proves the upload token is minimally scoped before the app saves or uses it:

1. ``GET /user``: 401 is ``rejected``; any ``X-OAuth-Scopes`` value means a classic PAT with broad scopes
   (``too_powerful``).
2. ``GET /user/repos?visibility=private&per_page=10``: the token must see *only* ``drop_repo``, ``drop_repo`` must be
   ``private: true``, and ``permissions.push`` must be ``true``. Seeing any second private repository is
   ``too_powerful``.
3. ``GET /user/repos?visibility=public&affiliation=owner,collaborator,organization_member&per_page=100``: any public
   repository with ``permissions.push == true`` is ``too_powerful``.

``upload_cycle(home, token, drop_repo)`` builds ``archives/manifest.json`` and deterministic ``.tar.gz`` archives,
creates or resumes a draft GitHub Release ``cycle-<date>-<sha12>`` on ``drop_repo``, streams ``candidates.jsonl`` and
every layer archive (splitting into ``.partNN`` slices when an archive exceeds ``MAX_ASSET_BYTES``), uploads
``manifest.json`` last, flips ``draft: false`` atomically, and records ``state/uploaded.json`` so an already-uploaded
cycle is not re-sent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from phone import archive, httpgate, tokencheck
from phone.control import RESERVED_REPO_NAMES, valid_drop_repo

__all__ = [
    "ACCEPTED",
    "BAD_REPO",
    "MAX_ASSET_BYTES",
    "REJECTED",
    "RESERVED_REPO_NAMES",
    "TOO_POWERFUL",
    "UNREACHABLE",
    "UploadError",
    "check_upload_token",
    "read_uploaded_state",
    "split_parts",
    "upload_cycle",
    "valid_drop_repo",
    "verify_drop_repo_safe",
]

API_BASE = "https://api.github.com"
UPLOAD_BASE = "https://uploads.github.com"
USER_AGENT = "porchlamp-phone/1"
API_VERSION = "2022-11-28"
TIMEOUT_S = 20.0
ASSET_TIMEOUT_S = 300.0
CHUNK_SIZE = 1 << 20
MAX_ASSET_BYTES = 1_800_000_000

ACCEPTED = "accepted"
REJECTED = "rejected"
TOO_POWERFUL = "too_powerful"
BAD_REPO = "bad_repo"
UNREACHABLE = "unreachable"


class UploadError(Exception):
    """A recoverable or configuration error during ``upload_cycle``."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Drop-box API and asset upload requests never follow redirects with an Authorization header."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _api_headers(token: str, content_type: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }
    if content_type is not None:
        headers["Content-Type"] = content_type
    return headers


def _call_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = TIMEOUT_S,
    scope: str = "upload",
    wait: bool = True,
) -> tuple[int, Any, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = _api_headers(token, "application/json" if body is not None else None)
    if body is not None:
        headers["Content-Length"] = str(len(body))
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with httpgate.urlopen(req, timeout, opener=_OPENER, scope=scope, wait=wait) as resp:
            raw = resp.read()
            parsed = json.loads(raw.decode("utf-8", "replace")) if raw else None
            return resp.status, resp.headers, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8", "replace")) if raw else None
        except ValueError:
            parsed = None
        return exc.code, exc.headers, parsed


MAX_PUBLIC_PAGES = 10
_SAFE_ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


def _has_next_page(headers: Any) -> bool:
    if headers is None:
        return False
    link = str(headers.get("link") or "")
    return 'rel="next"' in link or "rel='next'" in link


def verify_drop_repo_safe(
    api_base: str,
    repo: str,
    token: str,
    *,
    timeout: float = TIMEOUT_S,
    scope: str = "upload",
    wait: bool = True,
) -> None:
    """Prove ``repo`` is currently private, unarchived, non-fork, and has zero ``.github/workflows``."""
    api = api_base.rstrip("/")
    status, _headers, meta = _call_json(f"{api}/repos/{repo}", token, timeout=timeout, scope=scope, wait=wait)
    if status == 401:
        raise UploadError("GitHub rejected the upload token (HTTP 401)")
    if status != 200 or not isinstance(meta, dict):
        raise UploadError(f"verifying drop-box repository {repo} failed: HTTP {status}")
    if str(meta.get("full_name") or "").lower() != repo.lower():
        raise UploadError(f"repository identity mismatch for {repo}")
    if meta.get("private") is not True:
        raise UploadError(f"refusing {repo}: drop-box repository must be private")
    if meta.get("fork") is True or meta.get("archived") is True:
        raise UploadError(f"refusing {repo}: drop-box repository must not be a fork or archived")

    wf_status, _wf_headers, _wf_body = _call_json(
        f"{api}/repos/{repo}/contents/.github/workflows",
        token,
        timeout=timeout,
        scope=scope,
        wait=wait,
    )
    if wf_status == 200:
        raise UploadError(f"refusing {repo}: drop-box repository must not contain .github/workflows")
    if wf_status != 404:
        raise UploadError(f"verifying workflow absence on {repo} failed: HTTP {wf_status}")


def check_upload_token(
    token: str,
    drop_repo: str,
    *,
    base: str | None = None,
    timeout: float = TIMEOUT_S,
) -> dict[str, Any]:
    """Verify ``token`` is a fine-grained PAT with push access to private ``drop_repo`` and nowhere else."""
    api = (base or API_BASE).rstrip("/")
    if not tokencheck.shape_ok(token):
        return {"state": REJECTED, "detail": "That does not look like a GitHub token.", "blocked": False}
    repo = valid_drop_repo(drop_repo)
    # Reject classic PATs, multi-repo private PATs, and public-push PATs BEFORE returning BAD_REPO so an over-scoped
    # imported token (upload_token.import when drop_repo is still unset) is flagged and shredded immediately.
    try:
        status, headers, _user = _call_json(
            f"{api}/user", token, timeout=timeout, scope="token_test", wait=False
        )
        if status == 401:
            return {"state": REJECTED, "detail": "GitHub rejected the upload token.", "blocked": False}
        if status != 200:
            return {"state": UNREACHABLE, "detail": "Could not reach GitHub to check the upload token.", "blocked": False}
        scopes = (headers.get("x-oauth-scopes") or "").strip() if headers is not None else ""
        if scopes:
            return {
                "state": TOO_POWERFUL,
                "detail": "Classic tokens with scopes are refused. Use a fine-grained token scoped only to the drop-box repo.",
                "blocked": False,
            }

        status, priv_headers, priv = _call_json(
            f"{api}/user/repos?visibility=private&per_page=10",
            token,
            timeout=timeout,
            scope="token_test",
            wait=False,
        )
        if status == 401:
            return {"state": REJECTED, "detail": "GitHub rejected the upload token.", "blocked": False}
        if status != 200 or not isinstance(priv, list):
            return {"state": UNREACHABLE, "detail": "Could not reach GitHub to check the upload token.", "blocked": False}
        if len(priv) > 1 or _has_next_page(priv_headers):
            return {
                "state": TOO_POWERFUL,
                "detail": "That token can access multiple private repositories, so it was refused.",
                "blocked": False,
            }

        for page in range(1, MAX_PUBLIC_PAGES + 1):
            status, pub_headers, pub = _call_json(
                f"{api}/user/repos?visibility=public&affiliation=owner,collaborator,organization_member&per_page=100&page={page}",
                token,
                timeout=timeout,
                scope="token_test",
                wait=False,
            )
            if status == 401:
                return {"state": REJECTED, "detail": "GitHub rejected the upload token.", "blocked": False}
            if status != 200 or not isinstance(pub, list):
                return {"state": UNREACHABLE, "detail": "Could not reach GitHub to check the upload token.", "blocked": False}
            if any(
                isinstance(r, dict)
                and isinstance(r.get("permissions"), dict)
                and r["permissions"].get("push") is True
                for r in pub
            ):
                return {
                    "state": TOO_POWERFUL,
                    "detail": "That token can push to a public repository, so it was refused.",
                    "blocked": False,
                }
            if not _has_next_page(pub_headers):
                break
        else:
            return {
                "state": TOO_POWERFUL,
                "detail": "Too many public repositories to audit push permissions safely; use a single-repo token.",
                "blocked": False,
            }

        if repo is None:
            return {
                "state": BAD_REPO,
                "detail": "Set a dedicated private drop-box repo (owner/name) in Delivery first.",
                "blocked": False,
            }
        if not priv:
            return {
                "state": REJECTED,
                "detail": f"The token cannot see the private drop-box repository {repo}.",
                "blocked": False,
            }
        target = priv[0]
        if not isinstance(target, dict) or str(target.get("full_name", "")).lower() != repo.lower():
            return {
                "state": TOO_POWERFUL,
                "detail": "That token can access other private repositories, so it was refused.",
                "blocked": False,
            }
        perms = target.get("permissions") if isinstance(target.get("permissions"), dict) else {}
        if target.get("private") is not True or perms.get("push") is not True:
            return {
                "state": REJECTED,
                "detail": f"The token needs Contents write permission on private repository {repo}.",
                "blocked": False,
            }
        try:
            verify_drop_repo_safe(api, repo, token, timeout=timeout, scope="token_test", wait=False)
        except UploadError as exc:
            msg = str(exc)
            if ".github/workflows" in msg:
                return {
                    "state": TOO_POWERFUL,
                    "detail": f"Refused: {repo} contains .github/workflows. A drop-box repository must hold zero workflows.",
                    "blocked": False,
                }
            if "must be private" in msg or "fork or archived" in msg or "mismatch" in msg:
                return {"state": REJECTED, "detail": msg, "blocked": False}
            return {"state": UNREACHABLE, "detail": "Could not reach GitHub to check the upload token.", "blocked": False}
    except httpgate.WouldBlock:
        return {
            "state": UNREACHABLE,
            "detail": "There is no usable network right now. Try again when Wi-Fi returns.",
            "blocked": True,
        }
    except (OSError, ValueError, urllib.error.URLError):
        return {"state": UNREACHABLE, "detail": "Could not reach GitHub to check the upload token.", "blocked": False}

    return {"state": ACCEPTED, "detail": f"Accepted for private drop-box {repo} only.", "blocked": False}


def split_parts(total_bytes: int, max_asset_bytes: int = MAX_ASSET_BYTES) -> list[tuple[str, int, int]]:
    """Return ``[(suffix, offset, length), ...]`` for an archive of ``total_bytes``."""
    if max_asset_bytes <= 0:
        raise ValueError("max_asset_bytes must be positive")
    if total_bytes <= max_asset_bytes:
        return [("", 0, total_bytes)]
    parts: list[tuple[str, int, int]] = []
    offset = 0
    idx = 0
    while offset < total_bytes:
        length = min(max_asset_bytes, total_bytes - offset)
        parts.append((f".part{idx:02d}", offset, length))
        offset += length
        idx += 1
    return parts


def _sha256_slice(path: Path, offset: int, length: int) -> str:
    digest = hashlib.sha256()
    left = length
    with open(path, "rb") as fh:
        fh.seek(offset)
        while left > 0:
            chunk = fh.read(min(CHUNK_SIZE, left))
            if not chunk:
                raise UploadError(f"{path.name}: unexpected end of file while hashing slice")
            digest.update(chunk)
            left -= len(chunk)
    return digest.hexdigest()


class _FileSliceStream:
    """File-like reader over ``[offset, offset + length)`` that checks gate stop and touches progress per chunk."""

    def __init__(self, fh: BinaryIO, offset: int, length: int) -> None:
        self._fh = fh
        self._fh.seek(offset)
        self._left = length

    def read(self, size: int = -1) -> bytes:
        gate = httpgate.current()
        gate._check_stop()
        if self._left <= 0:
            return b""
        want = self._left if size is None or size < 0 else min(size, self._left)
        data = self._fh.read(min(want, CHUNK_SIZE))
        self._left -= len(data)
        if data:
            gate.touch_progress()
        return data


def _upload_asset_slice(
    upload_base: str,
    repo: str,
    release_id: int,
    asset_name: str,
    path: Path,
    offset: int,
    length: int,
    token: str,
    *,
    timeout: float = ASSET_TIMEOUT_S,
    scope: str = "upload",
) -> None:
    if not _SAFE_ASSET_NAME_RE.fullmatch(asset_name):
        raise UploadError(f"unsafe asset name refused: {asset_name!r}")
    query = urllib.parse.urlencode({"name": asset_name})
    url = f"{upload_base}/repos/{repo}/releases/{int(release_id)}/assets?{query}"
    headers = _api_headers(token, "application/octet-stream")
    headers["Content-Length"] = str(length)
    with open(path, "rb") as fh:
        stream = _FileSliceStream(fh, offset, length)
        req = urllib.request.Request(url, data=stream, headers=headers, method="POST")
        try:
            with httpgate.urlopen(req, timeout, opener=_OPENER, scope=scope, wait=True) as resp:
                resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            exc.read()
            raise UploadError(f"asset upload {asset_name} failed: HTTP {exc.code}") from None
    if status not in (200, 201):
        raise UploadError(f"asset upload {asset_name} returned unexpected status {status}")


def _upload_bytes_asset(
    upload_base: str,
    repo: str,
    release_id: int,
    asset_name: str,
    data: bytes,
    token: str,
    *,
    content_type: str = "application/json",
    timeout: float = TIMEOUT_S,
    scope: str = "upload",
) -> None:
    if not _SAFE_ASSET_NAME_RE.fullmatch(asset_name):
        raise UploadError(f"unsafe asset name refused: {asset_name!r}")
    query = urllib.parse.urlencode({"name": asset_name})
    url = f"{upload_base}/repos/{repo}/releases/{int(release_id)}/assets?{query}"
    headers = _api_headers(token, content_type)
    headers["Content-Length"] = str(len(data))
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with httpgate.urlopen(req, timeout, opener=_OPENER, scope=scope, wait=True) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        exc.read()
        raise UploadError(f"asset upload {asset_name} failed: HTTP {exc.code}") from None
    if status not in (200, 201):
        raise UploadError(f"asset upload {asset_name} returned unexpected status {status}")


def read_uploaded_state(home: Path | str) -> dict[str, Any] | None:
    """Return the persisted ``state/uploaded.json`` dictionary, or ``None`` when absent or unreadable."""
    path = Path(home) / "state" / "uploaded.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def upload_cycle(
    home: Path | str,
    token: str,
    drop_repo: str,
    *,
    selected_layers: list[str] | tuple[str, ...] | None = None,
    api_base: str | None = None,
    upload_base: str | None = None,
    log: Callable[[str], object] | None = None,
    clock: Callable[[], float] | None = None,
    max_asset_bytes: int = MAX_ASSET_BYTES,
    scope: str = "upload",
) -> dict[str, Any]:
    """Build deterministic archives for a finished cycle and publish them to ``drop_repo`` as a GitHub Release."""
    home_path = Path(home)
    repo = valid_drop_repo(drop_repo)
    if repo is None:
        raise UploadError("invalid or missing drop_repo")
    if not tokencheck.shape_ok(token):
        raise UploadError("invalid upload token")
    if not (home_path / "state" / "refresh" / "MAIN.done").is_file():
        raise UploadError("cycle is not finished yet (MAIN.done missing)")
    candidates_path = home_path / "raw" / "candidates.jsonl"
    if not candidates_path.is_file():
        raise UploadError("raw/candidates.jsonl is missing")

    base_manifest = archive.write_manifest(home_path)
    layers_map: dict[str, dict[str, Any]] = json.loads(json.dumps(base_manifest["layers"]))
    wanted = list(selected_layers) if selected_layers is not None else list(layers_map)
    missing = [lay for lay in wanted if lay not in layers_map]
    if missing:
        raise UploadError(f"completed layer archives missing: {', '.join(missing)}")

    # Enrich manifest copy (without mutating archives/manifest.json used by LAN pulls) when splitting >max_asset_bytes.
    asset_plan: list[tuple[str, Path, int, int]] = []
    cand_size = candidates_path.stat().st_size
    asset_plan.append(("candidates.jsonl", candidates_path, 0, cand_size))

    for lay in [k for k in archive.LAYERS if k in layers_map and k in wanted]:
        entry = layers_map[lay]
        arc_path = home_path / "archives" / entry["archive"]
        slices = split_parts(int(entry["bytes"]), max_asset_bytes)
        if len(slices) == 1:
            asset_plan.append((entry["archive"], arc_path, 0, int(entry["bytes"])))
        else:
            parts_meta: list[dict[str, Any]] = []
            for suffix, offset, length in slices:
                part_name = entry["archive"] + suffix
                parts_meta.append({
                    "name": part_name,
                    "bytes": length,
                    "sha256": _sha256_slice(arc_path, offset, length),
                })
                asset_plan.append((part_name, arc_path, offset, length))
            entry["parts"] = parts_meta

    drop_manifest = {
        "generated_at": base_manifest["generated_at"],
        "main_done": base_manifest["main_done"],
        "layers": {k: layers_map[k] for k in archive.LAYERS if k in layers_map and k in wanted},
        "candidates_sha256": base_manifest["candidates_sha256"],
        "candidates_bytes": cand_size,
    }
    # Cycle identity hashes the deterministic content (layers + main_done + candidates_sha256), never generated_at!
    identity_blob = json.dumps(
        {
            "main_done": drop_manifest["main_done"],
            "layers": drop_manifest["layers"],
            "candidates_sha256": drop_manifest["candidates_sha256"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    cycle_sha = hashlib.sha256(identity_blob).hexdigest()
    stamp_raw = str(drop_manifest.get("main_done") or drop_manifest["generated_at"])[:10]
    date_part = stamp_raw if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp_raw) else "cycle"
    tag = f"cycle-{date_part}-{cycle_sha[:12]}"
    manifest_bytes = (json.dumps(drop_manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")

    prev = read_uploaded_state(home_path)
    if prev and prev.get("tag") == tag and prev.get("drop_repo") == repo and prev.get("cycle_sha256") == cycle_sha:
        return {"ok": True, "skipped": True, "tag": tag, "bytes_uploaded": 0}

    api = (api_base or API_BASE).rstrip("/")
    up = (upload_base or UPLOAD_BASE).rstrip("/")
    now_iso = (
        datetime.fromtimestamp(clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if clock is not None
        else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    # Re-verify drop-box privacy and zero-workflow state immediately before uploading any cycle data.
    verify_drop_repo_safe(api, repo, token, scope=scope, wait=True)

    status, _headers, releases = _call_json(f"{api}/repos/{repo}/releases?per_page=20", token, scope=scope)
    if status != 200 or not isinstance(releases, list):
        raise UploadError(f"listing releases on {repo} failed: HTTP {status}")

    release = next((r for r in releases if isinstance(r, dict) and r.get("tag_name") == tag), None)
    if release is not None and release.get("draft") is False:
        state_doc = {
            "tag": tag,
            "drop_repo": repo,
            "cycle_sha256": cycle_sha,
            "uploaded_at": now_iso,
            "bytes": sum(x[3] for x in asset_plan) + len(manifest_bytes),
        }
        _atomic_write_json(home_path / "state" / "uploaded.json", state_doc)
        return {"ok": True, "skipped": True, "tag": tag, "bytes_uploaded": 0}

    if release is None:
        status, _headers, created = _call_json(
            f"{api}/repos/{repo}/releases",
            token,
            method="POST",
            payload={
                "tag_name": tag,
                "name": tag,
                "body": f"Porchlamp collector cycle {tag}",
                "draft": True,
            },
            scope=scope,
        )
        if status not in (200, 201) or not isinstance(created, dict) or "id" not in created:
            raise UploadError(f"creating draft release {tag} failed: HTTP {status}")
        release = created

    release_id = int(release["id"])
    existing_assets: dict[str, dict[str, Any]] = {}
    for asset in release.get("assets") or []:
        if isinstance(asset, dict) and isinstance(asset.get("name"), str):
            existing_assets[asset["name"]] = asset

    bytes_uploaded = 0
    for asset_name, file_path, offset, length in asset_plan:
        existing = existing_assets.get(asset_name)
        if existing is not None:
            if existing.get("state") == "uploaded" and int(existing.get("size") or -1) == length:
                if log is not None:
                    log(f"upload: {asset_name} already present on draft {tag}")
                continue
            asset_id = existing.get("id")
            if asset_id is not None:
                del_status, _, _ = _call_json(
                    f"{api}/repos/{repo}/releases/assets/{int(asset_id)}",
                    token,
                    method="DELETE",
                    scope=scope,
                )
                if del_status not in (204, 404):
                    raise UploadError(f"deleting partial asset {asset_name} failed: HTTP {del_status}")
        if log is not None:
            log(f"upload: sending {asset_name} ({length} bytes)")
        _upload_asset_slice(up, repo, release_id, asset_name, file_path, offset, length, token, scope=scope)
        bytes_uploaded += length

    # Always write manifest.json last (removing any stale draft copy first) before flipping draft -> false.
    if "manifest.json" in existing_assets and existing_assets["manifest.json"].get("id") is not None:
        del_status, _, _ = _call_json(
            f"{api}/repos/{repo}/releases/assets/{int(existing_assets['manifest.json']['id'])}",
            token,
            method="DELETE",
            scope=scope,
        )
        if del_status not in (204, 404):
            raise UploadError(f"replacing draft manifest.json failed: HTTP {del_status}")
    _upload_bytes_asset(up, repo, release_id, "manifest.json", manifest_bytes, token, scope=scope)
    bytes_uploaded += len(manifest_bytes)

    pub_status, _headers, published = _call_json(
        f"{api}/repos/{repo}/releases/{release_id}",
        token,
        method="PATCH",
        payload={"draft": False, "make_latest": "true"},
        scope=scope,
    )
    if pub_status != 200 or not isinstance(published, dict) or published.get("draft") is not False:
        raise UploadError(f"publishing release {tag} failed: HTTP {pub_status}")

    state_doc = {
        "tag": tag,
        "drop_repo": repo,
        "cycle_sha256": cycle_sha,
        "uploaded_at": now_iso,
        "bytes": sum(x[3] for x in asset_plan) + len(manifest_bytes),
    }
    _atomic_write_json(home_path / "state" / "uploaded.json", state_doc)
    if log is not None:
        log(f"upload: published {tag} to {repo} ({bytes_uploaded} bytes sent)")
    return {"ok": True, "skipped": False, "tag": tag, "bytes_uploaded": bytes_uploaded}
