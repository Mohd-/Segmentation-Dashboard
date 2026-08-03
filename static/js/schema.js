// schema.js declares field shapes; it stays otherwise dependency-free.
// dom.js is a zero-dependency leaf module (no cycle risk) and isFilled's
// "blank always passes" definition is exactly what validateStepFields needs.
import { isFilled } from './dom.js';

// PROSPECT_STAGES/BP_STAGES/STATUSES here are boot fallbacks only. GET /api/meta
// (Store.meta) is authoritative at runtime; its source of truth is workflow.py
// STAGE_ORDER / PROSPECT_STAGES / BP_EXECUTION_STAGES / STATUSES.
export var PROSPECT_STAGES = ['Lead Assessment', 'Risk Analysis', 'Pre-Well Delivery'];
export var BP_STAGES = ['Well Delivery', 'Post-Drilling', 'Post-Testing'];
// The 4 user-facing lifecycle states (display only; the UI never submits a
// status -- /assign and /transition drive it).
export var STATUSES = ['Not Assigned', 'In Progress', 'Ready', 'Approved'];
// The single done state: a component renders as done in the rail once Approved.
export var DONE = { 'Approved': 1 };
// Seismic blocks -> their AR (seismic volume) numbers. Boot fallback only; GET
// /api/meta (Store.meta.seismic_blocks) is authoritative at runtime -- the map
// is production-swappable there. Used by the Reservoir CoS mini-sheet's two
// dependent dropdowns (Seismic Block -> AR Number).
export var SEISMIC_BLOCKS = {
  'Block A': ['2525', '345346', '6345345'],
  'Block B': ['1201', '88421', '990017', '445120'],
  'Block C': ['73310', '73311'],
  'Block D': ['560001', '560002', '560003', '560004']
};

// Boot fallback for GET /api/meta's `resource_scenarios` (Store.meta is
// authoritative at runtime -- same production-swappable idiom as
// SEISMIC_BLOCKS above). Shape mirrors the meta contract: {id, label,
// resource_type}. Labels are verbatim copies of the resource-assessment
// engine's config/scenarios.yaml `display_name` entries -- keep them in sync
// if that config changes. Consumed by the Resource Assessment calculator's
// scenario segmented control (views/resource-calculator.js).
export var RESOURCE_SCENARIOS = [
  { id: 'dry_gas_high_pressure', label: 'Dry Gas - High Pressure Zone', resource_type: 'dry_gas' },
  { id: 'dry_gas_low_pressure', label: 'Dry Gas - Low Pressure Zone', resource_type: 'dry_gas' },
  { id: 'condensate_field_a', label: 'Condensate - Field A', resource_type: 'condensate' },
  { id: 'condensate_field_b', label: 'Condensate - Field B', resource_type: 'condensate' }
];

