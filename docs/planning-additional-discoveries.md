# Planning — additional discoveries across the ASAS tabs

**Status: Planning / Deferred. Not part of this release.** The card is explicit:
no production schema, data, workflow, approval, identity, Well Summary, filter,
KPI, Map, Audit Trail, route or permission change, and no placeholder fields,
columns, endpoints or states created in anticipation. Nothing in this document
has been built.

The business rules for additional discoveries have not been supplied, so this
records what the question *is* against the model that exists, and names the
decisions that must come first.

## What "an additional discovery" collides with

Today the data model assumes **one record is one segment is one well**:

- `projects` holds one row per lead/well. `project_id` is the identity;
  `project_name` is the lead name; since Card 3V the *canonical display name* is
  the staked well name once staking is confirmed.
- Every workflow step is one `project_tasks` row against that project, and
  every value is an EAV row against that task.
- Volumes are per record: `lead_piip_*`, `pre_drill_piip_*`, `post_drill_piip_*`
  and `resource_update_*`, each a P90/Mean/P10 trio, resolved newest-first.
- Formations are per record and per phase (`project_formations`, keyed by
  project + formation + phase), with pay intervals beneath them.
- The Portfolio counts a record once, in one status bucket, contributing its
  Mean OGIP to one category of the resource bar.

An additional discovery breaks that assumption in one of three ways, and which
one it is determines everything else:

**(a) A second reservoir in the same wellbore.** Arguably already expressible —
formations are per-record and multiple, each with its own pay intervals and
fluid. What is missing is a per-formation VOLUME; the PIIP trios are per record.

**(b) A second segment the same well proved up.** A new subsurface entity
sharing a wellbore. Needs a relationship the schema does not have.

**(c) A new lead spawned by the result.** Already expressible — create a lead —
but the provenance link back to the discovery that motivated it is not.

**These are different features.** Building for the wrong one is expensive to
undo, which is why nothing is built.

## Per-tab questions

**Segment Maturation.** Does a discovery appear as its own card? If yes, does it
carry its own nine steps, or inherit the parent's completed ones? If no, where
does its maturation state live?

**Portfolio Analysis.** Does it get its own row? Its Mean OGIP is either double
counting or a distinct volume, and the resource bar's "N segments · M wells"
counts are exactly where that ambiguity would show first.

**Business Plan Execution.** BPE is well-centred and its eighteen items describe
drilling one well. A discovery inside a drilled well has no second drilling
campaign, so it probably does not appear here — but "probably" is not a spec.

**Map.** Wells plot at one coordinate. A second segment at the same location is
either a second marker at the same point, or an attribute of the first.

**Audit Trail.** Every event hangs off `(task_id, project_id)`. If a discovery
is not a project, its events need an anchor that does not exist yet.

**Calculator.** Project-free already, so it needs nothing — unless a discovery's
volume should be calculable against the parent's parameters.

**Well Summary highlight.** The card asks for a proposal. The card is already
Card 3E's content in a fixed order; anything added has to earn its line. The
honest shape is a single line naming what else the well found, only when there
is something — the same "when values exist" rule the rest of that card follows.
Whether that line links anywhere depends on whether a discovery is a record.

## Decisions required before design

1. **Is a discovery a record?** Its own `projects` row, a child of one, or an
   attribute on the parent. Everything else follows from this.
2. **Volume accounting.** Does its OGIP add to the portfolio total, or is it
   part of the parent's already-counted volume? Double counting a resource
   estimate is the failure mode with the largest blast radius.
3. **Status.** Is a discovery inside a drilled well "Discovered" — and if so,
   does the parent's status change?
4. **Naming.** Under Card 3V a record has one canonical name. A discovery needs
   either its own or an explicit derivation from its parent's.
5. **Workflow.** Which steps, if any, apply.
6. **Provenance.** How the link to the parent is stored, and whether it survives
   the parent being renamed, promoted or recalled.

## Explicitly not done

No schema, migration, field, column, endpoint, state, filter, KPI, route or
permission added or changed. No placeholder anywhere. No prototype merged.
