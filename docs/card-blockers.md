# Blocked cards — what is missing, and what would unblock each

Seven cards in the Bug/Feature batch cannot be built from what exists. Each
names a reference that has not been supplied and tells us to stop and report
rather than guess. This is that report.

Nothing here was partially built, stubbed or approximated. A placeholder column,
an invented colour or a derived formula would each be a claim the data does not
support, and would be harder to correct later than an absence.

---

## 1. Card 3M — the supporting image

**Asked for:** a supporting image beside the Portfolio plot and waterfall.

**Missing:** the asset. The card says "Add only image explicitly supplied or
approved later" and "Do not use a placeholder image in production".

**Built anyway:** the rest of Card 3M — the compact cross-plot preview that
expands into the existing dialog, and the waterfall tile with its upload,
replace and enlarge path. Only the third tile is absent.

**To unblock:** supply the image file and say where it belongs in the row.

---

## 2. Card 3N — the `nucd Area` column

**Asked for:** a seventh Portfolio column labelled exactly `nucd Area`.

**Missing:** any such thing in this application. A case-insensitive search over
every `.py`, `.js`, `.html`, `.css`, `.json` and `.yaml` file, plus the seeded
database, returns **zero** matches for "nucd". There is no field, no config
entry, no import column and no stored value. The card is explicit: "Do not
silently change `nucd Area` to `NUCD Area`, `NUCD acreage`, or another label. Do
not guess its backend data key or unit."

**Built anyway:** the other six columns are in the card's requested order, and
multi-column sorting works as the card describes.

**To unblock:** name the source — which value it holds, in what unit. If it is
an area the app already stores (`p90_area_km2` / `p10_area_km2` live on Lead
Assessment and feed the Map, and the SAD steps carry their own pair), say which
one and it is a one-line addition.

---

## 3. Card 3O — bulk registration columns

**Asked for:** additional columns in the Portfolio bulk-registration workflow,
with the new ones highlighted.

**Missing:** two things. The column definitions (the card's own "Required
Portfolio reference — pending"), and the feature itself: **there is no bulk
registration in this codebase.** No entry route, no table or form component, no
import template, no parser, no API endpoint. Searching for "bulk" finds only
backend comments about batch data-loading scripts.

**Built anyway:** the card's second half — Portfolio inputs align left.

**To unblock:** this is a feature to design, not a set of columns to add. It
needs its own card: entry point, permitted roles, template, validation,
duplicate handling and persistence.

---

## 4. Card 3Q — the map background-colour control

**Asked for:** a background-colour control on the Map.

**Missing:** everything that would define it. The card requires "supported
background-color choices or palette; control type, label/icon, placement,
default, persistence scope, and reset behavior" and "whether background changes
map canvas, page workspace, panel area, or another exact surface" — then says
"Do not invent color options or persistence while reference is missing" and "Do
not assume `background` means basemap provider/style."

**Built anyway:** the card's layout half — the Map Summary sits immediately left
of the Toolbox.

**To unblock:** the palette, the target surface, the default, and whether the
choice is per-user, per-session or global.

---

## 5. Card 3W — SLB volume without an AR number

**Asked for:** an authoritative SLB volume, lacking an AR number, made available
under Reservoir CoS.

**Missing:** the data and its identity. "SLB" appears **nowhere** in this
repository — no field, table, config entry, import path or comment. There is
also no supplied value, source file or record id, and the card forbids creating
sample data or guessing the association: "Do not use filename similarity or
approximate name matching as final association."

What the app does have: `seismic_volume_ar_number` on each Reservoir CoS row,
backed by `seismic_blocks.json`, with the AR number chosen from a dropdown
dependent on the seismic block.

**To unblock:** the source (file, table or export), the volume with its unit and
precision, and — the hard part — the stable key that ties one SLB record to one
ASAS record now that the AR number cannot.

---

## 6. Import All Polygons

**Asked for:** one Map action importing the full polygon set and linking each
polygon to the right dashboard record.

**Missing:** the linking rule. The card says the mapping "MUST follow the
previously discussed polygon-linking logic (voice note/discussion sent to
Muzaini)" and "If the authoritative mapping instructions are not available or
clear, STOP the automatic-linking portion and report the blocker (do not
guess)." That discussion is not in the repository or on the card.

**Also relevant, and worth knowing before that rule is written:** polygons are
not in the database at all. They are static shapefiles read from
`data/map/layers/` at request time, and the association between a well and a
polygon is computed **client-side on every render** by point-in-polygon, never
stored. So "link imported polygons to records" has no existing relationship to
extend — it would create the first persisted polygon↔record link, which is a
schema decision, not an import detail.

**To unblock:** the matching rule, and a decision on whether polygon↔record
links become stored data.

---

## 7. Alidan LEAD folder structure

**Asked for:** create the approved LEAD folder structure and copy authorized
source files into 2024, 2025 and 2026 destinations.

**Missing:** the template, the source root, the destination root and the
year-assignment rule — all four of which the card says must be confirmed and not
inferred. It also requires a dry-run manifest and an owner approval gate before
any copy.

**Beyond that:** this is a filesystem operation on Aramco shares. It is not a
change to this application, and it cannot be performed or rehearsed from here:
the shares are not mounted in this environment (the app's own folder features
degrade gracefully for exactly that reason). Running it needs a machine with
authorized access.

**To unblock:** the four confirmations, then a person with share access to run
the dry run and review its manifest.
