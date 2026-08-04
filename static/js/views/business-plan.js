import { byId, all, esc, fmtNum, msg, truthy } from '../dom.js';
import { ICONS } from '../icons.js';
import { API } from '../api.js';
import { Store, currentRole, currentUserName } from '../state.js';

var FLUIDS = ['Gas', 'Gas over Water', 'Water Bearing', 'Dry Hole', 'Oil', 'Oil over Gas', 'Oil over Water'];
var STAGE_META = [
  { key: 'pre_drilling', label: 'Pre-Drilling', icon: 'clipboard-check' },
  { key: 'post_drilling', label: 'Post-Drilling', icon: 'rig' },
  { key: 'post_testing', label: 'Post-Testing', icon: 'gauge' }
];
var CONDITIONAL_FIELDS = {
  bp_gate_classification: true,
  bp_gate_logging_program: true,
  bp_gate_coring_program: true,
  gheer_vsp_required: true,
  post_drill_piip_has_liquid: true,
  resource_update_has_liquid: true,
  reserves_booking_response: true
};
var state = {
  initialized: false,
  dashboard: null,
  dashboardRequest: 0,
  detail: null,
  detailRequest: 0,
  projectId: null,
  detailSlug: null,
  saveQueue: Promise.resolve(),
  saveVersion: 0,
  saveDelay: 500,
  contextId: 0,
  fieldDrafts: {},
  structureDrafts: { formations: null, flowback: null },
  retryCommand: null,
  timers: {},
  collapsed: {},
  users: null
};

function icon(name) { return ICONS[name] || '' ; }

function selectOptions(values, selected, placeholder) {
  var html = placeholder ? '<option value="">' + esc(placeholder) + '</option>' : '';
  return html + (values || []).map(function (value) {
    var item = typeof value === 'object' ? value : { value: value, label: value };
    return '<option value="' + esc(item.value) + '" ' + (String(item.value) === String(selected) ? 'selected' : '') + '>' +
      esc(item.label) + '</option>';
  }).join('');
}

function setSelect(id, values, selected) {
  var element = byId(id);
  if (!element) return;
  element.innerHTML = selectOptions(values, selected);
  element.value = String(selected);
}

function currentFilters() {
  return {
    assignee: (byId('bp-assignee-filter') && byId('bp-assignee-filter').value) || 'All Assignees',
    field: (byId('bp-field-filter') && byId('bp-field-filter').value) || 'All Fields',
    status: (byId('bp-status-filter') && byId('bp-status-filter').value) || 'All Status',
    year: (byId('bp-year-filter') && byId('bp-year-filter').value) || String(new Date().getFullYear()),
    step: (byId('bp-step-filter') && byId('bp-step-filter').value) || 'business-plan-gate'
  };
}

function initialize() {
  if (state.initialized) return;
  state.initialized = true;
  var years = [];
  for (var year = 1999; year <= 2035; year += 1) years.push(String(year));
  setSelect('bp-assignee-filter', ['All Assignees', 'Unassigned'], 'All Assignees');
  setSelect('bp-field-filter', ['All Fields'], 'All Fields');
  setSelect('bp-status-filter', ['All Status', 'Completed', 'Pending Approval', 'In Progress'], 'All Status');
  setSelect('bp-year-filter', years, String(new Date().getFullYear()));
  setSelect('bp-step-filter', [
    { value: 'all', label: 'All Steps' },
    { value: 'business-plan-gate', label: 'Business Plan Gate' }
  ], 'business-plan-gate');
  ['bp-field-filter', 'bp-step-filter'].forEach(function (id) {
    var element = byId(id);
    if (element) element.addEventListener('change', refreshBusinessPlan);
  });
}

function showDashboard() {
  byId('bpe-main-view').classList.remove('hidden');
  byId('bpe-detail-view').classList.add('hidden');
  state.contextId += 1;
  state.detail = null;
  state.projectId = null;
  state.detailSlug = null;
}

function loadBusinessPlanDashboard() {
  if (!byId('bpe-main-view') || !byId('bpe-detail-view') || !byId('bp-pipeline')) {
    return Promise.resolve();
  }
  initialize();
  showDashboard();
  var requestId = ++state.dashboardRequest;
  var filters = currentFilters();
  byId('bp-pipeline').setAttribute('aria-busy', 'true');
  return API.businessPlanDashboard(filters).then(function (payload) {
    if (requestId !== state.dashboardRequest) return;
    state.dashboard = payload;
    renderDashboard(payload, filters);
  }).catch(function (error) {
    if (requestId === state.dashboardRequest) {
      byId('bp-pipeline').innerHTML = '<div class="empty-state">Business Plan Execution could not be loaded.</div>';
      msg(error.message, 'error');
    }
  }).finally(function () {
    if (requestId === state.dashboardRequest) byId('bp-pipeline').removeAttribute('aria-busy');
  });
}

export function refreshBusinessPlan() {
  if (!state.detail) return loadBusinessPlanDashboard();
  return flushPendingSaves().then(function (saved) {
    if (!saved) return null;
    return loadBusinessPlanDashboard();
  });
}

function renderDashboard(payload, selected) {
  var options = payload.options || {};
  setSelect('bp-assignee-filter', options.assignees || [], selected.assignee);
  setSelect('bp-field-filter', options.fields || [], selected.field);
  setSelect('bp-status-filter', options.statuses || [], selected.status);
  setSelect('bp-year-filter', (options.years || []).map(String), String(selected.year));
  setSelect('bp-step-filter', options.steps || [], selected.step);
  renderKpis(payload.kpis || {});
  renderDataNotice(payload);
  renderStageBoard(payload);
}

function dayValue(value) {
  var shown = fmtNum(value == null ? 0 : value);
  return esc(shown) + ' Days';
}

function kpiMarkup(iconName, value, label, support) {
  return '<div class="bpe-kpi">' +
    '<span class="bpe-kpi-icon">' + icon(iconName) + '</span>' +
    '<span class="bpe-kpi-copy"><strong>' + value + '</strong><span>' + esc(label) + '</span>' +
    (support ? '<small>' + esc(support) + '</small>' : '') + '</span></div>';
}

function renderKpis(kpis) {
  byId('bpe-kpis').innerHTML =
    kpiMarkup('calendar-days', dayValue(kpis.rig_inventory_days), 'Rig Inventory') +
    kpiMarkup('flag', dayValue(kpis.rig_target_days), 'Rig Target') +
    kpiMarkup('trending-up', esc(kpis.success_rate_pct || 0) + '%', 'Success Rate') +
    kpiMarkup('flame', esc(kpis.actual_mean_ogip_bcf || 0) + '/' + esc(kpis.simulated_mean_ogip_bcf || 0) + ' BCF',
      'Total Mean OGIP', 'Actual/Simulated');
}

function renderDataNotice(payload) {
  var notice = byId('bpe-data-notice');
  var missing = ((payload.data_quality || {}).missing_simulated_mean_project_ids || []).length;
  var inconsistent = ((payload.data_quality || {}).unsuccessful_with_actual_project_ids || []).length;
  var outside = payload.out_of_range_years || [];
  var messages = [];
  if (missing) messages.push(missing + ' visible well' + (missing === 1 ? '' : 's') + ' missing simulated Mean OGIP');
  if (inconsistent) messages.push(inconsistent + ' unsuccessful well' + (inconsistent === 1 ? '' : 's') +
    ' with stored Actual Mean OGIP excluded');
  if (outside.length) messages.push('Historical Business Plan years outside 1999-2035: ' + outside.join(', '));
  notice.textContent = messages.join(' | ');
  notice.classList.toggle('hidden', !messages.length);
}

function statusIcon(item) {
  if (item.status === 'Pending Approval') return icon('circle-minus');
  if (item.status === 'Completed') return icon('circle-check');
  return icon('circle');
}

