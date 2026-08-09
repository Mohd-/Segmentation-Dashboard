# ASAS Lucide icon mapping

All visible interface icons use the official `lucide-static` **v1.27.0** SVG
sources under the ISC license. The pinned npm tarball has SHA-512:

`7afd56b9f9be46c2a943a09f992d0f81817e35307569a96d7c4fa3520058181f0f5796f9aaa1e9cec4cb3ec31615115da5532feba7882bcfd77fd77c725dc784`

`static/icons/<key>.svg` contains each pristine upstream source, including its
Lucide license comment. `static/js/icons.js` contains the same markup inlined
with only `aria-hidden="true"` and `focusable="false"` added. Inline SVG lets
the official `currentColor` stroke follow both themes. The interactive control
or adjacent visible text supplies the accessible name.

`static/icons/asas-logo.svg` is the sole artwork exception. It remains the
ASAS brand logo and is never exposed through `ICONS` as a general-purpose icon.

## Semantic roles

| Interface role | Canonical Lucide key |
|---|---|
| Segment Maturation | `workflow` |
| Portfolio Analysis | `briefcase` |
| Business Plan Execution | `target` |
| Map | `map-pin` |
| Audit Trail | `shield-check` |
| Calculator | `calculator` |
| Lead Assessment | `clipboard-check` |
| Pre-Drilling / Well Delivery | `clipboard-list` |
| Risk Analysis / Post-Testing | `gauge` |
| Pre-Well Delivery / Post-Drilling | `drill` |
| Growth KPI | `trending-up` |
| Portfolio: Super Stars | `star` |
| Portfolio: Risk Takers | `dices` |
| Portfolio: Value Hunter | `search` |
| Portfolio: Dogs | `dog` |
| Enlarge plot | `maximize-2` |
| Swap values | `arrow-left-right` |
| Move forward / navigate | `arrow-right` |
| Error or warning | `triangle-alert` |

## Shared controls and status

The canonical control keys are `plus`, `minus`, `x`, `chevron-down`,
`chevron-up`, `chevron-right`, `arrow-left`, `arrow-right`, `arrow-up`,
`arrow-left-right`, `copy`, `folder`, `sun`, `moon`, `settings`, `bell`, and
`log-out`. Status and supporting keys are `circle-check`, `circle-minus`,
`circle`, `calendar-days`, `flag`, `flame`, `chart-scatter`,
`file-spreadsheet`, and `user`.

The notification unread indicator is a separate CSS element layered beside
the official `bell`; the bell SVG itself is unchanged.

## Compatibility aliases

Existing semantic names remain temporarily available while call sites migrate.
Every alias resolves to a canonical Lucide string; there are no alias SVG files
and no custom paths.

| Legacy key | Canonical key |
|---|---|
| `alert` | `triangle-alert` |
| `rig-trend` | `workflow` |
| `portfolio` | `briefcase` |
| `bp-execution` | `target` |
| `audit-trail` | `shield-check` |
| `growth-chart` | `trending-up` |
| `clipboard-steps` | `clipboard-list` |
| `rig` | `drill` |
| `quadrant-superstar` | `star` |
| `quadrant-risk-taker` | `dices` |
| `quadrant-value-hunter` | `search` |
| `quadrant-dog` | `dog` |

New code must use canonical keys. Aliases may be removed after all consumers
and cache-stable static HTML have migrated.

## Enforcement

`tests/test_icon_manifest.py` pins every upstream asset by SHA-256, verifies
the source-to-inline transformation, accounts for every file and key, and
checks that Unicode glyphs do not return as interactive controls. New icons
must come from the same pinned package and be added to this mapping and the
hash manifest.
