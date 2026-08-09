/* Shared, pure markup primitives for the Segment Maturation and BPE detail
   shells. Domain modules keep ownership of state, permissions, requests and
   event wiring; this module owns the visual/DOM contract the two shells share. */
import { esc } from '../dom.js';
import { ICONS } from '../icons.js';

function attributesHtml(attributes) {
  return Object.keys(attributes || {}).map(function (name) {
    var value = attributes[name];
    if (value === null || value === undefined || value === false) return '';
    return ' ' + name + (value === true ? '' : '="' + esc(value) + '"');
  }).join('');
}

export function detailBackButtonHtml(options) {
  var opts = options || {};
  var iconName = opts.icon || 'arrow-left';
  return '<button type="button" id="' + esc(opts.id || 'detail-back') +
    '" class="ghost back-to-board detail-back"' + attributesHtml(opts.attributes) + '>' +
    (ICONS[iconName] || '') + '<span>' + esc(opts.label || 'Back') + '</span></button>';
}

export function detailStepItemHtml(options) {
  var opts = options || {};
  var tag = opts.disabled ? 'div' : 'button';
  var classes = 'component-item status-' + esc(opts.statusSlug || 'not-assigned') +
    (opts.active ? ' active' : '') + (opts.disabled ? ' component-item-future' : '') +
    (opts.className ? ' ' + esc(opts.className) : '');
  return '<' + tag + (tag === 'button' ? ' type="button"' : '') + ' class="' + classes + '"' +
    attributesHtml(opts.attributes) + '><span class="component-num">' + esc(opts.number) +
    '</span><b>' + esc(opts.label || '') + '</b></' + tag + '>';
}

export function detailStageHtml(options) {
  var opts = options || {};
  var open = !!opts.open;
  var stage = opts.stage || '';
  return '<div class="rail-stage rail-stage-lead' + (open ? ' is-active' : '') +
    '" data-stage="' + esc(stage) + '">' +
    '<button type="button" class="rail-stage-head' + (open ? ' open' : '') +
      '" data-stage="' + esc(stage) + '" aria-expanded="' + open + '">' +
      '<span class="stage-icon" aria-hidden="true">' + (ICONS[opts.icon] || '') + '</span>' +
      '<span class="rail-stage-name">' + esc(opts.label || stage) + '</span>' +
      '<span class="rail-stage-count">' + esc(opts.done || 0) + '/' + esc(opts.total || 0) + '</span>' +
      '<span class="rail-stage-chevron" aria-hidden="true">' + (ICONS['chevron-down'] || '') + '</span>' +
    '</button>' +
    '<div class="rail-stage-body' + (open ? '' : ' collapsed') + '" data-stage="' + esc(stage) + '">' +
      (opts.itemsHtml || '') + '</div></div>';
}

export function detailEditorHeaderHtml(options) {
  var opts = options || {};
  return '<div class="editor-head">' +
    '<span class="component-number">' + esc(opts.number || 1) + '</span>' +
    '<div class="editor-title"><h2>' + esc(opts.title || '') + '</h2></div>' +
    (opts.saveStateHtml || '') + (opts.statusHtml || '') + (opts.controlsHtml || '') +
    '</div>';
}

export function assignmentMembersHtml(members, options) {
  var list = members || [];
  var opts = options || {};
  if (!list.length) return '<span class="assignee-chip">Unassigned</span>';
  return list.map(function (member) {
    var source = member.source === 'role' ? 'Role' :
      (member.source === 'creator' ? 'Creator' : 'Manual');
    var removable = member.source !== 'role' && opts.removable !== false;
    var removeAttrs = {};
    if (opts.removeAttribute) removeAttrs[opts.removeAttribute] = member.name;
    if (opts.editable === false) removeAttrs.disabled = true;
    return '<span class="assignee-chip" title="' + source + ' assignment">' + esc(member.name) +
      '<span class="assignee-chip-source">' + source + '</span>' +
      (removable ? '<button type="button" class="assignee-remove" aria-label="Remove ' + esc(member.name) +
        '"' + attributesHtml(removeAttrs) + '>' + ICONS.x + '</button>' : '') + '</span>';
  }).join('');
}