export function piip(prefix) {
  // Grouped layout: the gas P90/Mean/P10 trio sits under a 'Gas (BCF)' section
  // in one row; the liquid checkbox stands alone; the liquid trio is its own row
  // that shows only when the checkbox is on. Keys are unchanged (renaming EAV
  // keys orphans data); only the labels are concise and the row/section metadata
  // is new. Mean stays between P90 and P10 -- it feeds the portfolio/summary.
  return [
    { key: prefix + '_gas_p90', label: 'P90', type: 'number', section: 'Gas (BCF)', row: prefix + '_gas' },
    { key: prefix + '_gas_mean', label: 'Mean', type: 'number', row: prefix + '_gas' },
    { key: prefix + '_gas_p10', label: 'P10', type: 'number', row: prefix + '_gas' },
    { key: prefix + '_has_liquid', label: 'Liquid (MMSTB)', type: 'checkbox' },
    { key: prefix + '_liquid_p90', label: 'P90', type: 'number', showIf: prefix + '_has_liquid', row: prefix + '_liquid' },
    { key: prefix + '_liquid_mean', label: 'Mean', type: 'number', showIf: prefix + '_has_liquid', row: prefix + '_liquid' },
    { key: prefix + '_liquid_p10', label: 'P10', type: 'number', showIf: prefix + '_has_liquid', row: prefix + '_liquid' }
  ];
}
// Vocabulary. Legacy stored values ('Wet'/'Tight'/'Not Drilled Yet') simply
// render unselected on old projects; the stored data is untouched. 'Not Drilled
// Yet' is no longer a fluid-type choice: the token was dropped entirely and a
// well's pre-drill state is now conveyed by the derived Proposed/Staked
// portfolio status instead.
export var FLUID_TYPES = ['', 'Dry', 'Gas', 'Water', 'Condensate', 'Liquid', 'Gas over Water'];
// Flowback rate key + unit keyed by fluid type -- a well's headline flowback
// rate lives under a different key depending on what it produced. Gas / Gas
// over Water report gas (MMSCFD); Condensate / Liquid report liquid (BPD);
// Water reports water (BWPD). Dry/blank have no dedicated key -- the call site
// falls back to the gas entry. Imported by the well summary card (WS5). The
// keys address a flowback STAGE row first (FLOWBACK_STAGE_COLUMNS reuses the
// same names) and the retired step-level flat EAV keys as legacy fallback.
export var FLOWBACK_RATE_FIELDS = {
  'Gas': { key: 'flowback_gas_rate_mmscfd', unit: 'MMSCFD' },
  'Gas over Water': { key: 'flowback_gas_rate_mmscfd', unit: 'MMSCFD' },
  'Condensate': { key: 'flowback_liquid_rate_bpd', unit: 'BPD' },
  'Liquid': { key: 'flowback_liquid_rate_bpd', unit: 'BPD' },
  'Water': { key: 'flowback_water_rate_bwpd', unit: 'BWPD' }
};
// One flowback stage (#1..#n) per row of the Flowback Results mini-sheet (EAV
// key flowback_stages_rows, a JSON array exactly like reservoir_cos_rows).
// The column keys deliberately reuse the retired step-level flat keys so
// FLOWBACK_RATE_FIELDS and every reader address a stage row and legacy flat
// data with the same name; readers treat the FIRST non-empty stage as the
// well's primary flowback values, falling back to the flat keys only when no
// stage row exists. The index column is display-only (the stage number).
export var FLOWBACK_STAGE_COLUMNS = [
  { key: 'stage', label: 'Stage', type: 'index' },
  // bigOk: true -- see validateStepFields below. Measured depths (MD) routinely
  // run into the thousands of feet, well past the generic 9999 sanity cap.
  { key: 'flowback_top_md', label: 'Top', type: 'number', placeholder: 'Depth (MD)', bigOk: true },
  { key: 'flowback_base_md', label: 'Base', type: 'number', placeholder: 'Depth (MD)', bigOk: true },
  { key: 'flowback_formation', label: 'Formation', type: 'select', optionsFrom: 'formations', value: 'SARH' },
  { key: 'flowback_gas_rate_mmscfd', label: 'Gas Rate (MMSCFD)', type: 'number' },
  { key: 'flowback_water_rate_bwpd', label: 'Water Rate (BWPD)', type: 'number' },
  { key: 'flowback_liquid_rate_bpd', label: 'Liquid Rate (BPD)', type: 'number' },
  { key: 'flowback_choke_size_in', label: 'Choke Size (in)', type: 'number' },
  { key: 'flowback_fwhp_psi', label: 'FWHP (psi)', type: 'number' }
];
// Formation data is well-level (project_formations via /api/projects/<id>/
// formations), edited per phase across four steps -- phases quicklook /
// post_drill / final / resource_update (pipeline order). The canonical trio
// below always seeds the picker (SARH selected by default); users may add
// custom formations (names normalized strip().upper(), <=40 chars) through the
// picker's 'Add custom formation…' option. Legacy per-SARH task fields remain
// readable in old data (see detail.js fluid/tops fallbacks) but are no longer
// rendered as inputs.
export var FORMATIONS = ['SARH', 'QASM', 'QWRH'];
export var FORMATION_METRICS = [
  // bigOk: true -- TVDSS depths run well past the generic 9999 cap (see
  // validateStepFields). Declared for consistency/documentation: formation
  // metric values are edited/saved through the separate well-level formations
  // buffer (detail-form.js's formationEdits -> PUT /api/projects/<id>/
  // formations), not the plain `fields` object validateStepFields checks, so
  // this flag isn't currently read by any validator -- see the report note.
  { key: 'top_tvdss_ft', label: 'Top TVDSS (ft)', type: 'number', bigOk: true },
  { key: 'base_tvdss_ft', label: 'Base TVDSS (ft)', type: 'number', bigOk: true },
  { key: 'thickness_ft', label: 'Formation Thickness (ft)', type: 'number' },
  { key: 'porosity_pct', label: 'Porosity (%)', type: 'number' },
  { key: 'swt_pct', label: 'Swt (%)', type: 'number' },
  { key: 'pay_ft', label: 'Pay Thickness (ft)', type: 'number' },
  { key: 'ngr_pct', label: 'NGR (%)', type: 'number' },
  { key: 'fluid', label: 'Fluid', type: 'select', options: FLUID_TYPES }
];
// The block/AR pair are dependent selects: the block column's options are the
// keys of the seismic_blocks map (meta, or SEISMIC_BLOCKS fallback); the AR
// column (optionsFrom the same map, dependsOn the row's block) offers only that
// block's AR list. `optionsFrom` names the meta/schema map to read; `dependsOn`
// names the sibling column whose value scopes the options. Keys are unchanged
// (renaming EAV/row keys orphans stored data) -- seismic_volume_ar_number just
// switched from free text to a select and got a shorter label.
export var RESERVOIR_COS_COLUMNS = [
  { key: 'seismic_block', label: 'Seismic Block', type: 'select', optionsFrom: 'seismic_blocks' },
  { key: 'seismic_volume_ar_number', label: 'AR Number', type: 'select', optionsFrom: 'seismic_blocks', dependsOn: 'seismic_block' },
  { key: 'amplitude_ratio', label: 'Amplitude Ratio', type: 'number' },
  // bigOk: true -- a depth (see validateStepFields), routinely past 9999.
  { key: 'base_tight_sarah', label: 'Base Tight Sarah', type: 'number', bigOk: true },
  { key: 'pull_up', label: 'Pull-up', type: 'select', options: ['', 'No', 'Semi', 'Yes'] },
  { key: 'reservoir_cos_pct', label: 'Reservoir CoS (%)', type: 'number', readonly: true }
];
// One row per formation on the merged SAD Model / SAD Update steps (EAV keys
// sad_formation_rows / sad_update_formation_rows, JSON arrays exactly like
// reservoir_cos_rows). OPTIONAL and additive: the well-level formations table
// (project_formations, phases post_drill/resource_update) stays the
// petrophysical authority every reader resolves from -- this sheet is the
// SAD model's own per-formation working note and is read by nothing else.
//
// FLAGGED ASSUMPTION: the card only says "additional table (row per
// formation)". The column set below mirrors the well-level formation metrics
// minus pay (Top/Base/Thickness/Phit/Swt/NGR/Fluid) -- cheap to change, no
// reader depends on it.
export var SAD_FORMATION_COLUMNS = [
  { key: 'sad_formation', label: 'Formation', type: 'select', optionsFrom: 'formations', value: 'SARH' },
  // bigOk: true -- TVDSS depths run well past the generic 9999 cap.
  { key: 'sad_top_tvdss_ft', label: 'Top TVDSS (ft)', type: 'number', bigOk: true },
  { key: 'sad_base_tvdss_ft', label: 'Base TVDSS (ft)', type: 'number', bigOk: true },
  { key: 'sad_thickness_ft', label: 'Thickness (ft)', type: 'number' },
  { key: 'sad_phit_pct', label: 'Phit (%)', type: 'number' },
  { key: 'sad_swt_pct', label: 'Swt (%)', type: 'number' },
  { key: 'sad_ngr_pct', label: 'NGR (%)', type: 'number' },
  { key: 'sad_fluid', label: 'Fluid', type: 'select', options: FLUID_TYPES }
];
// ---------------------------------------------------------------------------
// Card 2B -- the four Lead Assessment steps
// ---------------------------------------------------------------------------
// These four entries are the FIELD REGISTRY for the consolidated Lead
// Assessment workspace (views/lead-assessment.js), which custom-renders every
// one of them into its four numbered sections. They stay declared here anyway,
// for three reasons:
//   * the ALL-FIELDS editor (views/project-editor.js) renders straight from
//     SCHEMA and knows nothing about the consolidated page -- an unregistered
//     key would be invisible and uneditable there;
//   * validateStepFields (below) is the shared pre-save sanity net BOTH surfaces
//     run, and it only sees fields SCHEMA declares;
//   * a key with no entry anywhere is a key nobody can find later.
// Every one is a plain typed field project-editor already handles (number /
// select / checkbox), so that editor renders them harmlessly with no special
// casing -- the consolidated page simply chooses not to use this renderer.
// `section` groups them for the all-fields card only; the consolidated page
// lays the sections out itself.
export var SCHEMA = {
  'Area Definition': [
    { key: 'p10_area_km2', label: 'P10 Area (km²)', type: 'number' },
    { key: 'p90_area_km2', label: 'P90 Area (km²)', type: 'number' },
    // Card 2B Section 3. Reference information, NOT a completion gate (it is
    // deliberately absent from the server's FIELD_COMPLETION entry for this
    // step) and NOT positive-only -- a TVDSS below datum is a negative number,
    // and a structure above it a positive one, so the only rule is "parses as a
    // number". bigOk: true because depths run well past the generic 9999 cap.
    { key: 'top_formation_tvdss_ft', label: 'Top Formation TVDSS (ft)', type: 'number', bigOk: true, section: 'Structure' }
  ],
  'Thickness Estimation': [
    { key: 'formation_thickness_ft', label: 'Sarah Formation Thickness (ft)', type: 'number' },
    { key: 'reservoir_thickness_ft', label: 'Reservoir Thickness (ft)', type: 'number' },
    // Card 2B Section 1's two-way time inputs. The FEET above stay the
    // canonical thickness reads every downstream surface resolves (Lead
    // Summary, portfolio, the resource calculator's box model) -- these are the
    // other end of the same measurement, kept so the capture is reproducible.
    // bigOk: true -- a deep target's two-way time runs past 9999 ms.
    { key: 'twt_reservoir_ms', label: 'Reservoir TWT (ms)', type: 'number', bigOk: true, section: 'Two-way time', row: 'twt' },
    { key: 'twt_formation_ms', label: 'Formation TWT (ms)', type: 'number', bigOk: true, row: 'twt' },
    // Which side of a row the user typed, when a conversion is configured
    // (config.TWT_THICKNESS_COEFFICIENTS): '' = both sides entered by hand
    // (the pending-configuration mode), 'twt' = thickness derived from time,
    // 'thickness' = time derived from thickness. A select rather than free text
    // so the all-fields editor offers the three real values and nothing else.
    { key: 'thickness_source_mode', label: 'Thickness source', type: 'select', options: ['', 'twt', 'thickness'] }
  ],
  // No editable dynamic fields: the Resource Assessment calculator
  // (views/resource-calculator.js) is now the step's entire body -- inline
  // scenario/method/inputs, read-only PIIP results, Apply to Lead. The
  // lead_piip_* / lead_calculation_method / lead_resource_scenario /
  // lead_grv_*_thousand_acre_ft EAV keys are unchanged and still written
  // (Apply PATCHes them straight via API.saveFields, bypassing this SCHEMA
  // entry and getFields() entirely) -- LATEST_PIIP_SOURCES and every other
  // reader of Store.allFields keep working unchanged, they were never
  // SCHEMA-driven. The project-editor.js "all fields" card for this step is
  // therefore now comments-only, same as any other schema-less step (e.g.
  // 'GRV Inputs' below) -- editing/viewing PIIP data lives in the pipeline
  // detail view's calculator now.
  // ...and card 2B added ONE typed input to it: the shared-folder confirmation
  // from Section 3, which is half of this step's FIELD_COMPLETION predicate
  // (the other half being a stored lead_piip_gas_mean). It lives on THIS task
  // rather than Area Definition's because it gates THIS item's completion. The
  // lead_piip_* results themselves are still written by a direct API.saveFields
  // PATCH (the consolidated page's auto-run, exactly as Apply used to), so they
  // stay deliberately unregistered here -- they are outputs, never typed.
  'Resource Assessment': [
    { key: 'polygons_surfaces_loaded', label: 'Polygons and surfaces are placed in the shared folder', type: 'checkbox' }
  ],
  // Card 2B Section 2's right half. NEW keys (nothing stored a lead's own GRV
  // before -- lead_grv_p90/p10_thousand_acre_ft on the Resource Assessment task
  // records what the CALCULATOR was last run with, which is a different thing
  // and stays untouched), named in the same 10^3 acre.ft convention as the SAD
  // sheets' sad_grv_*. bigOk: true for the same reason those carry it.
  'GRV Inputs': [
    { key: 'grv_p90_thousand_acre_ft', label: 'GRV (10³ acre.ft) P90', type: 'number', bigOk: true, row: 'lead_grv' },
    { key: 'grv_p10_thousand_acre_ft', label: 'GRV (10³ acre.ft) P10', type: 'number', bigOk: true, row: 'lead_grv' }
  ],
  // Card 4B, second half. The Wellsite Location letter's confirmation plus the
  // STAKED LOCATION it names -- the two coordinates the rest of the business
  // reads off that letter. `showIf` on the pair is the progressive disclosure
  // the consolidated page renders (views/staking-letters.js owns the real
  // page; this entry is what the project editor's all-fields card and any
  // reference view render, and it reproduces the same reveal through the
  // generic form's own data-show-if machinery).
  //
  // bigOk: true -- UTM eastings/northings, six/seven digits, exactly like the
  // Moving Tolerance pair below. The labels double as PLACEHOLDERS on the
  // consolidated page (the mockup's light-gray ghosts); the generic form shows
  // them as captions.
  //
  // Server-side twin: workflow/constants.py FIELD_COMPLETION['Well Site
  // Location'] -- the box AND both coordinates, so a ticked letter with no
  // location leaves the item In Progress.
  'Well Site Location': [
    { key: 'wellsite_letter_loaded', label: 'The Wellsite Location letter is placed in the shared folder', type: 'checkbox' },
    { key: 'staked_x', label: 'Staked X Coordinate', type: 'number', bigOk: true, showIf: 'wellsite_letter_loaded', section: 'Staking Location', row: 'staked_location' },
    { key: 'staked_y', label: 'Staked Y Coordinate', type: 'number', bigOk: true, showIf: 'wellsite_letter_loaded', row: 'staked_location' }
  ],
  // Card 3C. The step's ONLY input, and its whole definition of done: ticking
  // it and saving drives the step to Completed with no approve click, and
  // unticking it reopens the step. The rule itself is server-side --
  // workflow/constants.py FIELD_COMPLETION keys it on this exact field key --
  // so this entry only has to RENDER the box; nothing here decides status.
  'Seismic Signature Validation': [
    { key: 'seismic_slides_loaded', label: 'Seismic validation supporting slides are placed in the shared folder', type: 'checkbox' }
  ],
  // Card 3A. The confirmation sits BENEATH the evaluations mini-sheet (fields
  // render in array order into #dynamic-fields, which precedes the Comments
  // box and the folder card in the detail form), and is the second half of the
  // step's FIELD_COMPLETION predicate: the box AND a stored, model-scored
  // Reservoir CoS are both required before the step reads Completed.
  'Reservoir CoS': [
    { key: 'reservoir_cos_rows', label: 'Reservoir CoS Evaluations', type: 'repeatable', columns: RESERVOIR_COS_COLUMNS },
    { key: 'reservoir_slides_loaded', label: 'Reservoir CoS supporting slides are placed in the shared folder', type: 'checkbox' }
  ],
  // v5: the separate 'Trap CoS' and 'Seal CoS' steps merged into ONE component.
  // The two halves keep their EXACT field keys (renaming an EAV key orphans
  // stored data, and both server-side recompute hooks are keyed on them), so
  // this entry is literally the old Trap array followed by the old Seal array
  // with a section heading over each.
  //
  // ASAS redesign: both CoS percentages are REAL editable inputs now (readonly
  // dropped), computed LIVE client-side as their inputs change (cos-rules.js,
  // wired in detail-form.js) and overtypeable by hand -- a typed value persists
  // until an input next changes. The server hooks (workflow/lifecycle.py) skip
  // their recompute when the payload carries the pct explicitly, which every
  // save from this form now does. The Trap half's input and its CoS share one
  // row (`row: 'trap_cos'`) so the pair reads side by side.
  'Trap and Seal CoS': [
    { key: 'sarah_quwarah_thickness_ft', label: 'Sarah-Quwarah Thickness (ft)', type: 'number', section: 'Trap', row: 'trap_cos' },
    { key: 'trap_cos_pct', label: 'Trap CoS (%)', type: 'number', row: 'trap_cos' },
    { key: 'seal_recent_activity_age', label: 'Most recent age of activity', type: 'number', section: 'Seal' },
    { key: 'seal_dip', label: 'Dip', type: 'number' },
    { key: 'seal_azimuth_vs_shmax', label: 'Azimuth vs. SHmax', type: 'number' },
    { key: 'seal_fault_level_confidence', label: 'Fault Level of Confidence', type: 'number' },
    { key: 'seal_fracture_permeability', label: 'Fracture Permeability', type: 'number' },
    { key: 'seal_pore_pressure_gradient_psi_ft', label: 'Pore Pressure Gradient (psi/ft)', type: 'number' },
    { key: 'seal_cos_pct', label: 'Seal CoS (%)', type: 'number' },
    // Card 3B. Last field of the (section-less continuation of the) Seal half,
    // so it renders beneath the Seal inputs and above the Comments box -- the
    // dynamic-fields grid is emitted in array order and precedes Comments in
    // the form. It is the third of the step's FIELD_COMPLETION requirements
    // (workflow/constants.py): the box AND both stored CoS percentages, so a
    // Trap half filled in on its own leaves the step In Progress.
    { key: 'seal_slides_loaded', label: 'Seal CoS supporting slides are placed in the shared folder', type: 'checkbox' }
  ],
  // v18: 'Presence CoS Evaluation' removed as a step -- the derived value is
  // surfaced as "Total Chance of Success" from /detail's overview.derisking.
  // Card 4C. NO editable dynamic fields: the step HOSTS the resource
  // calculator (views/resource-calculator.js, prefix 'pre_drill' -- see
  // FIELD_PREFIX_BY_STEP there), and the calculator both reads and writes the
  // pre_drill_piip_* trio itself. The piip() grid this entry used to carry
  // declared THE SAME EIGHT KEYS as editable inputs, so the two rendered side
  // by side on one page and the generic Save harvested the grid straight over
  // whatever the calculator's Apply had just written.
  //
  // The fix MIRRORS 'Resource Assessment' above, which met the identical clash
  // when it started hosting the calculator: the grid goes, the calculator's own
  // read-only results panel is the step's display of those numbers, and the EAV
  // keys are untouched (nothing is renamed, nothing is orphaned -- every reader
  // in _OVERVIEW_READ_SOURCES / LATEST_PIIP_SOURCES keeps resolving). The
  // project-editor.js all-fields card for this step is therefore comments-only
  // now, exactly like Resource Assessment's.
  'Pre-Drilling GeoX Assessment': [],
  // Card 4A. Old moving_* values remain in the DB untouched; the step captures
  // the LEAD's coordinates (prefilled from the project's lead X/Y) plus three
  // max-distance/azimuth option pairs -- eight fields in four 2-column rows,
  // which is the card's mockup verbatim. Row ids group each pair; the KEYS are
  // production data and never change, only the two location labels did.
  //
  // Server-side twin: workflow/constants.py FIELD_COMPLETION['Moving
  // Tolerance'] requires ALL EIGHT. No azimuth-range or distance constraint is
  // added here either -- the generic numericFieldError rules (numeric, not
  // negative, bigOk cap) are the whole validation, unchanged.
  'Moving Tolerance': [
    // bigOk: true -- UTM coordinates (see validateStepFields), routinely
    // six/seven digits.
    { key: 'staking_well_x', label: 'Lead X Coordinate', type: 'number', defaultFrom: 'lead_x', row: 'staking_loc', bigOk: true },
    { key: 'staking_well_y', label: 'Lead Y Coordinate', type: 'number', defaultFrom: 'lead_y', row: 'staking_loc', bigOk: true },
    { key: 'staking_opt1_max_distance_m', label: 'Option 1 Max Distance (m)', type: 'number', row: 'staking_opt1' },
    { key: 'staking_opt1_azimuth_deg', label: 'Option 1 Azimuth (°)', type: 'number', row: 'staking_opt1' },
    { key: 'staking_opt2_max_distance_m', label: 'Option 2 Max Distance (m)', type: 'number', row: 'staking_opt2' },
    { key: 'staking_opt2_azimuth_deg', label: 'Option 2 Azimuth (°)', type: 'number', row: 'staking_opt2' },
    { key: 'staking_opt3_max_distance_m', label: 'Option 3 Max Distance (m)', type: 'number', row: 'staking_opt3' },
    { key: 'staking_opt3_azimuth_deg', label: 'Option 3 Azimuth (°)', type: 'number', row: 'staking_opt3' }
  ],
  // Card 4B, first half. Two confirmations, in the order the work happens:
  // the well record exists, then its Approval to Stake letter is filed.
  //
  // staking_well_created is the v5 BACKFILLED key (workflow/constants.py
  // STAKING_WELL_CREATED_KEY): the retired "Well Creation" step's sign-off
  // became this box, and the migration ticked it for every project whose Well
  // Creation row had been Approved -- so a pre-v5 lead opens the page with box
  // one already ticked. It is a prerequisite recorded here, NOT a third tracked
  // item.
  //
  // Server-side twin: FIELD_COMPLETION['Approval to Stake'] requires BOTH, and
  // that step's status is what the Portfolio reads as "Staked"
  // (reporting._approval_to_stake_map).
  'Approval to Stake': [
    { key: 'staking_well_created', label: 'Well creation and well folder are completed', type: 'checkbox' },
    { key: 'approval_stake_letter_loaded', label: 'The Approval to Stake letter is placed in the shared folder', type: 'checkbox' }
  ],
  // sarh_formation_prognosis_pre_drill keeps its key (renaming EAV keys
  // orphans stored data); only the label dropped "(Pre-Drill)".
  'Well Proposal': [{ key: 'sarh_formation_prognosis_pre_drill', label: 'SARH Formation Prognosis', type: 'text' }, { key: 'vsp_required', label: 'VSP Required?', type: 'select', options: ['No', 'Yes'] }, { key: 'vsp_request_link', label: 'New Request Placeholder', type: 'link', value: '#' }, { key: 'urinsight_link', label: 'URINSIGHT', type: 'link', value: 'https://urinsight/', linkText: 'Create the well proposal in URINSIGHT' }],
  // Classification moved to the BP Execution Gate (bp_gate_classification); the
  // legacy gheer_classification key stays readable in old data via reporting's
  // read-fallback but is no longer entered here.
  'GHEER': [{ key: 'gheer_base_map', label: 'Base Map', type: 'checkbox' }, { key: 'gheer_offset_wells', label: 'Offset Wells', type: 'checkbox' }, { key: 'gheer_target_polygon', label: 'Target Drilling Polygon (50x50 m)', type: 'checkbox' }, { key: 'gheer_prognosis_tops', label: 'Prognosis Tops', type: 'checkbox' }, { key: 'gheer_depth_top_sarah_grid', label: 'Depth Top Sarah Formation Grid', type: 'checkbox' }, { key: 'gheer_drilling_hazards', label: 'Drilling Hazards', type: 'checkbox' }, { key: 'gheer_pore_pressure_fracture_gradient', label: 'Pore Pressure Gradient and Fracture Gradient', type: 'checkbox' }, { key: 'gheer_wellbore_stability', label: 'Wellbore Stability', type: 'checkbox' }],
  // WS6: per-SARH scalar fields + the quickNew clone block are replaced by the
  // well-level formations picker; per-formation tops/fluid now live inside the
  // formation panel (the well inherits SARH's fluid -- backend resolves that),
  // so the old step-level tops/fluid keys are dropped. Only the log-file
  // checkboxes remain as normal task fields (grouped in one row).
  'Quicklook Logs': [
    { key: 'quicklook_formations', label: 'Formation Interpretation (Quicklook)', type: 'formations', phase: 'quicklook' },
    { key: 'quicklook_pdf', label: 'Logs in PDF', type: 'checkbox', row: 'quicklook_logs' },
    { key: 'quicklook_las', label: 'Logs as LAS', type: 'checkbox', row: 'quicklook_logs' }
  ],
  'Aramco Picks': [{ key: 'aramco_picks_loaded', label: 'AAP are loaded in Petrel & GK', type: 'checkbox' }],
  // v4 merge: the retired 'Post-Drilling Resource Assessment' step folded in
  // here. Its EAV keys are kept verbatim (post_drill_piip_* via piip(),
  // post_drill_fluid_type) so no stored value is orphaned and every reader
  // keeps resolving; only the formations PICKER (post_drill_formations) was
  // dropped, replaced by the optional per-formation table below. Wells written
  // before the merge hold these same keys under the retired task name, which
  // Store.allFields still carries (the backend field map is retired-inclusive).
  'SAD Model': [
    { key: 'sad_area_km2_p90', label: 'Area (km²) P90', type: 'number', row: 'sad_area' },
    { key: 'sad_area_km2_p10', label: 'Area (km²) P10', type: 'number', row: 'sad_area' },
    // bigOk: GRV in 10³ acre.ft runs past the generic 9999 cap on big segments.
    { key: 'sad_grv_p90', label: 'GRV (10³ acre.ft) P90', type: 'number', row: 'sad_grv', bigOk: true },
    { key: 'sad_grv_p10', label: 'GRV (10³ acre.ft) P10', type: 'number', row: 'sad_grv', bigOk: true },
    { key: 'sad_surfaces_polygons_loaded', label: 'Surfaces and polygons are placed in the shared folder', type: 'checkbox' }
  ].concat(piip('post_drill_piip')).concat([
    { key: 'post_drill_fluid_type', label: 'Fluid Type', type: 'select', options: FLUID_TYPES },
    { key: 'sad_formation_rows', label: 'Formations (optional)', type: 'repeatable', columns: SAD_FORMATION_COLUMNS }
  ]),
  // v4 merge: 'URED Update' folded in here as the second checkbox.
  'Executive Summary': [
    { key: 'exec_summary_loaded', label: 'Executive Summary is loaded in the shared folder', type: 'checkbox', row: 'exec_summary_docs' },
    { key: 'ured_update_loaded', label: 'URED Update is loaded in the shared folder', type: 'checkbox', row: 'exec_summary_docs' }
  ],
  // Per-stage measurements moved into the flowback_stages_rows mini-sheet
  // (stage #1 is the primary read everywhere); the retired flat rate keys stay
  // readable in old data via the readers' fallback but are no longer rendered.
  // Each stage row carries its own Formation column (optionsFrom:'formations':
  // canonical trio + the well's custom formations) naming the tested formation,
  // SARH by default.
  'Flowback Results': [
    // Keys predate the reworded labels: existing '1' values in task_dynamic_fields
    // must keep counting, so the labels changed but the keys did not.
    { key: 'flowback_sheet', label: 'Flowback Sheet is loaded in the shared folder', type: 'checkbox', row: 'flowback_docs' },
    { key: 'flowback_slide', label: 'Flowback Slide is loaded in the shared folder', type: 'checkbox', row: 'flowback_docs' },
    { key: 'flowback_stages_rows', label: 'Flowback Stages', type: 'repeatable', columns: FLOWBACK_STAGE_COLUMNS },
    { key: 'flowback_dynamic_area_km2', label: 'Dynamic Reservoir Area (km²)', type: 'number', row: 'flowback_dyn' },
    { key: 'flowback_dynamic_ogip_bcf', label: 'Dynamic OGIP (BCF)', type: 'number', row: 'flowback_dyn' }
  ],
  // v4 merge: the retired 'Resource Assessment Update' (its resource_update_*
  // EAV keys are kept verbatim; the formations picker is replaced by the
  // optional per-formation table) and 'Executive Summary Final' (now the
  // final_exec_summary_done checkbox) both folded in here. The last two
  // checkboxes GATE submit -- see REQUIRED_FIELDS_FOR_SUBMIT below and its
  // authoritative server twin in workflow/constants.py.
  'SAD Update': [
    { key: 'sad_update_area_km2_p90', label: 'Area (km²) P90', type: 'number', row: 'sad_update_area' },
    { key: 'sad_update_area_km2_p10', label: 'Area (km²) P10', type: 'number', row: 'sad_update_area' },
    { key: 'sad_update_grv_p90', label: 'GRV (10³ acre.ft) P90', type: 'number', row: 'sad_update_grv', bigOk: true },
    { key: 'sad_update_grv_p10', label: 'GRV (10³ acre.ft) P10', type: 'number', row: 'sad_update_grv', bigOk: true },
    { key: 'sad_update_surfaces_polygons_loaded', label: 'Surfaces and polygons are placed in the shared folder', type: 'checkbox' }
  ].concat(piip('resource_update')).concat([
    { key: 'resource_update_fluid_type', label: 'Fluid Type', type: 'select', options: FLUID_TYPES },
    { key: 'sad_update_formation_rows', label: 'Formations (optional)', type: 'repeatable', columns: SAD_FORMATION_COLUMNS },
    { key: 'sad_update_done', label: 'SAD Update', type: 'checkbox', row: 'sad_update_signoff' },
    { key: 'final_exec_summary_done', label: 'Final Executive Summary', type: 'checkbox', row: 'sad_update_signoff' }
  ]),
  // Same shape as Quicklook (formations picker replaces the step-level
  // tops/fluid keys, dropped here too); final_petrel is this step's unique
  // extra. The three log-file checkboxes (Petrel/PDF/LAS) stay grouped in one
  // row.
  'Final Log Analysis': [
    { key: 'final_formations', label: 'Formation Interpretation (Final)', type: 'formations', phase: 'final' },
    { key: 'final_petrel', label: 'Logs in Petrel', type: 'checkbox', row: 'final_logs' },
    { key: 'final_pdf', label: 'Logs in PDF', type: 'checkbox', row: 'final_logs' },
    { key: 'final_las', label: 'Logs as LAS', type: 'checkbox', row: 'final_logs' }
  ],
  'PVAD Structural MTR': [{ key: 'pvad_mtr_link', label: 'DRAS', type: 'link', value: 'https://DRAS/', linkText: 'Open PVAD Structural MTR (DRAS)' }],
  // Card 3D. The one tracked item whose completion stays a HUMAN APPROVAL: the
  // box is not a completion predicate (this step is deliberately absent from
  // the server's FIELD_COMPLETION table), it is the REQUEST for one. Saving it
  // ticked submits the step for review in the same action (server-side twin:
  // workflow/constants.py CHECKBOX_SUBMIT_STEPS), which is why the employee's
  // action row shows Save Updates alone -- see SPECIAL_ACTION_ROWS in
  // views/detail-form.js.
  'Segmentation Slides': [
    { key: 'segmentation_slides_loaded', label: 'Segmentation slides are placed in the shared folder', type: 'checkbox' }
  ],
  // Classification lives here now (moved off GHEER); reporting reads the new key
  // first, falling back to the legacy gheer_classification for old wells.
  'BP Execution Gate': [{ key: 'bp_gate_classification', label: 'Classification', type: 'radio', options: ['Development', 'Appraisal', 'Exploration'] }],
  'Site Preparation': [],
  'Post-Well Outcome & Decision Gate': [{ key: 'post_well_slides_loaded', label: 'Slides are loaded in the shared folder', type: 'checkbox' }],
  'PDA': [{ key: 'pda_booked', label: 'Booked', type: 'checkbox' }, { key: 'pda_urinsight_link', label: 'URINSIGHT', type: 'link', value: 'https://urinsight/', linkText: 'Open URINSIGHT' }],
  'Approval To Drill': []
};