function wellCard(well) {
  var items = well.items.map(function (item) {
    return '<button type="button" class="bpe-tracking-item state-' + esc(item.color) + '" ' +
      'data-project-id="' + well.project_id + '" data-step="' + esc(item.detail_slug) + '" ' +
      'title="Open ' + esc(item.label) + ': ' + esc(item.status) + '">' +
      '<span class="bpe-track-icon">' + statusIcon(item) + '</span><span>' + esc(item.label) + '</span></button>';
  }).join('');
  return '<article class="bpe-well-card priority-' + esc(String(well.priority).toLowerCase()) + '" data-project-id="' + well.project_id + '">' +
    '<header><button type="button" class="bpe-well-name" data-project-id="' + well.project_id + '" data-step="' +
      esc(well.items[0].detail_slug) + '">' + esc(well.project_name) + '</button>' +
      '<span class="bpe-progress-label">' + esc(well.completed_count) + '/6</span></header>' +
    '<div class="bpe-assignees">' + icon('user') + '<span>' + esc(well.assignee_label) + '</span></div>' +
    '<div class="bpe-tracking-list">' + items + '</div>' +
    '<div class="bpe-progress" aria-label="' + esc(well.progress_percent) + '% complete"><span style="width:' +
      esc(well.progress_percent) + '%"></span></div></article>';
}

function renderStageBoard(payload) {
  var wells = payload.wells || [];
  var markup = STAGE_META.map(function (stage) {
    var rows = wells.filter(function (well) { return well.stage_key === stage.key; });
    var collapsed = !!state.collapsed[stage.key];
    return '<section class="bpe-stage ' + (collapsed ? 'is-collapsed' : '') + '" data-stage="' + stage.key + '">' +
      '<header class="bpe-stage-head"><span class="bpe-stage-icon">' + icon(stage.icon) + '</span>' +
      '<h2>' + esc(stage.label) + '</h2><span class="bpe-stage-count">' + rows.length + '</span>' +
      '<button type="button" class="icon-btn bpe-stage-toggle" data-stage="' + stage.key + '" ' +
      'aria-expanded="' + (!collapsed) + '" title="' + (collapsed ? 'Expand' : 'Collapse') + ' ' + esc(stage.label) + '">' +
      icon(collapsed ? 'chevron-down' : 'chevron-up') + '</button></header>' +
      '<div class="bpe-stage-cards">' + (rows.length ? rows.map(wellCard).join('') :
        '<div class="bpe-stage-empty">No wells match these filters.</div>') + '</div></section>';
  }).join('');
  byId('bp-pipeline').innerHTML = markup;
  all('.bpe-tracking-item, .bpe-well-name', byId('bp-pipeline')).forEach(function (button) {
    button.addEventListener('click', function () {
      openBusinessPlanDetail(Number(button.dataset.projectId), button.dataset.step);
    });
  });
  all('.bpe-stage-toggle', byId('bp-pipeline')).forEach(function (button) {
    button.addEventListener('click', function () {
      state.collapsed[button.dataset.stage] = !state.collapsed[button.dataset.stage];
      renderStageBoard(payload);
    });
  });
}

function loadBusinessPlanDetail(projectId, detailSlug) {
  initialize();
  state.contextId += 1;
  state.projectId = projectId;
  state.detailSlug = detailSlug;
  state.fieldDrafts = {};
  state.structureDrafts = { formations: null, flowback: null };
  state.retryCommand = null;
  state.timers = {};
  var requestId = ++state.detailRequest;
  var root = byId('bpe-detail-view');
  byId('bpe-main-view').classList.add('hidden');
  root.classList.remove('hidden');
  root.innerHTML = '<div class="bpe-detail-loading">Loading...</div>';
  return API.businessPlanDetail(projectId, detailSlug).then(function (detail) {
    if (requestId !== state.detailRequest) return;
    state.detail = detail;
    renderDetail();
    window.scrollTo({ top: 0, behavior: 'auto' });
  }).catch(function (error) {
    root.innerHTML = '<button id="bpe-load-back" type="button" class="ghost">' + icon('arrow-left') +
      ' Back to Business Plan Execution</button><div class="empty-state">This step could not be loaded.</div>';
    var back = byId('bpe-load-back');
    if (back) back.addEventListener('click', refreshBusinessPlan);
    msg(error.message, 'error');
  });
}

export function openBusinessPlanDetail(projectId, detailSlug) {
  if (!state.detail) return loadBusinessPlanDetail(projectId, detailSlug);
  return flushPendingSaves().then(function (saved) {
    if (!saved) return null;
    return loadBusinessPlanDetail(projectId, detailSlug);
  });
}

function trackingByKey(key) {
  var items = (state.detail && state.detail.stage_items) || [];
  return items.find(function (item) { return item.key === key; }) || { status: 'In Progress', color: 'empty', locked: false };
}

function value(key) { return (state.detail.values || {})[key]; }

function checkbox(key, label, options) {
  options = options || {};
  var checked = options.checked == null ? truthy(value(key)) : !!options.checked;
  var disabled = options.disabled ? 'disabled' : '';
  return '<label class="bpe-check ' + (options.system ? 'is-system' : '') + '">' +
    '<input type="checkbox" data-bpe-field="' + esc(key) + '" ' + (checked ? 'checked' : '') + ' ' + disabled + '>' +
    '<span>' + esc(label) + '</span></label>';
}

function textInput(key, label, options) {
  options = options || {};
  var type = options.type || 'text';
  return '<label class="bpe-field ' + (options.className || '') + '"><span>' + esc(label) +
    (options.required ? '<b aria-hidden="true">*</b>' : '') + '</span><input type="' + type + '" data-bpe-field="' + esc(key) +
    '" value="' + esc(value(key) || '') + '" ' + (options.disabled ? 'disabled' : '') +
    (options.readonly ? ' readonly' : '') + (options.placeholder ? ' placeholder="' + esc(options.placeholder) + '"' : '') + '></label>';
}

function selectInput(key, label, options, configOptions) {
  configOptions = configOptions || {};
  var labelMarkup = configOptions.headingLabel ?
    '<span class="bpe-heading-label-spacer" aria-hidden="true"></span><span class="visually-hidden">' + esc(label) +
      (configOptions.required ? ' (required)' : '') + '</span>' :
    '<span>' + esc(label) + (configOptions.required ? '<b aria-hidden="true">*</b>' : '') + '</span>';
  return '<label class="bpe-field ' + (configOptions.className || '') + '">' + labelMarkup +
    '<select data-bpe-field="' + esc(key) + '" ' +
    (configOptions.disabled ? 'disabled ' : '') + (configOptions.invalid ? 'aria-invalid="true" ' : '') + '>' +
    selectOptions(options, value(key), configOptions.placeholder) + '</select></label>';
}

function radioGroup(key, label, options, disabled, hideLegend) {
  return '<fieldset class="bpe-radio-group"><legend class="' + (hideLegend ? 'visually-hidden' : '') + '">' + esc(label) + '</legend><div>' + options.map(function (option) {
    return '<label><input type="radio" name="' + esc(key) + '" data-bpe-field="' + esc(key) + '" value="' + esc(option) + '" ' +
      (String(value(key)) === option ? 'checked' : '') + ' ' + (disabled ? 'disabled' : '') + '><span>' + esc(option) + '</span></label>';
  }).join('') + '</div></fieldset>';
}

function commentsMarkup() {
  var key = state.detail.comments_key;
  return '<label class="bpe-comments"><span>Comments</span><textarea data-bpe-field="' + esc(key) + '">' +
    esc(value(key) || '') + '</textarea></label>';
}

function folderMarkup(omit) {
  if (omit) return '';
  var folder = state.detail.folder || {};
  if (!folder.path) return '';
  return '<div class="bpe-folder-row"><a href="' + esc(folder.file_url || '#') + '" title="Open shared folder">' +
    '<span class="bpe-folder-icon">' + icon('folder') + '</span><span>' + esc(folder.path) + '</span></a>' +
    '<button type="button" id="bpe-copy-folder" class="bpe-copy-button" title="Copy shared-folder path" aria-label="Copy shared-folder path">' +
    icon('copy') + '</button></div>';
}

