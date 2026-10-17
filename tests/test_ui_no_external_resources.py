"""Zero-external-request check for `web/app/` (06 §8 checklist item 10).

`web/app/` is many files (styles/*.css, *.js, components/*.js, views/*.js), so
this test applies the rule to every file actually served.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB_APP = Path(__file__).resolve().parent.parent / "web" / "app"

#: Every file this packet ships that a browser will actually fetch.
SERVED_EXTENSIONS = {".html", ".css", ".js", ".mjs"}

#: The char right after "//" must be alphanumeric (a host name never starts
#: with punctuation) — this is what tells a real "//example.com" URL apart
#: from a `//:`-prefixed doc comment (this file's own house style; see
#: web/app/state.js), which would otherwise false-positive.
EXTERNAL_URL_RE = re.compile(r"""(https?:)?//[a-zA-Z0-9][^\s'"()]*""")
#: The only "//" this codebase may legitimately contain: outbound
#: `https://github.com/<owner>/<name>` anchor links rendered in the repository
#: drawer (`web/app/copy.js::drawerGithubUrl`), which the browser never fetches
#: automatically as a subresource. Kept as an explicit allowlist so any other
#: external URL is still flagged.
ALLOWED_URL_SUBSTRINGS: tuple[str, ...] = ("https://github.com/",)
#: Per-file exceptions (keyed by path under web/app/), each an origin one module
#: must name. Only the opt-in live check (06 §3 C, `web/app/livecheck.js`) may
#: name api.github.com: it contacts it solely when the visitor presses its
#: button, never on load, so the page still makes no external request on its own.
#: copy.js may print the site's own address in a sentence (the methodology says
#: where a project's page is): text a reader reads, never a request.
PER_FILE_ALLOWED: dict[str, tuple[str, ...]] = {
    "livecheck.js": ("https://api.github.com",),
    "copy.js": ("https://porchlamp.org",),
}
#: The GitHub API origin as an address (scheme-relative too), not as prose.
GITHUB_API_ADDRESS_RE = re.compile(r"(https?:)?//api\.github\.com", re.IGNORECASE)


#: Link-preview, canonical, alternate (hreflang/feed), and JSON-LD structured
#: data tags must carry absolute URLs -- crawlers do not resolve relative ones
#: -- yet the browser never fetches them as subresources. Only the content/href
#: of such an inert SEO tag is exempt; the same URL anywhere else in a served
#: file is still a violation.
LINK_PREVIEW_META_RE = re.compile(
    r"""<meta\s+(?:property="og:[\w:]+"|name="twitter:[\w:]+")\s+content="[^"]*">|<link\s+rel="(?:canonical|alternate)"[^>]*href="[^"]*">|<script\s+type="application/ld\+json">[\s\S]*?</script>"""
)
#: A page's own Content-Security-Policy names the origins requests are RESTRICTED
#: to; the tag itself makes no request.
CSP_META_RE = re.compile(r"""<meta\s+http-equiv="Content-Security-Policy"\s+content="[^"]*">""")


def served_files() -> list[Path]:
    return [p for p in WEB_APP.rglob("*") if p.is_file() and p.suffix in SERVED_EXTENSIONS]


def scannable_text(text: str) -> str:
    """The text a browser could turn into a request: link-preview <meta>, canonical <link> and CSP <meta> tags removed."""
    return CSP_META_RE.sub("", LINK_PREVIEW_META_RE.sub("", text))


def url_allowed(url: str, relative_path: str) -> bool:
    """An anchor-link substring, or for the files `PER_FILE_ALLOWED` names, exactly
    their origin or a path under it (so "https://api.github.com.example" is not)."""
    if any(allowed in url for allowed in ALLOWED_URL_SUBSTRINGS):
        return True
    return any(url == origin or url.startswith(f"{origin}/") for origin in PER_FILE_ALLOWED.get(relative_path, ()))