// ---------------------------------------------------------------------------
// Submit gating -- the CLIENT MIRROR of workflow/constants.py's
// REQUIRED_FIELDS_FOR_SUBMIT. The server check (lifecycle._check_submit_
// requirements) is the authority; this exists only so transitionComponent can
// refuse with a toast instead of a round-trip. Keep the two tables in sync:
// task name -> ordered [[field_key, label], ...] of checkboxes that must be
// ticked (and SAVED) before the step may be submitted.
// ---------------------------------------------------------------------------

export var REQUIRED_FIELDS_FOR_SUBMIT = {
  'SAD Update': [
    ['sad_update_done', 'SAD Update'],
    ['final_exec_summary_done', 'Final Executive Summary']
  ]
};

// ---------------------------------------------------------------------------
// Checkbox-driven SUBMISSION (card 3D) -- the CLIENT MIRROR of
// workflow/constants.py's CHECKBOX_SUBMIT_STEPS. task name -> the confirmation
// whose ticked state makes a SAVE double as a submit-for-review.
//
// The server owns the behavior end to end (lifecycle.apply_checkbox_submission
// runs on the same PATCH the Save button already sends, so a save is one round
// trip either way). This mirror exists so the CLIENT can describe what just
// happened -- the action row it renders (SPECIAL_ACTION_ROWS in
// views/detail-form.js) and the toast the save shows -- without hard-coding a
// step name in the view layer. Keep the two tables in sync.
// ---------------------------------------------------------------------------