function commonTail(options) {
  options = options || {};
  return commentsMarkup() + folderMarkup(options.omitFolder) +
    '<div class="bpe-save-line"><span>All changes are saved automatically</span>' +
    '<small id="bpe-save-feedback" aria-live="polite"></small>' +
    '<button type="button" id="bpe-retry-save" class="ghost hidden">Retry</button></div>' +
    approvalMarkup();
}

function gateForm() {
  var classification = value('bp_gate_classification');
  var formations = state.detail.formation_options || [];
  var intervalOptions = formations.concat(state.detail.hole_sections || []);
  var calcTdEditable = state.detail.role === 'supervisor';
  var coring = value('bp_gate_coring_program') === 'Yes';
  var intervalConflict = ['Standard A', 'Standard B'].indexOf(value('bp_gate_logging_program')) >= 0 &&
    value('bp_gate_interval_from') && value('bp_gate_interval_from') === value('bp_gate_interval_to');
  var selectedCoring = [];
  if (Array.isArray(value('bp_gate_coring_formations'))) selectedCoring = value('bp_gate_coring_formations');
  else {
  try { selectedCoring = JSON.parse(value('bp_gate_coring_formations') || '[]'); } catch (error) { selectedCoring = []; }
  }
  return '<div class="bpe-form-section"><h3>Well Classification</h3>' +
    radioGroup('bp_gate_classification', 'Well Classification', ['Development', 'Appraisal', 'Exploration'], false, true) + '</div>' +
    '<div class="bpe-form-section"><h3>Depth &amp; Schedule</h3><div class="bpe-gate-depth">' +
    textInput('bp_gate_calculated_td_ft_md', 'Calculated Business Plan TD (ft MD)', {
      type: 'number', required: true, readonly: !calcTdEditable,
      placeholder: calcTdEditable ? '' : 'Awaiting approved calculation'
    }) + textInput('bp_gate_actual_td_ft_md', 'Actual Business Plan TD (ft MD)', { type: 'number', required: true }) +
    textInput('bp_gate_calculated_drilling_days', 'Calculated Drilling Days (days)', {
      type: 'number', required: true, readonly: true, placeholder: 'Awaiting approved equation'
    }) + textInput('bp_gate_actual_drilling_days', 'Actual Drilling Days (days)', { type: 'number', required: true }) +
    '</div>' + (calcTdEditable ? textInput('bp_gate_calculated_td_override_reason', 'Calculated TD Override Reason', {
      className: 'bpe-override-reason'
    }) : '') + '</div>' +
    '<div class="bpe-form-section"><h3>Logging Program</h3><div class="bpe-gate-logging">' +
    selectInput('bp_gate_logging_program', 'Logging Program', ['Standard A', 'Standard B', 'Optimized Standard B'], {
      required: true, placeholder: 'Select Program', headingLabel: true
    }) +
    selectInput('bp_gate_interval_from', 'Interval From', intervalOptions, {
      required: true, placeholder: 'Select Formation', invalid: intervalConflict
    }) +
    selectInput('bp_gate_interval_to', 'Interval To', intervalOptions, {
      required: true, placeholder: 'Select Formation', invalid: intervalConflict
    }) +
    textInput('bp_gate_swc', 'SWC', { type: 'number', required: true }) +
    textInput('bp_gate_pressure_points', 'Pressure Points', { type: 'number', required: true }) +
    textInput('bp_gate_fluid_samples', 'Fluid Samples', { type: 'number', required: true }) + '</div>' +
    (intervalConflict ? '<p class="bpe-field-error" role="alert">Interval From and Interval To must differ for Standard A and Standard B.</p>' : '') +
    '</div>' +
    '<div class="bpe-form-section"><h3>Coring Program</h3><div class="bpe-gate-coring">' +
    selectInput('bp_gate_coring_program', 'Coring Program', ['Yes', 'No'], {
      required: true, placeholder: 'Select', headingLabel: true
    }) +
    textInput('bp_gate_coring_thickness_ft', 'Coring Thickness (ft)', { type: 'number', required: coring, disabled: !coring }) +
    '<label class="bpe-field"><span>Coring Formations' + (coring ? '<b aria-hidden="true">*</b>' : '') +
    '</span><select multiple data-bpe-field="bp_gate_coring_formations" ' + (!coring ? 'disabled' : '') + '>' +
    formations.map(function (formation) { return '<option ' + (selectedCoring.indexOf(formation) >= 0 ? 'selected' : '') + '>' + esc(formation) + '</option>'; }).join('') +
    '</select></label></div></div>' +
    checkbox('bp_gate_slides_saved', 'Business Plan Execution Gate slides are saved in the shared folder.') +
    commonTail();
}

function wellLettersForm() {
  var proposal = trackingByKey('well-proposal');
  return checkbox('well_proposal_shared', 'Well Proposal is completed and placed in the shared folder.', {
    disabled: proposal.locked, system: proposal.source === 'system',
    checked: proposal.source === 'system' && proposal.status === 'Completed' ? true : null
  }) + checkbox('site_preparation_shared', 'Site Preparation Letter is completed and placed in the shared folder.') +
    checkbox('approval_to_drill_shared', 'Approval to Drill Letter is completed and placed in the shared folder.') + commonTail();
}

function gheerForm() {
  var vsp = truthy(value('gheer_vsp_required'));
  var link = (state.detail.links || {}).vsp;
  return checkbox('gheer_geophysical_shared', 'Geophysical GHEER inputs are loaded in the shared folder.') +
    checkbox('gheer_geomechanical_shared', 'Geomechanical GHEER inputs are loaded in the shared folder.') +
    checkbox('gheer_vsp_required', 'VSP is required.') +
    (vsp ? '<div class="bpe-external-link">' + (link ? '<a href="' + esc(link) + '" target="_blank" rel="noopener">Open VSP form</a>' :
      '<span>VSP form link: Not configured</span>') + '</div>' : '') + commonTail();
}

function aapForm() {
  return checkbox('aap_petrel_loaded', 'Aramco Approved PICS are loaded into the PETREL repository.') +
    checkbox('aap_geoknowledge_loaded', 'Aramco Approved PICS are loaded into the GeoKnowledge database.') +
    commonTail({ omitFolder: true });
}

function summaryForm(finalSummary) {
  if (finalSummary) {
    var dry = (state.detail.fluid_state || {}).decision === 'all_water_or_dry';
    var copied = state.detail.sad_update_branch === 'copied_from_sad';
    return checkbox('final_exec_summary_done', 'Final Executive Summary slides are placed in the shared folder.', {
      disabled: dry, system: dry, checked: dry ? true : null
    }) + checkbox('final_ured_update_done', 'Final URED Update slides are placed in the shared folder.', {
      disabled: dry || copied, system: dry || copied, checked: dry || copied ? true : null
    }) + commonTail();
  }
  var executive = trackingByKey('executive-summary');
  var ured = trackingByKey('ured-update');
  return checkbox('exec_summary_loaded', 'Executive Summary slides are placed in the shared folder.', {
    disabled: executive.locked, system: executive.source === 'system',
    checked: executive.source === 'system' && executive.status === 'Completed' ? true : null
  }) + checkbox('ured_update_loaded', 'URED Update slides are placed in the shared folder.', {
    disabled: ured.locked, system: ured.source === 'system',
    checked: ured.source === 'system' && ured.status === 'Completed' ? true : null
  }) + commonTail();
}

