# Front-end guide

This is the front end for the Segment Maturation and Execution System. It's plain HTML/CSS/JS —
no framework, no build step, no npm, no external dependencies. You edit a file, refresh the
browser, done. This guide is aimed at someone who hasn't touched this codebase before and needs
to make changes ranging from "make the buttons square" to "add a whole new tab."

Run it locally: `python3 main.py` from the repo root, then open `http://127.0.0.1:8020`. Every
edit takes effect on the next browser refresh — there is nothing to rebuild or restart (except
the Python process itself, and only if you touch backend files).

## 1. The file map

```
static/
  index.html                 the only HTML page — a single-page app with 6 "screens"
  css/
    base.css                 design tokens (colors/spacing/etc) + page shell (header, tabs, panels, forms, buttons, tables)
    components.css            everything screen-specific (pipeline board, chips, detail panel, dialog, portfolio, audit)
  js/
    dom.js                    tiny generic helpers: find elements, escape text, build a <table>, render a status/priority chip
    api.js                    every backend call lives here — one function per endpoint
    state.js                  the one object holding "what's currently on screen" + the signed-in user (currentUserName())
    schema.js                 the data that describes each workflow component's form fields (this is the biggest lever for change — see §4)
    dialog.js                 the confirm/rename popup + the sign-in dialog (replaces the browser's ugly native confirm/prompt)
    auth.js                   api.js-free login primitives shared by the boot-time login page (main.js) and the mid-session login dialog (dialog.js)
    icons.js                  the vendored Lucide icons inlined as SVG strings, keyed by icon name (source SVGs live in static/icons/)
    navigation.js             activateTab(): the DOM-only "show this tab" helper — it decides nothing about what to fetch
    main.js                   boots the app: wires up the tab buttons and the one static form (#component-form)
    views/
      pipeline.js              Prospect Maturation + Business Plan Execution tabs (the kanban-style boards)
      lead-filters.js          the Segment Maturation filter row (Assignee/Field/Status/Priority) AND the one
                               filtered-leads selector the board — and later the KPIs — render from
      lead-create.js           the "+ Add New Lead" control: expand-in-place, three inline fields, Enter to
                               create, Escape/outside-click to cancel, inline validation (no modal, no form)
      lead-kpis.js             the three Segment Maturation KPI tiles (completion donut, Total Active Leads,
                               Total Mean OGIP) — computed from the same filtered rowset the board renders
      detail.js                the right-hand detail panel: compact summary card, rename/delete, BP/Active flags
      detail-form.js           the middle detail panel: the component form itself, dynamic fields, repeatable rows
      lead-summary.js          the shared Lead Summary card (header + gear, progress bar, five sections, footer)
                               rendered on every detailed LEAD page — pure: it reads no app state, callers hand it one object
      lead-assessment.js       the consolidated Lead Assessment workspace: one page whose four numbered sections
                               are the four Lead Assessment steps (they keep their four rail rows and statuses)
      staking-letters.js       the consolidated Staking Letters page: the three checkboxes behind
                               "Approval to Stake" + "Well Site Location"
      resource-calculator.js   the Resource Assessment PIIP calculator rendered inline in that step's body
                               (Calculate → POST /api/tasks/<id>/resource-assessment → Apply to Lead)
      transitions.js           the ONLY way a record moves between the lead and BP pipelines — confirm, then
                               PATCH /flags (supervisor-gated server-side)
      header-menus.js          the header's two dropdowns: the notification bell (feed, unread count, mark-as-read)
                               and the gear menu
      project-editor.js         the secondary "all fields" editor opened from pipeline detail
      portfolio.js              Portfolio tab
      portfolio-analysis.js     the Portfolio Analysis widgets: the resource progress bar and the CoS/OGIP cross plot
      audit.js                  Audit Trail tab
  icons/                     the pristine Lucide SVG downloads — the source of truth icons.js is inlined from
  tests/                     the front-end test suite: harness.js, runner.html and the test-*.js modules (see §10)
```

Nothing here is a "component framework" — there's no JSX, no virtual DOM, no reactivity system.
Every `render*` function does the same three things: build an HTML string, set it via
`element.innerHTML = ...`, then attach event listeners to the elements it just created. That
pattern repeats everywhere; once you've read one `render` function you've basically read them all.

## 2. How a screen actually gets on the page

There's no router and no client-side page framework. It works like this:

1. `index.html` has six `<section class="tab">` blocks (one per tab — prospect, portfolio,
   bp, map, audit, calculator) plus the shared
   `#detail-shell` panel, all sitting in the DOM at once. CSS just shows/hides them
   (`.tab { display: none } .tab.active { display: block }`).
2. `main.js`'s `showTab(name)` toggles which section has `.active`, then calls that tab's
   `refresh*()` function (`refreshProspect`, `refreshBP`, `refreshPortfolio`, `refreshAudit`).