export var CHECKBOX_SUBMIT_STEPS = {
  'Segmentation Slides': 'segmentation_slides_loaded'
};

// Checkbox truthiness, matching dom.js truthy() and the server's
// constants._CHECKBOX_TRUTHY.
function checkboxOn(value) {
  return ['1', 'true', 'yes', 'on'].indexOf(String(value == null ? '' : value).trim().toLowerCase()) >= 0;
}

// The message a blocked submit should show, or null when the step may be
// submitted. `fields` is the task's SAVED dynamic-fields map (what the server
// will check) -- an unsaved tick in the form does not unlock it, exactly as
// the server sees it. Wording mirrors the server's ValueError.
export function submitBlockedMessage(taskName, fields) {
  fields = fields || {};
  var unmet = (REQUIRED_FIELDS_FOR_SUBMIT[taskName] || []).filter(function (entry) {
    return !checkboxOn(fields[entry[0]]);
  }).map(function (entry) { return entry[1]; });
  return unmet.length ? 'Cannot submit until these are checked: ' + unmet.join(', ') + '.' : null;
}

// ---------------------------------------------------------------------------
// validateStepFields -- generic client-side sanity checks for the regular
// step forms, mirroring the Resource Assessment calculator's own
// validateResourceInputs (views/resource-calculator.js). Wired into
// saveComponent (detail-form.js) before it POSTs; see that call site for the
// "surface the message and abort" behavior.
// ---------------------------------------------------------------------------

