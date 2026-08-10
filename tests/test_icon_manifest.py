"""Mechanical contract for the pinned, official Lucide icon subsystem."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICONS_JS = ROOT / "static" / "js" / "icons.js"
ICONS_DIR = ROOT / "static" / "icons"
MANIFEST = ROOT / "docs" / "asas-svg-icon-mapping.md"

LUCIDE_VERSION = "1.27.0"
ALIASES = {
    "alert": "triangle-alert",
    "rig-trend": "workflow",
    "portfolio": "briefcase",
    "bp-execution": "target",
    "audit-trail": "shield-check",
    "growth-chart": "trending-up",
    "clipboard-steps": "clipboard-list",
    "rig": "drill",
    "quadrant-superstar": "star",
    "quadrant-risk-taker": "dices",
    "quadrant-value-hunter": "search",
    "quadrant-dog": "dog",
}

# SHA-256 of each pristine SVG in the published lucide-static 1.27.0 tarball.
UPSTREAM_SHA256 = {
    "arrow-left": "db25b4c2ffeab88a3837a8682f96d2f745775a6a8025771ddf10e428e4a8446a",
    "arrow-left-right": "cb5726abb3859cd7ae7d486490897080bab98244dd8ed9c8f80ad74b149b2bb9",
    "arrow-right": "9c28bcba969fca2db8cb608207c5b5c70bfdd51b671de783121dff9f38f37ee3",
    "arrow-up": "a4b33b471e536de3aff235f9347fa6cba6d8a13e56b47ca56dd482228a113044",
    "bell": "b4ce939d5007c8341c9a6e7b18fdbbab2dc0943c2f80ff11cb0bc97bdf260b97",
    "briefcase": "94f14de0a61286747fc697eb205055a419fada0e56bdc0bd2bf4f870aff110af",
    "calculator": "1a22a967a43024a75fa2132fb5199440c9b5782cf327dd97f79dcf9f43ab5fd0",
    "calendar-days": "b0614d5ba13cbd7a986386e4e1da4c443be0d0d3731a9186c61f56763a8b70e5",
    "chart-scatter": "7c0c3d31740307252b6006a90a810b3a980c7981a9d712f535103c8d5221fe8e",
    "chevron-down": "731bb0d0b17ece722ceb69b4dca7b3a116bafe0308bae96135a2f968790f72a0",
    "chevron-right": "124f696e152c8e34b2dc3988975befdab55bdb5f4528345e3064cfc92d77bc1f",
    "chevron-up": "b521236da74cb746c6b88cd1814ae953138b8d550c70f81cf98fd6d5ad796a75",
    "circle": "4cf9f7d81c3fca9a0d169e1ecff811ca3f1e99c8c3fb1cf2b5457aeec501334e",
    "circle-check": "313a4db4353ab2d843f8d7db31d6319c5a7707cf52cb5d1686f02ade4c0068dc",
    "circle-minus": "0c202eb35cedc3ed86087cdcdee9bb279c374cc186dbcae7c8176e981339f938",
    "clipboard-check": "08a99adfd05b0ef5d0979ccbea31931d0fd3a8f1a2ee1dece264a6642aa33091",
    "clipboard-list": "0dbb113011420e6b80f807b2c06fd0885cceb66e7c1398df28915f0c76d04e30",
    "copy": "a2c6dd8a29fcc46173bd51cba33ec98bde5f0263af42149873edcd4bd4155a43",
    "dices": "a1e97850a39cb44c6a80e0763ebbb2465a0fa054550b6645282e92aceb4e1ed5",
    "dog": "0a0bd4f427c533ac8d43fb143f7fe3fe7b46d4a87e7f8f88715ea85e9ded99cd",
    "drill": "117f071717cb8961b8053c55a6788dd6db549586c4580267b37632b06d84b09c",
    "file-spreadsheet": "465510cb263821714ca05acc6464a0748a19fbc0fdf945e5572a432f9f00ae95",
    "flag": "8751cabc71f8a39b4885765598b047961c4d5236235bb0c0f0fab1144652f8cb",
    "flame": "1e08bad84bb5dac8c7a1288a3aa131139dcc8cdc7c6b4314f87f29b11dbcbb21",
    "folder": "70b3353c40341e637b5480d37f9f2382f334d7f6f6ae29f957402e290445e725",
    "gauge": "22d4937f6c9cee8e0a8480b21b7fec32a72f5a30c228ac6f2dae13ef6b4d81ad",
    "log-out": "0a888146eabae0b51204c17b7b2387a1f663efdd52ae2a246a6bac0e6927e207",
    "map-pin": "df63fd1d077d83bd250904057e643cd3db71673558c1c6f3cf472f19b465aef1",
    "maximize-2": "612c90fdee0f5e916dab41717fe9679d06584360e0dbc001a71fa107550a2d3c",
    "minus": "4e2588967fa992cec4df8a3476645e91db796838b6523d9af095f82753e1a340",
    "moon": "ed2822f6dfdb6feebd18ccc00851ea6088e3fec0eab6e9c4f0d7bc24d854b7d0",
    "plus": "7fde3025581d69b7d0229536c22f59efb11786df082a0a5f44eba78fef13a7d5",
    "search": "e62a1359f6b47091cd27048086ca56855b6c066c8d580f6c366a4f4cae4be9dd",
    "settings": "d3a23d97022d1c9b98237881155ab5cd5bc4ad9798fc8909956d837bb906a792",
    "shield-check": "6eb34dbd171307ff58b98530712338b745e9562824baff38bfd08a9f74125b54",
    "star": "de5a5befe984a58bf46fae21e95cf77bb2ea81d65448c13053c7cb7e5cf568e6",
    "sun": "eb28466e8babab491190139a90722306212c84878ce30cebd0ad8e1c70df1751",
    "target": "7069694cd0807f25342b42788efc35544d96a50af688dec87c37a7c86e397758",
    "trending-up": "6e01f912caff582cda1d9965a5b7740aa1d82de8f9929614030445deffd66660",
    "triangle-alert": "145a6febdf3f77b407967c6c4320aca2d706917e5030aafe79d5243cc63e0992",
    "user": "1195a3fa1a6245c5dae012be81e0f1eae88c7addb0a1ecb4bb09cc360307dfd3",
    "workflow": "39878d9e1cb4d155ff5b61e44ea246c711e7dad131f16b82c40b43a9494ebaa7",
    "x": "91838db27fa28755a0c4d78662aadf275e6cb8ac06d30c1941e6c9d29c976522",
}

# Drill is intentionally supplied by the Font Awesome set used by the current
# UI. Keep it pinned as an approved alternate rather than treating it as a
# Lucide asset.
APPROVED_ALTERNATE_SHA256 = {
    "drill": "9c84ff84fb142d25ee1ee90269875e48694b9cf206edaadae865a2fb62ef3bc7",
}


def _source() -> str:
    return ICONS_JS.read_text(encoding="utf-8")


def _icon_block() -> str:
    return _source().split("export var ICONS = {", 1)[1].split("\n};", 1)[0]


def _inline_icons() -> dict:
    icons = {}
    for key, escaped in re.findall(r"^  '([a-z0-9-]+)': \"(.*)\",$", _icon_block(), re.M):
        icons[key] = json.loads('"' + escaped + '"')
    return icons


def _inline_markup(source: str) -> str:
    source = re.sub(r"^<!--[^\n]*-->\n", "", source)
    source = re.sub(r"\n\s*", " ", source).replace("> <", "><").strip()
    return source.replace(
        "<svg ", '<svg aria-hidden="true" focusable="false" ', 1)


def test_assets_are_the_exact_pinned_lucide_sources():
    assert f"LUCIDE_VERSION = '{LUCIDE_VERSION}'" in _source()
    actual_files = {path.stem for path in ICONS_DIR.glob("*.svg")}
    assert actual_files == set(UPSTREAM_SHA256) | {"asas-logo"}
    assert (ICONS_DIR / "LICENSE").is_file()
    assert "ISC License" in (ICONS_DIR / "LICENSE").read_text(encoding="utf-8")

    for key, expected in UPSTREAM_SHA256.items():
        asset = ICONS_DIR / f"{key}.svg"
        text = asset.read_text(encoding="utf-8")
        if key in APPROVED_ALTERNATE_SHA256:
            assert hashlib.sha256(asset.read_bytes()).hexdigest() == APPROVED_ALTERNATE_SHA256[key], key
            assert "Font Awesome Free" in text
            continue
        assert hashlib.sha256(asset.read_bytes()).hexdigest() == expected, key
        assert text.startswith(f"<!-- @license lucide-static v{LUCIDE_VERSION} - ISC -->")
        assert f'class="lucide lucide-{key}"' in text


def test_inline_map_is_a_lossless_accessibility_transform_of_assets():
    inline = _inline_icons()
    assert set(inline) == set(UPSTREAM_SHA256)
    for key, markup in inline.items():
        asset = (ICONS_DIR / f"{key}.svg").read_text(encoding="utf-8")
        if key in APPROVED_ALTERNATE_SHA256:
            assert "Font Awesome Free" in markup
            assert 'fill="currentColor"' in markup
            continue
        assert markup == _inline_markup(asset), key
        assert 'aria-hidden="true"' in markup
        assert 'focusable="false"' in markup
        assert 'stroke="currentColor"' in markup


def test_only_documented_aliases_exist_and_resolve_to_official_keys():
    alias_block = _source().split("export var ICON_ALIASES = {", 1)[1].split("\n};", 1)[0]
    actual = dict(re.findall(r"^  '([a-z0-9-]+)': '([a-z0-9-]+)'", alias_block, re.M))
    assert actual == ALIASES
    assert set(actual.values()) <= set(UPSTREAM_SHA256)


def test_manifest_documents_every_key_and_alias():
    text = MANIFEST.read_text(encoding="utf-8")
    for key in set(UPSTREAM_SHA256) | set(ALIASES):
        assert f"`{key}`" in text, f"{key} is missing from the icon mapping"


def test_asas_logo_is_the_only_non_lucide_icon_artwork():
    for asset in ICONS_DIR.glob("*.svg"):
        if asset.name == "asas-logo.svg" or asset.stem in APPROVED_ALTERNATE_SHA256:
            continue
        assert "@license lucide-static" in asset.read_text(encoding="utf-8")


def test_static_html_uses_only_canonical_lucide_placeholders():
    """The shell hydrates official icons from the checked inline map."""
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "<svg" not in html, "static HTML must not carry private SVG paths"
    keys = re.findall(r'data-lucide-icon="([a-z0-9-]+)"', html)
    assert keys, "the static shell should declare its icon placeholders"
    assert set(keys) <= set(UPSTREAM_SHA256)


def test_production_views_do_not_draw_private_svg_icons():
    """Only genuine data visualizations may author SVG outside icons.js."""
    allowed_visualizations = {
        Path("static/js/views/board-widgets.js"),  # circular progress chart
        Path("static/js/views/portfolio-analysis.js"),  # portfolio cross plot
    }
    offenders = set()
    for path in (ROOT / "static" / "js").rglob("*.js"):
        if path == ICONS_JS:
            continue
        if "<svg" in path.read_text(encoding="utf-8"):
            relative = path.relative_to(ROOT)
            if relative not in allowed_visualizations:
                offenders.add(relative)
    assert not offenders, f"private SVG markup outside icon subsystem: {sorted(offenders)}"


def test_unicode_glyphs_are_not_used_as_interactive_controls():
    offenders = []
    button_glyph = re.compile(
        r"<button\b[^>]*>\s*(?:<span[^>]*>\s*)?[+×−✕✖❌⇄←→›‹⌄⌃]"
        r"(?:\s*</span>)?\s*</button>", re.S)
    js_control = re.compile(r"(?:innerHTML|textContent)\s*=.*[×−✕✖❌←→›‹⌄⌃]")
    css_content = re.compile(r"content:\s*['\"]\s*[+×−✕✖❌⇄←→›‹⌄⌃]\s*['\"]")

    for path in sorted((ROOT / "static").rglob("*")):
        if path.suffix not in {".html", ".js", ".css"} or "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        patterns = [button_glyph]
        if path.suffix == ".js":
            patterns.append(js_control)
        if path.suffix == ".css":
            patterns.append(css_content)
        for pattern in patterns:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line}: {match.group(0)[:80]}")
    assert not offenders, "glyph characters used as controls:\n" + "\n".join(offenders)
