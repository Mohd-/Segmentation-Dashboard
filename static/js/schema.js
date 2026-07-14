// PROSPECT_STAGES/BP_STAGES/STATUSES here are boot fallbacks only. GET /api/meta
// (Store.meta) is authoritative at runtime; its source of truth is workflow.py
// STAGE_ORDER / PROSPECT_STAGES / BP_EXECUTION_STAGES / STATUSES.
export var PROSPECT_STAGES = ['Lead Identification', 'Risking', 'Segmentation', 'Pre-Well Delivery'];
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
// rate lives in a different EAV field depending on what it produced. Gas / Gas
// over Water report gas (MMSCFD); Condensate / Liquid report liquid (BPD);
// Water reports water (BWPD). Dry/blank have no dedicated key -- the call site
// falls back to the gas entry. Imported by the well summary card (WS5).
export var FLOWBACK_RATE_FIELDS = {
  'Gas': { key: 'flowback_gas_rate_mmscfd', unit: 'MMSCFD' },
  'Gas over Water': { key: 'flowback_gas_rate_mmscfd', unit: 'MMSCFD' },
  'Condensate': { key: 'flowback_liquid_rate_bpd', unit: 'BPD' },
  'Liquid': { key: 'flowback_liquid_rate_bpd', unit: 'BPD' },
  'Water': { key: 'flowback_water_rate_bwpd', unit: 'BWPD' }
};
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
  { key: 'top_tvdss_ft', label: 'Top TVDSS (ft)', type: 'number' },
  { key: 'base_tvdss_ft', label: 'Base TVDSS (ft)', type: 'number' },
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
  { key: 'base_tight_sarah', label: 'Base Tight Sarah', type: 'number' },
  { key: 'pull_up', label: 'Pull-up', type: 'select', options: ['', 'No', 'Semi', 'Yes'] },
  { key: 'reservoir_cos_pct', label: 'Reservoir CoS (%)', type: 'number', readonly: true }
];
export var SCHEMA = {
  'Reservoir Area Definition': [{ key: 'p10_area_km2', label: 'P10 Area (km²)', type: 'number' }, { key: 'p90_area_km2', label: 'P90 Area (km²)', type: 'number' }],
  'Thickness Estimation': [{ key: 'formation_thickness_ft', label: 'Sarah Formation Thickness (ft)', type: 'number' }, { key: 'reservoir_thickness_ft', label: 'Reservoir Thickness (ft)', type: 'number' }],
  'Lead Resource Assessment': piip('lead_piip').concat([{ key: 'lead_calculation_method', label: 'Calculation Method', type: 'select', options: ['', 'GRV', 'Box Model'] }]),
  'Seismic Signature Validation': [],
  'Reservoir CoS': [{ key: 'reservoir_cos_rows', label: 'Reservoir CoS Evaluations', type: 'repeatable', columns: RESERVOIR_COS_COLUMNS }],
  'Trap CoS': [{ key: 'sarah_quwarah_thickness_ft', label: 'Sarah-Quwarah Thickness (ft)', type: 'number' }, { key: 'trap_cos_pct', label: 'Trap CoS (%)', type: 'number' }],
  'Seal CoS': [{ key: 'seal_recent_activity_age', label: 'Most recent age of activity', type: 'number' }, { key: 'seal_dip', label: 'Dip', type: 'number' }, { key: 'seal_azimuth_vs_shmax', label: 'Azimuth vs. SHmax', type: 'number' }, { key: 'seal_fault_level_confidence', label: 'Fault Level of Confidence', type: 'number' }, { key: 'seal_fracture_permeability', label: 'Fracture Permeability', type: 'number' }, { key: 'seal_pore_pressure_gradient_psi_ft', label: 'Pore Pressure Gradient (psi/ft)', type: 'number' }, { key: 'seal_cos_pct', label: 'Seal CoS (%)', type: 'number', readonly: true }],
  // v18: 'Presence CoS Evaluation' removed as a step -- the derived value is
  // surfaced as "Total Chance of Success" from /detail's overview.derisking.
  'Pre-Drilling Resource Assessment': piip('pre_drill_piip'),
  // Old moving_* values remain in the DB untouched; the step now captures the
  // well location (prefilled from the project's lead X/Y) plus three
  // distance/azimuth option pairs.
  // Four 2-column rows stacked: the well location pair, then one row per staking
  // option (max distance + azimuth). Row ids group each pair; keys/labels stay.
  'Staking Moving Tolerance': [
    { key: 'staking_well_x', label: 'Well Location X', type: 'number', defaultFrom: 'lead_x', row: 'staking_loc' },
    { key: 'staking_well_y', label: 'Well Location Y', type: 'number', defaultFrom: 'lead_y', row: 'staking_loc' },
    { key: 'staking_opt1_max_distance_m', label: 'Option 1 Max Distance (m)', type: 'number', row: 'staking_opt1' },
    { key: 'staking_opt1_azimuth_deg', label: 'Option 1 Azimuth (°)', type: 'number', row: 'staking_opt1' },
    { key: 'staking_opt2_max_distance_m', label: 'Option 2 Max Distance (m)', type: 'number', row: 'staking_opt2' },
    { key: 'staking_opt2_azimuth_deg', label: 'Option 2 Azimuth (°)', type: 'number', row: 'staking_opt2' },
    { key: 'staking_opt3_max_distance_m', label: 'Option 3 Max Distance (m)', type: 'number', row: 'staking_opt3' },
    { key: 'staking_opt3_azimuth_deg', label: 'Option 3 Azimuth (°)', type: 'number', row: 'staking_opt3' }
  ],
  'Approval to Stake': [],
  // sarh_formation_prognosis_pre_drill keeps its key (renaming EAV keys
  // orphans stored data); only the label dropped "(Pre-Drill)".
  'Well Proposal': [{ key: 'sarh_formation_prognosis_pre_drill', label: 'SARH Formation Prognosis', type: 'text' }, { key: 'vsp_required', label: 'VSP Required?', type: 'select', options: ['', 'No', 'Yes'] }, { key: 'vsp_request_link', label: 'New Request Placeholder', type: 'link', value: '#' }, { key: 'urinsight_link', label: 'URINSIGHT', type: 'link', value: 'https://urinsight/', linkText: 'Create the well proposal in URINSIGHT' }],
  // Classification moved to the BP Execution Gate (bp_gate_classification); the
  // legacy gheer_classification key stays readable in old data via reporting's
  // read-fallback but is no longer entered here.
  'GHEER': [{ key: 'gheer_base_map', label: 'Base Map', type: 'checkbox' }, { key: 'gheer_offset_wells', label: 'Offset Wells', type: 'checkbox' }, { key: 'gheer_target_polygon', label: 'Target Drilling Polygon (50x50 m)', type: 'checkbox' }, { key: 'gheer_prognosis_tops', label: 'Prognosis Tops', type: 'checkbox' }, { key: 'gheer_depth_top_sarah_grid', label: 'Depth Top Sarah Formation Grid', type: 'checkbox' }, { key: 'gheer_drilling_hazards', label: 'Drilling Hazards', type: 'checkbox' }, { key: 'gheer_pore_pressure_fracture_gradient', label: 'Pore Pressure Gradient and Fracture Gradient', type: 'checkbox' }, { key: 'gheer_wellbore_stability', label: 'Wellbore Stability', type: 'checkbox' }],
  // WS6: per-SARH scalar fields + the quickNew clone block are replaced by the
  // well-level formations picker; per-formation tops/fluid now live inside the
  // formation panel (the well inherits SARH's fluid -- backend resolves that),
  // so the old step-level tops/fluid keys are dropped. Only the log-file
  // checkboxes remain as normal task fields (grouped in one row).
  'Quicklook Logs Interpretation': [
    { key: 'quicklook_formations', label: 'Formation Interpretation (Quicklook)', type: 'formations', phase: 'quicklook' },
    { key: 'quicklook_pdf', label: 'Logs in PDF', type: 'checkbox', row: 'quicklook_logs' },
    { key: 'quicklook_las', label: 'Logs as LAS', type: 'checkbox', row: 'quicklook_logs' }
  ],
  'Aramco Picks': [],
  'Post-Drilling Resource Assessment': [{ key: 'post_drill_formations', label: 'Formation Interpretation (Post-Drill)', type: 'formations', phase: 'post_drill' }].concat(piip('post_drill_piip')).concat([{ key: 'post_drill_fluid_type', label: 'Fluid Type', type: 'select', options: FLUID_TYPES }]),
  'SAD Model': [],
  'Executive Summary': [],
  'URED Update': [],
  'Flowback Results': [{ key: 'flowback_gas_rate_mmscfd', label: 'Gas Rate (MMSCFD)', type: 'number' }, { key: 'flowback_water_rate_bwpd', label: 'Water Rate (BWPD)', type: 'number' }, { key: 'flowback_liquid_rate_bpd', label: 'Liquid Rate (BPD)', type: 'number' }, { key: 'flowback_choke_size_in', label: 'Choke Size (in)', type: 'number' }, { key: 'flowback_fwhp_psi', label: 'FWHP (psi)', type: 'number' }, { key: 'flowback_dynamic_area_km2', label: 'Dynamic Reservoir Area (km²)', type: 'number' }, { key: 'flowback_dynamic_ogip_bcf', label: 'Dynamic OGIP (BCF)', type: 'number' }, { key: 'flowback_sheet', label: 'Flowback Sheet', type: 'checkbox' }, { key: 'flowback_slide', label: 'Flowback Slide', type: 'checkbox' }],
  'SAD Update': [],
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
  'PVAD Structural MTR': [{ key: 'pvad_mtr_link', label: 'Hyperlink Placeholder', type: 'text' }],
  'Resource Assessment Update': [{ key: 'resource_update_formations', label: 'Formation Interpretation (Resource Update)', type: 'formations', phase: 'resource_update' }].concat(piip('resource_update')).concat([{ key: 'resource_update_fluid_type', label: 'Fluid Type', type: 'select', options: FLUID_TYPES }]),
  'Prospect Evaluation Presentation': [], 'Well Creation': [],
  // Classification lives here now (moved off GHEER); reporting reads the new key
  // first, falling back to the legacy gheer_classification for old wells.
  'BP Execution Gate': [{ key: 'bp_gate_classification', label: 'Classification', type: 'select', options: ['', 'Development', 'Appraisal', 'Exploration'] }],
  'Site Preparation': [], 'Post-Well Outcome & Decision Gate': [], 'Executive Summary Final': [],
  'PDA': [{ key: 'pda_booked', label: 'Booked', type: 'checkbox' }],
  'Approval To Drill': []
};