// Generic sanity cap for a plain numeric input. `bigOk: true` on a field (or
// repeatable column) declaration exempts it -- reserved for the handful of
// fields that legitimately run past four digits: UTM coordinates
// (staking_well_x/_y) and TVDSS/MD depths (top_tvdss_ft, base_tvdss_ft,
// flowback_top_md, flowback_base_md, base_tight_sarah).
var MAX_NUMBER = 9999;

// Every rule is skipped for a blank value -- every field here is optional;
// only a value the user actually typed gets sanity-checked. `pct` runs the
// <=100 rule for keys ending in `_pct`. Writable `_pct` fields exist since the
// ASAS redesign made trap_cos_pct / seal_cos_pct editable, so rule (d) now has
// a real end-to-end path through validateStepFields (it mirrors the server's
// KI-004-shaped 0-100 guard on explicitly-sent CoS values); the export also
// lets the rule stay covered in isolation.
export function numericFieldError(label, raw, bigOk, pct) {
  if (!isFilled(raw)) return null;
  var value = Number(raw);
  if (isNaN(value)) return label + ' must be numeric.';
  if (value < 0) return label + ' must not be negative.';
  if (!bigOk && value > MAX_NUMBER) return label + ' looks too large (max ' + MAX_NUMBER + ').';
  if (pct && value > 100) return label + ' must not exceed 100%.';
  return null;
}

