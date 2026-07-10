// PROSPECT_STAGES/BP_STAGES/STATUSES here are boot fallbacks only. GET /api/meta
// (Store.meta) is authoritative at runtime; its source of truth is workflow.py
// STAGE_ORDER / PROSPECT_STAGES / BP_EXECUTION_STAGES / STATUSES.
export var PROSPECT_STAGES = ['Lead Identification', 'Risking', 'Segmentation', 'Pre-Well Delivery'];
export var BP_STAGES = ['Well Delivery', 'Post-Drilling', 'Post-Testing'];
// The 4 user-facing lifecycle states (display only; the UI never submits a
// status -- /assign and /transition drive it). 'Not Applicable' is internal.
export var STATUSES = ['Not Assigned', 'In Progress', 'Ready', 'Approved'];
// DONE keeps the legacy keys so old data still renders as done in the rail.
export var DONE = { 'Approved': 1, 'Not Applicable': 1, 'Complete': 1 };

export function piip(prefix) {
  return [
    { key: prefix + '_gas_p90', label: 'P90 Gas (BCF)', type: 'number' },
    { key: prefix + '_gas_mean', label: 'Mean Gas (BCF)', type: 'number' },
    { key: prefix + '_gas_p10', label: 'P10 Gas (BCF)', type: 'number' },
    { key: prefix + '_has_liquid', label: 'Liquid (MMSTB)', type: 'checkbox' },
    { key: prefix + '_liquid_p90', label: 'P90 Liquid (MMSTB)', type: 'number', showIf: prefix + '_has_liquid' },
    { key: prefix + '_liquid_mean', label: 'Mean Liquid (MMSTB)', type: 'number', showIf: prefix + '_has_liquid' },
    { key: prefix + '_liquid_p10', label: 'P10 Liquid (MMSTB)', type: 'number', showIf: prefix + '_has_liquid' }
  ];
}
export var quickNew = [
  { key: 'quicklook_add_new_formation', label: 'Add a new formation', type: 'checkbox' },
  { key: 'quicklook_new_formation_name', label: 'New Formation Name', type: 'text', showIf: 'quicklook_add_new_formation' },
  { key: 'quicklook_new_formation_thickness_ft', label: 'New Formation Thickness (ft)', type: 'number', showIf: 'quicklook_add_new_formation' },
  { key: 'quicklook_top_new_formation_tvdss_ft', label: 'Top New Formation TVDSS (ft)', type: 'number', showIf: 'quicklook_add_new_formation' },
  { key: 'quicklook_new_top_reservoir_tvdss_ft', label: 'Top Reservoir TVDSS (ft)', type: 'number', showIf: 'quicklook_add_new_formation' },
  { key: 'quicklook_new_base_reservoir_tvdss_ft', label: 'Base Reservoir TVDSS (ft)', type: 'number', showIf: 'quicklook_add_new_formation' },
  { key: 'quicklook_base_new_formation_tvdss_ft', label: 'Base New Formation TVDSS (ft)', type: 'number', showIf: 'quicklook_add_new_formation' },
  { key: 'quicklook_new_average_porosity_pct', label: 'Average Porosity (%)', type: 'number', showIf: 'quicklook_add_new_formation' },
  { key: 'quicklook_new_average_swt_pct', label: 'Average Swt (%)', type: 'number', showIf: 'quicklook_add_new_formation' },
  { key: 'quicklook_new_pay_thickness_ft', label: 'Pay Thickness (ft)', type: 'number', showIf: 'quicklook_add_new_formation' },
  { key: 'quicklook_new_ngr_pct', label: 'NGR (%)', type: 'number', showIf: 'quicklook_add_new_formation' }
];
export var finalNew = quickNew.map(function (field) { var clone = Object.assign({}, field); clone.key = clone.key.replace('quicklook', 'final'); if (clone.showIf) clone.showIf = clone.showIf.replace('quicklook', 'final'); return clone; });
export var FLUID_TYPES = ['', 'Gas', 'Gas over Water', 'Wet', 'Tight'];
export var RESERVOIR_COS_COLUMNS = [
  { key: 'seismic_volume_ar_number', label: 'Seismic Volume AR Number', type: 'text' },
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
  'Seal CoS': [{ key: 'seal_recent_activity_age', label: 'Most recent age of activity', type: 'number' }, { key: 'seal_dip', label: 'Dip', type: 'number' }, { key: 'seal_azimuth_vs_shmax', label: 'Azimuth vs. SHmax', type: 'number' }, { key: 'seal_fault_level_confidence', label: 'Fault Level of Confidence', type: 'number' }, { key: 'seal_fracture_permeability', label: 'Fracture Permeability', type: 'number' }, { key: 'seal_cos_pct', label: 'Seal CoS (%)', type: 'number', readonly: true }],
  // v18: 'Presence CoS Evaluation' removed as a step -- the derived value is
  // surfaced as "Total Chance of Success" from /detail's overview.derisking.
  'Pre-Drilling Resource Assessment': piip('pre_drill_piip'),
  // Old moving_* values remain in the DB untouched; the step now captures the
  // well location (prefilled from the project's lead X/Y) plus three
  // distance/azimuth option pairs.
  'Staking Moving Tolerance': [
    { key: 'staking_well_x', label: 'Well Location X', type: 'number', defaultFrom: 'lead_x' },
    { key: 'staking_well_y', label: 'Well Location Y', type: 'number', defaultFrom: 'lead_y' },
    { key: 'staking_opt1_max_distance_m', label: 'Option 1 Max Distance (m)', type: 'number' },
    { key: 'staking_opt1_azimuth_deg', label: 'Option 1 Azimuth (°)', type: 'number' },
    { key: 'staking_opt2_max_distance_m', label: 'Option 2 Max Distance (m)', type: 'number' },
    { key: 'staking_opt2_azimuth_deg', label: 'Option 2 Azimuth (°)', type: 'number' },
    { key: 'staking_opt3_max_distance_m', label: 'Option 3 Max Distance (m)', type: 'number' },
    { key: 'staking_opt3_azimuth_deg', label: 'Option 3 Azimuth (°)', type: 'number' }
  ],
  'Approval to Stake': [],
  // sarh_formation_prognosis_pre_drill keeps its key (renaming EAV keys
  // orphans stored data); only the label dropped "(Pre-Drill)".
  'Well Proposal': [{ key: 'sarh_formation_prognosis_pre_drill', label: 'SARH Formation Prognosis', type: 'text' }, { key: 'vsp_required', label: 'VSP Required?', type: 'select', options: ['', 'No', 'Yes'] }, { key: 'vsp_request_link', label: 'New Request Placeholder', type: 'link', value: '#' }, { key: 'urinsight_link', label: 'URINSIGHT', type: 'link', value: 'https://urinsight/', linkText: 'Create the well proposal in URINSIGHT' }],
  'GHEER': [{ key: 'gheer_base_map', label: 'Base Map', type: 'checkbox' }, { key: 'gheer_offset_wells', label: 'Offset Wells', type: 'checkbox' }, { key: 'gheer_target_polygon', label: 'Target Drilling Polygon (50x50 m)', type: 'checkbox' }, { key: 'gheer_prognosis_tops', label: 'Prognosis Tops', type: 'checkbox' }, { key: 'gheer_depth_top_sarah_grid', label: 'Depth Top Sarah Formation Grid', type: 'checkbox' }, { key: 'gheer_drilling_hazards', label: 'Drilling Hazards', type: 'checkbox' }, { key: 'gheer_pore_pressure_fracture_gradient', label: 'Pore Pressure Gradient and Fracture Gradient', type: 'checkbox' }, { key: 'gheer_wellbore_stability', label: 'Wellbore Stability', type: 'checkbox' }],
  'Quicklook Logs Interpretation': [{ key: 'quicklook_formation_thickness_ft', label: 'Sarah Formation Thickness (ft)', type: 'number' }, { key: 'quicklook_top_sarah_tvdss_ft', label: 'Top Sarah TVDSS (ft)', type: 'number' }, { key: 'quicklook_top_reservoir_tvdss_ft', label: 'Top Reservoir TVDSS (ft)', type: 'number' }, { key: 'quicklook_base_reservoir_tvdss_ft', label: 'Base Reservoir TVDSS (ft)', type: 'number' }, { key: 'quicklook_base_sarah_tvdss_ft', label: 'Base Sarah TVDSS (ft)', type: 'number' }, { key: 'quicklook_average_porosity_pct', label: 'Average Porosity (%)', type: 'number' }, { key: 'quicklook_average_swt_pct', label: 'Average Swt (%)', type: 'number' }, { key: 'quicklook_pay_thickness_ft', label: 'Pay Thickness (ft)', type: 'number' }, { key: 'quicklook_ngr_pct', label: 'NGR (%)', type: 'number' }, { key: 'quicklook_fluid_type', label: 'Fluid Type', type: 'select', options: FLUID_TYPES }, { key: 'quicklook_pdf', label: 'Logs in PDF', type: 'checkbox' }, { key: 'quicklook_las', label: 'Logs as LAS', type: 'checkbox' }].concat(quickNew),
  'Aramco Picks': [],
  'Post-Drilling Resource Assessment': piip('post_drill_piip'),
  'SAD Model': [],
  'Executive Summary': [],
  'URED Update': [],
  'Flowback Results': [{ key: 'flowback_gas_rate_mmscfd', label: 'Gas Rate (MMSCFD)', type: 'number' }, { key: 'flowback_water_rate_bwpd', label: 'Water Rate (BWPD)', type: 'number' }, { key: 'flowback_choke_size_in', label: 'Choke Size (in)', type: 'number' }, { key: 'flowback_fwhp_psi', label: 'FWHP (psi)', type: 'number' }, { key: 'flowback_dynamic_area_km2', label: 'Dynamic Reservoir Area (km²)', type: 'number' }, { key: 'flowback_dynamic_ogip_bcf', label: 'Dynamic OGIP (BCF)', type: 'number' }, { key: 'flowback_sheet', label: 'Flowback Sheet', type: 'checkbox' }, { key: 'flowback_slide', label: 'Flowback Slide', type: 'checkbox' }],
  'SAD Update': [],
  'Final Log Analysis': [{ key: 'final_formation_thickness_ft', label: 'Sarah Formation Thickness (ft)', type: 'number' }, { key: 'final_top_sarah_tvdss_ft', label: 'Top Sarah TVDSS (ft)', type: 'number' }, { key: 'final_top_reservoir_tvdss_ft', label: 'Top Reservoir TVDSS (ft)', type: 'number' }, { key: 'final_base_reservoir_tvdss_ft', label: 'Base Reservoir TVDSS (ft)', type: 'number' }, { key: 'final_base_sarah_tvdss_ft', label: 'Base Sarah TVDSS (ft)', type: 'number' }, { key: 'final_average_porosity_pct', label: 'Average Porosity (%)', type: 'number' }, { key: 'final_average_swt_pct', label: 'Average Swt (%)', type: 'number' }, { key: 'final_pay_thickness_ft', label: 'Pay Thickness (ft)', type: 'number' }, { key: 'final_ngr_pct', label: 'NGR (%)', type: 'number' }, { key: 'final_fluid_type', label: 'Fluid Type', type: 'select', options: FLUID_TYPES }, { key: 'final_pdf', label: 'Logs in PDF', type: 'checkbox' }, { key: 'final_las', label: 'Logs as LAS', type: 'checkbox' }, { key: 'final_petrel', label: 'Logs in Petrel', type: 'checkbox' }].concat(finalNew),
  'PVAD Structural MTR': [{ key: 'pvad_mtr_link', label: 'Hyperlink Placeholder', type: 'text' }],
  'Resource Assessment Update': piip('resource_update').concat([{ key: 'resource_update_note', label: '', type: 'summary' }]),
  'Prospect Evaluation Presentation': [], 'Well Creation': [], 'BP Execution Gate': [], 'Site Preparation': [], 'Post-Well Outcome & Decision Gate': [], 'Executive Summary Final': [], 'PDA': [], 'Approval To Drill': []
};

export function schemaIndex(componentName) {
  var out = {};
  (SCHEMA[componentName] || []).forEach(function (field) { out[field.key] = field; });
  return out;
}