function sadForm(update) {
  var base = update ? 'sad_update' : 'sad';
  var prefix = update ? 'resource_update' : 'post_drill_piip';
  var locked = update && trackingByKey('sad-update').locked;
  var liquid = truthy(value(prefix + '_has_liquid'));
  return '<div class="bpe-form-section"><h3>Reservoir Area (km²)</h3><div class="bpe-pair">' +
    textInput(base + '_area_km2_p90', 'B90', { type: 'number', required: true, disabled: locked }) +
    textInput(base + '_area_km2_p10', 'B10', { type: 'number', required: true, disabled: locked }) + '</div></div>' +
    '<div class="bpe-form-section"><h3>GRV (10³ acre&middot;ft)</h3><div class="bpe-pair">' +
    textInput(base + '_grv_p90', 'B90', { type: 'number', required: true, disabled: locked }) +
    textInput(base + '_grv_p10', 'B10', { type: 'number', required: true, disabled: locked }) + '</div></div>' +
    checkbox(base + '_surfaces_polygons_loaded', 'The polygons and surfaces are placed in the shared folder.', { disabled: locked, system: locked }) +
    checkbox(base + '_slides_loaded', 'SAD Model slides are placed in the shared folder.', { disabled: locked, system: locked }) +
    '<div class="bpe-form-section"><h3>Gas Field Inputs</h3><div class="bpe-trio">' +
    textInput(prefix + '_gas_p90', 'P90 (BCF)', { type: 'number', required: true, disabled: locked }) +
    textInput(prefix + '_gas_mean', 'Mean OGIP (BCF)', { type: 'number', required: true, disabled: locked }) +
    textInput(prefix + '_gas_p10', 'P10 (BCF)', { type: 'number', required: true, disabled: locked }) + '</div></div>' +
    checkbox(prefix + '_has_liquid', 'Liquid (MMSTB)', { disabled: locked }) +
    (liquid ? '<div class="bpe-trio bpe-liquid-fields">' +
      textInput(prefix + '_liquid_p90', 'P90 (MMSTB)', { type: 'number', required: true, disabled: locked }) +
      textInput(prefix + '_liquid_mean', 'Mean (MMSTB)', { type: 'number', required: true, disabled: locked }) +
      textInput(prefix + '_liquid_p10', 'P10 (MMSTB)', { type: 'number', required: true, disabled: locked }) + '</div>' : '') +
    (update && state.detail.sad_update_branch === 'unresolved_comparison' ?
      '<div class="bpe-branch-note">Comparison is unresolved. No branch has been selected.</div>' : '') + commonTail();
}

function learningForm() {
  return checkbox('post_well_slides_loaded', 'Post-Drill Learning Review slides are placed in the shared folder.') + commonTail();
}

function formationRowMarkup(row, formationIndex) {
  var options = (state.detail.formation_options || []).slice();
  if (row.formation && options.indexOf(row.formation) < 0) options.push(row.formation);
  var payRows = (row.pay_intervals || []).map(function (interval, payIndex) {
    return '<div class="bpe-pay-row" data-pay-index="' + payIndex + '">' +
      formationCell(formationIndex, payIndex, 'top_tvdss_ft', 'Top TVDSS (ft)', interval.top_tvdss_ft, 'number') +
      formationCell(formationIndex, payIndex, 'base_tvdss_ft', 'Base TVDSS (ft)', interval.base_tvdss_ft, 'number') +
      formationCell(formationIndex, payIndex, 'phit_pct', 'Phit (%)', interval.phit_pct, 'number') +
      formationCell(formationIndex, payIndex, 'swt_pct', 'Swt (%)', interval.swt_pct, 'number') +
      formationCell(formationIndex, payIndex, 'ngr_pct', 'NGR (%)', interval.ngr_pct, 'number') +
      formationCell(formationIndex, payIndex, 'kint_md', 'Kint (mD)', interval.kint_md, 'number') +
      '<label><span>Fluid*</span><select data-formation-index="' + formationIndex + '" data-pay-index="' + payIndex +
      '" data-pay-field="fluid">' + selectOptions(FLUIDS, interval.fluid, 'Select Fluid') + '</select></label>' +
      '<button type="button" class="icon-btn bpe-remove-pay" data-formation-index="' + formationIndex +
      '" data-pay-index="' + payIndex + '" title="Remove Pay Interval" aria-label="Remove Pay Interval">' + icon('x') + '</button></div>';
  }).join('');
  return '<section class="bpe-formation-block" data-formation-index="' + formationIndex + '">' +
    '<header><label><span>Formation*</span><select data-formation-index="' + formationIndex + '" data-formation-field="formation">' +
    selectOptions(options, row.formation, 'Select Formation') + '</select></label>' +
    '<button type="button" class="icon-btn bpe-remove-formation" data-formation-index="' + formationIndex +
    '" title="Remove Formation" aria-label="Remove Formation">' + icon('x') + '</button></header>' +
    '<div class="bpe-formation-envelope">' +
    formationEnvelopeCell(formationIndex, 'top_tvdss_ft', 'Formation Top*', row.top_tvdss_ft) +
    formationEnvelopeCell(formationIndex, 'base_tvdss_ft', 'Formation Base*', row.base_tvdss_ft) +
    formationEnvelopeCell(formationIndex, 'thickness_ft', 'Formation Thickness*', row.thickness_ft) + '</div>' +
    '<div class="bpe-pay-heading"><h4>Pay Intervals</h4><button type="button" class="ghost bpe-add-pay" data-formation-index="' +
    formationIndex + '">' + icon('plus') + ' Add Pay Interval</button></div>' +
    '<div class="bpe-pay-list">' + (payRows || '<div class="bpe-inline-empty">No Pay Intervals.</div>') + '</div></section>';
}

function formationEnvelopeCell(index, key, label, cellValue) {
  return '<label><span>' + esc(label) + '</span><input type="number" data-formation-index="' + index +
    '" data-formation-field="' + esc(key) + '" value="' + esc(cellValue == null ? '' : cellValue) + '"></label>';
}

function formationCell(formationIndex, payIndex, key, label, cellValue, type) {
  return '<label><span>' + esc(label) + '</span><input type="' + type + '" data-formation-index="' + formationIndex +
    '" data-pay-index="' + payIndex + '" data-pay-field="' + esc(key) + '" value="' +
    esc(cellValue == null ? '' : cellValue) + '"></label>';
}

function formationsForm() {
  var rows = state.detail.formations || [];
  var confirmations = state.detailSlug === 'quicklook-logs' ?
    checkbox('quicklook_pdf', 'Logs in PDF') + checkbox('quicklook_las', 'Logs as LAS') :
    checkbox('final_petrel', 'Logs in Petrel') + checkbox('final_pdf', 'Logs in PDF') + checkbox('final_las', 'Logs as LAS');
  return '<div class="bpe-formations">' + rows.map(formationRowMarkup).join('') +
    '<button type="button" id="bpe-add-formation" class="ghost">' + icon('plus') + ' Add Formation</button></div>' +
    confirmations + commonTail();
}

