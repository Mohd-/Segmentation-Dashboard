# Planning — Calculated BP TD and Calculated BP Drilling Days

**Status: formula hold.** Card 3R asks for the integration boundary to be
documented and for nothing to be derived until the approved formulas arrive.
This document is that boundary. No formula, coefficient, rounding rule, unit
conversion or fallback is proposed here, because inferring one from the stored
values would produce numbers that look authoritative and are not.

## What exists today

Both fields are on the Business Plan Execution Gate, and each has an **Actual**
twin the user enters directly:

| Field key | Label | Who writes it |
|---|---|---|
| `bp_gate_calculated_td_ft_md` | Calculated Business Plan TD (ft MD) | Supervisor only; read-only for everyone else |
| `bp_gate_actual_td_ft_md` | Actual Business Plan TD (ft MD) | The user |
| `bp_gate_calculated_drilling_days` | Calculated Drilling Days (days) | Read-only today |
| `bp_gate_actual_drilling_days` | Actual Drilling Days (days) | The user |

Storage is `task_dynamic_fields` on the "BP Execution Gate" task. The calculated
TD accepts an override reason (`bp_gate_calculated_td_override_reason`), which
travels with the value on save — the mechanism for "a person disagreed with the
calculation" already exists.

The read-only field shows the placeholder *"Awaiting approved calculation"* to
anyone who cannot edit it, which is honest about the current state.

## What consumes them

- **Rig Inventory / Rig Target KPIs** read `bp_gate_actual_drilling_days` — the
  ACTUAL, never the calculated one. Introducing a formula does not silently move
  a KPI.
- The **Well Summary** shows Drilling Days and TD, preferring Actual over
  Calculated (Card 3E).
- Neither field gates completion, approval, or any stage transition.

## Dependency boundaries the card names

**Calculated BP TD depends on a DVM surface.** The application already reads
surfaces for two other purposes — `config.tsq_surface_file()` fills the TSQ
thickness and `config.ground_elevation_surface_file()` fills ground elevation,
both through `workflow/surfaces_fill.py`, both no-ops when the configured file
is absent. A DVM surface would follow the same shape: a configured file, a
fill that runs post-save, and a value that stays empty when the file is not
there. What is NOT known: which surface, in which format, sampled at which
coordinate, and what the arithmetic is once sampled.

**Calculated BP Drilling Days depends on classification, logging and coring.**
All three are on the same Gate step and already stored:
`bp_gate_classification` (Development / Appraisal / Exploration),
`bp_gate_logging_program` with its interval pair, and `bp_gate_coring_program`
with `bp_gate_coring_thickness_ft` and `bp_gate_coring_formations`. Changing the
classification already resets the logging and sampling defaults, so the
dependency is real and directional. What is NOT known: the day contribution of
each input, or how they combine.

## Required decisions

| # | Decision | Why it cannot be inferred |
|---|---|---|
| 1 | The TD formula, its inputs and units | Nothing in the stored data implies it |
| 2 | Which DVM surface, and how it is sampled | No surface is configured for this |
| 3 | The Drilling Days formula | Same |
| 4 | Rounding, and where it applies | A day count rounded at storage and one rounded at display are different numbers |
| 5 | Behaviour when an input is missing | Blank, zero and "not calculable" are three different answers |
| 6 | Whether a calculated value ever overwrites a stored one | Today nothing does; this is a data-safety decision |
| 7 | Whether the override reason becomes mandatory on divergence | The field exists but nothing requires it |

## What this document commits to NOT doing

- Deriving either formula from stored values, however suggestive the pattern.
- Overwriting `bp_gate_actual_*` under any circumstance.
- Changing KPI, approval or submission behaviour.
- Adding a schema column before the formula exists — the EAV keys are already
  there and cost nothing while empty.
