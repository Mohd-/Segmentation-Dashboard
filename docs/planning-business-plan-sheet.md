# Planning — the Business Plan sheet as dashboard input and output

**Status: planning only.** Card 3U is explicit that no upload, import, sync,
export, workbook write or schema change happens until the workbook, mapping,
direction, validation, conflict rules and authorization are approved. Nothing
below has been built. No workbook has been supplied, so no mapping here is
final and none was invented.

## Current state — what this application already has

**Export exists. Import exists. Neither is the Business Plan sheet.**

| Path | Direction | What it moves |
|---|---|---|
| `portfolio_export.py` → `GET /api/portfolio/export.xlsx` | ASAS → Excel | A multi-sheet workbook: Portfolio Export, a Staking sheet, and per-area sheets. Column POSITIONS are an external contract — downstream readers index by position, so columns are appended, never inserted. |
| `export_excel.py` | ASAS → Excel | The older whole-database dump behind the gear menu's "Export to Excel". |
| `import_excel.py` | Excel → ASAS | A one-off loader for historical wells. Writes with `reconcile=False` (a bulk writer: it lays down partial field sets then drives status explicitly). |
| `import_seismic_blocks.py` | JSON → config | Refreshes the seismic-block/AR map. |

None is a two-way sync, none is scheduled, and none writes to a shared folder.

**Record identity.** `projects.project_id` is the stable key. `project_name` is
the lead name and also stable, but it is a DISPLAY name — and since Card 3V a
record may be *known by* its staked well name instead. **Any import keyed on a
name would break on the day a well is staked.** This is the single most
important constraint on the design.

**Field storage.** Most values are EAV rows in `task_dynamic_fields`, keyed by
`(task_id, field_key)` — so a "column" in the sheet maps to a
`(step, field_key)` pair, not to a table column. `static/js/schema.js` is the
declarative list of every field, its type and its owning step; that file is the
natural source for the mapping matrix's left-hand side.

**Audit.** `task_history` is append-only, `action_type` is free text, and
`workflow/history.py log_task_event` is its only writer. An import would need
one event per changed record at minimum.

**Authorization.** Roles are supervisor / staff / employee; `require_role`
gates a route. There is no file-upload endpoint except the portfolio waterfall
image (`uploads.py`), which validates by magic bytes, caps size at 5 MB and
stores one file — the nearest existing precedent for accepting a file at all.

## The mapping matrix — to be filled per workbook column

One row per column of the supplied workbook. Everything unknown stays
`Requires decision`; nothing is filled with a guess.

| Field | Notes |
|---|---|
| Workbook file / version | |
| Sheet / tab | |
| Column header | Exact text |
| Direction | input / output / both — decided per column, not per sheet |
| ASAS target | `(step, field_key)` from schema.js, or a `projects` column |
| Stable match key | **must not be a display name** |
| Type / unit | |
| Required / optional | |
| Allowed values | |
| Transformation | |
| Blank handling | Blank ≠ zero ≠ "leave unchanged" |
| Source of truth | Which side wins |
| Conflict behaviour | |
| Validation message | |
| Audit requirement | |
| Approval status | |

## Decisions needed before any code

1. **The workbook.** Which file, which version, who owns it.
2. **The match key.** `project_id` is the honest answer; if the sheet cannot
   carry it, an agreed business identifier must exist that no rename touches.
3. **Direction per column.** "Both" is a source-of-truth question, not a
   convenience.
4. **Conflict policy.** Sheet wins, ASAS wins, or reject and report.
5. **Approved records.** May an import modify a step already approved? Today
   nothing reopens a step except an explicit action.
6. **Atomicity.** All-or-nothing, or row-by-row with a report.
7. **Cadence and trigger.** Manual upload, or scheduled.
8. **Who may do it**, and whether import and export differ.

## The shape worth proposing when those are answered

Preview-then-commit, not upload-and-apply: parse, validate every row, show a
dry-run report (row, record, field, old value, new value, and what would be
rejected), and write only on explicit confirmation. That mirrors the migration
rehearsal this codebase already uses for schema changes, and it is the only
shape where a mis-mapped column is caught by a person rather than by a support
ticket a week later.

## Explicitly not done under this card

No upload or export control added. No parser or workbook writer. No schema
change. No scheduled exchange. No sample or real record imported. No workbook
overwritten. No change to approval, KPI or workflow logic.
