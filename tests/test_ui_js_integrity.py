"""Static integrity and parity test suite for frontend ES modules (``web/app/**/*.js``).

Because Node.js is not required on every test runner, this suite parses and
verifies the frontend ES module graph, keymap handler parity, and core
contract classification logic directly from source under ``pytest``.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_APP_DIR = REPO_ROOT / "web" / "app"

_ARROW_KEY_MAP: dict[str, str] = {
    "↑": "arrowup",
    "↓": "arrowdown",
    "←": "arrowleft",
    "→": "arrowright",
}


def _strip_js_comments(source: str) -> str:
    """Remove // single-line and /* ... */ multi-line JS comments while preserving strings."""
    pattern = re.compile(
        r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|//.*?$|/\*[\s\S]*?\*/""",
        re.MULTILINE,
    )
    return pattern.sub(lambda m: m.group(1) if m.group(1) else "", source)


def _iter_production_js_files() -> list[Path]:
    """Return all .js files under web/app/ excluding web/app/tests/."""
    assert WEB_APP_DIR.is_dir(), f"Missing frontend directory: {WEB_APP_DIR}"
    files = [
        path
        for path in sorted(WEB_APP_DIR.rglob("*.js"))
        if "tests" not in path.relative_to(WEB_APP_DIR).parts
    ]
    assert files, "No production .js files found under web/app/"
    return files


def _parse_exports(source: str) -> set[str]:
    """Extract all exported symbol names from an ES module source string."""
    clean = _strip_js_comments(source)
    exported: set[str] = set()

    # export [async] function|const|let|var|class Name
    for match in re.finditer(
        r"\bexport\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)",
        clean,
    ):
        exported.add(match.group(1))

    # export { a, b as c, ... }
    for match in re.finditer(r"\bexport\s*\{([^}]*)\}", clean):
        items = match.group(1).split(",")
        for raw_item in items:
            item = raw_item.strip()
            if not item:
                continue
            parts = re.split(r"\s+as\s+", item)
            exported.add(parts[-1].strip())

    # export default ...
    if re.search(r"\bexport\s+default\b", clean):
        exported.add("default")

    return exported


def _parse_imports(source: str) -> list[tuple[str, list[str], bool, str | None]]:
    """Extract import statements as (module_specifier, imported_symbols, is_namespace_import,
    namespace_alias).

    For ``import { a, b as c } from "./mod.js"``, ``imported_symbols`` contains
    the upstream exported names ``["a", "b"]``.  For ``import * as copy from
    "./mod.js"``, ``namespace_alias`` is the local binding ``"copy"``.
    """
    clean = _strip_js_comments(source)
    results: list[tuple[str, list[str], bool, str | None]] = []

    # Match standard `import ... from "specifier"` (including multi-line braces)
    import_from_pattern = re.compile(
        r"\bimport\s+([\s\S]*?)\s+from\s+[\"']([^\"']+)[\"']",
    )
    for match in import_from_pattern.finditer(clean):
        clause = match.group(1).strip()
        specifier = match.group(2).strip()
        symbols: list[str] = []
        is_namespace = False
        ns_alias: str | None = None

        # Check namespace import: `* as name`
        ns_match = re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
        if ns_match:
            is_namespace = True
            ns_alias = ns_match.group(1)

        # Check named imports: `{ ... }`
        brace_match = re.search(r"\{([^}]*)\}", clause)
        if brace_match:
            for raw_item in brace_match.group(1).split(","):
                item = raw_item.strip()
                if not item:
                    continue
                parts = re.split(r"\s+as\s+", item)
                symbols.append(parts[0].strip())

        # Check default import identifier before `{` or `*` or standalone
        clause_without_braces = re.sub(r"\{[^}]*\}", "", clause)
        clause_without_ns = re.sub(r"\*\s+as\s+[A-Za-z_$][\w$]*", "", clause_without_braces)
        for token in clause_without_ns.split(","):
            tok = token.strip()
            if tok and re.match(r"^[A-Za-z_$][\w$]*$", tok):
                symbols.append("default")

        results.append((specifier, symbols, is_namespace, ns_alias))

    # Match side-effect imports: `import "./specifier.js"`
    side_effect_pattern = re.compile(r"\bimport\s+[\"']([^\"']+)[\"']")
    for match in side_effect_pattern.finditer(clean):
        results.append((match.group(1).strip(), [], False, None))

    return results


_IMPORT_STMT_PATTERN = re.compile(r"\bimport\s+[^;]*?[\"'][^\"']+[\"']")


def _namespace_property_reads(source: str, alias: str) -> set[str]:
    """Return every property name read off namespace-import ``alias`` in ``source``.

    Exclusions kept deliberately small (H2): import statements are blanked
    before scanning so the alias inside a specifier string (``from
    "./copy.js"``) is not mistaken for a property read, comments are removed
    via :func:`_strip_js_comments` (``//`` line comments and ``/* */`` blocks),
    and a name immediately followed by ``=`` (an assignment to the property,
    which does not occur in this codebase) is ignored.
    """
    scan_source = _IMPORT_STMT_PATTERN.sub("", _strip_js_comments(source))
    reads_pattern = re.compile(rf"\b{re.escape(alias)}\.([A-Za-z_$][A-Za-z0-9_$]*)(?!=)")
    return set(reads_pattern.findall(scan_source))