3. A `refresh*()` function calls the backend (via `api.js`), gets JSON back, and re-renders
   that tab's HTML from scratch. There's no diffing — every refresh throws away the old markup
   and builds new markup. This is simple and always correct, at the cost of being not
   performance-optimal — completely fine at this app's size, don't "fix" it.
4. Clicking a pipeline card or a Portfolio well name calls `openDetail(projectId, pipeline)` in `views/detail.js`, which
   fetches that lead/well's full detail payload and fills in `#detail-shell`.

## 3. The design system — how to restyle things

Every color, spacing value, corner radius, and shadow in the app is a CSS custom property
defined once at the top of `static/css/base.css`, under `:root`. **Never hardcode a color or
a pixel value directly in a selector — use a token.** This is the single biggest rule for
keeping the app looking coherent as more people edit it.

```css
:root {
  --brand-600: #12879a;   /* the teal accent color */
  --radius-sm: 7px;       /* small corners: buttons, inputs, chips */
  --radius-md: 10px;      /* medium corners: cards, panels */
  --radius-lg: 14px;      /* large corners: top-level panels */
  --space-3: 12px;        /* the spacing scale — 4px steps */
  ...
}
```

**Worked example — "change the round boxes to square ones":** every rounded corner in the app
traces back to `--radius-sm`, `--radius-md`, `--radius-lg`, or `--radius-pill` (pills, i.e. the
status/priority chips, stay fully rounded on purpose — that's a different, deliberate shape
language, not a bug). To make cards/panels square, set `--radius-md: 0` and `--radius-lg: 0` in
`base.css` — that one edit reaches every panel, card, and input in the app, because every rule
in both CSS files references the token instead of writing its own radius.

**Worked example — "change the accent color":** edit the `--brand-*` group (five shades, light
to dark) in `base.css`. Everything that uses the brand color — active tab underline, primary
buttons, the "in progress" status chip, focus rings — updates together.

Where things live:

- `base.css` — tokens (`:root`), reset, header/tabs/page shell, forms/inputs/buttons, generic
  tables, the toast message. Anything that isn't specific to one screen.
- `components.css` — status/priority chips, the pipeline board + cards, the detail
  panel (component rail, form, summary panel, repeatable rows), portfolio stats, audit table,
  the dialog popup. Organized in sections with a banner comment per section — if you're
  restyling "the thing that shows a lead's tasks," search for its section header, not `Ctrl+F`
  through the whole file.

If you only want to restyle *one* screen (e.g. just the Portfolio table), you don't need to
touch tokens at all — just edit the rules under that screen's section in `components.css`.

## 4. Adding a field to an existing workflow component

Each workflow step (e.g. "Thickness Estimation", "Well Proposal") has its input form
auto-generated from `static/js/schema.js`. You almost never write HTML for a new field — you
add an entry to that component's array in the `SCHEMA` object:

```js
'Thickness Estimation': [
  { key: 'formation_thickness_ft', label: 'Sarah Formation Thickness (ft)', type: 'number' },
  { key: 'reservoir_thickness_ft', label: 'Reservoir Thickness (ft)', type: 'number' },
],
```

