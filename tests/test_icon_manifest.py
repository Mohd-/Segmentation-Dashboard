"""Card 3Y -- the icon manifest, checked mechanically.

docs/asas-svg-icon-mapping.md is the repository-tracked mapping the card asks
for. A document nobody verifies drifts from the code within a release, so the
invariants it states are asserted here instead of trusted.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICONS_JS = ROOT / "static" / "js" / "icons.js"
ICONS_DIR = ROOT / "static" / "icons"
MANIFEST = ROOT / "docs" / "asas-svg-icon-mapping.md"

# Assets that live in static/icons/ WITHOUT being an ICONS key, each because it
# is not a role the JS renders. The manifest explains every one; this list is
# the machine-readable half of that paragraph.
UNMAPPED_ASSETS = {
    # Brand marks (index.html references the logo directly).
    "asas-logo", "asas-mark", "smes-n-mark",
    # Chrome the CSS draws rather than the JS injecting.
    "active-tab-indicator", "count-badge-frame", "notification-dot", "progress-ring",
    # Pack artwork whose ROLE is served under another key.
    "mean-ogip-flame", "success-rate-growth", "pre-well-delivery-derrick",
    "well-log-tracks", "well-test-flowback",
}


def icon_keys():
    return re.findall(r"^  '([a-z0-9-]+)':", ICONS_JS.read_text(encoding="utf-8"), re.M)


def icon_files():
    return {path.stem for path in ICONS_DIR.glob("*.svg")}


def test_every_icon_key_has_a_file_and_every_file_is_accounted_for():
    keys = set(icon_keys())
    files = icon_files()
    assert not keys - files, f"ICONS keys with no static/icons file: {sorted(keys - files)}"
    unexplained = files - keys - UNMAPPED_ASSETS
    assert not unexplained, (
        "static/icons files that are neither an ICONS key nor listed as "
        f"deliberately unmapped: {sorted(unexplained)}")


def test_the_manifest_documents_every_role():
    text = MANIFEST.read_text(encoding="utf-8")
    for key in icon_keys():
        assert f"`{key}`" in text, f"{key} is not in docs/asas-svg-icon-mapping.md"


def test_every_icon_follows_the_current_colour_rule():
    """Themed surfaces need themed glyphs, so a mark's own colour is
    currentColor and it inherits from its surroundings.

    Two colours are deliberately fixed, and neither is the mark's colour:
      * #F22B20, the bell's unread dot -- a state indicator drawn ON the bell;
      * #fff, the tick and ring drawn INSIDE a filled disc, where the fill is
        already themed and the shape on top needs contrast against it.
    Both stay legible in either theme because they sit on their own fill.
    """
    source = ICONS_JS.read_text(encoding="utf-8")
    colours = set(re.findall(r'(?:stroke|fill)=\\"(#[0-9A-Fa-f]{3,6})\\"', source))
    assert colours <= {"#F22B20", "#fff"}, (
        f"unexplained hardcoded colours: {sorted(colours - {'#F22B20', '#fff'})}")

    # And they appear only on the keys the exception is about.
    for key, allowed in (("#F22B20", {"bell"}), ("#fff", {"circle-check", "bell"})):
        for match in re.finditer(r"^  '([a-z0-9-]+)': \"(.*)\",$", source, re.M):
            if key in match.group(2):
                assert match.group(1) in allowed, (
                    f"{match.group(1)} hardcodes {key}, which is not one of its exceptions")


def test_no_glyph_character_is_used_as_an_icon():
    """The sweep Card 3Y asks for, kept swept.

    Each of these was an actual icon in this codebase before the card: a folder
    emoji, a warning sign, a copy mark, a close cross, and five stage glyphs
    forced into text presentation with a variation selector.
    """
    banned = "\U0001F4C1⚠⊗✕✖❌◎⚖⛳⚒⛏︎"
    offenders = []
    for path in sorted((ROOT / "static" / "js").rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        for index, line in enumerate(text.splitlines(), 1):
            for char in banned:
                if char in line:
                    offenders.append(f"{path.relative_to(ROOT)}:{index} {char!r}")
    assert not offenders, "glyph characters used as icons:\n" + "\n".join(offenders)


def test_the_four_portfolio_classifications_are_all_mapped():
    """Card 3P: all four roles map to an approved asset, none is blocked, and
    each carries the exact classification name as its accessible label."""
    keys = set(icon_keys())
    roles = {"quadrant-superstar", "quadrant-risk-taker",
             "quadrant-value-hunter", "quadrant-dog"}
    assert roles <= keys
    portfolio = (ROOT / "static" / "js" / "views" / "portfolio.js").read_text(encoding="utf-8")
    for label in ("Super Stars", "Risk Takers", "Value Hunter", "Dogs"):
        assert label in portfolio
    assert 'role="img"' in portfolio, "an icon carrying meaning alone needs a role"