def test_es_module_import_export_graph_integrity() -> None:
    """Verify every relative ES module import resolves to a real file under
    web/app/ and every named symbol imported is exported by the target module."""
    js_files = _iter_production_js_files()
    exports_by_path: dict[Path, set[str]] = {}

    for path in js_files:
        source = path.read_text(encoding="utf-8")
        exports_by_path[path.resolve()] = _parse_exports(source)

    for path in js_files:
        source = path.read_text(encoding="utf-8")
        imports = _parse_imports(source)
        rel_importer = path.relative_to(REPO_ROOT)

        for specifier, imported_symbols, is_namespace, ns_alias in imports:
            assert specifier.startswith(("./", "../")), (
                f"{rel_importer}: unexpected non-relative module specifier {specifier!r}"
            )
            target_path = (path.parent / specifier).resolve()
            assert target_path.is_file(), (
                f"{rel_importer}: relative import {specifier!r} does not resolve to an existing file "
                f"(expected {target_path})"
            )
            assert target_path in exports_by_path, (
                f"{rel_importer}: target module {target_path} is outside tracked web/app/ production modules"
            )

            target_exports = exports_by_path[target_path]
            if is_namespace:
                assert target_exports, (
                    f"{rel_importer}: namespace import from {specifier!r} points to module with no exports"
                )
                assert ns_alias is not None
                for name in sorted(_namespace_property_reads(source, ns_alias)):
                    assert name in target_exports, (
                        f"{rel_importer}: reads {ns_alias}.{name} but "
                        f"{target_path.relative_to(REPO_ROOT)} does not export {name!r} "
                        f"(namespace import of {specifier!r})"
                    )
            for symbol in imported_symbols:
                assert symbol in target_exports, (
                    f"{rel_importer} imports symbol {symbol!r} from {specifier!r}, "
                    f"but {target_path.relative_to(REPO_ROOT)} only exports {sorted(target_exports)}"
                )


def _expand_keymap_entry_keys(keys_str: str) -> list[str]:
    """Expand a human-readable copy.js KEYMAP_ENTRIES.keys string into normalized
    lowercase KeyboardEvent.key strings."""
    cleaned = keys_str.strip()
    if cleaned.lower() == "esc":
        return ["escape"]
    if cleaned in _ARROW_KEY_MAP:
        return [_ARROW_KEY_MAP[cleaned]]
    if all(token in _ARROW_KEY_MAP for token in cleaned.split()):
        return [_ARROW_KEY_MAP[token] for token in cleaned.split()]

    # Range like "1 - 4" (with hyphen or \u2013 en-dash)
    range_match = re.match(r"^(\d+)\s*[\u2013\-]\s*(\d+)$", cleaned)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        return [str(n) for n in range(start, end + 1)]

    # Slash-separated alternatives like "J / K" or "] / ["
    if " / " in cleaned:
        parts = [part.strip() for part in cleaned.split(" / ") if part.strip()]
        expanded: list[str] = []
        for part in parts:
            expanded.extend(_expand_keymap_entry_keys(part))
        return expanded

    return [cleaned.lower()]


