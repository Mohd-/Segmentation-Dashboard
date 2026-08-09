# Calculated BP TD and Drilling Days

**Status: implemented and configuration-driven.** The Business Plan Execution
Gate publishes two server-owned, read-only outputs. Missing dependencies leave
an output unavailable and never block Gate submission.

## Formulas

- **Calculated BP TD (ft MD)** = configured TD base + the dedicated SARH
  thickness surface sampled at the well coordinates + the digital-elevation
  surface sampled at the same point.
- **Calculated Drilling Days** = the configured classification baseline plus
  the configured coring uplift when Coring Program is Yes.

The shipped values live in `config/bp_calculations.json`: a 1,200 ft TD base,
50 days for Development, 127 days for Appraisal/Exploration, and a 10-day
coring uplift. Both results round half-up to whole units.

The SARH grid is deliberately separate from the TSQ SARH-QWRH grid. Its default
path is `data/map/surfaces/sarh_thickness.dat`; deployments may override it
with `SEGMENT_TRACKER_SARH_THICKNESS_SURFACE_FILE`. The existing DEM override
continues to be `SEGMENT_TRACKER_GROUND_ELEVATION_SURFACE_FILE`. Staked X/Y
wins when both values are usable; otherwise the lead X/Y pair is used.

## Storage and provenance

Results use the existing `bp_gate_calculated_td_ft_md` and
`bp_gate_calculated_drilling_days` EAV keys. Calculation metadata records the
formula, inputs, availability, and source. Direct writes are rejected for every
role. A pre-existing supervisor/imported value is retained as read-only legacy
provenance before a governed calculation replaces or clears the active result.

Calculated values never initialize or overwrite `bp_gate_actual_td_ft_md` or
`bp_gate_actual_drilling_days`. Rig Inventory and Rig Target KPIs continue to
use Actual Drilling Days only.

Recalculation runs after relevant project/task saves, promotion, and through
`scripts/backfill_surfaces.py`. Missing configuration, coordinates, grids, or
samples clears a stale active calculated value and reports why it is
unavailable.
