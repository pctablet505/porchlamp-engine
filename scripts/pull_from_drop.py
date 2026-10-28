#!/usr/bin/env python3
"""Pull a finished phone cycle from a dedicated private GitHub drop-box repository into a data root.

    python scripts/pull_from_drop.py --repo owner/porchlamp-drop --dest DATA_ROOT [--token-file F]
    python scripts/pull_from_drop.py --repo owner/porchlamp-drop --dest DATA_ROOT --check-only

Exit codes: 0 ok, 1 usage or HTTP/network error, 3 sha256/size mismatch (nothing extracted),
4 unsafe archive member (nothing extracted), 5 a set-aside target already exists (nothing moved).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phone.archive import LAYERS, MAIN_LAYERS
from phone.control import valid_drop_repo

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "porchlamp-drop-pull/1"
CHUNK = 1 << 20
TIMEOUT_S = 60
ASSET_TIMEOUT_S = 300
CANDIDATES_CAP = 16 * 1024 * 1024
MANIFEST_CAP = 4 * 1024 * 1024
MAX_API_JSON_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_PARTS = 32
MAX_LAYER_FILES = 250_000
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_LAYER_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
COMPRESSION_RATIO_FLOOR = 1024 * 1024

CYCLE_TAG_RE = re.compile(r"^cycle-(?:\d{4}-\d{2}-\d{2}|cycle)-[0-9a-f]{12}$")
SAFE_MEMBER_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,219}\.json$")
SAFE_PART_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_API_HOSTS: frozenset[str] = frozenset({"api.github.com"})
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})


class PullError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class _NoRedirect(request.HTTPRedirectHandler):
    """Never automatically forward an Authorization header across redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = request.build_opener(_NoRedirect)


def _log(message: str) -> None:
    print(message, flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True))


def _is_loopback_base(api_base: str) -> bool:
    host = (parse.urlsplit(api_base).hostname or "").lower()
    return host in LOOPBACK_HOSTS


def _validate_api_base(api_base: str) -> str:
    cleaned = api_base.strip().rstrip("/")
    parts = parse.urlsplit(cleaned)
    host = (parts.hostname or "").lower()
    if "@" in parts.netloc or not host:
        raise PullError(1, "invalid --api-base URL")
    try:
        port = parts.port
    except ValueError:
        raise PullError(1, "invalid --api-base port") from None
    if host in LOOPBACK_HOSTS and parts.scheme in ("http", "https"):
        return cleaned
    if parts.scheme == "https" and host in ALLOWED_API_HOSTS and port in (None, 443):
        return cleaned
    raise PullError(1, "refused --api-base: only https://api.github.com (or loopback in tests) is permitted")