// Minimal local twin of detail.js's parseRepeatableRows (schema.js stays
// import-free of the view layer -- see the isFilled note at the top of this
// file). `fields[key]` for a repeatable field is the JSON string getFields()
// serializes; this is the cheap parse-back validateStepFields uses to reach
// each row's numeric columns.
function parseRowsForValidation(value) {
  if (Array.isArray(value)) return value;
  try {
    var parsed = JSON.parse(value || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) { return []; }
}

// Rules (a)-(d) over every plain type:'number' field of a step (skipping
// readonly:true -- users can't type into a calculated output), PLUS the same
// rules over every numeric, non-readonly column of the step's repeatable
// tables (Reservoir CoS's amplitude_ratio/base_tight_sarah, Flowback
// Results' flowback_stages_rows columns) -- getFields() already hands those
// rows back as a JSON string, so parseRowsForValidation reaches them cheaply.
// NOT covered: the formations mini-sheet's own numeric metrics (top_tvdss_ft,
// base_tvdss_ft, porosity_pct, ...) -- those live in detail-form.js's private
// formationEdits buffer and save through PUT /api/projects/<id>/formations,
// never through the `fields` object this function receives. Reaching them
// would mean either passing that buffer in here or duplicating range checks
// next to validateFormationRows; both are a restructuring beyond
// "generalize the regular step forms", so it's intentionally skipped (their
// bigOk flags above are declarative-only for now).
function genericFieldErrors(taskName, fields) {
  var stepFields = SCHEMA[taskName] || [];
  for (var i = 0; i < stepFields.length; i += 1) {
    var field = stepFields[i];
    if (field.type === 'number' && !field.readonly) {
      var err = numericFieldError(field.label, fields[field.key], !!field.bigOk, /_pct$/.test(field.key));
      if (err) return err;
    } else if (field.type === 'repeatable' && Array.isArray(field.columns)) {
      var rows = parseRowsForValidation(fields[field.key]);
      for (var r = 0; r < rows.length; r += 1) {
        var row = rows[r] || {};
        for (var c = 0; c < field.columns.length; c += 1) {
          var col = field.columns[c];
          if (col.type !== 'number' || col.readonly) continue;
          var colErr = numericFieldError(col.label, row[col.key], !!col.bigOk, /_pct$/.test(col.key));
          if (colErr) return colErr;
        }
      }
    }
  }
  return null;
}

// Declarative per-step cross-field rules. Each entry gets the task's own
// `fields` object (already passed the generic scan above, so any filled
// number field it reads is guaranteed to already be a valid, in-range
// number) and returns an error string or null.
var CROSS_FIELD_RULES = {
  'Area Definition': function (fields) {
    if (isFilled(fields.p90_area_km2) && isFilled(fields.p10_area_km2) &&
        Number(fields.p90_area_km2) >= Number(fields.p10_area_km2)) {
      return 'Area P90 must be lower than Area P10.'; // same wording as the popup
    }
    return null;
  },
  'Thickness Estimation': function (fields) {
    if (isFilled(fields.reservoir_thickness_ft) && isFilled(fields.formation_thickness_ft) &&
        Number(fields.reservoir_thickness_ft) > Number(fields.formation_thickness_ft)) {
      return 'Reservoir Thickness must not exceed Sarah Formation Thickness.';
    }
    return null;
  },
  // Card 2B. Same shape as Area Definition's rule, over the lead's own GRV
  // percentiles. Note the ASYMMETRY with the consolidated page's own inline
  // check (views/lead-assessment.js), which rejects equality too: this generic
  // rule is the ALL-FIELDS editor's net, where a step is edited key-by-key and
  // the permissive reading matches every other cross-field rule here; the
  // consolidated page owns the stricter capture rule for the surface the card
  // specifies, and the SERVER's FIELD_COMPLETION is stricter still (it simply
  // will not mark the item complete on an equal pair).
  'GRV Inputs': function (fields) {
    if (isFilled(fields.grv_p90_thousand_acre_ft) && isFilled(fields.grv_p10_thousand_acre_ft) &&
        Number(fields.grv_p90_thousand_acre_ft) > Number(fields.grv_p10_thousand_acre_ft)) {
      return 'GRV P90 must be lower than GRV P10.';
    }
    return null;
  }
};

// Every piip()-family trio, addressed by prefix rather than by task name so
// a future step reusing piip() is covered automatically. `fields` only ever
// carries keys for the task actually being saved, so a prefix that doesn't
// belong to the current step simply has nothing filled and no-ops here.
//
// 'lead_piip' is now unreachable through any real form: Resource
// Assessment's SCHEMA entry is `[]` (see above) -- the Resource Assessment
// calculator (views/resource-calculator.js) writes lead_piip_* directly via
// API.saveFields on Apply, bypassing getFields()/validateStepFields
// entirely, so this function is never called with those keys populated in
// practice. Left in place rather than special-cased out: the rule itself is
// still correct, costs nothing to keep, and covers a future step that
// reintroduces an editable lead_piip trio for free.
var PIIP_PREFIXES = ['lead_piip', 'pre_drill_piip', 'post_drill_piip', 'resource_update'];

// p90 <= mean <= p10, checked pairwise (not chained through a possibly-blank
// middle value) and only when BOTH members of a pair are filled -- permissive
// equality on purpose, manual deterministic entries may legitimately repeat
// one value. `kind` ('gas'/'liquid') only decides the message's qualifier;
// the liquid trio is naturally skipped whenever its values are blank (the
// common case when "Liquid" is unchecked), with no need to read the
// checkbox itself.
function piipTrioError(fields, prefix, kind) {
  var p90 = fields[prefix + '_' + kind + '_p90'];
  var mean = fields[prefix + '_' + kind + '_mean'];
  var p10 = fields[prefix + '_' + kind + '_p10'];
  var qualifier = kind === 'gas' ? 'Gas' : 'Liquid';
  if (isFilled(p90) && isFilled(mean) && Number(p90) > Number(mean)) {
    return qualifier + ' P90 must not exceed Mean.';
  }
  if (isFilled(mean) && isFilled(p10) && Number(mean) > Number(p10)) {
    return qualifier + ' Mean must not exceed P10.';
  }
  return null;
}

// Entry point: the first error string across generic per-field checks, the
// step's declarative cross-field rule (if any), then every piip() trio --
// or null when `fields` (a task's about-to-save dynamic-fields object, same
// shape getFields() returns) is clean. Every rule applies only to filled
// values; a wholly blank step always passes, matching the fact that every
// field here is optional.
export function validateStepFields(taskName, fields) {
  fields = fields || {};
  var genericError = genericFieldErrors(taskName, fields);
  if (genericError) return genericError;
  var crossFieldRule = CROSS_FIELD_RULES[taskName];
  var crossFieldError = crossFieldRule ? crossFieldRule(fields) : null;
  if (crossFieldError) return crossFieldError;
  for (var p = 0; p < PIIP_PREFIXES.length; p += 1) {
    var gasError = piipTrioError(fields, PIIP_PREFIXES[p], 'gas');
    if (gasError) return gasError;
    var liquidError = piipTrioError(fields, PIIP_PREFIXES[p], 'liquid');
    if (liquidError) return liquidError;
  }
  return null;
}