class TestNoExternalResources:
    def test_every_served_file_exists(self) -> None:
        files = served_files()
        assert len(files) >= 15, f"expected at least 15 served files under web/app/, found {len(files)}"

    def test_no_external_script_or_style_tags(self) -> None:
        for path in served_files():
            if path.suffix != ".html":
                continue
            html = scannable_text(path.read_text())
            assert "<script src=\"http" not in html, f"{path}: external <script src>"
            assert "<link" not in html or 'href="http' not in html, f"{path}: external <link>"

    def test_no_external_urls_anywhere(self) -> None:
        violations: list[str] = []
        for path in served_files():
            relative_path = path.relative_to(WEB_APP).as_posix()
            for match in EXTERNAL_URL_RE.finditer(scannable_text(path.read_text())):
                url = match.group(0)
                if not url_allowed(url, relative_path):
                    violations.append(f"{relative_path}: {url}")
        assert not violations, "external URL(s) found:\n" + "\n".join(violations)

    def test_only_the_live_check_module_names_the_github_api(self) -> None:
        # Copy may SAY "api.github.com" (the button's note does); only
        # livecheck.js may hold it as an address a request could be built from.
        holders = sorted(
            path.relative_to(WEB_APP).as_posix()
            for path in served_files()
            if GITHUB_API_ADDRESS_RE.search(scannable_text(path.read_text()))
        )
        assert holders == ["livecheck.js"], holders

    def test_no_google_fonts_or_webfont_at_import(self) -> None:
        for path in served_files():
            text = path.read_text()
            assert "fonts.googleapis" not in text, f"{path}: Google Fonts reference"
            assert "fonts.gstatic" not in text, f"{path}: Google Fonts reference"
            assert "@font-face" not in text, f"{path}: a @font-face rule always names an external or embedded font file — this project uses the system font stack only"

    def test_no_cdn_hosts(self) -> None:
        cdn_hosts = ("cdnjs.cloudflare.com", "cdn.jsdelivr.net", "unpkg.com", "esm.sh", "jquery.com")
        for path in served_files():
            text = path.read_text()
            for host in cdn_hosts:
                assert host not in text, f"{path}: references CDN host {host}"

    def test_files_are_valid_utf8_with_no_nul_bytes(self) -> None:
        # A regression guard: this exact defect (stray NUL bytes silently
        # corrupting a template-literal line) was caught and fixed while
        # building this package.
        for path in served_files():
            data = path.read_bytes()
            assert b"\x00" not in data, f"{path}: contains a NUL byte"
            data.decode("utf-8")  # raises UnicodeDecodeError on invalid UTF-8


def test_link_preview_exemption_covers_only_preview_meta_tags() -> None:
    url = "https://example.org/og.png"
    exempt = (
        f'<meta property="og:image" content="{url}">\n'
        f'<meta name="twitter:image" content="{url}">\n'
        f'<link rel="canonical" href="{url}">\n'
        f'<meta http-equiv="Content-Security-Policy" content="default-src \'self\'; connect-src \'self\' {url}">'
    )
    assert not EXTERNAL_URL_RE.search(scannable_text(exempt))
    for still_flagged in (
        f'<img src="{url}">',
        f'<meta http-equiv="refresh" content="0; url={url}">',
        f'<link rel="preload" href="{url}">',
        f'<link rel="stylesheet" href="{url}">',
        f'const u = "{url}";',
    ):
        assert EXTERNAL_URL_RE.search(scannable_text(still_flagged)), still_flagged


def test_per_file_allowance_is_one_exact_origin_in_one_file() -> None:
    assert url_allowed("https://api.github.com", "livecheck.js")
    assert url_allowed("https://api.github.com/rate_limit", "livecheck.js")
    for look_alike in ("https://api.github.com.example.org/x", "https://api.github.community", "http://api.github.com"):
        assert not url_allowed(look_alike, "livecheck.js"), look_alike
    assert not url_allowed("https://api.github.com", "app.js")
    assert not url_allowed("https://api.github.com", "views/livecheck.js")
    assert url_allowed("https://porchlamp.org/projects/<owner>/<name>/", "copy.js")
    assert not url_allowed("https://porchlamp.org/projects/<owner>/<name>/", "views/methodology.js")
    assert not url_allowed("https://porchlamp.org.example.com/x", "copy.js")
