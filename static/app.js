(function () {
  'use strict';

  var API_VERSION = '12';
  var STAGES = ['Lead Identification', 'Risking', 'Segmentation', 'Pre-Well Delivery', 'Well Delivery', 'Post-Drilling', 'Post-Testing'];
  var PROSPECT_STAGES = ['Lead Identification', 'Risking', 'Segmentation', 'Pre-Well Delivery'];
  var BP_STAGES = ['Well Delivery', 'Post-Drilling', 'Post-Testing'];
  var STATUSES = ['Not Assigned', 'Assigned', 'In Progress', 'Ready for Review', 'Under Review', 'Ready for Approval', 'Returned for Update', 'Approved', 'Not Applicable'];
  var DONE = { 'Approved': 1, 'Not Applicable': 1, 'Complete': 1 };

  var selectedProjectId = null;
  var selectedProject = null;
  var selectedTasks = [];
  var selectedTask = null;
  var selectedAllFields = {};
  var selectedLeadSummary = null;
  var selectedPipeline = 'prospect';

  function byId(id) { return document.getElementById(id); }
  function all(selector, root) { return Array.prototype.slice.call((root || document).querySelectorAll(selector)); }
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>\"]/g, function (char) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[char];
    });
  }
  function compact(value, maxLength) {
    var text = String(value == null || value === '' ? '-' : value);
    var limit = maxLength || 48;
    return text.length <= limit ? text : text.slice(0, limit - 1) + '…';
  }
  function range(start, end) {
    var values = [];
    for (var year = start; year <= end; year += 1) values.push(String(year));
    return values;
  }
  function isFilled(value) { return value !== null && value !== undefined && String(value).trim() !== ''; }
  function truthy(value) { return ['1', 'true', 'yes', 'on'].indexOf(String(value || '').toLowerCase()) >= 0; }
  function stamp() {
    var el = byId('last-refreshed');
    if (el) el.textContent = 'Last refreshed: ' + new Date().toLocaleString();
  }
  function msg(message, type) {
    var el = byId('app-message');
    if (!el) {
      el = document.createElement('div');
      el.id = 'app-message';
      document.body.insertBefore(el, document.body.firstChild);
    }
    el.className = 'app-message ' + (type || 'info');
    el.textContent = message;
    clearTimeout(msg.timer);
    msg.timer = setTimeout(function () {
      el.className = 'app-message';
      el.textContent = '';
    }, 5000);
  }
  function requestUrl(path) {
    return path + (path.indexOf('?') >= 0 ? '&' : '?') + '_v=' + API_VERSION + '&_t=' + Date.now();
  }
  function jsonOptions(method, payload) {
    return { method: method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) };
  }
  function api(path, options) {
    var opts = options || {};
    opts.cache = 'no-store';
    return fetch(requestUrl(path), opts).then(function (response) {
      var contentType = response.headers.get('content-type') || '';
      if (!response.ok) {
        return (contentType.indexOf('json') >= 0 ? response.json() : response.text()).then(function (payload) {
          throw new Error(typeof payload === 'string' ? payload : (payload.detail || payload.message || JSON.stringify(payload)));
        });
      }
      return contentType.indexOf('json') >= 0 ? response.json() : response;
    });
  }

  var API = {
    projects: function (query) {
      var qs = new URLSearchParams(query || {}).toString();
      return api('/api/projects' + (qs ? '?' + qs : ''));
    },
    create: function (payload) { return api('/api/projects', jsonOptions('POST', payload)); },
    project: function (id) { return api('/api/projects/' + id); },
    detail: function (id) { return api('/api/projects/' + id + '/detail'); },
    rename: function (id, payload) { return api('/api/projects/' + id + '/rename', jsonOptions('PATCH', payload)); },
    deleteProject: function (id) { return api('/api/projects/' + id, { method: 'DELETE' }); },
    flags: function (id, payload) { return api('/api/projects/' + id + '/flags', jsonOptions('PATCH', payload)); },
    tasks: function (id) { return api('/api/projects/' + id + '/tasks'); },
    completion: function (id) { return api('/api/projects/' + id + '/completion'); },
    projectFields: function (id) { return api('/api/projects/' + id + '/dynamic-fields'); },
    componentFolder: function (projectId, taskId) { return api('/api/projects/' + projectId + '/component-folder/' + taskId); },
    fields: function (id) { return api('/api/tasks/' + id + '/dynamic-fields'); },
    saveFields: function (id, fields) { return api('/api/tasks/' + id + '/dynamic-fields', jsonOptions('PATCH', { fields: fields, changed_by: 'Web User' })); },
    updateTask: function (id, payload) { return api('/api/tasks/' + id, jsonOptions('PATCH', payload)); },
    priority: function (id, payload) { return api('/api/tasks/' + id + '/priority', jsonOptions('PATCH', payload)); },
    openFolder: function (id, section) { return api('/api/open-folder?project_id=' + encodeURIComponent(id) + '&section=' + encodeURIComponent(section || 'well')); },
    activity: function (projectId) { return api('/api/activity' + (projectId ? '?project_id=' + encodeURIComponent(projectId) : '')); },
    businessRows: function () { return api('/api/business-plan/rows'); },
    portfolioRows: function (query) {
      var qs = new URLSearchParams(query || {}).toString();
      return api('/api/portfolio/rows' + (qs ? '?' + qs : ''));
    }
  };

  function fillSelect(element, values, withAll) {
    if (!element) return;
    var previous = element.value;
    element.innerHTML = (withAll ? '<option>All</option>' : '') + values.map(function (value) {
      return '<option>' + esc(value) + '</option>';
    }).join('');
    if (values.indexOf(previous) >= 0 || (withAll && previous === 'All')) element.value = previous;
  }
  function table(element, headings, rows, onClick) {
    if (!element) return;
    var body = rows.length ? rows.map(function (row, index) {
      return '<tr data-index="' + index + '">' + row.map(function (cell) { return '<td>' + cell + '</td>'; }).join('') + '</tr>';
    }).join('') : '<tr><td colspan="' + headings.length + '" class="empty-state">No records yet.</td></tr>';
    element.innerHTML = '<thead><tr>' + headings.map(function (heading) { return '<th>' + esc(heading) + '</th>'; }).join('') + '</tr></thead><tbody>' + body + '</tbody>';
    if (onClick) {
      all('tbody tr[data-index]', element).forEach(function (rowEl) {
        rowEl.addEventListener('click', function () {
          onClick(Number(rowEl.getAttribute('data-index')));
        });
      });
    }
  }
  function statusChip(status) {
    var value = status || '-';
    return '<span class="status ' + String(value).toLowerCase().replace(/\s+/g, '-') + '">' + esc(value) + '</span>';
  }
  function priorityChip(priority) {
    var value = priority || 'Medium';
    return '<span class="priority priority-' + String(value).toLowerCase() + '">' + esc(value) + '</span>';
  }
  function classChip(value) {
    if (!value) return '';
    return '<span class="class-chip ' + String(value).toLowerCase().replace(/\s+/g, '-') + '">' + esc(value) + '</span>';
  }
  function yesNo(value) { return Number(value || 0) === 1 ? 'Yes' : 'No'; }
  function projectRow(project) {
    return [
      esc(project.project_name),
      esc(compact(project.current_stage)),
      esc(compact(project.current_task)),
      esc(project.current_owner || '-'),
      statusChip(project.overall_status || 'In Progress')
    ];
  }

  function projectMatchesPipeline(project, pipeline) {
    var origin = String(project.pipeline_type || '').toLowerCase();
    if (origin !== 'bp' && origin !== 'prospect') {
      origin = BP_STAGES.indexOf(project.current_stage || '') >= 0 ? 'bp' : 'prospect';
    }
    return origin === pipeline;
  }
  function pipelineStageForProject(project, pipeline) {
    var current = project.current_stage || '';
    if (pipeline === 'bp') return BP_STAGES.indexOf(current) >= 0 ? current : BP_STAGES[0];
    return PROSPECT_STAGES.indexOf(current) >= 0 ? current : PROSPECT_STAGES[PROSPECT_STAGES.length - 1];
  }
  function renderPipeline(element, projects, stages, pipeline) {
    if (!element) return;
    var grouped = {};
    stages.forEach(function (stage) { grouped[stage] = []; });
    projects.forEach(function (project) {
      var stage = pipelineStageForProject(project, pipeline);
      if (grouped[stage]) grouped[stage].push(project);
    });
    element.innerHTML = stages.map(function (stage) {
      var cards = grouped[stage] || [];
      var cardHtml = cards.length ? cards.map(function (project) {
        return '<button type="button" class="pipeline-card" data-project-id="' + project.project_id + '" data-pipeline="' + pipeline + '">' +
          '<strong>' + esc(project.project_name) + '</strong>' +
          '<span class="pipeline-card-component">' + esc(compact(project.current_task || 'No current component', 44)) + '</span>' +
          '<span class="pipeline-card-meta">' + statusChip(project.overall_status || 'In Progress') + priorityChip(project.current_task_priority || 'Medium') + '</span>' +
          '<span class="pipeline-card-assignee">Assignee: ' + esc(project.current_owner || 'Unassigned') + '</span>' +
          '</button>';
      }).join('') : '<div class="pipeline-empty">No leads / wells in this stage.</div>';
      return '<section class="pipeline-column"><header><h3>' + esc(stage) + '</h3><span>' + cards.length + '</span></header><div class="pipeline-cards">' + cardHtml + '</div></section>';
    }).join('');
    all('.pipeline-card', element).forEach(function (card) {
      card.addEventListener('click', function () {
        openDetail(Number(card.getAttribute('data-project-id')), card.getAttribute('data-pipeline'));
      });
    });
  }

  function refreshProspect() {
    var query = { search: byId('prospect-search').value, status_filter: byId('prospect-status-filter').value, pipeline_filter: 'prospect' };
    API.projects(query).then(function (rows) {
      renderPipeline(byId('prospect-pipeline'), rows || [], PROSPECT_STAGES, 'prospect');
      stamp();
    }).catch(function (error) { msg(error.message, 'error'); });
  }
  function refreshBP() {
    var query = {
      search: byId('bp-search').value,
      status_filter: byId('bp-status-filter').value,
      pipeline_filter: 'bp'
    };
    API.projects(query).then(function (projects) {
      var year = byId('bp-year-filter').value;
      var rows = (projects || []).filter(function (project) {
        return year === 'All' || String(project.business_plan_year || '') === year;
      });
      renderPipeline(byId('bp-pipeline'), rows, BP_STAGES, 'bp');
      stamp();
    }).catch(function (error) { msg(error.message, 'error'); });
  }
  function formatNumber(value) {
    var numeric = Number(value);
    if (!isFinite(numeric)) return '0.0';
    return numeric.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }
  function renderPortfolioStats(summary) {
    var element = byId('portfolio-stats');
    if (!element) return;
    summary = summary || {};
    element.innerHTML =
      '<div class="portfolio-stat"><small>Business Plan Wells</small><b>' + esc(summary.business_plan_wells || 0) + '</b></div>' +
      '<div class="portfolio-stat"><small>Cumulative OGIP (BCF)</small><b>' + esc(formatNumber(summary.cumulative_ogip || 0)) + '</b></div>';
  }
  function refreshPortfolio() {
    var year = byId('portfolio-year-filter').value || 'All';
    var activity = byId('portfolio-activity-filter').value || 'All';
    API.portfolioRows({ year: year, activity: activity }).then(function (payload) {
      var rows = (payload && payload.rows) || [];
      renderPortfolioStats((payload && payload.summary) || {});
      table(byId('portfolio-table'), ['Year', 'Well', 'Pre-Drill OGIP (BCF)', 'Post-Drill OGIP (BCF)', 'Chance of Success (%)', 'Class'], rows.map(function (row) {
        return [
          esc(row.year || ''),
          esc(row.well_name || ''),
          esc(row.pre_drill_ogip || ''),
          esc(row.post_drill_ogip || ''),
          esc(row.chance_of_success || ''),
          classChip(row.segment_class || '')
        ];
      }));
      stamp();
    }).catch(function (error) { msg(error.message, 'error'); });
  }
  function auditChange(row) {
    var action = row.action_type || 'Update';
    var comment = row.comment || '';
    if (comment) return '<b>' + esc(action) + '</b><span class="audit-note">' + esc(comment) + '</span>';
    return esc(action);
  }
  function refreshAudit() {
    API.projects({}).then(function (projects) {
      var filter = byId('audit-project-filter');
      var previous = filter.value;
      filter.innerHTML = '<option value="">All leads / wells</option>' + (projects || []).map(function (project) {
        return '<option value="' + project.project_id + '">' + esc(project.project_name) + '</option>';
      }).join('');
      filter.value = previous;
      return API.activity(filter.value);
    }).then(function (rows) {
      table(byId('audit-table'), ['When', 'Lead / Well', 'Component', 'Change', 'By'], (rows || []).map(function (row) {
        return [esc(row.changed_at || ''), esc(row.project_name || ''), esc(row.task_name || ''), auditChange(row), esc(row.changed_by || '')];
      }));
      stamp();
    }).catch(function (error) { msg(error.message, 'error'); });
  }

  function showTab(name) {
    all('.tab').forEach(function (tab) { tab.classList.toggle('active', tab.id === 'tab-' + name); });
    all('.tabs button').forEach(function (button) { button.classList.toggle('active', button.getAttribute('data-tab') === name); });
    byId('detail-shell').classList.add('hidden');
    if (name === 'prospect') refreshProspect();
    if (name === 'bp') refreshBP();
    if (name === 'portfolio') refreshPortfolio();
    if (name === 'audit') refreshAudit();
  }

  function createLead(event) {
    event.preventDefault();
    var name = byId('new-lead-name').value.trim();
    if (!name) return msg('Lead Name is required.', 'error');
    API.create({ project_name: name, pipeline_type: 'prospect', changed_by: 'Web User' }).then(function (result) {
      byId('create-lead-form').reset();
      msg('Lead created.', 'success');
      refreshProspect();
      refreshPortfolio();
      if (result.project_id) openDetail(result.project_id, 'prospect');
    }).catch(function (error) { msg(error.message, 'error'); });
  }
  function addWell(event) {
    event.preventDefault();
    var name = byId('new-well-name').value.trim();
    if (!name) return msg('Well Name is required.', 'error');
    API.create({ project_name: name, business_plan_enabled: true, business_plan_year: byId('new-well-bp-year').value, pipeline_type: 'bp', changed_by: 'Web User' }).then(function (result) {
      byId('add-well-form').reset();
      fillSelect(byId('new-well-bp-year'), range(2026, 2040), false);
      msg('Well added.', 'success');
      refreshBP();
      refreshPortfolio();
      if (result.project_id) openDetail(result.project_id, 'bp');
    }).catch(function (error) { msg(error.message, 'error'); });
  }

  function tasksForPipeline(pipeline) {
    if (pipeline === 'bp') return selectedTasks.filter(function (task) { return BP_STAGES.indexOf(task.stage_group) >= 0; });
    if (pipeline === 'prospect') return selectedTasks.filter(function (task) { return PROSPECT_STAGES.indexOf(task.stage_group) >= 0; });
    return selectedTasks.slice();
  }
  function chooseInitialTask(tasks) {
    if (!tasks.length) return null;
    var currentName = selectedProject && selectedProject.current_task;
    return tasks.find(function (task) { return task.task_name === currentName; }) ||
      tasks.find(function (task) { return !DONE[task.status]; }) || tasks[0];
  }
  function openDetail(projectId, pipeline) {
    selectedProjectId = projectId;
    selectedPipeline = pipeline || 'prospect';
    byId('detail-shell').classList.remove('hidden');
    API.detail(projectId).then(function (detail) {
      selectedProject = detail.project || {};
      selectedTasks = detail.tasks || [];
      selectedAllFields = detail.fields || {};
      selectedLeadSummary = detail.lead_summary || null;
      renderDetail(detail.completion || {});
      loadComponent(chooseInitialTask(tasksForPipeline(selectedPipeline)));
      byId('detail-shell').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }).catch(function (error) { msg(error.message, 'error'); });
  }
  function renderDetail(completion) {
    var tasks = tasksForPipeline(selectedPipeline);
    byId('detail-name').textContent = selectedProject.project_name || 'Lead / Well';
    byId('detail-subtitle').textContent = selectedPipeline === 'bp' ? 'Business Plan Execution' : 'Prospect Maturation';
    byId('back-to-overview').textContent = '← Back to ' + (selectedPipeline === 'bp' ? 'Business Plan Execution' : 'Prospect Maturation');
    byId('component-list').innerHTML = tasks.map(function (task) {
      return '<button type="button" class="component-item ' + (DONE[task.status] ? 'done' : '') + '" data-task-id="' + task.task_id + '"><span>' + esc(task.sequence_no) + '</span><b>' + esc(task.task_name) + '</b><small>' + esc(task.status || 'Not Assigned') + '</small></button>';
    }).join('') || '<div class="empty-state">No components in this pipeline.</div>';
    all('.component-item').forEach(function (button) {
      button.addEventListener('click', function () {
        var taskId = Number(button.getAttribute('data-task-id'));
        loadComponent(selectedTasks.find(function (task) { return task.task_id === taskId; }));
      });
    });
    renderRightPanel(tasks, completion || {});
  }

  function summaryValue(sources, fieldMap) {
    var sourceMap = fieldMap || selectedAllFields;
    for (var i = 0; i < sources.length; i += 1) {
      var component = sources[i][0];
      var key = sources[i][1];
      var componentFields = sourceMap[component] || {};
      if (isFilled(componentFields[key])) return componentFields[key];
    }
    return '';
  }

  var SUMMARY_KEY_ALIASES = {
    'formation_thickness_ft': 'sarah_formation_thickness_ft',
    'quicklook_formation_thickness_ft': 'sarah_formation_thickness_ft',
    'final_formation_thickness_ft': 'sarah_formation_thickness_ft',
    'quicklook_top_sarah_tvdss_ft': 'top_sarah_tvdss_ft',
    'final_top_sarah_tvdss_ft': 'top_sarah_tvdss_ft',
    'quicklook_top_reservoir_tvdss_ft': 'top_reservoir_tvdss_ft',
    'final_top_reservoir_tvdss_ft': 'top_reservoir_tvdss_ft',
    'quicklook_base_reservoir_tvdss_ft': 'base_reservoir_tvdss_ft',
    'final_base_reservoir_tvdss_ft': 'base_reservoir_tvdss_ft',
    'quicklook_base_sarah_tvdss_ft': 'base_sarah_tvdss_ft',
    'final_base_sarah_tvdss_ft': 'base_sarah_tvdss_ft',
    'quicklook_average_porosity_pct': 'average_porosity_pct',
    'final_average_porosity_pct': 'average_porosity_pct',
    'quicklook_average_swt_pct': 'average_swt_pct',
    'final_average_swt_pct': 'average_swt_pct',
    'quicklook_pay_thickness_ft': 'pay_thickness_ft',
    'final_pay_thickness_ft': 'pay_thickness_ft',
    'quicklook_ngr_pct': 'ngr_pct',
    'final_ngr_pct': 'ngr_pct',
    'quicklook_new_formation_thickness_ft': 'new_formation_thickness_ft',
    'final_new_formation_thickness_ft': 'new_formation_thickness_ft',
    'quicklook_top_new_formation_tvdss_ft': 'top_new_formation_tvdss_ft',
    'final_top_new_formation_tvdss_ft': 'top_new_formation_tvdss_ft',
    'quicklook_new_top_reservoir_tvdss_ft': 'new_top_reservoir_tvdss_ft',
    'final_new_top_reservoir_tvdss_ft': 'new_top_reservoir_tvdss_ft',
    'quicklook_new_base_reservoir_tvdss_ft': 'new_base_reservoir_tvdss_ft',
    'final_new_base_reservoir_tvdss_ft': 'new_base_reservoir_tvdss_ft',
    'quicklook_base_new_formation_tvdss_ft': 'base_new_formation_tvdss_ft',
    'final_base_new_formation_tvdss_ft': 'base_new_formation_tvdss_ft',
    'quicklook_new_average_porosity_pct': 'new_average_porosity_pct',
    'final_new_average_porosity_pct': 'new_average_porosity_pct',
    'quicklook_new_average_swt_pct': 'new_average_swt_pct',
    'final_new_average_swt_pct': 'new_average_swt_pct',
    'quicklook_new_pay_thickness_ft': 'new_pay_thickness_ft',
    'final_new_pay_thickness_ft': 'new_pay_thickness_ft',
    'quicklook_new_ngr_pct': 'new_ngr_pct',
    'final_new_ngr_pct': 'new_ngr_pct',
    'lead_piip_gas_p90': 'piip_gas_p90',
    'pre_drill_piip_gas_p90': 'piip_gas_p90',
    'post_drill_piip_gas_p90': 'piip_gas_p90',
    'resource_update_gas_p90': 'piip_gas_p90',
    'lead_piip_gas_mean': 'piip_gas_mean',
    'pre_drill_piip_gas_mean': 'piip_gas_mean',
    'post_drill_piip_gas_mean': 'piip_gas_mean',
    'resource_update_gas_mean': 'piip_gas_mean',
    'lead_piip_gas_p10': 'piip_gas_p10',
    'pre_drill_piip_gas_p10': 'piip_gas_p10',
    'post_drill_piip_gas_p10': 'piip_gas_p10',
    'resource_update_gas_p10': 'piip_gas_p10',
    'lead_piip_liquid_p90': 'piip_liquid_p90',
    'pre_drill_piip_liquid_p90': 'piip_liquid_p90',
    'post_drill_piip_liquid_p90': 'piip_liquid_p90',
    'resource_update_liquid_p90': 'piip_liquid_p90',
    'lead_piip_liquid_mean': 'piip_liquid_mean',
    'pre_drill_piip_liquid_mean': 'piip_liquid_mean',
    'post_drill_piip_liquid_mean': 'piip_liquid_mean',
    'resource_update_liquid_mean': 'piip_liquid_mean',
    'lead_piip_liquid_p10': 'piip_liquid_p10',
    'pre_drill_piip_liquid_p10': 'piip_liquid_p10',
    'post_drill_piip_liquid_p10': 'piip_liquid_p10',
    'resource_update_liquid_p10': 'piip_liquid_p10',
    'ured_p10_area_km2': 'p10_area_km2',
    'ured_p90_area_km2': 'p90_area_km2',
    'presence_reservoir_cos_pct': 'reservoir_cos_pct',
    'presence_trap_cos_pct': 'trap_cos_pct',
    'presence_seal_cos_pct': 'seal_cos_pct'
  };

  var SUMMARY_LABEL_OVERRIDES = {
    'sarah_formation_thickness_ft': 'Sarah Formation Thickness (ft)',
    'top_sarah_tvdss_ft': 'Top Sarah TVDSS (ft)',
    'top_reservoir_tvdss_ft': 'Top Reservoir TVDSS (ft)',
    'base_reservoir_tvdss_ft': 'Base Reservoir TVDSS (ft)',
    'base_sarah_tvdss_ft': 'Base Sarah TVDSS (ft)',
    'average_porosity_pct': 'Average Porosity (%)',
    'average_swt_pct': 'Average Swt (%)',
    'pay_thickness_ft': 'Pay Thickness (ft)',
    'ngr_pct': 'NGR (%)',
    'new_formation_thickness_ft': 'New Formation Thickness (ft)',
    'top_new_formation_tvdss_ft': 'Top New Formation TVDSS (ft)',
    'new_top_reservoir_tvdss_ft': 'Top Reservoir TVDSS (ft)',
    'new_base_reservoir_tvdss_ft': 'Base Reservoir TVDSS (ft)',
    'base_new_formation_tvdss_ft': 'Base New Formation TVDSS (ft)',
    'new_average_porosity_pct': 'Average Porosity (%)',
    'new_average_swt_pct': 'Average Swt (%)',
    'new_pay_thickness_ft': 'Pay Thickness (ft)',
    'new_ngr_pct': 'NGR (%)',
    'piip_gas_p90': 'P90 PIIP Gas (BCF)',
    'piip_gas_mean': 'Mean PIIP Gas (BCF)',
    'piip_gas_p10': 'P10 PIIP Gas (BCF)',
    'piip_liquid_p90': 'P90 PIIP Liquid (MMSTB)',
    'piip_liquid_mean': 'Mean PIIP Liquid (MMSTB)',
    'piip_liquid_p10': 'P10 PIIP Liquid (MMSTB)'
  };

  function labelFromKey(key) {
    return String(key || '').replace(/_/g, ' ').replace(/\b\w/g, function (letter) { return letter.toUpperCase(); });
  }
  function schemaIndex(componentName) {
    var out = {};
    (SCHEMA[componentName] || []).forEach(function (field) { out[field.key] = field; });
    return out;
  }
  function shouldIncludeSummaryValue(value, type) {
    if (type === 'summary' || type === 'link' || type === 'repeatable') return false;
    return type === 'checkbox' ? truthy(value) : isFilled(value);
  }
  function summaryCanonicalKey(key) {
    return SUMMARY_KEY_ALIASES[key] || key;
  }
  function collectEnteredSummaryRows() {
    var latest = {};
    selectedTasks.slice().sort(function (a, b) { return Number(a.sequence_no || 0) - Number(b.sequence_no || 0); }).forEach(function (task) {
      var fields = selectedAllFields[task.task_name] || {};
      var metadata = schemaIndex(task.task_name);
      Object.keys(fields).forEach(function (key) {
        var field = metadata[key] || {};
        var type = field.type || '';
        var value = fields[key];
        if (!shouldIncludeSummaryValue(value, type)) return;
        var canonical = summaryCanonicalKey(key);
        latest[canonical] = {
          component: task.task_name,
          label: SUMMARY_LABEL_OVERRIDES[canonical] || field.label || labelFromKey(key),
          value: type === 'checkbox' ? 'Yes' : value,
          sequence: Number(task.sequence_no || 0)
        };
      });
    });
    return Object.keys(latest).map(function (key) { return latest[key]; }).sort(function (a, b) {
      if (a.sequence !== b.sequence) return a.sequence - b.sequence;
      return String(a.label).localeCompare(String(b.label));
    });
  }
  function summaryItemMarkup(label, value, component, className, valueIsHtml) {
    var source = component ? '<small>' + esc(component) + '</small>' : '';
    var classes = 'summary-item' + (className ? ' ' + className : '');
    return '<div class="' + classes + '"><div class="summary-item-label">' + source + '<span>' + esc(label) + '</span></div><div class="summary-item-value">' + (valueIsHtml ? value : esc(value)) + '</div></div>';
  }
  function enteredSummaryMarkup() {
    return collectEnteredSummaryRows().map(function (entry) {
      return summaryItemMarkup(entry.label, entry.value, entry.component, '', false);
    }).join('');
  }
  function parseRepeatableRows(value) {
    if (Array.isArray(value)) return value;
    try {
      var parsed = JSON.parse(value || '[]');
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) { return []; }
  }
  function reservoirCosSummary(fieldMap) {
    var sourceMap = fieldMap || selectedAllFields;
    var rows = parseRepeatableRows(((sourceMap['Reservoir CoS'] || {}).reservoir_cos_rows) || '[]');
    return rows.map(function (row) {
      var ref = row.seismic_volume_ar_number ? 'AR ' + row.seismic_volume_ar_number + ': ' : '';
      return isFilled(row.reservoir_cos_pct) ? ref + row.reservoir_cos_pct + '%' : '';
    }).filter(Boolean).join(' · ');
  }
  function curatedOverviewMarkup(fieldMap) {
    var sourceMap = fieldMap || selectedAllFields;
    function sourceVal(component, key) { return ((sourceMap[component] || {})[key]) || ''; }
    var rows = [];
    function add(label, value, component) {
      if (isFilled(value)) rows.push(summaryItemMarkup(label, value, component || '', '', false));
    }
    var finalOrQuick = function (finalKey, quickKey) {
      return summaryValue([['Final Log Analysis', finalKey], ['Quicklook Logs Interpretation', quickKey]], sourceMap);
    };
    add('P90 Area (km²)', sourceVal('Reservoir Area Definition', 'p90_area_km2'), 'Reservoir Area Definition');
    add('P10 Area (km²)', sourceVal('Reservoir Area Definition', 'p10_area_km2'), 'Reservoir Area Definition');
    add('Sarah Formation Thickness (ft)', sourceVal('Thickness Estimation', 'formation_thickness_ft'), 'Thickness Estimation');
    add('Reservoir Thickness (ft)', sourceVal('Thickness Estimation', 'reservoir_thickness_ft'), 'Thickness Estimation');
    add('Reservoir CoS (%)', reservoirCosSummary(sourceMap), 'Reservoir CoS');
    add('Trap CoS (%)', sourceVal('Trap CoS', 'trap_cos_pct'), 'Trap CoS');
    add('Seal CoS (%)', sourceVal('Seal CoS', 'seal_cos_pct'), 'Seal CoS');
    add('Presence CoS (%)', sourceVal('Presence CoS Evaluation', 'presence_cos'), 'Presence CoS Evaluation');
    add('Mean PIIP Gas (BCF) — Lead Phase', sourceVal('Lead Resource Assessment', 'lead_piip_gas_mean'), 'Lead Resource Assessment');
    add('Mean PIIP Gas (BCF) — Pre-Drilling', sourceVal('Pre-Drilling Resource Assessment', 'pre_drill_piip_gas_mean'), 'Pre-Drilling Resource Assessment');
    add('Mean PIIP Gas (BCF) — Post-Drilling', sourceVal('Post-Drilling Resource Assessment', 'post_drill_piip_gas_mean'), 'Post-Drilling Resource Assessment');
    add('SARH Formation Prognosis — Pre-Drill', sourceVal('Well Proposal', 'sarh_formation_prognosis_pre_drill'), 'Well Proposal');
    add('SARH Formation Prognosis — Post-Drill', finalOrQuick('final_top_sarah_tvdss_ft', 'quicklook_top_sarah_tvdss_ft'), 'Final / Quicklook Logs');
    add('SARH Formation Thickness (ft) — Pre-Drill', sourceVal('Thickness Estimation', 'formation_thickness_ft'), 'Thickness Estimation');
    add('SARH Formation Thickness (ft) — Post-Drill', finalOrQuick('final_formation_thickness_ft', 'quicklook_formation_thickness_ft'), 'Final / Quicklook Logs');
    add('Pay Thickness (ft)', finalOrQuick('final_pay_thickness_ft', 'quicklook_pay_thickness_ft'), 'Final / Quicklook Logs');
    add('PHIT (%)', finalOrQuick('final_average_porosity_pct', 'quicklook_average_porosity_pct'), 'Final / Quicklook Logs');
    add('SWT (%)', finalOrQuick('final_average_swt_pct', 'quicklook_average_swt_pct'), 'Final / Quicklook Logs');
    add('Fluid Type', finalOrQuick('final_fluid_type', 'quicklook_fluid_type'), 'Final / Quicklook Logs');
    var flowback = sourceMap['Flowback Results'] || {};
    var flowbackMeta = schemaIndex('Flowback Results');
    Object.keys(flowbackMeta).forEach(function (key) {
      if (isFilled(flowback[key])) add(flowbackMeta[key].label, flowback[key], 'Flowback Results');
    });
    return rows.join('');
  }
  function repeatableInputMarkup(field, row, rowIndex) {
    var cols = field.columns || [];
    return '<div class="repeatable-row" data-repeatable-row="' + rowIndex + '">' + cols.map(function (col) {
      var value = row[col.key] == null ? '' : row[col.key];
      var attr = 'data-repeatable-input="' + esc(field.key) + '" data-repeatable-row="' + rowIndex + '" data-repeatable-column="' + esc(col.key) + '"';
      if (col.readonly) {
        return '<label class="calculated-output">' + esc(col.label) + '<output>' + (isFilled(value) ? esc(value) + '%' : 'Calculated on save') + '</output></label>';
      }
      if (col.type === 'select') {
        return '<label>' + esc(col.label) + '<select ' + attr + '>' + (col.options || []).map(function (option) { return '<option value="' + esc(option) + '" ' + (String(value) === String(option) ? 'selected' : '') + '>' + esc(option || 'Select') + '</option>'; }).join('') + '</select></label>';
      }
      return '<label>' + esc(col.label) + '<input type="' + (col.type === 'number' ? 'number' : 'text') + '" step="any" ' + attr + ' value="' + esc(value) + '"></label>';
    }).join('') + '<button type="button" class="ghost remove-repeatable-row" data-repeatable-key="' + esc(field.key) + '" data-repeatable-row="' + rowIndex + '">Remove row</button></div>';
  }
  function renderRepeatableField(field, value) {
    var rows = parseRepeatableRows(value);
    if (!rows.length) rows = [{}];
    return '<div class="repeatable-field wide-field" data-repeatable="' + esc(field.key) + '"><div class="repeatable-heading"><b>' + esc(field.label) + '</b><button type="button" class="secondary add-repeatable-row" data-repeatable-key="' + esc(field.key) + '">Add row</button></div><div class="repeatable-rows">' + rows.map(function (row, index) { return repeatableInputMarkup(field, row || {}, index); }).join('') + '</div></div>';
  }
  function bindRepeatableFields() {
    all('.add-repeatable-row', byId('dynamic-fields')).forEach(function (button) {
      button.addEventListener('click', function () {
        var key = button.getAttribute('data-repeatable-key');
        var field = (SCHEMA[selectedTask.task_name] || []).find(function (item) { return item.key === key; });
        var parent = button.closest('[data-repeatable]');
        var rows = parent.querySelector('.repeatable-rows');
        rows.insertAdjacentHTML('beforeend', repeatableInputMarkup(field, {}, rows.querySelectorAll('.repeatable-row').length));
        bindRepeatableFields();
        previewSummaryInputs();
      });
    });
    all('.remove-repeatable-row', byId('dynamic-fields')).forEach(function (button) {
      button.addEventListener('click', function () {
        var parent = button.closest('[data-repeatable]');
        var rows = parent.querySelectorAll('.repeatable-row');
        if (rows.length === 1) { all('input,select', rows[0]).forEach(function (element) { element.value = ''; }); }
        else { button.closest('.repeatable-row').remove(); }
        previewSummaryInputs();
      });
    });
  }
  function renderRightPanel(tasks) {
    var applicableTasks = tasks.filter(function (task) { return task.status !== 'Not Applicable'; });
    var completed = applicableTasks.filter(function (task) { return DONE[task.status] && task.status !== 'Not Applicable'; }).length;
    var percent = applicableTasks.length ? Math.round((completed / applicableTasks.length) * 100) : 0;
    byId('progress-percent').textContent = percent + '%';
    byId('progress-count').textContent = completed + ' / ' + applicableTasks.length;

    var isBP = Number(selectedProject.business_plan_enabled || 0) === 1;
    var isActive = Number(selectedProject.active_well_enabled || 0) === 1;
    var year = Number(selectedProject.business_plan_year || new Date().getFullYear());
    if (year < 2026 || year > 2040) year = 2026;
    var folder = selectedProject.lead_folder_path || '';
    var items = [summaryItemMarkup('Lead / Well', selectedProject.project_name || '-', '', 'summary-item-primary', false)];
    if (isBP) {
      var yearSelect = '<select id="summary-bp-year" class="summary-year" aria-label="Business Plan Year">' + range(2026, 2040).map(function (value) { return '<option ' + (Number(value) === year ? 'selected' : '') + '>' + value + '</option>'; }).join('') + '</select>';
      items.push(summaryItemMarkup('BP Year', yearSelect, '', 'summary-item-control', true));
    }
    var recordKind = String(selectedProject.pipeline_type || '').toLowerCase() === 'bp' ? 'Well' : 'Lead';
    var leadSnapshotFields = (selectedLeadSummary && selectedLeadSummary.fields) || {};
    var hasLeadSnapshot = recordKind === 'Well' && Object.keys(leadSnapshotFields).length > 0;
    var leadSnapshotHtml = hasLeadSnapshot ?
      '<div class="lead-summary-toggle"><button id="toggle-lead-summary" type="button" class="ghost">Lead Summary</button></div>' +
      '<div id="lead-summary-snapshot" class="summary-grid hidden"><div class="summary-item summary-item-primary"><div class="summary-item-label"><span>Lead Summary at BP Promotion</span></div><div class="summary-item-value">Captured ' + esc((selectedLeadSummary && selectedLeadSummary.captured_at) || '') + '</div></div>' + curatedOverviewMarkup(leadSnapshotFields) + '</div>' : '';
    var summaryHtml =
      '<div class="flag-controls"><label><input id="summary-bp-flag" type="checkbox" ' + (isBP ? 'checked' : '') + '> Business Plan</label><label><input id="summary-active-flag" type="checkbox" ' + (isActive ? 'checked' : '') + '> Active Well</label></div>' +
      '<div class="summary-grid">' + items.join('') + curatedOverviewMarkup() + '</div>' +
      leadSnapshotHtml +
      '<div class="record-actions"><button id="rename-record" type="button" class="ghost">Rename ' + recordKind + '</button><button id="delete-record" type="button" class="danger">Archive ' + recordKind + '</button></div>';
    byId('summary-title').textContent = recordKind + ' Summary';
    byId('lead-summary').innerHTML = summaryHtml;

    var copyButtonElement = byId('copy-lead-folder');
    if (copyButtonElement) copyButtonElement.addEventListener('click', function () { copyText(folder); });
    var bpFlag = byId('summary-bp-flag');
    var activeFlag = byId('summary-active-flag');
    var bpYear = byId('summary-bp-year');
    if (bpFlag) bpFlag.addEventListener('change', function () { saveProjectFlags({ business_plan_enabled: bpFlag.checked, business_plan_year: bpFlag.checked ? year : null }); });
    if (activeFlag) activeFlag.addEventListener('change', function () { saveProjectFlags({ active_well_enabled: activeFlag.checked }); });
    if (bpYear) bpYear.addEventListener('change', function () { saveProjectFlags({ business_plan_enabled: true, business_plan_year: bpYear.value }); });
    var leadSummaryToggle = byId('toggle-lead-summary');
    if (leadSummaryToggle) leadSummaryToggle.addEventListener('click', function () {
      var panel = byId('lead-summary-snapshot');
      if (!panel) return;
      var opening = panel.classList.contains('hidden');
      panel.classList.toggle('hidden', !opening);
      leadSummaryToggle.textContent = opening ? 'Hide Lead Summary' : 'Lead Summary';
    });
    var renameButton = byId('rename-record');
    var deleteButton = byId('delete-record');
    if (renameButton) renameButton.addEventListener('click', renameSelectedProject);
    if (deleteButton) deleteButton.addEventListener('click', deleteSelectedProject);
  }
  function refreshAfterRecordChange(message) {
    return API.detail(selectedProjectId)
      .then(function (detail) {
        var currentTaskId = selectedTask && selectedTask.task_id;
        selectedProject = detail.project || {};
        selectedTasks = detail.tasks || [];
        selectedAllFields = detail.fields || {};
        selectedLeadSummary = detail.lead_summary || null;
        renderDetail(detail.completion || {});
        loadComponent(selectedTasks.find(function (task) { return task.task_id === currentTaskId; }) || chooseInitialTask(tasksForPipeline(selectedPipeline)));
        refreshProspect();
        refreshBP();
        refreshPortfolio();
        if (message) msg(message, 'success');
      });
  }
  function renameSelectedProject() {
    if (!selectedProjectId || !selectedProject) return;
    var recordKind = String(selectedProject.pipeline_type || '').toLowerCase() === 'bp' ? 'Well' : 'Lead';
    var nextName = window.prompt('Rename ' + recordKind, selectedProject.project_name || '');
    if (nextName === null) return;
    nextName = nextName.trim();
    if (!nextName) return msg(recordKind + ' name is required.', 'error');
    if (nextName === String(selectedProject.project_name || '').trim()) return;
    API.rename(selectedProjectId, { new_name: nextName, changed_by: 'Web User' })
      .then(function () { return refreshAfterRecordChange(recordKind + ' renamed.'); })
      .catch(function (error) { msg(error.message, 'error'); });
  }
  function deleteSelectedProject() {
    if (!selectedProjectId || !selectedProject) return;
    var recordKind = String(selectedProject.pipeline_type || '').toLowerCase() === 'bp' ? 'Well' : 'Lead';
    var name = selectedProject.project_name || recordKind;
    if (!window.confirm('Archive ' + recordKind.toLowerCase() + ' "' + name + '"? Its components, saved inputs, and audit trail will be preserved.')) return;
    API.deleteProject(selectedProjectId).then(function () {
      selectedProjectId = null;
      selectedProject = null;
      selectedTasks = [];
      selectedTask = null;
      selectedAllFields = {};
      byId('detail-shell').classList.add('hidden');
      refreshProspect();
      refreshBP();
      refreshPortfolio();
      refreshAudit();
      msg(recordKind + ' archived.', 'success');
    }).catch(function (error) { msg(error.message, 'error'); });
  }

  function saveProjectFlags(payload) {
    if (!selectedProjectId) return;
    payload.changed_by = 'Web User';
    API.flags(selectedProjectId, payload).then(function () {
      return API.detail(selectedProjectId);
    }).then(function (detail) {
      selectedProject = detail.project || {};
      selectedTasks = detail.tasks || [];
      selectedAllFields = detail.fields || {};
      selectedLeadSummary = detail.lead_summary || null;
      selectedPipeline = String(selectedProject.pipeline_type || '').toLowerCase() === 'bp' ? 'bp' : 'prospect';
      renderDetail(detail.completion || {});
      loadComponent(chooseInitialTask(tasksForPipeline(selectedPipeline)));
      refreshProspect();
      refreshBP();
      refreshPortfolio();
      msg('Lead / well flags updated.', 'success');
    }).catch(function (error) { msg(error.message, 'error'); });
  }

  function piip(prefix) {
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
  var quickNew = [
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
  var finalNew = quickNew.map(function (field) { var clone = Object.assign({}, field); clone.key = clone.key.replace('quicklook', 'final'); if (clone.showIf) clone.showIf = clone.showIf.replace('quicklook', 'final'); return clone; });
  var FLUID_TYPES = ['', 'Gas', 'Gas over Water', 'Wet', 'Tight'];
  var RESERVOIR_COS_COLUMNS = [
    { key: 'seismic_volume_ar_number', label: 'Seismic Volume AR Number', type: 'text' },
    { key: 'amplitude_ratio', label: 'Amplitude Ratio', type: 'number' },
    { key: 'base_tight_sarah', label: 'Base Tight Sarah', type: 'number' },
    { key: 'pull_up', label: 'Pull-up', type: 'select', options: ['', 'No', 'Semi', 'Yes'] },
    { key: 'reservoir_cos_pct', label: 'Reservoir CoS (%)', type: 'number', readonly: true }
  ];
  var SCHEMA = {
    'Reservoir Area Definition': [{ key: 'p10_area_km2', label: 'P10 Area (km²)', type: 'number' }, { key: 'p90_area_km2', label: 'P90 Area (km²)', type: 'number' }],
    'Thickness Estimation': [{ key: 'formation_thickness_ft', label: 'Sarah Formation Thickness (ft)', type: 'number' }, { key: 'reservoir_thickness_ft', label: 'Reservoir Thickness (ft)', type: 'number' }],
    'Lead Resource Assessment': piip('lead_piip').concat([{ key: 'lead_calculation_method', label: 'Calculation Method', type: 'select', options: ['', 'GRV', 'Box Model'] }]),
    'Seismic Signature Validation': [],
    'Reservoir CoS': [{ key: 'reservoir_cos_rows', label: 'Reservoir CoS Evaluations', type: 'repeatable', columns: RESERVOIR_COS_COLUMNS }],
    'Trap CoS': [{ key: 'sarah_quwarah_thickness_ft', label: 'Sarah-Quwarah Thickness (ft)', type: 'number' }, { key: 'trap_cos_pct', label: 'Trap CoS (%)', type: 'number' }],
    'Seal CoS': [{ key: 'seal_recent_activity_age', label: 'Most recent age of activity', type: 'number' }, { key: 'seal_dip', label: 'Dip', type: 'number' }, { key: 'seal_azimuth_vs_shmax', label: 'Azimuth vs. SHmax', type: 'number' }, { key: 'seal_fault_level_confidence', label: 'Fault Level of Confidence', type: 'number' }, { key: 'seal_fracture_permeability', label: 'Fracture Permeability', type: 'number' }, { key: 'seal_cos_pct', label: 'Seal CoS (%)', type: 'number', readonly: true }],
    'Presence CoS Evaluation': [{ key: 'presence_reservoir_cos_pct', label: 'Final Reservoir CoS (%)', type: 'number', readonly: true }, { key: 'presence_trap_cos_pct', label: 'Trap CoS (%)', type: 'number', readonly: true }, { key: 'presence_seal_cos_pct', label: 'Seal CoS (%)', type: 'number', readonly: true }, { key: 'presence_cos', label: 'Presence CoS (%)', type: 'number', readonly: true }],
    'Pre-Drilling Resource Assessment': piip('pre_drill_piip'),
    'Staking Moving Tolerance': [{ key: 'moving_original_location_x', label: 'Original Location X', type: 'number' }, { key: 'moving_original_location_y', label: 'Original Location Y', type: 'number' }, { key: 'moving_option_1_x', label: 'Options 1 X', type: 'number' }, { key: 'moving_option_1_y', label: 'Options 1 Y', type: 'number' }, { key: 'moving_option_2_x', label: 'Options 2 X', type: 'number' }, { key: 'moving_option_2_y', label: 'Options 2 Y', type: 'number' }, { key: 'moving_option_3_x', label: 'Options 3 X', type: 'number' }, { key: 'moving_option_3_y', label: 'Options 3 Y', type: 'number' }],
    'Approval to Stake': [],
    'Well Proposal': [{ key: 'sarh_formation_prognosis_pre_drill', label: 'SARH Formation Prognosis (Pre-Drill)', type: 'text' }, { key: 'vsp_required', label: 'VSP Required?', type: 'select', options: ['', 'No', 'Yes'] }, { key: 'vsp_request_link', label: 'New Request Placeholder', type: 'link', value: '#' }],
    'GHEER': [{ key: 'gheer_base_map', label: 'Base Map', type: 'checkbox' }, { key: 'gheer_offset_wells', label: 'Offset Wells', type: 'checkbox' }, { key: 'gheer_target_polygon', label: 'Target Drilling Polygon (50x50 m)', type: 'checkbox' }, { key: 'gheer_prognosis_tops', label: 'Prognosis Tops', type: 'checkbox' }, { key: 'gheer_depth_top_sarah_grid', label: 'Depth Top Sarah Formation Grid', type: 'checkbox' }, { key: 'gheer_drilling_hazards', label: 'Drilling Hazards', type: 'checkbox' }, { key: 'gheer_pore_pressure_fracture_gradient', label: 'Pore Pressure Gradient and Fracture Gradient', type: 'checkbox' }, { key: 'gheer_wellbore_stability', label: 'Wellbore Stability', type: 'checkbox' }],
    'Quicklook Logs Interpretation': [{ key: 'quicklook_formation_thickness_ft', label: 'Sarah Formation Thickness (ft)', type: 'number' }, { key: 'quicklook_top_sarah_tvdss_ft', label: 'Top Sarah TVDSS (ft)', type: 'number' }, { key: 'quicklook_top_reservoir_tvdss_ft', label: 'Top Reservoir TVDSS (ft)', type: 'number' }, { key: 'quicklook_base_reservoir_tvdss_ft', label: 'Base Reservoir TVDSS (ft)', type: 'number' }, { key: 'quicklook_base_sarah_tvdss_ft', label: 'Base Sarah TVDSS (ft)', type: 'number' }, { key: 'quicklook_average_porosity_pct', label: 'Average Porosity (%)', type: 'number' }, { key: 'quicklook_average_swt_pct', label: 'Average Swt (%)', type: 'number' }, { key: 'quicklook_pay_thickness_ft', label: 'Pay Thickness (ft)', type: 'number' }, { key: 'quicklook_ngr_pct', label: 'NGR (%)', type: 'number' }, { key: 'quicklook_fluid_type', label: 'Fluid Type', type: 'select', options: FLUID_TYPES }, { key: 'quicklook_pdf', label: 'Logs in PDF', type: 'checkbox' }, { key: 'quicklook_las', label: 'Logs as LAS', type: 'checkbox' }].concat(quickNew),
    'Aramco Picks': [],
    'Post-Drilling Resource Assessment': piip('post_drill_piip'),
    'SAD Model': [],
    'Executive Summary': [],
    'URED Update': [],
    'Flowback Results': [{ key: 'flowback_gas_rate_mmscfd', label: 'Gas Rate (MMSCFD)', type: 'number' }, { key: 'flowback_water_rate_bwpd', label: 'Water Rate (BWPD)', type: 'number' }, { key: 'flowback_choke_size_in', label: 'Choke Size (in)', type: 'number' }, { key: 'flowback_fwhp_psi', label: 'FWHP (psi)', type: 'number' }, { key: 'flowback_dynamic_area_km2', label: 'Dynamic Reservoir Area (km²)', type: 'number' }, { key: 'flowback_dynamic_ogip_bcf', label: 'Dynamic OGIP (BCF)', type: 'number' }, { key: 'flowback_sheet', label: 'Flowback Sheet', type: 'text' }, { key: 'flowback_slide', label: 'Flowback Slide', type: 'text' }],
    'SAD Update': [],
    'Final Log Analysis': [{ key: 'final_formation_thickness_ft', label: 'Sarah Formation Thickness (ft)', type: 'number' }, { key: 'final_top_sarah_tvdss_ft', label: 'Top Sarah TVDSS (ft)', type: 'number' }, { key: 'final_top_reservoir_tvdss_ft', label: 'Top Reservoir TVDSS (ft)', type: 'number' }, { key: 'final_base_reservoir_tvdss_ft', label: 'Base Reservoir TVDSS (ft)', type: 'number' }, { key: 'final_base_sarah_tvdss_ft', label: 'Base Sarah TVDSS (ft)', type: 'number' }, { key: 'final_average_porosity_pct', label: 'Average Porosity (%)', type: 'number' }, { key: 'final_average_swt_pct', label: 'Average Swt (%)', type: 'number' }, { key: 'final_pay_thickness_ft', label: 'Pay Thickness (ft)', type: 'number' }, { key: 'final_ngr_pct', label: 'NGR (%)', type: 'number' }, { key: 'final_fluid_type', label: 'Fluid Type', type: 'select', options: FLUID_TYPES }, { key: 'final_pdf', label: 'Logs in PDF', type: 'checkbox' }, { key: 'final_las', label: 'Logs as LAS', type: 'checkbox' }, { key: 'final_petrel', label: 'Logs in Petrel', type: 'checkbox' }].concat(finalNew),
    'PVAD Structural MTR': [{ key: 'pvad_mtr_link', label: 'Hyperlink Placeholder', type: 'text' }],
    'Resource Assessment Update': piip('resource_update').concat([{ key: 'resource_update_note', label: '', type: 'summary' }]),
    'Prospect Evaluation Presentation': [], 'Well Creation': [], 'BP Execution Gate': [], 'Site Preparation': [], 'Post-Well Outcome & Decision Gate': [], 'Executive Summary Final': [], 'PDA': [], 'Approval To Drill': []
  };

  function loadComponent(task) {
    if (!task) return;
    selectedTask = task;
    all('.component-item').forEach(function (button) { button.classList.toggle('active', Number(button.getAttribute('data-task-id')) === task.task_id); });
    byId('component-number').textContent = String(task.sequence_no || '');
    byId('component-title').textContent = task.task_name;
    byId('component-stage').textContent = task.stage_group;
    byId('assigned-to').value = task.assigned_to || '';
    fillSelect(byId('component-status'), STATUSES, false);
    byId('component-status').value = task.status || 'Not Assigned';
    byId('component-priority').value = task.priority || 'Medium';
    byId('comments').placeholder = commentPlaceholder(task.task_name);
    byId('comments').value = task.comments || '';
    Promise.all([API.fields(task.task_id), API.componentFolder(selectedProjectId, task.task_id)]).then(function (results) {
      renderFields(task.task_name, results[0] || {});
      renderComponentFolder(results[1] || {});
      renderRightPanel(tasksForPipeline(selectedPipeline));
    }).catch(function (error) { msg(error.message, 'error'); });
  }
  function commentPlaceholder(componentName) {
    if (componentName === 'Approval To Drill') return 'Include the requirement for the Approval to Drill letter';
    return 'Comments, assumptions, rationale, or required notes...';
  }
  function renderFields(componentName, values) {
    var fields = SCHEMA[componentName] || [];
    var html = '';
    fields.forEach(function (field) {
      var value = values[field.key] != null ? values[field.key] : (field.value || '');
      var hidden = field.showIf && !truthy(values[field.showIf]);
      var classes = (hidden ? ' conditional hidden' : ' conditional') + (field.type === 'text' ? ' wide-field' : '');
      if (field.type === 'repeatable') {
        html += renderRepeatableField(field, value);
      } else if (field.readonly) {
        html += '<label class="calculated-output' + classes + '" data-show-if="' + esc(field.showIf || '') + '">' + esc(field.label) + '<output>' + (isFilled(value) ? esc(value) + '%' : 'Calculated on save') + '</output></label>';
      } else if (field.type === 'select') {
        html += '<label class="' + classes + '" data-show-if="' + esc(field.showIf || '') + '">' + esc(field.label) + '<select data-field="' + esc(field.key) + '">' + (field.options || []).map(function (option) { return '<option ' + (String(value) === String(option) ? 'selected' : '') + '>' + esc(option) + '</option>'; }).join('') + '</select></label>';
      } else if (field.type === 'checkbox') {
        html += '<label class="check-label' + classes + '" data-show-if="' + esc(field.showIf || '') + '"><input type="checkbox" data-field="' + esc(field.key) + '" ' + (truthy(value) ? 'checked' : '') + '> ' + esc(field.label) + '</label>';
      } else if (field.type === 'text') {
        html += '<label class="' + classes + '" data-show-if="' + esc(field.showIf || '') + '">' + esc(field.label) + '<input data-field="' + esc(field.key) + '" value="' + esc(value) + '"></label>';
      } else if (field.type === 'link') {
        html += '<div class="summary-box' + classes + '"><b>' + esc(field.label) + '</b><p><a href="' + esc(field.value || '#') + '" target="_blank" rel="noreferrer">New Request</a></p></div>';
      } else if (field.type === 'summary') {
        var summaryHtml = autoSummaryHtml(componentName);
        if (summaryHtml) html += '<div class="summary-box' + classes + '">' + summaryHtml + '</div>';
      } else {
        html += '<label class="' + classes + '" data-show-if="' + esc(field.showIf || '') + '">' + esc(field.label) + '<input type="number" step="any" data-field="' + esc(field.key) + '" value="' + esc(value) + '"></label>';
      }
    });
    byId('dynamic-fields').innerHTML = html;
    all('[data-field], [data-repeatable-input]', byId('dynamic-fields')).forEach(function (element) {
      function syncPreview() {
        updateConditionalVisibility();
        previewSummaryInputs();
      }
      element.addEventListener('change', syncPreview);
      element.addEventListener('input', syncPreview);
    });
    bindRepeatableFields();
    updateConditionalVisibility();
  }
  function val(component, key) {
    var value = ((selectedAllFields || {})[component] || {})[key];
    return isFilled(value) ? value : '';
  }
  function autoSummaryHtml(componentName) {
    if (componentName !== 'Resource Assessment Update') return '';
    var rows = [];
    function add(label, value) {
      if (isFilled(value)) rows.push('<li><span>' + esc(label) + '</span><b>' + esc(value) + '</b></li>');
    }
    add('Dynamic OGIP (BCF)', val('Flowback Results', 'flowback_dynamic_ogip_bcf'));
    add('Post-Drilling Mean PIIP Gas (BCF)', val('Post-Drilling Resource Assessment', 'post_drill_piip_gas_mean'));
    return rows.length ? '<ul class="summary-list">' + rows.join('') + '</ul>' : '';
  }

  function renderComponentFolder(info) {
    var previous = byId('component-folder-card');
    if (previous) previous.remove();
    if (!info || !Number(info.requires_folder)) return;
    var card = document.createElement('div');
    card.id = 'component-folder-card';
    card.className = 'folder-card';
    card.innerHTML = '<b>Component File Location</b><p>' + esc(info.unc_path || 'Folder path placeholder not configured.') + '</p><button type="button" class="secondary" id="copy-component-folder">Copy Folder Link</button>';
    var container = byId('dynamic-fields');
    container.parentNode.insertBefore(card, container.nextSibling);
    byId('copy-component-folder').addEventListener('click', function () { copyText(info.unc_path || ''); });
  }
  function updateConditionalVisibility() {
    var fields = getFields();
    all('[data-show-if]', byId('dynamic-fields')).forEach(function (element) {
      var key = element.getAttribute('data-show-if');
      if (key) element.classList.toggle('hidden', !truthy(fields[key]));
    });
  }
  function getFields() {
    var fields = {};
    all('[data-field]', byId('dynamic-fields')).forEach(function (element) {
      fields[element.getAttribute('data-field')] = element.type === 'checkbox' ? (element.checked ? '1' : '') : element.value;
    });
    all('[data-repeatable]', byId('dynamic-fields')).forEach(function (container) {
      var key = container.getAttribute('data-repeatable');
      var rows = [];
      all('.repeatable-row', container).forEach(function (row) {
        var data = {};
        all('[data-repeatable-input]', row).forEach(function (element) {
          data[element.getAttribute('data-repeatable-column')] = element.value;
        });
        if (Object.keys(data).some(function (column) { return isFilled(data[column]); })) rows.push(data);
      });
      fields[key] = JSON.stringify(rows);
    });
    return fields;
  }
  function previewSummaryInputs() {
    if (!selectedTask) return;
    var saved = selectedAllFields[selectedTask.task_name] || {};
    selectedAllFields[selectedTask.task_name] = Object.assign({}, saved, getFields());
    renderRightPanel(tasksForPipeline(selectedPipeline));
  }
  function copyText(text) {
    if (!text) return msg('No folder path to copy.', 'error');
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(function () { msg('Folder link copied.', 'success'); }).catch(function () { fallbackCopy(text); });
    } else {
      fallbackCopy(text);
    }
  }
  function fallbackCopy(text) {
    var area = document.createElement('textarea');
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
    msg('Folder link copied.', 'success');
  }
  function saveComponent(event) {
    event.preventDefault();
    if (!selectedTask) return;
    var fields = getFields();
    API.updateTask(selectedTask.task_id, {
      status: byId('component-status').value,
      assigned_to: byId('assigned-to').value,
      comments: byId('comments').value,
      priority: byId('component-priority').value,
      fields: fields,
      revision: selectedTask.revision,
      changed_by: 'Web User',
      business_plan_enabled: Number(selectedProject.business_plan_enabled || 0) === 1,
      business_plan_year: selectedProject.business_plan_year
    }).then(function () {
      return API.detail(selectedProjectId);
    }).then(function (detail) {
      var selectedTaskId = selectedTask.task_id;
      selectedProject = detail.project || {};
      selectedTasks = detail.tasks || [];
      selectedAllFields = detail.fields || {};
      selectedLeadSummary = detail.lead_summary || null;
      renderDetail(detail.completion || {});
      loadComponent(selectedTasks.find(function (task) { return task.task_id === selectedTaskId; }) || chooseInitialTask(tasksForPipeline(selectedPipeline)));
      refreshProspect();
      refreshBP();
      refreshPortfolio();
      msg('Component saved.', 'success');
    }).catch(function (error) { msg(error.message, 'error'); });
  }
  function openClientFolderLink(info) {
    var fileUrl = (info || {}).file_url || '';
    var path = (info || {}).unc_path || (info || {}).path || '';
    if (fileUrl) {
      window.location.href = fileUrl;
      msg('Folder link opened. If blocked, copy the folder path from the summary.', 'info');
    } else {
      msg(path ? 'Folder path: ' + path : 'Folder path placeholder not configured.', 'info');
    }
  }
  function safeOn(id, event, handler) { var element = byId(id); if (element) element.addEventListener(event, handler); }
  function wire() {
    all('.tabs button').forEach(function (button) { button.addEventListener('click', function () { showTab(button.getAttribute('data-tab')); }); });
    safeOn('export-excel', 'click', function () { window.location.href = '/api/export/excel'; });
    safeOn('create-lead-form', 'submit', createLead);
    safeOn('add-well-form', 'submit', addWell);
    safeOn('component-form', 'submit', saveComponent);
    safeOn('back-to-overview', 'click', function () { byId('detail-shell').classList.add('hidden'); byId('tab-' + selectedPipeline).scrollIntoView({ behavior: 'smooth', block: 'start' }); });
    safeOn('open-folder', 'click', function () { if (selectedProjectId) API.openFolder(selectedProjectId, 'well').then(openClientFolderLink).catch(function (error) { msg(error.message, 'error'); }); });
    safeOn('upload-files', 'click', function () { msg('Upload Files button placeholder only. File upload is not enabled yet.', 'info'); });
    ['prospect-search', 'prospect-status-filter'].forEach(function (id) { safeOn(id, 'input', refreshProspect); safeOn(id, 'change', refreshProspect); });
    ['bp-search', 'bp-year-filter', 'bp-status-filter'].forEach(function (id) { safeOn(id, 'input', refreshBP); safeOn(id, 'change', refreshBP); });
    ['portfolio-year-filter', 'portfolio-activity-filter'].forEach(function (id) { safeOn(id, 'change', refreshPortfolio); });
    safeOn('audit-project-filter', 'change', refreshAudit);
  }
  document.addEventListener('DOMContentLoaded', function () {
    fillSelect(byId('prospect-status-filter'), STATUSES, true);
    fillSelect(byId('bp-status-filter'), STATUSES, true);
    fillSelect(byId('portfolio-year-filter'), range(2026, 2040), true);
    fillSelect(byId('new-well-bp-year'), range(2026, 2040), false);
    fillSelect(byId('bp-year-filter'), range(2026, 2040), true);
    wire();
    showTab('prospect');
  });
}());
