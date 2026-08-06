# ASAS SVG icon mapping

Card 3Y's repository-tracked manifest: one row per **semantic role**, not per
filename. A role is what the mark MEANS on screen; the file is how it is drawn.

## How icons are rendered

`static/js/icons.js` exports `ICONS`, a map of key to inline SVG string.
Consumers index it directly (`ICONS['folder']`); there is no wrapper component.
`static/index.html` also pastes a handful of the same strings verbatim, because
the header and primary navigation exist before any module runs.

**Invariant: one `static/icons/<key>.svg` per `ICONS` key.** The files are the
editable source; `icons.js` is what ships. They are checked against each other
below.

Every icon is drawn with `stroke="currentColor"`, so it inherits its
surrounding text colour and inverts with the theme for free. The two deliberate
exceptions are the bell's unread dot (`#F22B20` — a state indicator, not part
of the glyph) and the brand marks.

Sizing is by class, never inline: `.lucide` at the shared 24px box, with
per-surface overrides (`.lucide-wide` for the 48x24 composite, 16-18px inside
rails and folder cards).

## The manifest

| Role key | Meaning | Surfaces | Approved SVG | Accessible name | Consumers |
|---|---|---|---|---|---|
| `sun` | Switch to light theme | Gear menu | `static/icons/sun.svg` | Decorative (label says it) | — |
| `moon` | Switch to dark theme | Gear menu | `static/icons/moon.svg` | Decorative (label says it) | — |
| `settings` | Open settings | Header, BPE summary | `static/icons/settings.svg` | aria-label on the button | `index.html`, `views/business-plan.js` |
| `bell` | Notifications, unread state | Header | `static/icons/bell.svg` | aria-label on the button | `index.html` |
| `log-out` | Sign out | Gear menu | `static/icons/log-out.svg` | Decorative (label says it) | — |
| `plus` | Add a row / record | Boards, BPE forms | `static/icons/plus.svg` | Decorative (label says it) | `views/business-plan.js` |
| `chevron-down` | Disclosure, closed | Filters, dropdowns, table heads | `static/icons/chevron-down.svg` | Decorative | `views/board-widgets.js`, `views/business-plan.js`, `views/map-view.js`, `views/portfolio.js` |
| `chevron-up` | Disclosure, open | Filters, dropdowns | `static/icons/chevron-up.svg` | Decorative | `views/map-view.js`, `views/portfolio.js` |
| `x` | Remove / close | Repeatable rows, BPE formations | `static/icons/x.svg` | aria-label on the button | `index.html`, `views/business-plan.js`, `views/detail-form.js` |
| `minus` | Collapse | Map controls | `static/icons/minus.svg` | Decorative | — |
| `arrow-left` | Back | Detail pages | `static/icons/arrow-left.svg` | Decorative (label says it) | `index.html`, `views/business-plan.js`, `views/project-editor.js` |
| `arrow-up` | Back to the board above | Lead detail | `static/icons/arrow-up.svg` | Decorative (label says it) | `index.html` |
| `copy` | Copy to clipboard | Folder cards | `static/icons/copy.svg` | aria-label on the button | `views/business-plan.js`, `views/detail-form.js`, `views/detail.js`, `views/lead-assessment.js`, `views/staking-letters.js` |
| `folder` | A shared folder | Folder cards (5 surfaces) | `static/icons/folder.svg` | Decorative (path is the content) | `views/business-plan.js`, `views/detail-form.js`, `views/detail.js`, `views/lead-assessment.js`, `views/staking-letters.js` |
| `alert` | Something failed to load | Map layer list | `static/icons/alert.svg` | aria-label on the span | `views/map-view.js` |
| `rig-trend` | Segment Maturation | Primary navigation | `static/icons/rig-trend.svg` | Tab label | `index.html` |
| `portfolio` | Portfolio Analysis | Primary navigation | `static/icons/portfolio.svg` | Tab label | `index.html` |
| `bp-execution` | Business Plan Execution | Primary navigation | `static/icons/bp-execution.svg` | Tab label | `index.html` |
| `map-pin` | Map | Primary navigation | `static/icons/map-pin.svg` | Tab label | `index.html` |
| `audit-trail` | Audit Trail | Primary navigation | `static/icons/audit-trail.svg` | Tab label | `index.html` |
| `calculator` | Calculator | Primary navigation | `static/icons/calculator.svg` | Tab label | `index.html` |
| `growth-chart` | Growth / trend | KPI tiles | `static/icons/growth-chart.svg` | Decorative | — |
| `clipboard-check` | Lead Assessment stage | Board columns, detail rail | `static/icons/clipboard-check.svg` | Decorative (stage named beside it) | — |
| `clipboard-steps` | Pre-Drilling / Well Delivery stage | BPE board, detail rail | `static/icons/clipboard-steps.svg` | Decorative (stage named beside it) | — |
| `gauge` | Risk Analysis / Post-Testing stage | Board columns, detail rail | `static/icons/gauge.svg` | Decorative (stage named beside it) | — |
| `rig` | Pre-Well Delivery / Post-Drilling stage | Board columns, detail rail | `static/icons/rig.svg` | Decorative (stage named beside it) | `index.html` |
| `circle-check` | Tracked item: completed | Board cards, filter menus | `static/icons/circle-check.svg` | aria-label on the dot | `views/business-plan.js` |
| `circle-minus` | Tracked item: pending approval | Board cards, filter menus | `static/icons/circle-minus.svg` | aria-label on the dot | `views/business-plan.js` |
| `circle` | Tracked item: not started | Board cards, filter menus | `static/icons/circle.svg` | aria-label on the dot | `views/business-plan.js` |
| `calendar-days` | Rig days / dates | KPI tiles | `static/icons/calendar-days.svg` | Decorative | — |
| `flag` | Rig target | KPI tiles | `static/icons/flag.svg` | Decorative | — |
| `flame` | Mean OGIP | KPI tiles | `static/icons/flame.svg` | Decorative | — |
| `target` | Business plan target | KPI tiles | `static/icons/target.svg` | Decorative | `views/map-view.js` |
| `chart-scatter` | Cross plot | Portfolio | `static/icons/chart-scatter.svg` | Decorative | `views/portfolio-waterfall.js` |
| `file-spreadsheet` | Export to Excel | Gear menu | `static/icons/file-spreadsheet.svg` | Decorative (label says it) | — |
| `user` | Assignee | Board cards, assignee chips | `static/icons/user.svg` | Decorative (name beside it) | — |
| `quadrant-superstar` | Portfolio class: Super Stars | Portfolio name cell | `static/icons/quadrant-superstar.svg` | role=img, aria-label = class name | — |
| `quadrant-risk-taker` | Portfolio class: Risk Takers | Portfolio name cell | `static/icons/quadrant-risk-taker.svg` | role=img, aria-label = class name | — |
| `quadrant-value-hunter` | Portfolio class: Value Hunter | Portfolio name cell | `static/icons/quadrant-value-hunter.svg` | role=img, aria-label = class name | — |
| `quadrant-dog` | Portfolio class: Dogs | Portfolio name cell | `static/icons/quadrant-dog.svg` | role=img, aria-label = class name | — |