function blankFlowbackStage() {
  return { id: (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : 'draft-' + Date.now() + '-' + Math.random(),
    formation: '', top_md: '', base_md: '', dynamic_area_km2: '', dynamic_ogip_bcf: '', gas_rate_mmscfd: '',
    water_rate_bwpd: '', liquid_rate_bpd: '', choke_size_in: '', fwhp_psi: '' };
}

function flowCell(index, key, label, required, formation) {
  var disabled = trackingByKey('flowback').locked ? ' disabled' : '';
  if (key === 'formation') {
    return '<label><span>' + esc(label) + (required ? '<b>*</b>' : '') + '</span><select data-flow-index="' + index +
      '" data-flow-field="' + key + '"' + disabled + '>' + selectOptions(state.detail.formation_options || [], formation, 'Select Formation') + '</select></label>';
  }
  var row = state.detail.flowback_stages[index];
  return '<label><span>' + esc(label) + (required ? '<b>*</b>' : '') + '</span><input type="number" data-flow-index="' + index +
    '" data-flow-field="' + key + '" value="' + esc(row[key] == null ? '' : row[key]) + '"' + disabled + '></label>';
}

function flowbackForm() {
  var locked = trackingByKey('flowback').locked;
  if (!state.detail.flowback_stages.length && !state.detail.flowback_initialized) {
    state.detail.flowback_stages = [blankFlowbackStage()];
    state.detail.flowback_initialized = true;
  }
  var panels = state.detail.flowback_stages.map(function (row, index) {
    return '<section class="bpe-flow-stage ' + (locked ? 'is-locked' : '') + '"><header>' +
      '<button type="button" class="icon-btn bpe-remove-flow" data-flow-index="' + index + '" title="Delete Stage ' + (index + 1) +
      '" aria-label="Delete Stage ' + (index + 1) + '" ' + (locked ? 'disabled' : '') + '>' + icon('x') + '</button>' +
      '<h4>Stage ' + (index + 1) + '</h4></header><div class="bpe-flow-grid">' +
      flowCell(index, 'formation', 'Formation', true, row.formation) +
      flowCell(index, 'top_md', 'Top (Measured Depth)', true) +
      flowCell(index, 'base_md', 'Base (Measured Depth)', true) +
      flowCell(index, 'dynamic_area_km2', 'Dynamic Area (km²)', false) +
      flowCell(index, 'dynamic_ogip_bcf', 'Dynamic OGIP (BCF)', false) +
      flowCell(index, 'gas_rate_mmscfd', 'Gas Rate (MMSCFD)', false) +
      flowCell(index, 'water_rate_bwpd', 'Water Rate (BWPD)', false) +
      flowCell(index, 'liquid_rate_bpd', 'Liquid Rate (BPD)', false) +
      flowCell(index, 'choke_size_in', 'Choke Size (in)', true) +
      flowCell(index, 'fwhp_psi', 'FWHP (psi)', true) + '</div></section>';
  }).join('');
  return '<div class="bpe-flow-heading"><h3>Flowback Stage Results</h3><button type="button" id="bpe-add-flow" class="icon-btn" ' +
    (locked ? 'disabled' : '') + ' title="Add Flowback stage" aria-label="Add Flowback stage">' + icon('plus') + '</button></div>' + panels +
    checkbox('flowback_shared_confirmed', 'Flowback sheet and slides are placed in the shared folder', {
      disabled: locked, system: locked, checked: locked ? true : null
    }) +
    commonTail();
}

function mtrForm() {
  var item = trackingByKey('mtr');
  var link = (state.detail.links || {}).structural_mtr;
  return checkbox('structural_mtr_shared', 'Structural MTR slides are placed in the shared folder.', {
    disabled: item.locked, system: item.source === 'system',
    checked: item.source === 'system' && item.status === 'Completed' ? true : null
  }) + '<div class="bpe-external-link">' + (link ? '<a href="' + esc(link) + '" target="_blank" rel="noopener">Open Structural MTR</a>' :
    '<span>Structural MTR link: Not configured</span>') + '</div>' + commonTail();
}

function pdaForm() {
  var item = trackingByKey('pda-booking');
  var development = value('bp_gate_classification') === 'Development';
  var response = value('reserves_booking_response') || '';
  var years = (state.detail.booking_years || []).map(String);
  if (value('reserves_booking_year') && years.indexOf(String(value('reserves_booking_year'))) < 0) years.unshift(String(value('reserves_booking_year')));
  return checkbox('pda_complete', 'Post-Drilling Analysis is completed and placed in the shared folder.', {
    disabled: development, system: development, checked: development ? true : null
  }) + radioGroup('reserves_booking_response', 'Is the well included in a Reserves Booking Cycle?', ['Yes', 'No'], item.locked) +
    (response === 'Yes' ? selectInput('reserves_booking_year', 'Reserves Booking Year', years, { required: true, disabled: item.locked }) : '') +
    commonTail();
}

function bodyMarkup() {
  var slug = state.detailSlug;
  if (slug === 'business-plan-gate') return gateForm();
  if (slug === 'well-letters') return wellLettersForm();
  if (slug === 'gheer-inputs') return gheerForm();
  if (slug === 'quicklook-logs' || slug === 'final-log-analysis') return formationsForm();
  if (slug === 'aramco-approved-pics') return aapForm();
  if (slug === 'sad-model') return sadForm(false);
  if (slug === 'summary-slides') return summaryForm(false);
  if (slug === 'post-drill-learning-review') return learningForm();
  if (slug === 'flowback-results') return flowbackForm();
  if (slug === 'sad-model-update') return sadForm(true);
  if (slug === 'final-summary-slides') return summaryForm(true);
  if (slug === 'structural-mtr') return mtrForm();
  if (slug === 'pda-booking') return pdaForm();
  return commonTail();
}

function detailNavMarkup() {
  return (state.detail.navigation || []).map(function (group) {
    return '<div class="bpe-nav-group"><h3>' + esc(group.stage_label) + '</h3>' + group.details.map(function (item) {
      return '<button type="button" data-detail-slug="' + esc(item.slug) + '" class="bpe-nav-item ' +
        (item.slug === state.detailSlug ? 'active' : '') + '">' + esc(item.label) + '</button>';
    }).join('') + '</div>';
  }).join('');
}

function assigneeOptions() {
  var users = state.users || Store.users || [];
  return [{ value: '', label: 'Not Assigned' }].concat(users.map(function (user) { return { value: user.name, label: user.name }; }));
}

function topControlsMarkup() {
  var role = state.detail.role || currentRole();
  return '<div class="bpe-detail-controls"><label>Assignee<select id="bpe-assignee" ' +
    (role === 'employee' ? 'disabled' : '') + '>' + selectOptions(assigneeOptions(), state.detail.assignee) + '</select></label>' +
    '<label>Priority<select id="bpe-priority" ' + (role !== 'supervisor' ? 'disabled' : '') + '>' +
    selectOptions(['Low', 'Medium', 'High'], state.detail.project.priority) + '</select></label></div>';
}

function summaryMarkup() {
  var completed = (state.detail.stage_items || []).filter(function (item) { return item.status === 'Completed'; }).length;
  return '<aside class="bpe-well-summary"><header><h3>Well Summary</h3><button type="button" id="bpe-summary-gear" class="icon-btn" ' +
    'aria-haspopup="menu" aria-expanded="false" title="Well Summary actions">' + icon('settings') + '</button>' +
    '<div id="bpe-summary-menu" class="bpe-summary-menu hidden" role="menu"><button type="button" id="bpe-edit-all" role="menuitem">Edit all project fields</button></div></header>' +
    '<dl><div><dt>Well</dt><dd>' + esc(state.detail.project.project_name) + '</dd></div>' +
    '<div><dt>Field</dt><dd>' + esc(state.detail.project.field || '-') + '</dd></div>' +
    '<div><dt>Business Plan Year</dt><dd>' + esc(state.detail.project.business_plan_year || '-') + '</dd></div>' +
    '<div><dt>Stage Progress</dt><dd>' + completed + '/6</dd></div></dl>' +
    '<div class="bpe-summary-items">' + (state.detail.stage_items || []).map(function (item) {
      return '<div class="state-' + esc(item.color) + '"><span>' + statusIcon(item) + '</span><span>' + esc(item.label) + '</span></div>';
    }).join('') + '</div></aside>';
}

function approvalMarkup() {
  if (['business-plan-gate', 'sad-model', 'post-drill-learning-review', 'sad-model-update'].indexOf(state.detailSlug) < 0) return '';
  if (state.detailSlug === 'sad-model-update' && state.detail.sad_update_branch !== 'manual_update') return '';
  var tracking = (state.detail.tracking || [])[0] || { status: 'In Progress' };
  var role = state.detail.role || currentRole();
  var buttons = '';
  if (role === 'supervisor') {
    buttons += '<button type="button" data-bpe-transition="return" class="ghost" ' +
      (tracking.status !== 'Pending Approval' ? 'disabled' : '') + '>Return</button>';
    buttons += '<button type="button" data-bpe-transition="approve" ' +
      (tracking.status !== 'Pending Approval' ? 'disabled' : '') + '>Approve</button>';
  }
  buttons += '<button type="button" data-bpe-transition="submit" ' +
    (tracking.status !== 'In Progress' ? 'disabled' : '') + '>Submit for Approval</button>';
  if (tracking.status === 'Completed' && role === 'supervisor') {
    buttons += '<button type="button" data-bpe-transition="reopen" class="ghost">Reopen</button>';
  }
  return '<div class="bpe-approval-row">' + buttons + '</div>';
}

var DETAIL_FOCUS_ATTRIBUTES = [
  'data-bpe-field', 'data-formation-index', 'data-formation-field',
  'data-pay-index', 'data-pay-field', 'data-flow-index', 'data-flow-field',
  'data-bpe-transition', 'data-detail-slug'
];

function focusAttributeValue(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function captureDetailFocus() {
  var root = byId('bpe-detail-view');
  var active = document.activeElement;
  if (!root || !active || !root.contains(active)) return null;
  var selector = active.id ? '#' + active.id : active.tagName.toLowerCase();
  var identified = !!active.id;
  if (!active.id) {
    DETAIL_FOCUS_ATTRIBUTES.forEach(function (name) {
      if (active.hasAttribute(name)) {
        identified = true;
        selector += '[' + name + '="' + focusAttributeValue(active.getAttribute(name)) + '"]';
      }
    });
    if (active.type === 'radio') selector += '[value="' + focusAttributeValue(active.value) + '"]';
  }
  if (!identified) return null;
  var snapshot = { selector: selector, tagName: active.tagName, type: active.type };
  if (active.tagName === 'SELECT' && active.multiple) {
    snapshot.values = all('option:checked', active).map(function (option) { return option.value; });
  } else if (active.type === 'checkbox' || active.type === 'radio') {
    snapshot.checked = active.checked;
  } else if ('value' in active) {
    snapshot.value = active.value;
    try {
      snapshot.selectionStart = active.selectionStart;
      snapshot.selectionEnd = active.selectionEnd;
    } catch (error) { /* Selection is unavailable for number inputs and selects. */ }
  }
  return snapshot;
}

function restoreDetailFocus(snapshot) {
  if (!snapshot) return;
  var element = document.querySelector(snapshot.selector);
  if (!element || element.disabled) return;
  if (snapshot.values) {
    all('option', element).forEach(function (option) { option.selected = snapshot.values.indexOf(option.value) >= 0; });
  } else if (snapshot.checked != null) {
    element.checked = snapshot.checked;
  } else if (snapshot.value != null) {
    element.value = snapshot.value;
  }
  try { element.focus({ preventScroll: true }); } catch (error) { element.focus(); }
  if (snapshot.selectionStart != null) {
    try { element.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd); } catch (error) { /* Unsupported input type. */ }
  }
}

function applyDetailControlLocks() {
  var tracking = (state.detail.tracking || [])[0] || {};
  var approvalLocked = tracking.source === 'approval' &&
    (tracking.status === 'Pending Approval' || tracking.status === 'Completed');
  var comparisonLocked = state.detailSlug === 'sad-model-update' &&
    state.detail.sad_update_branch !== 'manual_update';
  if (!approvalLocked && !comparisonLocked) return;
  all('[data-bpe-field]', byId('bpe-detail-view')).forEach(function (element) {
    if (element.dataset.bpeField !== state.detail.comments_key) element.disabled = true;
  });
}

function renderDetail() {
  var detail = state.detail;
  if (!detail) return;
  var focus = captureDetailFocus();
  byId('bpe-detail-view').innerHTML =
    '<button type="button" id="bpe-back" class="bpe-back ghost">' + icon('arrow-left') + ' Back to Business Plan Execution</button>' +
    '<header class="bpe-detail-head"><div><span>' + esc(detail.detail.stage_label) + '</span><h1>' + esc(detail.project.project_name) + '</h1>' +
    '<p>' + esc(detail.detail.label) + '</p></div>' + topControlsMarkup() + '</header>' +
    '<div class="bpe-detail-grid"><nav class="bpe-detail-nav" aria-label="Business Plan steps">' + detailNavMarkup() + '</nav>' +
    '<section class="bpe-detail-form"><header><h2>' + esc(detail.detail.label) + '</h2>' +
    ((detail.tracking || []).length ? '<span class="bpe-detail-status state-' + esc(detail.tracking[0].color) + '">' +
      statusIcon(detail.tracking[0]) + esc(detail.tracking[0].status) + '</span>' : '') + '</header>' + bodyMarkup() + '</section>' +
    summaryMarkup() + '</div>';
  applyDetailControlLocks();
  bindDetail();
  ensureUsers();
  restoreDetailFocus(focus);
}

function ensureUsers() {
  if (state.users || Store.users) return;
  API.users().then(function (users) {
    state.users = users || [];
    if (byId('bpe-assignee')) {
      var selected = state.detail.assignee || '';
      byId('bpe-assignee').innerHTML = selectOptions(assigneeOptions(), selected);
      byId('bpe-assignee').value = selected;
    }
  }).catch(function () {});
}

function setFeedback(text, error) {
  var feedback = byId('bpe-save-feedback');
  if (!feedback) return;
  feedback.textContent = text || '';
  feedback.classList.toggle('is-error', !!error);
  var retry = byId('bpe-retry-save');
  if (retry) retry.classList.toggle('hidden', !error);
}

function copyRows(rows) {
  return JSON.parse(JSON.stringify(rows || []));
}

function currentContext() {
  return { id: state.contextId, projectId: state.projectId, detailSlug: state.detailSlug };
}

function isCurrentContext(context) {
  return !!context && context.id === state.contextId && context.projectId === state.projectId &&
    context.detailSlug === state.detailSlug;
}

function draftIsCurrent(draft) {
  return !!draft && isCurrentContext(draft.context);
}

function hasCurrentDrafts() {
  return Object.keys(state.fieldDrafts).some(function (key) { return draftIsCurrent(state.fieldDrafts[key]); }) ||
    draftIsCurrent(state.structureDrafts.formations) || draftIsCurrent(state.structureDrafts.flowback);
}

function hasFailedCurrentDrafts() {
  return !!(state.retryCommand && isCurrentContext(state.retryCommand.context)) ||
    Object.keys(state.fieldDrafts).some(function (key) {
    var draft = state.fieldDrafts[key];
    return draftIsCurrent(draft) && draft.failed;
  }) || ['formations', 'flowback'].some(function (key) {
    var draft = state.structureDrafts[key];
    return draftIsCurrent(draft) && draft.failed;
  });
}

function applyCurrentDrafts() {
  if (!state.detail) return;
  state.detail.values = state.detail.values || {};
  Object.keys(state.fieldDrafts).forEach(function (key) {
    var draft = state.fieldDrafts[key];
    if (draftIsCurrent(draft)) state.detail.values[key] = draft.value;
  });
  if (draftIsCurrent(state.structureDrafts.formations)) {
    state.detail.formations = copyRows(state.structureDrafts.formations.rows);
  }
  if (draftIsCurrent(state.structureDrafts.flowback)) {
    state.detail.flowback_stages = copyRows(state.structureDrafts.flowback.rows);
    state.detail.flowback_initialized = true;
  }
}

function mergeReturnedDetail(next) {
  next.folder = next.folder || state.detail.folder;
  next.role = next.role || state.detail.role;
  state.detail = next;
  applyCurrentDrafts();
}

function detailStateSignature(detail) {
  return JSON.stringify({
    branch: detail.sad_update_branch,
    fluid: detail.fluid_state,
    tracking: detail.tracking,
    stage: detail.stage_items
  });
}

function queueSave(work, options) {
  options = options || {};
  var context = options.context || currentContext();
  if (isCurrentContext(context)) setFeedback('Saving...', false);
  var job = state.saveQueue.catch(function () {}).then(function () {
    var before = isCurrentContext(context) && state.detail ? detailStateSignature(state.detail) : null;
    return work().then(function (response) { return { response: response, before: before }; });
  }).then(function (result) {
    var response = result.response;
    if (options.onSuccess) options.onSuccess(response);
    if (!isCurrentContext(context)) return response;
    if (options.merge !== false) {
      var next = response.detail || response;
      mergeReturnedDetail(next);
      if (options.rerender || result.before !== detailStateSignature(state.detail)) renderDetail();
    }
    setFeedback(hasCurrentDrafts() ? 'Saving...' : 'Saved', false);
    return response;
  }).catch(function (error) {
    if (options.onFailure) options.onFailure(error);
    if (isCurrentContext(context)) {
      setFeedback('Save failed', true);
      msg(error.message, 'error');
    }
    return null;
  });
  state.saveQueue = job;
  return job;
}

function queueCommandSave(work, options) {
  options = options || {};
  var context = options.context || currentContext();
  var originalSuccess = options.onSuccess;
  var originalFailure = options.onFailure;
  function run() {
    state.retryCommand = null;
    var commandOptions = Object.assign({}, options, {
      context: context,
      onSuccess: function (response) {
        if (state.retryCommand && state.retryCommand.run === run) state.retryCommand = null;
        if (originalSuccess) originalSuccess(response);
      },
      onFailure: function (error) {
        state.retryCommand = { context: context, run: run };
        if (originalFailure) originalFailure(error);
      }
    });
    return queueSave(work, commandOptions);
  }
  return run();
}

function inputValue(element) {
  if (element.type === 'checkbox') return element.checked;
  if (element.multiple) return all('option:checked', element).map(function (option) { return option.value; });
  return element.value;
}

function enqueueFieldDraft(key, version) {
  var draft = state.fieldDrafts[key];
  if (!draft || draft.version !== version || draft.queuedVersion === version) return state.saveQueue;
  clearTimeout(state.timers[key]);
  draft.queuedVersion = version;
  draft.failed = false;
  var payload = Object.assign({}, draft.payload);
  if (key === 'bp_gate_calculated_td_ft_md' && isCurrentContext(draft.context)) {
    payload.override_reason = value('bp_gate_calculated_td_override_reason') || '';
  }
  var context = draft.context;
  return queueSave(function () {
    return API.saveBusinessPlanField(context.projectId, context.detailSlug, payload);
  }, {
    context: context,
    rerender: draft.rerender,
    onSuccess: function () {
      var latest = state.fieldDrafts[key];
      if (latest && latest.version === version && latest.context.id === context.id) delete state.fieldDrafts[key];
    },
    onFailure: function () {
      var latest = state.fieldDrafts[key];
      if (latest && latest.version === version && latest.context.id === context.id) {
        latest.failed = true;
        latest.queuedVersion = null;
      }
    }
  });
}

function valueFromElement(key) {
  var element = document.querySelector('[data-bpe-field="' + key + '"]');
  return element ? inputValue(element) : value(key);
}

function bindFieldInputs() {
  all('[data-bpe-field]', byId('bpe-detail-view')).forEach(function (element) {
    var immediate = element.type === 'checkbox' || element.type === 'radio' || element.tagName === 'SELECT';
    element.addEventListener(immediate ? 'change' : 'input', function () {
      var key = element.dataset.bpeField;
      var nextValue = inputValue(element);
      var previous = value(key);
      var payload = { field_key: key, value: nextValue, changed_by: currentUserName() };
      if (key === 'bp_gate_classification' && previous && previous !== nextValue) {
        if (!window.confirm('Changing Well Classification will reset classification-driven defaults. Continue?')) {
          renderDetail();
          return;
        }
        payload.confirm_reset = true;
      }
      if (key === 'bp_gate_calculated_td_ft_md') {
        payload.override_reason = valueFromElement('bp_gate_calculated_td_override_reason');
      }
      var version = ++state.saveVersion;
      state.detail.values[key] = nextValue;
      state.fieldDrafts[key] = {
        context: currentContext(), version: version, queuedVersion: null, failed: false,
        value: nextValue, payload: payload,
        rerender: !!CONDITIONAL_FIELDS[key] || element.type === 'checkbox' || element.tagName === 'SELECT'
      };
      state.retryCommand = null;
      setFeedback('Saving...', false);
      clearTimeout(state.timers[key]);
      if (immediate) enqueueFieldDraft(key, version);
      else state.timers[key] = setTimeout(function () { enqueueFieldDraft(key, version); }, state.saveDelay);
    });
  });
}

function markStructureDraft(kind, rows) {
  var draft = {
    context: currentContext(), version: ++state.saveVersion, queuedVersion: null,
    failed: false, rows: copyRows(rows)
  };
  state.structureDrafts[kind] = draft;
  setFeedback('Saving...', false);
  return draft.version;
}

function hydrateFormationDraftIds(draftRows, serverRows) {
  var usedFormationIds = {};
  (draftRows || []).forEach(function (row) { if (row.id != null) usedFormationIds[String(row.id)] = true; });
  (draftRows || []).forEach(function (row, rowIndex) {
    var serverRow = (serverRows || []).find(function (candidate) {
      return candidate.formation === row.formation && !usedFormationIds[String(candidate.id)];
    });
    if (!serverRow && serverRows && serverRows[rowIndex] &&
        !usedFormationIds[String(serverRows[rowIndex].id)]) serverRow = serverRows[rowIndex];
    if (!serverRow) return;
    if (row.id == null) row.id = serverRow.id;
    usedFormationIds[String(serverRow.id)] = true;
    var usedPayIds = {};
    (row.pay_intervals || []).forEach(function (interval) { if (interval.id != null) usedPayIds[String(interval.id)] = true; });
    (row.pay_intervals || []).forEach(function (interval, intervalIndex) {
      if (interval.id != null) return;
      var serverInterval = (serverRow.pay_intervals || [])[intervalIndex];
      if (serverInterval && !usedPayIds[String(serverInterval.id)]) {
        interval.id = serverInterval.id;
        usedPayIds[String(serverInterval.id)] = true;
      }
    });
  });
}

function enqueueStructureDraft(kind, version, rerender) {
  var draft = state.structureDrafts[kind];
  if (!draft || draft.version !== version || draft.queuedVersion === version) return state.saveQueue;
  clearTimeout(state.timers[kind]);
  draft.queuedVersion = version;
  draft.failed = false;
  var context = draft.context;
  var rows = copyRows(draft.rows);
  var work = kind === 'formations' ? function () {
    return API.saveBusinessPlanFormations(context.projectId, context.detailSlug, rows);
  } : function () {
    return API.saveBusinessPlanFlowback(context.projectId, rows);
  };
  return queueSave(work, {
    context: context,
    rerender: !!rerender,
    onSuccess: function (response) {
      var latest = state.structureDrafts[kind];
      if (kind === 'formations' && latest && latest.version !== version && latest.context.id === context.id) {
        hydrateFormationDraftIds(latest.rows, ((response.detail || response).formations || []));
      }
      if (latest && latest.version === version && latest.context.id === context.id) state.structureDrafts[kind] = null;
    },
    onFailure: function () {
      var latest = state.structureDrafts[kind];
      if (latest && latest.version === version && latest.context.id === context.id) {
        latest.failed = true;
        latest.queuedVersion = null;
      }
    }
  });
}

function updateFormationBuffer(element) {
  var formationIndex = Number(element.dataset.formationIndex);
  var row = state.detail.formations[formationIndex];
  if (!row) return;
  if (element.dataset.formationField) row[element.dataset.formationField] = element.value;
  if (element.dataset.payField) {
    var pay = row.pay_intervals[Number(element.dataset.payIndex)];
    if (pay) pay[element.dataset.payField] = element.value;
  }
}

function saveFormationBuffer(rerender) {
  var version = markStructureDraft('formations', state.detail.formations || []);
  return enqueueStructureDraft('formations', version, rerender);
}

function bindFormationInputs() {
  all('[data-formation-field], [data-pay-field]', byId('bpe-detail-view')).forEach(function (element) {
    element.addEventListener(element.tagName === 'SELECT' ? 'change' : 'input', function () {
      updateFormationBuffer(element);
      var version = markStructureDraft('formations', state.detail.formations || []);
      clearTimeout(state.timers.formations);
      state.timers.formations = setTimeout(function () {
        enqueueStructureDraft('formations', version, false);
      }, element.tagName === 'SELECT' ? 0 : state.saveDelay);
    });
  });
  var addFormation = byId('bpe-add-formation');
  if (addFormation) addFormation.addEventListener('click', function () {
    var used = state.detail.formations.map(function (row) { return row.formation; });
    var formation = (state.detail.formation_options || []).find(function (name) { return used.indexOf(name) < 0; }) || 'SARH';
    state.detail.formations.push({ formation: formation, top_tvdss_ft: '', base_tvdss_ft: '', thickness_ft: '', pay_intervals: [
      { top_tvdss_ft: '', base_tvdss_ft: '', phit_pct: '', swt_pct: '', ngr_pct: '', kint_md: '', fluid: '' }
    ] });
    saveFormationBuffer(true);
  });
  all('.bpe-add-pay', byId('bpe-detail-view')).forEach(function (button) {
    button.addEventListener('click', function () {
      state.detail.formations[Number(button.dataset.formationIndex)].pay_intervals.push({
        top_tvdss_ft: '', base_tvdss_ft: '', phit_pct: '', swt_pct: '', ngr_pct: '', kint_md: '', fluid: ''
      });
      saveFormationBuffer(true);
    });
  });
  all('.bpe-remove-pay', byId('bpe-detail-view')).forEach(function (button) {
    button.addEventListener('click', function () {
      if (!window.confirm('Remove this Pay Interval?')) return;
      state.detail.formations[Number(button.dataset.formationIndex)].pay_intervals.splice(Number(button.dataset.payIndex), 1);
      saveFormationBuffer(true);
    });
  });
  all('.bpe-remove-formation', byId('bpe-detail-view')).forEach(function (button) {
    button.addEventListener('click', function () {
      if (!window.confirm('Remove this Formation and its Pay Intervals?')) return;
      state.detail.formations.splice(Number(button.dataset.formationIndex), 1);
      saveFormationBuffer(true);
    });
  });
}

function updateFlowBuffer(element) {
  var row = state.detail.flowback_stages[Number(element.dataset.flowIndex)];
  if (row) row[element.dataset.flowField] = element.value;
}

function saveFlowback(rerender) {
  var version = markStructureDraft('flowback', state.detail.flowback_stages || []);
  return enqueueStructureDraft('flowback', version, rerender);
}

function bindFlowbackInputs() {
  all('[data-flow-field]', byId('bpe-detail-view')).forEach(function (element) {
    element.addEventListener(element.tagName === 'SELECT' ? 'change' : 'input', function () {
      updateFlowBuffer(element);
      var version = markStructureDraft('flowback', state.detail.flowback_stages || []);
      clearTimeout(state.timers.flowback);
      state.timers.flowback = setTimeout(function () {
        enqueueStructureDraft('flowback', version, true);
      }, element.tagName === 'SELECT' ? 0 : state.saveDelay);
    });
  });
  var add = byId('bpe-add-flow');
  if (add) add.addEventListener('click', function () {
    state.detail.flowback_stages.push(blankFlowbackStage());
    saveFlowback(true);
  });
  all('.bpe-remove-flow', byId('bpe-detail-view')).forEach(function (button) {
    button.addEventListener('click', function () {
      if (!window.confirm('Delete this Flowback stage?')) return;
      state.detail.flowback_stages.splice(Number(button.dataset.flowIndex), 1);
      saveFlowback(true);
    });
  });
}

function flushPendingSaves() {
  Object.keys(state.timers).forEach(function (key) {
    clearTimeout(state.timers[key]);
    delete state.timers[key];
  });
  Object.keys(state.fieldDrafts).forEach(function (key) {
    var draft = state.fieldDrafts[key];
    if (draftIsCurrent(draft) && draft.queuedVersion !== draft.version) {
      enqueueFieldDraft(key, draft.version);
    }
  });
  ['formations', 'flowback'].forEach(function (kind) {
    var draft = state.structureDrafts[kind];
    if (draftIsCurrent(draft) && draft.queuedVersion !== draft.version) {
      enqueueStructureDraft(kind, draft.version, true);
    }
  });
  return state.saveQueue.then(function () {
    if (hasFailedCurrentDrafts()) {
      setFeedback('Save failed', true);
      return false;
    }
    if (hasCurrentDrafts()) return flushPendingSaves();
    return true;
  });
}

function transition(action) {
  flushPendingSaves().then(function (saved) {
    if (!saved) return;
    var context = currentContext();
    var comment = value(state.detail.comments_key) || '';
    queueCommandSave(function () {
      return API.transitionBusinessPlan(context.projectId, context.detailSlug, action, comment);
    }, { context: context, rerender: true });
  });
}

function bindDetail() {
  byId('bpe-back').addEventListener('click', refreshBusinessPlan);
  all('.bpe-nav-item', byId('bpe-detail-view')).forEach(function (button) {
    button.addEventListener('click', function () { openBusinessPlanDetail(state.projectId, button.dataset.detailSlug); });
  });
  bindFieldInputs();
  bindFormationInputs();
  bindFlowbackInputs();
  var retry = byId('bpe-retry-save');
  if (retry) retry.addEventListener('click', function () {
    if (state.retryCommand && isCurrentContext(state.retryCommand.context)) {
      state.retryCommand.run();
      return;
    }
    Object.keys(state.fieldDrafts).forEach(function (key) {
      if (draftIsCurrent(state.fieldDrafts[key])) state.fieldDrafts[key].failed = false;
    });
    ['formations', 'flowback'].forEach(function (key) {
      if (draftIsCurrent(state.structureDrafts[key])) state.structureDrafts[key].failed = false;
    });
    flushPendingSaves();
  });
  all('[data-bpe-transition]', byId('bpe-detail-view')).forEach(function (button) {
    button.addEventListener('click', function () { transition(button.dataset.bpeTransition); });
  });
  var copy = byId('bpe-copy-folder');
  if (copy) copy.addEventListener('click', function () {
    navigator.clipboard.writeText((state.detail.folder || {}).path || '').then(function () {
      msg('Shared-folder path copied.', 'success');
    }).catch(function () { msg('The shared-folder path could not be copied.', 'error'); });
  });
  var assignee = byId('bpe-assignee');
  if (assignee) assignee.addEventListener('change', function () {
    var context = currentContext();
    var selected = assignee.value;
    queueCommandSave(function () { return API.assignBusinessPlan(context.projectId, context.detailSlug, selected); }, {
      context: context, rerender: true
    });
  });
  var priority = byId('bpe-priority');
  if (priority) priority.addEventListener('change', function () {
    var context = currentContext();
    var selected = priority.value;
    queueCommandSave(function () {
      return API.projectPriority(context.projectId, { priority: selected, changed_by: currentUserName() });
    }, {
      context: context,
      merge: false,
      onSuccess: function () {
        if (isCurrentContext(context)) state.detail.project.priority = selected;
      }
    });
  });
  var gear = byId('bpe-summary-gear');
  var menu = byId('bpe-summary-menu');
  if (gear && menu) gear.addEventListener('click', function () {
    var open = menu.classList.toggle('hidden') === false;
    gear.setAttribute('aria-expanded', String(open));
  });
  var edit = byId('bpe-edit-all');
  if (edit) edit.addEventListener('click', function () {
    flushPendingSaves().then(function (saved) {
      if (!saved) return;
      var projectId = state.projectId;
      byId('bpe-detail-view').classList.add('hidden');
      import('./project-editor.js').then(function (module) { module.openProjectEditor(projectId); });
    });
  });
}

export function businessPlanTestHooks() {
  return {
    currentFilters: currentFilters,
    statusIcon: statusIcon,
    blankFlowbackStage: blankFlowbackStage,
    configureSaveDelay: function (delay) { state.saveDelay = delay == null ? 500 : delay; },
    flushPendingSaves: flushPendingSaves,
    state: state
  };
}