Field object reference (`detail-form.js`'s `renderFields()` is what reads these):

| Property | Meaning |
|---|---|
| `key` | the field's storage key — must be unique within that component, becomes the payload key sent to the backend |
| `label` | the visible field label |
| `type` | `number` \| `text` \| `checkbox` \| `radio` \| `select` \| `repeatable` \| `formations` \| `link` — controls which input renders (`formations` renders the well-level formation mini-sheet; see `renderFormationsField()`) |
| `options` | for `type: 'select'`, the dropdown choices |
| `columns` | for `type: 'repeatable'`, the column definitions of each row (see `Reservoir CoS` in schema.js for a full example) |
| `readonly` | true for a calculated/backend-computed value — renders as a read-only output, not an input |
| `showIf` | another field's `key` — this field only shows once that field is truthy (checkbox checked / select set) |

A brand-new field with none of `readonly`/`showIf`/`options` set is the common case — just
`key`, `label`, `type`. **You do not need to touch any other file** for a plain new field:
`renderFields()` renders it, `getFields()` collects it on save, and the backend receives it
through the existing generic `fields` payload (the backend's `task_dynamic_fields` table
accepts arbitrary keys for a task — see the `workflow/` package if unsure, since the backend
is a separate system this guide doesn't cover).

Note: the right-hand summary panel (`views/detail.js`'s `renderRightPanel()`) does **not**
auto-pick-up new fields — it's a compact hand-curated card, not schema-driven. See the next
paragraph.

That summary card is deliberately small: a completion progress bar, the latest P90/P10 gas
figures, the Reservoir / Trap / Seal CoS values, and a gear popover holding the BP / Active
flags, BP year, and Rename / Delete. (A LEAD is different: its gear — in `views/lead-summary.js`
— holds exactly three items, Edit All Inputs / Rename Lead / Delete Lead.) To surface a new
value there (or in the backend-composed
`overview` the detail payload returns — the "Total CoS" style read-time values), edit
`views/detail.js`'s `renderRightPanel()` and add a `metricRow('Label', value, 'note')`
line following the existing ones — the third argument is an optional display note rendered
small under the label (a unit, a source hint), not a component name (the `overview` values
themselves are backend-composed).

## 5. Adding a whole new tab

This is the biggest kind of change this app supports. Steps, in order:

1. **`index.html`**: add a `<button data-tab="yourtab" role="tab" aria-selected="false" type="button">Your Tab</button>`
   inside `<nav class="tabs">`, and a `<section id="tab-yourtab" class="tab" role="tabpanel">...</section>`
   inside `<main>` with whatever markup your screen needs (copy the structure of an existing
   simple tab like `#tab-audit` as a starting point).
2. **`static/js/views/yourtab.js`** (new file): write a `refreshYourTab()` function that fetches
   data via `api.js` and renders it into your new section's elements, following the same
   `innerHTML` + re-bind-listeners pattern every other view uses.
3. **`static/js/api.js`**: if your tab needs a new backend endpoint, add one function to the
   `API` object (this assumes the backend route already exists or you're adding it separately —
   this guide doesn't cover backend changes).
4. **`static/js/main.js`**: import your `refreshYourTab` and add one line to `showTab()`:
   `if (name === 'yourtab') refreshYourTab();`
5. **`components.css`**: add a new section for your tab's styling, using existing tokens.

That's the whole mechanism — there's no route config, no registry, no build manifest to update.

## 6. The DOM contract — the one rule that can break things silently

JavaScript builds HTML as strings (e.g. `'<div class="pipeline-card">...'`), and CSS styles
those exact class names. **If you rename a class or id on one side without the other, nothing
throws an error — the element just silently loses its styling or its click handler.** This is
the main way a "small" edit causes a confusing bug in a codebase like this.

Rule of thumb: if you're renaming an `id="..."` or `class="..."` string, grep both `static/js/`
and `static/index.html` for every occurrence of the old name before you save. Ids are usually
looked up via `byId('the-id')`; classes are usually matched via `all('.the-class', ...)` or
`querySelector`. A handful of ids (like `summary-phase-action`, `rename-record`) are never in
`index.html` at all — they're written into the page later by JS itself (search for the id string
inside `views/detail.js`/`detail-form.js` if you don't find it in the HTML).

## 7. State — where "what's on screen" lives

`static/js/state.js` exports one object, `Store`, holding whatever lead/well is currently open
in the detail panel (`Store.project`, `Store.tasks`, `Store.allFields`, etc.) plus which pipeline
tab it belongs to. There's no framework-managed reactivity — when you change `Store`, nothing
re-renders automatically. Every place that mutates `Store` calls a `render*()` function
immediately afterward on the next line. If you add a new mutation, follow that same pattern:
update `Store`, then explicitly call the render function for whatever's now stale.

## 8. Confirm / rename popups

`window.confirm()` and `window.prompt()` are not used anywhere in this app (they're ugly and
inconsistent across browsers) — use `static/js/dialog.js` instead:

```js
import { confirmDialog, promptDialog } from '../dialog.js';

const ok = await confirmDialog({ title: 'Archive Lead', message: 'Are you sure?', danger: true });
if (!ok) return;

const name = await promptDialog({ title: 'Rename Lead', initialValue: currentName });
if (name === null) return; // user cancelled
```

Both are `async` — they return a Promise that resolves once the user clicks a button.

## 9. What this front end can't do on its own

The front end talks to the backend purely through the endpoints already defined in
`static/js/api.js` / `main.py`. If a change needs new data the backend doesn't currently
store or expose (a new column, a new endpoint, a new computed value), that's a backend change
first — this guide, and the front-end code it describes, is a separate layer from
`main.py` and the `workflow/` package and doesn't cover editing those.

## 10. Quick sanity check after any change

There IS a front-end test suite (no linter, though). It lives in `static/tests/`: a tiny
zero-dependency ES-module harness (`harness.js` — `test()`, `assert.*`, `fixture()`,
`mockFetch()`, `waitFor()`), a page that imports every test module and runs them
(`runner.html`), and 25 `test-*.js` modules totalling 610 tests. Run it from the repo root:

```bash
.venv/bin/python run_frontend_tests.py                  # headless, prints a report
.venv/bin/python run_frontend_tests.py --browser open   # watch it in a browser
```

The driver boots the app on port 8021 against a scratch database (never `pipeline_tracker.db`)
and drives headless Firefox at the runner; exit 0 = all passed, 1 = failures, 2 = harness
trouble. To add a test module, create `static/tests/test-<thing>.js` **and** register it in the
import list in `static/tests/runner.html` — an unregistered file simply never runs.

Then, after a change:

1. Run `python3 main.py`, open `http://127.0.0.1:8020`.
2. Open the browser's DevTools console (F12) and watch for red errors on page load and after
   your change — a typo'd import or a missing element id shows up here immediately.
3. Click through the tab(s) you touched. If you changed `schema.js`, open the relevant workflow
   component and confirm the field renders, saves, and reloads correctly.