## Retained, replaced and blocked

**Nothing is retained as a legacy icon.** Card 3Y's sweep is complete: there is
no emoji, icon font, bitmap, Unicode symbol or improvised glyph left acting as
an icon anywhere in `static/js` or `static/index.html`. What was replaced, and
why each mattered:

| Was | Now | Why |
|---|---|---|
| Folder emoji on five folder cards | `folder` | An emoji renders in each platform's own colour and weight; it never matched the monoline set around it, and it could not follow the theme. |
| `⚠` in the map's layer errors | `alert` | Same reason, plus it carried no accessible name of its own. |
| `⧉` on every copy-folder button | `copy` | It was a text character standing in for the icon the rest of the app already had. |
| `✕` on repeatable-row removal | `x` | Ditto -- `ICONS.x` existed and was not used here. |
| `◎ ⚖ ⛳ ⚒ ⛏ ✓` as detail-rail stage marks | `clipboard-check`, `gauge`, `rig`, `clipboard-steps` | These were emoji forced into text presentation with a variation selector -- a workaround for being emoji at all. They now use the SAME approved glyphs both boards already use for the same stages, so a stage reads identically wherever it appears. |

**Navigation roles.** `references/Card_1A_ASAS_Header_Approved_Baseline.md` is
not present in this repository, so its table could not be read directly. Its
roles are recorded in Card 3Y as Briefcase (Portfolio Analysis), Target
(Business Plan Execution) and Shield (Audit Trail). The approved pack's
artwork under those keys **is** a briefcase, a target and a shield
respectively; only the KEY NAMES differ (`portfolio`, `bp-execution`,
`audit-trail`), which are internal identifiers and reach no screen. No
mismatch to resolve.

**Assets present but unmapped.** These live in `static/icons/` and are not
`ICONS` keys, because they are not roles the JS renders: brand marks
(`asas-logo`, `asas-mark`, `smes-n-mark`), chrome fragments the CSS draws
(`active-tab-indicator`, `count-badge-frame`, `notification-dot`,
`progress-ring`), and pack artwork whose role is served by another key
(`mean-ogip-flame` -> `flame`, `success-rate-growth` -> `growth-chart`,
`pre-well-delivery-derrick` -> `rig`, `well-log-tracks`,
`well-test-flowback`). They are kept as source, not shipped as icons.

## Card 3P -- the four Portfolio classifications

All four roles are mapped and in use; none is blocked. They render beside the
well name in the Portfolio table (`views/portfolio.js` `quadrantMarkMarkup`),
never as a column of their own, and they are the one place in the app where an
icon carries meaning ALONE -- so each has `role="img"` and an `aria-label`
holding the exact classification name. A record missing either measure gets no
mark at all rather than a fifth "unclassified" glyph.

The classification itself is unchanged: `quadrantOf` in
`views/portfolio-analysis.js` decides it, from the same thresholds the cross
plot draws, and no icon influences it.

## Keeping this file true

`tests/test_icon_manifest.py` asserts the invariant mechanically: every `ICONS`
key has a file, every file is either a key or listed above as deliberately
unmapped, and no glyph character has crept back into the JS. If that test
fails, this document is what needs updating.