def _validate_redirect_url(location: str, *, allow_loopback: bool, asset_id: int) -> str:
    parts = parse.urlsplit(location)
    host = (parts.hostname or "").lower()
    if "@" in parts.netloc or not host:
        raise PullError(1, f"asset {asset_id}: refused malformed redirect target")
    try:
        port = parts.port
    except ValueError:
        raise PullError(1, f"asset {asset_id}: refused redirect target with bad port") from None
    if allow_loopback and host in LOOPBACK_HOSTS and parts.scheme in ("http", "https"):
        return location
    if parts.scheme != "https" or port not in (None, 443):
        raise PullError(1, f"asset {asset_id}: refused non-HTTPS or non-443 redirect target")
    allowed_redirect_host = (
        host in ("github.com", "api.github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
        or host.endswith(".githubusercontent.com")
    )
    if not allowed_redirect_host:
        raise PullError(1, f"asset {asset_id}: refused redirect to untrusted host {host}")
    return location


def _clean_iso_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise PullError(1, f"invalid {label}: expected ISO-8601 string")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 64 or "\n" in cleaned or "\r" in cleaned:
        raise PullError(1, f"invalid {label}: control characters or bad length")
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        raise PullError(1, f"invalid {label}: not a valid ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        raise PullError(1, f"invalid {label}: timestamp must include UTC offset")
    return cleaned


def _api_get_status_json(url: str, token: str) -> tuple[int, Any]:
    req = request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        with _NO_REDIRECT_OPENER.open(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read(MAX_API_JSON_BYTES + 1)
            status = int(getattr(resp, "status", 200) or 200)
    except error.HTTPError as exc:
        code = exc.code
        exc.close()
        return code, None
    except (error.URLError, OSError) as exc:
        raise PullError(1, f"GET {parse.urlsplit(url).path} failed: {type(exc).__name__}") from None
    if len(raw) > MAX_API_JSON_BYTES:
        raise PullError(1, f"GET {parse.urlsplit(url).path}: API reply exceeded {MAX_API_JSON_BYTES} bytes")
    try:
        return status, (json.loads(raw.decode("utf-8")) if raw else None)
    except ValueError:
        raise PullError(1, f"GET {parse.urlsplit(url).path}: invalid JSON reply") from None


def _api_get_json(url: str, token: str) -> Any:
    status, data = _api_get_status_json(url, token)
    if status != 200:
        raise PullError(1, f"GET {parse.urlsplit(url).path} failed: HTTP {status}")
    return data


def _verify_drop_repo_safe(api_base: str, repo: str, token: str) -> None:
    """Refuse to ingest from a drop-box repo that is public, forked, archived, or holds workflows."""
    meta = _api_get_json(f"{api_base}/repos/{repo}", token)
    if not isinstance(meta, dict):
        raise PullError(1, f"GET /repos/{repo}: unexpected repository metadata")
    if str(meta.get("full_name") or "").lower() != repo.lower():
        raise PullError(1, f"repository identity mismatch for {repo}")
    if meta.get("private") is not True:
        raise PullError(1, f"refused drop-box {repo}: repository is not private")
    if meta.get("fork") is True or meta.get("archived") is True:
        raise PullError(1, f"refused drop-box {repo}: repository must not be a fork or archived")

    wf_status, _wf = _api_get_status_json(f"{api_base}/repos/{repo}/contents/.github/workflows", token)
    if wf_status == 200:
        raise PullError(1, f"refused drop-box {repo}: repository must not contain .github/workflows")
    if wf_status != 404:
        raise PullError(1, f"GET /repos/{repo}/contents/.github/workflows failed: HTTP {wf_status}")


def _open_asset_stream(api_base: str, repo: str, asset_id: int, token: str):
    """Open a binary release asset stream, stripping Authorization if GitHub redirects to blob storage."""
    url = f"{api_base}/repos/{repo}/releases/assets/{int(asset_id)}"
    req = request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/octet-stream",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        return _NO_REDIRECT_OPENER.open(req, timeout=ASSET_TIMEOUT_S)
    except error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            location = exc.headers.get("Location") if exc.headers is not None else None
            exc.close()
            if not location:
                raise PullError(1, f"asset {asset_id}: redirect missing Location header") from None
            safe_location = _validate_redirect_url(
                location,
                allow_loopback=_is_loopback_base(api_base),
                asset_id=asset_id,
            )
            follow = request.Request(safe_location, headers={"User-Agent": USER_AGENT}, method="GET")
            try:
                return _NO_REDIRECT_OPENER.open(follow, timeout=ASSET_TIMEOUT_S)
            except error.HTTPError as redirect_exc:
                redirect_exc.close()
                raise PullError(1, f"asset {asset_id} redirect failed: HTTP {redirect_exc.code}") from None
            except (error.URLError, OSError) as redirect_exc:
                raise PullError(1, f"asset {asset_id} redirect failed: {type(redirect_exc).__name__}") from None
        exc.close()
        raise PullError(1, f"asset {asset_id} download failed: HTTP {exc.code}") from None
    except (error.URLError, OSError) as exc:
        raise PullError(1, f"asset {asset_id} download failed: {type(exc).__name__}") from None


def _download_bounded(api_base: str, repo: str, asset_id: int, token: str, out_path: Path, limit: int) -> None:
    if limit < 0:
        raise PullError(3, f"asset {asset_id}: negative byte limit refused")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with _open_asset_stream(api_base, repo, asset_id, token) as resp, open(out_path, "wb") as fh:
        while total <= limit:
            chunk = resp.read(min(CHUNK, limit + 1 - total))
            if not chunk:
                break
            fh.write(chunk)
            total += len(chunk)
    if total > limit:
        out_path.unlink(missing_ok=True)
        raise PullError(3, f"asset {asset_id} exceeded expected size ({limit} bytes); deleted")


def _select_release(api_base: str, repo: str, token: str, tag: str | None) -> dict[str, Any] | None:
    if tag:
        cleaned_tag = tag.strip()
        if not CYCLE_TAG_RE.fullmatch(cleaned_tag):
            raise PullError(1, f"invalid --tag {tag!r}: must match cycle-<YYYY-MM-DD>-<12hex>")
        encoded = parse.quote(cleaned_tag, safe="")
        rel = _api_get_json(f"{api_base}/repos/{repo}/releases/tags/{encoded}", token)
        if not isinstance(rel, dict) or rel.get("draft") is True or rel.get("tag_name") != cleaned_tag:
            raise PullError(1, f"release {cleaned_tag} is missing or still a draft")
        return rel
    releases = _api_get_json(f"{api_base}/repos/{repo}/releases?per_page=20", token)
    if not isinstance(releases, list):
        raise PullError(1, "unexpected releases list reply")
    for rel in releases:
        if (
            isinstance(rel, dict)
            and rel.get("draft") is False
            and rel.get("prerelease") is False
            and isinstance(rel.get("tag_name"), str)
            and CYCLE_TAG_RE.fullmatch(rel["tag_name"]) is not None
        ):
            return rel
    return None


def _assets_by_name(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for asset in release.get("assets") or []:
        if (
            isinstance(asset, dict)
            and isinstance(asset.get("name"), str)
            and SAFE_PART_NAME_RE.fullmatch(asset["name"])
            and asset.get("state") in (None, "uploaded")
            and "id" in asset
        ):
            out[asset["name"]] = asset
    return out


def _read_pulled_release(dest: Path) -> dict[str, Any] | None:
    path = dest / "state" / "pulled" / "release.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _max_uncompressed_for_archive(compressed_bytes: int) -> int:
    ratio_bound = max(COMPRESSION_RATIO_FLOOR, max(1, compressed_bytes) * MAX_COMPRESSION_RATIO)
    return min(MAX_LAYER_UNCOMPRESSED_BYTES, ratio_bound)


def _check_members(archive_path: Path, layer: str) -> list[tarfile.TarInfo]:
    """Every member must be a bounded regular ``raw/<collector>/<name>.json`` of this layer; otherwise exit 4."""
    compressed_bytes = archive_path.stat().st_size
    uncompressed_cap = _max_uncompressed_for_archive(compressed_bytes)
    members: list[tarfile.TarInfo] = []
    seen_names: set[str] = set()
    total_declared = 0
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            for info in tar:
                parts = info.name.split("/")
                ok = (
                    info.isreg()
                    and not info.issym()
                    and not info.islnk()
                    and len(parts) == 3
                    and parts[0] == "raw"
                    and parts[1] in LAYERS[layer]
                    and SAFE_MEMBER_FILENAME_RE.fullmatch(parts[2]) is not None
                    and "\\" not in info.name
                    and 0 <= info.size <= MAX_MEMBER_BYTES
                    and info.name not in seen_names
                )
                if not ok:
                    raise PullError(4, f"{layer}: unsafe archive member refused")
                seen_names.add(info.name)
                members.append(info)
                if len(members) > MAX_LAYER_FILES:
                    raise PullError(4, f"{layer}: archive exceeds {MAX_LAYER_FILES} member cap")
                total_declared += info.size
                if total_declared > uncompressed_cap:
                    raise PullError(4, f"{layer}: archive exceeds uncompressed size or compression-ratio cap")
    except (tarfile.TarError, EOFError, OSError) as exc:
        if isinstance(exc, PullError):
            raise
        raise PullError(4, f"{layer}: corrupt or unsafe tar.gz archive ({type(exc).__name__})") from None
    return members


def _set_aside(dest: Path, layer: str) -> None:
    """Rename each existing raw/<collector> of the layer into raw-stale-<date>-pull/. Never deletes."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stale = dest / f"raw-stale-{stamp}-pull"
    existing = [c for c in LAYERS[layer] if (dest / "raw" / c).exists()]
    for collector in existing:
        if (stale / collector).exists():
            raise PullError(5, f"{layer}: {stale.name}/{collector} already exists")
    for collector in existing:
        stale.mkdir(parents=True, exist_ok=True)
        os.rename(dest / "raw" / collector, stale / collector)


def _extract(archive_path: Path, members: list[tarfile.TarInfo], dest: Path) -> int:
    compressed_bytes = archive_path.stat().st_size
    uncompressed_cap = _max_uncompressed_for_archive(compressed_bytes)
    total_extracted = 0
    count = 0
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            for info in members:
                source = tar.extractfile(info)
                if source is None:
                    raise PullError(4, f"missing payload for {info.name}")
                expected = int(info.size)
                target = dest / info.name
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name("." + target.name + ".tmp")
                written = 0
                try:
                    with open(tmp, "wb") as out:
                        while written <= expected:
                            chunk = source.read(min(CHUNK, expected + 1 - written))
                            if not chunk:
                                break
                            written += len(chunk)
                            total_extracted += len(chunk)
                            if (
                                written > expected
                                or written > MAX_MEMBER_BYTES
                                or total_extracted > uncompressed_cap
                            ):
                                raise PullError(4, f"stream overrun while extracting {info.name}")
                            out.write(chunk)
                    if written != expected:
                        raise PullError(4, f"truncated tar payload for {info.name}")
                    with open(tmp, "rb") as verify_fh:
                        parsed = json.load(verify_fh)
                    if not isinstance(parsed, dict):
                        raise PullError(4, f"non-object JSON envelope in {info.name}")
                    os.replace(tmp, target)
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
                count += 1
    except (tarfile.TarError, EOFError, UnicodeDecodeError, ValueError) as exc:
        if isinstance(exc, PullError):
            raise
        raise PullError(4, f"corrupt payload while extracting {archive_path.name}: {type(exc).__name__}") from None
    return count


def _validate_candidates_jsonl(path: Path) -> None:
    try:
        with open(path, encoding="utf-8") as fh:
            seen = 0
            for lineno, line in enumerate(fh, 1):
                stripped = line.strip()
                if not stripped:
                    continue
                obj = json.loads(stripped)
                if (
                    not isinstance(obj, dict)
                    or not isinstance(obj.get("owner"), str)
                    or not isinstance(obj.get("name"), str)
                    or not obj["owner"]
                    or not obj["name"]
                ):
                    raise PullError(4, f"candidates.jsonl line {lineno}: invalid candidate object")
                seen += 1
    except (UnicodeDecodeError, ValueError) as exc:
        if isinstance(exc, PullError):
            raise
        raise PullError(4, f"candidates.jsonl is not valid UTF-8 JSONL ({type(exc).__name__})") from None
    if seen == 0:
        raise PullError(4, "candidates.jsonl is empty")


def _download_layer_archive(
    api_base: str,
    repo: str,
    token: str,
    layer: str,
    entry: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    tmp_dir: Path,
) -> Path:
    expected_sha = str(entry.get("sha256") or "")
    expected_bytes = int(entry.get("bytes") if entry.get("bytes") is not None else -1)
    if not SHA256_HEX_RE.fullmatch(expected_sha) or expected_bytes <= 0:
        raise PullError(1, f"{layer}: invalid sha256 or byte count in manifest.json")
    target = tmp_dir / f"{layer}.tar.gz"
    parts = entry.get("parts")
    if isinstance(parts, list) and parts:
        if len(parts) > MAX_ARCHIVE_PARTS:
            raise PullError(1, f"{layer}: manifest lists too many archive slices ({len(parts)})")
        target.parent.mkdir(parents=True, exist_ok=True)
        combined_total = 0
        with open(target, "wb") as combined:
            for idx, part_meta in enumerate(parts):
                if not isinstance(part_meta, dict):
                    raise PullError(1, f"{layer}: malformed part metadata at index {idx}")
                part_name = str(part_meta.get("name") or "")
                part_bytes = int(part_meta.get("bytes") if part_meta.get("bytes") is not None else -1)
                part_sha = str(part_meta.get("sha256") or "")
                if not SAFE_PART_NAME_RE.fullmatch(part_name) or not SHA256_HEX_RE.fullmatch(part_sha) or part_bytes <= 0:
                    raise PullError(1, f"{layer}: invalid slice metadata at index {idx}")
                combined_total += part_bytes
                if combined_total > expected_bytes:
                    raise PullError(3, f"{layer}: slice byte sum exceeds manifest layer bytes")
                asset = assets.get(part_name)
                if asset is None:
                    raise PullError(1, f"{layer}: release asset {part_name!r} is missing")
                slice_tmp = tmp_dir / f"{part_name}.tmp"
                _download_bounded(api_base, repo, int(asset["id"]), token, slice_tmp, part_bytes)
                if slice_tmp.stat().st_size != part_bytes or _sha256(slice_tmp) != part_sha:
                    slice_tmp.unlink(missing_ok=True)
                    target.unlink(missing_ok=True)
                    raise PullError(3, f"{layer}: sha256 or size mismatch in {part_name}")
                with open(slice_tmp, "rb") as sfh:
                    shutil.copyfileobj(sfh, combined, CHUNK)
                slice_tmp.unlink(missing_ok=True)
    else:
        arc_name = str(entry.get("archive") or f"{layer}.tar.gz")
        if not SAFE_PART_NAME_RE.fullmatch(arc_name):
            raise PullError(1, f"{layer}: invalid archive name in manifest.json")
        asset = assets.get(arc_name)
        if asset is None:
            raise PullError(1, f"{layer}: release asset {arc_name!r} is missing")
        _download_bounded(api_base, repo, int(asset["id"]), token, target, expected_bytes)

    if target.stat().st_size != expected_bytes or _sha256(target) != expected_sha:
        target.unlink(missing_ok=True)
        raise PullError(3, f"{layer}: sha256 or size mismatch with manifest.json; archive deleted")
    return target


def pull_from_drop(
    *,
    api_base: str,
    repo: str,
    token: str,
    dest: Path,
    tag: str | None = None,
    wanted: list[str] | None = None,
    check_only: bool = False,
) -> dict[str, Any]:
    safe_api = _validate_api_base(api_base)
    _verify_drop_repo_safe(safe_api, repo, token)

    release = _select_release(safe_api, repo, token, tag)
    if release is None:
        return {"has_new": False, "tag": None, "reason": "no_cycle_release"}

    rel_tag = str(release["tag_name"])
    prev = _read_pulled_release(dest)
    if prev and prev.get("tag") == rel_tag and prev.get("repo") == repo and wanted is None:
        prev_as_of = prev.get("main_done")
        safe_prev_as_of = _clean_iso_timestamp(prev_as_of, "cached main_done") if prev_as_of else None
        return {
            "has_new": False,
            "tag": rel_tag,
            "as_of": safe_prev_as_of,
            "reason": "already_pulled",
        }
    if check_only:
        return {"has_new": True, "tag": rel_tag, "reason": "new_release"}

    assets = _assets_by_name(release)
    manifest_asset = assets.get("manifest.json")
    if manifest_asset is None:
        raise PullError(1, f"release {rel_tag} is missing manifest.json")

    tmp_dir = dest / "pull-tmp"
    try:
        manifest_path = tmp_dir / "manifest.json"
        _download_bounded(safe_api, repo, int(manifest_asset["id"]), token, manifest_path, MANIFEST_CAP)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError:
            manifest_path.unlink(missing_ok=True)
            raise PullError(1, f"release {rel_tag}: manifest.json is not valid JSON") from None
        manifest_path.unlink(missing_ok=True)

        if not isinstance(manifest, dict) or not isinstance(manifest.get("layers"), dict):
            raise PullError(1, f"release {rel_tag}: manifest.json missing layers map")

        raw_main_done = manifest.get("main_done")
        main_done = _clean_iso_timestamp(raw_main_done, "manifest main_done") if raw_main_done is not None else None
        raw_generated = manifest.get("generated_at")
        generated_at = _clean_iso_timestamp(raw_generated, "manifest generated_at") if raw_generated is not None else None
        if main_done is None and generated_at is None:
            raise PullError(1, f"release {rel_tag}: manifest.json missing main_done and generated_at")

        # Stage and verify candidates.jsonl first when listed in the manifest; commit only after validation.
        staged_cand: tuple[Path, str] | None = None
        cand_sha = manifest.get("candidates_sha256")
        if cand_sha is not None:
            if not isinstance(cand_sha, str) or not SHA256_HEX_RE.fullmatch(cand_sha):
                raise PullError(1, f"release {rel_tag}: invalid candidates_sha256 in manifest.json")
            cand_asset = assets.get("candidates.jsonl")
            if cand_asset is None:
                raise PullError(1, f"release {rel_tag} is missing candidates.jsonl")
            cand_limit = int(manifest.get("candidates_bytes") or CANDIDATES_CAP)
            if cand_limit <= 0 or cand_limit > CANDIDATES_CAP:
                raise PullError(3, f"candidates.jsonl exceeds {CANDIDATES_CAP} byte cap")
            cand_tmp = tmp_dir / "candidates.jsonl.tmp"
            _download_bounded(safe_api, repo, int(cand_asset["id"]), token, cand_tmp, cand_limit)
            if _sha256(cand_tmp) != cand_sha:
                cand_tmp.unlink(missing_ok=True)
                raise PullError(3, "candidates.jsonl sha256 mismatch with manifest.json")
            _validate_candidates_jsonl(cand_tmp)
            staged_cand = (cand_tmp, cand_sha)

        layers = manifest["layers"]
        todo = [layer for layer in LAYERS if layer in layers and (wanted is None or layer in wanted)]
        pulled_dir = dest / "state" / "pulled"
        pulled_layers: list[str] = []

        for layer in todo:
            entry = layers[layer]
            if not isinstance(entry, dict):
                raise PullError(1, f"{layer}: malformed layer entry in manifest.json")
            completed_at = _clean_iso_timestamp(entry.get("completed_at"), f"{layer}.completed_at")
            marker = pulled_dir / f"{layer}.sha256"
            if marker.exists() and marker.read_text(encoding="utf-8").strip() == entry.get("sha256"):
                _log(f"{layer}: unchanged")
                continue
            arc_path = _download_layer_archive(safe_api, repo, token, layer, entry, assets, tmp_dir)
            stage_dir = tmp_dir / f"stage-{layer}"
            if stage_dir.exists():
                shutil.rmtree(stage_dir)
            try:
                members = _check_members(arc_path, layer)
                count = _extract(arc_path, members, stage_dir)
                _set_aside(dest, layer)
                staged_raw = stage_dir / "raw"
                if staged_raw.is_dir():
                    (dest / "raw").mkdir(parents=True, exist_ok=True)
                    for collector in LAYERS[layer]:
                        src_col = staged_raw / collector
                        if src_col.is_dir():
                            os.rename(src_col, dest / "raw" / collector)
                _write_text(pulled_dir / f"{layer}.sha256", str(entry["sha256"]))
                _write_text(dest / "state" / "refresh" / f"{layer}.complete", completed_at)
                pulled_layers.append(layer)
                _log(f"{layer}: pulled files={count} bytes={entry['bytes']}")
            finally:
                arc_path.unlink(missing_ok=True)
                if stage_dir.exists():
                    shutil.rmtree(stage_dir, ignore_errors=True)

        if staged_cand is not None:
            cand_tmp, verified_sha = staged_cand
            cand_dest = dest / "raw" / "candidates.jsonl"
            cand_dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(cand_tmp, cand_dest)
            _log(f"candidates.jsonl: verified sha256={verified_sha[:12]}")
    finally:
        if tmp_dir.is_dir():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if main_done and all((pulled_dir / f"{layer}.sha256").exists() for layer in MAIN_LAYERS):
        _write_text(dest / "state" / "refresh" / "MAIN.done", main_done)
        _log("MAIN.done written")

    as_of = str(main_done or generated_at or "")
    release_state = {
        "repo": repo,
        "tag": rel_tag,
        "main_done": main_done,
        "as_of": as_of,
        "pulled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "layers": [k for k in LAYERS if k in layers],
    }
    _write_json(pulled_dir / "release.json", release_state)
    return {
        "has_new": True,
        "tag": rel_tag,
        "as_of": as_of,
        "pulled_layers": pulled_layers,
        "reason": "pulled",
    }


def _read_token(token_file: Path | None) -> str:
    if token_file is not None:
        token = token_file.read_text(encoding="utf-8").strip()
    else:
        token = (os.environ.get("PORCHLAMP_DROP_READ_TOKEN") or "").strip()
    if not token:
        raise PullError(1, "missing drop-box read token (pass --token-file or set PORCHLAMP_DROP_READ_TOKEN)")
    return token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", required=True, help="private drop-box repository owner/name")
    parser.add_argument("--dest", required=True, type=Path, help="destination data directory")
    parser.add_argument("--token-file", type=Path, default=None, help="path to fine-grained read token")
    parser.add_argument("--tag", default=None, help="specific cycle-* release tag (default: newest)")
    parser.add_argument("--layers", default=None, help="comma-separated layer names; default all listed")
    parser.add_argument("--check-only", action="store_true", help="check whether a new cycle release exists")
    parser.add_argument("--status-out", type=Path, default=None, help="write JSON result summary to this path")
    parser.add_argument("--api-base", default=API_BASE, help="GitHub REST API base URL")
    args = parser.parse_args(argv)

    try:
        repo = valid_drop_repo(args.repo)
        if repo is None:
            raise PullError(1, "invalid --repo (must be a dedicated owner/name, never porchlamp or engine repos)")
        safe_api = _validate_api_base(args.api_base)
        token = _read_token(args.token_file)
        wanted = args.layers.split(",") if args.layers else None
        if wanted and any(layer not in LAYERS for layer in wanted):
            raise PullError(1, "unknown layer in --layers")
        summary = pull_from_drop(
            api_base=safe_api,
            repo=repo,
            token=token,
            dest=args.dest,
            tag=args.tag,
            wanted=wanted,
            check_only=args.check_only,
        )
        if args.status_out is not None:
            _write_json(args.status_out, summary)
        if args.check_only:
            _log(json.dumps(summary, sort_keys=True))
        return 0
    except PullError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"error: {type(exc).__name__}: {exc.filename or ''}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