def test_keymap_overlay_and_handler_table_parity() -> None:
    """Verify KEYMAP_ENTRIES in web/app/copy.js and KEY_ACTIONS in
    web/app/components/keymap.js have 1-to-1 key coverage and non-empty actions."""
    copy_source = _strip_js_comments((WEB_APP_DIR / "copy.js").read_text(encoding="utf-8"))
    keymap_source = _strip_js_comments((WEB_APP_DIR / "components" / "keymap.js").read_text(encoding="utf-8"))

    # Extract KEYMAP_ENTRIES block from copy.js
    entries_block_match = re.search(
        r"export\s+const\s+KEYMAP_ENTRIES\s*=\s*Object\.freeze\(\s*\[([\s\S]*?)\]\s*\)",
        copy_source,
    )
    assert entries_block_match, "Could not locate KEYMAP_ENTRIES in web/app/copy.js"
    entries_block = entries_block_match.group(1)

    entry_pattern = re.compile(
        r"""\{\s*keys:\s*(?:"([^"]*)"|'([^']*)')\s*,\s*action:\s*(?:"([^"]*)"|'([^']*)')\s*\}"""
    )
    raw_matches = entry_pattern.findall(entries_block)
    documented_entries = [(m[0] or m[1], m[2] or m[3]) for m in raw_matches]
    assert documented_entries, "No entries parsed from KEYMAP_ENTRIES in web/app/copy.js"

    expanded_documented_keys: list[str] = []
    for keys_label, action_desc in documented_entries:
        assert keys_label.strip(), "Empty keys label in KEYMAP_ENTRIES"
        assert action_desc.strip(), f"Empty action description for {keys_label!r} in KEYMAP_ENTRIES"
        expanded_documented_keys.extend(_expand_keymap_entry_keys(keys_label))

    # Extract KEY_ACTIONS block from components/keymap.js
    actions_block_match = re.search(
        r"export\s+const\s+KEY_ACTIONS\s*=\s*Object\.freeze\(\s*\[([\s\S]*?)\]\s*\)",
        keymap_source,
    )
    assert actions_block_match, "Could not locate KEY_ACTIONS in web/app/components/keymap.js"
    actions_block = actions_block_match.group(1)

    action_row_pattern = re.compile(
        r"""\{\s*key:\s*(?:"([^"]*)"|'([^']*)')\s*,\s*action:\s*(?:"([^"]*)"|'([^']*)')"""
    )
    raw_action_matches = action_row_pattern.findall(actions_block)
    handler_rows = [(m[0] or m[1], m[2] or m[3]) for m in raw_action_matches]
    assert handler_rows, "No rows parsed from KEY_ACTIONS in web/app/components/keymap.js"

    handler_keys: list[str] = []
    for key_name, action_name in handler_rows:
        assert key_name.strip(), "Empty key in KEY_ACTIONS"
        assert action_name.strip(), f"Empty action string for key {key_name!r} in KEY_ACTIONS"
        handler_keys.append(key_name.lower())

    assert sorted(expanded_documented_keys) == sorted(handler_keys), (
        f"Keymap drift detected between copy.js KEYMAP_ENTRIES and components/keymap.js KEY_ACTIONS:\n"
        f"  Documented (expanded): {sorted(expanded_documented_keys)}\n"
        f"  Handlers:              {sorted(handler_keys)}"
    )


def _extract_classify_kind_evaluator():
    """Parse classifyKind(found) from web/app/views/drawer.js and construct an
    equivalent Python function directly from its JS conditional branches."""
    drawer_source = _strip_js_comments((WEB_APP_DIR / "views" / "drawer.js").read_text(encoding="utf-8"))
    match = re.search(
        r"export\s+function\s+classifyKind\s*\(\s*([A-Za-z_$][\w$]*)\s*\)\s*\{([\s\S]*?)\}",
        drawer_source,
    )
    assert match, "Could not find export function classifyKind in web/app/views/drawer.js"
    param_name = match.group(1)
    body = match.group(2)

    # Translate the JS function body statements into a Python function
    # to execute against test doubles.
    py_lines = [f"def classify_kind({param_name}):"]
    for raw_line in body.splitlines():
        line = raw_line.strip().rstrip(";")
        if not line:
            continue
        # Convert `if (!found.field) return "val"`
        m_if_not = re.match(
            rf"^if\s*\(\s*!{re.escape(param_name)}\.([A-Za-z_$][\w$]*)\s*\)\s*return\s+([\"'][^\"']+[\"'])$",
            line,
        )
        if m_if_not:
            prop, ret_literal = m_if_not.group(1), m_if_not.group(2)
            py_lines.append(f"    if not getattr({param_name}, {prop!r}):")
            py_lines.append(f"        return {ret_literal}")
            continue

        # Convert `if (found.field) return "val"`
        m_if = re.match(
            rf"^if\s*\(\s*{re.escape(param_name)}\.([A-Za-z_$][\w$]*)\s*\)\s*return\s+([\"'][^\"']+[\"'])$",
            line,
        )
        if m_if:
            prop, ret_literal = m_if.group(1), m_if.group(2)
            py_lines.append(f"    if getattr({param_name}, {prop!r}):")
            py_lines.append(f"        return {ret_literal}")
            continue

        # Convert `return "val"`
        m_ret = re.match(r"^return\s+([\"'][^\"']+[\"'])$", line)
        if m_ret:
            py_lines.append(f"    return {m_ret.group(1)}")
            continue

        raise AssertionError(f"Unrecognized JS statement in classifyKind: {raw_line!r}")

    namespace: dict[str, object] = {}
    exec("\n".join(py_lines), namespace)
    return namespace["classify_kind"]


def test_classify_kind_matches_backend_contract() -> None:
    """Verify classifyKind(eligible, rated) in web/app/views/drawer.js matches
    the backend contract:
      - not eligible -> 'gated'
      - eligible and not rated -> 'unrated'
      - eligible and rated -> 'rated'
    """
    classify_kind = _extract_classify_kind_evaluator()

    for eligible in (False, True):
        for rated in (False, True):
            row = SimpleNamespace(eligible=eligible, rated=rated)
            expected = "gated" if not eligible else ("unrated" if not rated else "rated")
            actual = classify_kind(row)
            assert actual == expected, (
                f"classifyKind mismatch for eligible={eligible}, rated={rated}: "
                f"expected {expected!r}, got {actual!r}"
            )
