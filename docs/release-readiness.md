# Release readiness — Trello Bug/Feature batch

**State: `Ready for approval`.**

Card 3AA sets a target of Thursday 6 August 2026, 09:00 Asia/Riyadh, and is
explicit that the target is not permission. Nothing has been pushed, merged or
deployed, and nothing will be without an explicit instruction.

## Identity

| | |
|---|---|
| Branch | `asas-redesign` |
| Base (last shared commit with `main`) | `e804d9f` |
| Head | `ee30010` |
| Commits in this batch | 14 (`df23a8c` … `ee30010`) |
| Diff | 58 files, +3908 / −540 |
| Remote | **nothing pushed** |
| Target environment | **not identified** — see Blockers |

## What is in it

| Commit | Card(s) |
|---|---|
| `df23a8c` | 3G (labels), 3A, 3B, 3C — B90→P90, PDF wording, PICS→Picks, gear-menu exports, formation sheet open on arrival |
| `b69e882` | The BPE 6 navigation lock |
| `20069a9` | 3F — 18/18 Completed, All Years, the Pre-Drilling BP Gate toggle |
| `2ba77a9` | 3E — Well Summary content |
| `029d091` | 3H — TVDSS stored positive (**migration v11**) |
| `5caea8d` | 3T — Coring Formations checkbox dropdown |
| `21842df` | 3AB — the stage/step folder mapping |
| `e59a577` | 3V — canonical staking name + handover confirmation |
| `657ba82` | 3X — Active Drilling |
| `483915b` | 3L, 3N, 3O(part) — resource bar, column order, input alignment |
| `434e553` | 3Y, 3P — SVG sweep and the icon manifest |
| `c547d80` | 3Q(part) — Map Summary beside the Toolbox |
| `80b6486` | 3S — Segmentation Slides on the shared approval framework |
| `ee30010` | 3I — BPE detail-shell parity |

Excluded, per Card 3AA §3: every deferred formula, every image-dependent layout
detail, the polygon linking rule, and anything else in
[`docs/card-blockers.md`](card-blockers.md). Planning-only cards produced
documents, not code.

## Quality gate

| Check | Result |
|---|---|
| Back-end tests | **741 passed** (`pytest -q`) |
| Front-end tests | **607 passed** (`run_frontend_tests.py --browser firefox`) |
| New failures | none |
| Pre-existing failures | none |
| Clean startup | yes, on a seeded scratch database |
| Console errors | none observed on any changed surface |
| Responsive check | 1440 / 1100 / 640 on the changed surfaces, light and dark |
| Secrets / artifacts in the diff | none |

Every phase was additionally exercised live against a seeded database driven
through a real browser, not only through tests. Each commit message records
what was checked.

## Migration

**One: v11, `_migrate_v11_tvdss_positive`.** `LATEST_SCHEMA_VERSION` 10 → 11.

- **Effect:** stores every TVDSS as a magnitude and writes one
  `TVDSS Sign Normalized` audit event per converted value, carrying the prior
  signed number.
- **Scope:** `project_formations`, `project_formation_pay_intervals`, and the
  `top_formation_tvdss_ft` dynamic field.
- **Idempotent:** it selects only still-negative rows, so a replay converts
  nothing and writes no second event.
- **Rehearsed:** against a byte-identical copy of the working database, with
  negatives planted on the copy (the live data holds none). 5 negatives → 0,
  exactly 5 events, no row counts changed, stamped v11, source untouched.
- **On production data this is a no-op** — there are currently no negative
  TVDSS values stored.
- **Rollback:** restore the pre-migration backup. A forward fix is also possible
  without one: every prior value is in `task_history`.

## Behaviour changes worth telling users about

These are correct and intended, and someone will notice them on day one:

1. **Record names.** A record whose staking is confirmed is now called by its
   staked well name **everywhere**, Segment Maturation included. The lead name
   is never lost — it shows beneath the canonical name where they differ.
2. **The BPE step filter is gone.** Filtering by any of the other seventeen
   tracking items is no longer possible. The Pre-Drilling column's BP Gate
   toggle replaces its default behaviour.
3. **Three folder cards disappeared** — Pre-Drilling GeoX Assessment, Moving
   Tolerance and Aramco Picks are not in the approved mapping.
4. **Segmentation Slides needs an explicit Submit.** Ticking the box and saving
   no longer files the review request.
5. **TVDSS reads as a magnitude.** Anyone reading a negative depth downstream
   will now see its positive value.
6. **The Portfolio export's "Well Name" column** carries the staked name; the
   lead name is an appended column. Column positions are unchanged.

## Blockers to release, not to the work

Card 3AA §1 says release stays blocked while any of these is unknown. All of
them are unknown here:

- the configured remote and the authoritative base branch to release from;
- the target Aramco environment and its URL;
- the deployment mechanism and the authorized deployer;
- the change ticket / release approval reference and its approver;
- the change window;
- the backup and rollback owner.

## Two open questions for the reviewer

- **The Active Drilling border is a proposal.** No agreed animation exists in
  the repository; Card 3X says to report rather than invent one. What ships is a
  slow sheen in the priority colour with a reduced-motion fallback. Say the word
  and it changes or comes out.
- **The BP Gate toggle's meaning is an interpretation.** Card 3F says it
  "filters only Pre-Drilling visible wells", but every Pre-Drilling well
  trivially has the gate among its items, so the literal reading makes the
  control do nothing. It filters to wells *still at* the gate. One line to
  change.

## Recommendation

Ready for business review. Do not deploy until the six identity items above are
answered and an explicit go is recorded.
