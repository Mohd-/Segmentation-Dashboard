"""Central application configuration.

This is the ONLY place configuration lives. Every other module imports from
here rather than reading ``os.environ`` directly. That keeps the "where do I
change a setting?" answer to exactly one file, which is what a junior developer
needs.

What belongs here:
- Application identity (name, reported version).
- Database location / URL resolution (env-driven, Postgres-ready).
- Filesystem locations for external assets (the RF model).
- Security / auth flags.
- The Windows share roots, directory maps and component-file section names that
  the folder-link builders in ``folders.py`` consume.

What does NOT belong here:
- Any database access, SQL, Flask objects or business logic.

IMPORTANT — laziness: the test suite re-points the database per test by mutating
``SEGMENT_TRACKER_DB_PATH`` / passing explicit paths. Therefore anything derived
from the environment that can change at runtime is exposed as a *function*
(``db_path()``, ``database_url()``, ``rf_model_path()``) that reads current
state, never as a value frozen at import time.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Application identity
# ---------------------------------------------------------------------------

APP_NAME = "Segment Maturation and Execution System"

# APP_VERSION is the RELEASE LABEL shown to users (the /api/health "version"
# field). It is a product/release axis, distinct from migrations'
# LATEST_SCHEMA_VERSION, which describes the DATABASE SHAPE and only advances
# when the schema changes. The two numbers are only coincidentally similar --
# never derive one from the other. Bump APP_VERSION when a release ships;
# bump the schema version when a migration is added.
APP_VERSION = "v18"

BASE_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Database location
# ---------------------------------------------------------------------------

def db_path() -> Path:
    """Resolve the on-disk SQLite file from SEGMENT_TRACKER_DB_PATH.

    Defaults to ``./pipeline_tracker.db`` next to the application. Read lazily so
    tests can re-point the database between cases.
    """
    raw = os.environ.get("SEGMENT_TRACKER_DB_PATH", str(BASE_DIR / "pipeline_tracker.db"))
    return Path(raw).expanduser().resolve()


def database_url() -> str:
    """Build the SQLAlchemy database URL.

    A direct ``DATABASE_URL`` env var wins when set (so moving to Postgres later
    is a configuration change, not a code change). Otherwise a SQLite URL is
    built from :func:`db_path`.
    """
    override = os.environ.get("DATABASE_URL")
    if override:
        return override
    return "sqlite:///" + str(db_path())


# ---------------------------------------------------------------------------
# External assets
# ---------------------------------------------------------------------------

def rf_model_path() -> Path:
    """Path to the approved Reservoir-CoS RandomForest model (joblib)."""
    raw = os.environ.get("SEGMENT_TRACKER_RF_MODEL_PATH", str(BASE_DIR / "RF_model.joblib"))
    return Path(raw).expanduser().resolve()


def resource_scenarios_path() -> Path:
    """Path to the vendored resource_engine scenarios.yaml."""
    raw = os.environ.get("SEGMENT_TRACKER_SCENARIOS_PATH", str(BASE_DIR / "config" / "scenarios.yaml"))
    return Path(raw).expanduser().resolve()


# ---------------------------------------------------------------------------
# Map data (the UTM Zone 37N viewer -- map_layers.py + the /api/map/* routes)
# ---------------------------------------------------------------------------
# Everything the map draws lives under ONE directory so a deployment points at
# a share with a single env var (SEGMENT_TRACKER_MAP_DATA_DIR):
#
#   <map data dir>/layers/            the shapefile sets (.shp + .shx + .dbf)
#   <map data dir>/borders_utm37.json the prebuilt country outlines
#
# Read lazily (like db_path()) so tests can re-point the map directory per test.
# Coordinates are used AS-IS: every file here must already be in UTM37N metres.

def map_data_dir() -> Path:
    """Root of the map data tree, from SEGMENT_TRACKER_MAP_DATA_DIR."""
    raw = os.environ.get("SEGMENT_TRACKER_MAP_DATA_DIR", str(BASE_DIR / "data" / "map"))
    return Path(raw).expanduser().resolve()


def map_layers_dir() -> Path:
    """Directory holding the shapefile sets; one set = one selectable layer.

    Deployment data (or generated samples from scripts/seed_map_layers.py), so
    it is NOT versioned -- see .gitignore. A missing directory is not an error:
    the layer list is simply empty.
    """
    return map_data_dir() / "layers"


def map_borders_file() -> Path:
    """The prebuilt Saudi/Iraq/Jordan/Kuwait outlines in UTM37N metres.

    Unlike the layers directory this file IS versioned: it is the fixed
    backdrop every other layer is read against.
    """
    return map_data_dir() / "borders_utm37.json"


# ---------------------------------------------------------------------------
# Grid surfaces (ZMAP+ ASCII grids, read by surfaces.py and sampled at a
# project's coordinates by workflow/surfaces_fill.py)
# ---------------------------------------------------------------------------
# Surfaces live INSIDE the map data tree (they are map-plane data: every grid
# must already be in UTM Zone 37N metres, like the shapefile layers), so a
# deployment that re-points SEGMENT_TRACKER_MAP_DATA_DIR carries its surfaces
# along for free. Deployment data (dropped on the share), so the directory is
# NOT versioned -- see .gitignore, same block as data/map/layers/. A missing
# directory or file is not an error: sampling simply returns no value.

def map_surfaces_dir() -> Path:
    """Directory holding the ZMAP+ grid surface files (``.dat``)."""
    raw = os.environ.get("SEGMENT_TRACKER_SURFACES_DIR", str(map_data_dir() / "surfaces"))
    return Path(raw).expanduser().resolve()


# !!! PLACEHOLDER FILENAMES -- EDIT BEFORE DEPLOYING !!!
# The two defaults below are stand-in names; swap them for the real delivered
# grid filenames (or set the env override) when the surfaces land on the share.

def tsq_surface_file() -> Path:
    """The SARH-QWRH thickness ("TSQ") grid: sampled at a lead's coordinates to
    auto-fill the Trap and Seal CoS step's SARH-QWRH thickness when empty."""
    raw = os.environ.get("SEGMENT_TRACKER_TSQ_SURFACE_FILE",
                         str(map_surfaces_dir() / "tsq_sarh_qwrh.dat"))
    return Path(raw).expanduser().resolve()


def ground_elevation_surface_file() -> Path:
    """The digital-elevation grid: sampled at a project's coordinates to keep
    the machine-derived ``projects.ground_elevation`` column current."""
    raw = os.environ.get("SEGMENT_TRACKER_GROUND_ELEVATION_SURFACE_FILE",
                         str(map_surfaces_dir() / "ground_elevation.dat"))
    return Path(raw).expanduser().resolve()


# ---------------------------------------------------------------------------
# Security / auth
# ---------------------------------------------------------------------------

# Flask session-cookie signing key. Set SEGMENT_TRACKER_SECRET_KEY in
# production; the fallback below is a DEV-ONLY default (fine on a workstation,
# never for a shared deployment -- anyone knowing it can forge session cookies).
SECRET_KEY = os.environ.get("SEGMENT_TRACKER_SECRET_KEY", "dev-insecure-change-me")

# Optional shared passcode required by POST /api/login. When the env var is
# unset (the default), login is NAME-ONLY -- acceptable on the trusted internal
# network this app runs on. Set SEGMENT_TRACKER_PASSCODE to make every login
# supply it (compared with secrets.compare_digest in main.py).
SHARED_PASSCODE = os.environ.get("SEGMENT_TRACKER_PASSCODE") or None

# When true, every /api/* endpoint except /api/health, /api/login, /api/logout
# and /api/me requires a logged-in session (enforced by main.py's before_request
# hook, which reads this attribute at REQUEST time so tests can monkeypatch it).
# Default false: the API stays open exactly as before.
AUTH_REQUIRED = os.environ.get("AUTH_REQUIRED", "false").strip().lower() in {"1", "true", "yes", "on"}

# Mark the session cookie Secure (HTTPS-only). Leave false for the plain-HTTP
# internal deployment; set SEGMENT_TRACKER_COOKIE_SECURE=1 once the app is
# served over TLS so the signed cookie never rides an unencrypted connection.
SESSION_COOKIE_SECURE = os.environ.get("SEGMENT_TRACKER_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}

# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
# !!! PLACEHOLDER LIST -- EDIT BEFORE DEPLOYING !!!
# Replace these entries with the real team members and their roles. Login only
# accepts names on this list (seeded into the ``users`` table on every startup;
# seeding is idempotent, so renaming here adds NEW users -- deactivate old rows
# with ``UPDATE users SET is_active = 0`` rather than deleting them).
# Roles: 'supervisor' (approve/return), 'staff', 'employee'.
# Batch-adding the real roster (optionally with per-user passwords) is what
# add_users.py is for -- see its docstring for the name:role[:password] format.
SEED_USERS = [
    ("Supervisor", "supervisor"),
    ("Staff Member", "staff"),
    ("Employee", "employee"),
    # Named step assignees for the creation auto-assignment rules below
    # (STEP_ASSIGNMENT_RULES / PRE_WELL_ASSIGNEES). Assignment always requires
    # an ACTIVE ``users`` row (workflow.lifecycle.assign_task), so the three
    # ride the seed list -- as employees -- until the real roster replaces it.
    ("Tahira", "employee"),
    ("Saad", "employee"),
    ("Salem", "employee"),
]


# ---------------------------------------------------------------------------
# Step auto-assignment at lead creation
# ---------------------------------------------------------------------------
# When a NEW prospect lead is created, every step of its operating pipeline
# (the Lead Assessment / Risk Analysis / Pre-Well Delivery stage groups) is
# assigned automatically -- which also moves it Not Assigned -> In Progress,
# exactly like a manual assignment would. BP-pipeline records ("Well added to
# BP") are never touched by these rules, and neither is promotion.
#
# Resolution order, per step (first match wins; see
# workflow.projects._resolve_creation_assignee):
#   1. an explicit per-step rule below naming "assignees";
#   2. the Pre-Well Delivery stage rule (PRE_WELL_ASSIGNEES);
#   3. a per-step "role" rule, resolved through STEP_ROLE_POOLS (skipped while
#      that role's pool is empty);
#   4. the lead's CREATOR (the signed-in name that created it). A creator that
#      is blank, "System", or not an active user leaves the step Not Assigned.
# When a rule lists several candidates, one is picked at RANDOM.
#
# This file is hand-edited by the owner: keep the shapes below exactly --
# plain lists of names, and per-step dicts with either an "assignees" list or
# a "role" string.

# Role name -> list of member names. !!! PLACEHOLDER -- fill from Nawaf's
# sheet when it arrives !!! While a pool is empty, any "role" rule pointing at
# it simply does not fire (the step falls through to the creator default), so
# populating this dict is the only edit needed to turn role rules on.
# Example: {"petrophysicist": ["Name A", "Name B"],
#           "structural geologist": ["Name C"]}
STEP_ROLE_POOLS = {}

# Every Pre-Well Delivery step is auto-assigned to a random pick from this
# list at lead creation (unless an explicit per-step rule above it wins).
PRE_WELL_ASSIGNEES = ["Saad", "Salem"]

# Per-step overrides: step name -> {"assignees": [names...]} for a fixed
# assignment, or {"role": "role name"} to draw from STEP_ROLE_POOLS.
STEP_ASSIGNMENT_RULES = {
    "Seismic Signature Validation": {"assignees": ["Tahira"]},
    # Role-based example (inert until STEP_ROLE_POOLS carries the pool):
    # "Reservoir CoS": {"role": "petrophysicist"},
}


# ---------------------------------------------------------------------------
# Seismic block dictionary (consumed by reporting.get_portfolio_rows and the
# /api/meta "seismic_blocks" field that feeds the Reservoir CoS sheet's
# dependent Block/AR dropdowns)
# ---------------------------------------------------------------------------
# !!! PLACEHOLDER FILE -- EDIT/REPLACE BEFORE DEPLOYING !!!
# Production swaps out seismic_blocks.json (block name -> list of AR-number
# strings) without touching code; import_seismic_blocks.py validates and
# merges/replaces the file from a same-shaped JSON source. SEISMIC_BLOCK_AR_MAP is that file's parsed
# contents, loaded once at import time; AR_TO_SEISMIC_BLOCK is the reverse
# index (AR -> block name) it's built from, used to label the Portfolio
# "Seismic Block" column. AR numbers not found in the map fall back to
# displaying the raw AR number, so an incomplete/missing file degrades
# gracefully instead of crashing the app.
SEISMIC_BLOCKS_FILE = Path(__file__).resolve().parent / "seismic_blocks.json"


def _load_seismic_block_ar_map(path: Path) -> dict:
    """Parse SEISMIC_BLOCKS_FILE into {block: [ar, ...]}, tolerating a missing
    or malformed file (falls back to {} so the app still boots)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    normalized: dict = {}
    for block, ars in raw.items():
        if not isinstance(ars, list):
            continue
        block_name = str(block).strip()
        if not block_name:
            continue
        normalized[block_name] = [str(ar).strip() for ar in ars if str(ar).strip()]
    return normalized


def _invert_seismic_block_ar_map(block_ar_map: dict) -> dict:
    """Reverse index {ar: block}, first block wins on a duplicate AR (should
    not happen in a well-formed file, but stays graceful)."""
    reverse: dict = {}
    for block, ars in block_ar_map.items():
        for ar in ars:
            reverse.setdefault(ar, block)
    return reverse


SEISMIC_BLOCK_AR_MAP = _load_seismic_block_ar_map(SEISMIC_BLOCKS_FILE)

# Reverse index {ar: block} for O(1) Portfolio lookups.
AR_TO_SEISMIC_BLOCK = _invert_seismic_block_ar_map(SEISMIC_BLOCK_AR_MAP)


# ---------------------------------------------------------------------------
# TWT <-> thickness conversion (Card 2B, Section 1)
# ---------------------------------------------------------------------------
# The consolidated Lead Assessment page captures each of its two thickness rows
# (Reservoir / Formation) as a two-way pair: a two-way time in milliseconds and
# a thickness in feet. Where a calibrated conversion exists, entering ONE side
# derives the other through the straight line
#
#     thickness_ft = m * twt_ms + b          (and its inverse)
#
# with the coefficients keyed by ROW:
#
#     TWT_THICKNESS_COEFFICIENTS = {
#         "reservoir": {"m": 0.42, "b": -105.0},
#         "formation": {"m": 0.51, "b": -160.0},
#     }
#
# SHIPS EMPTY, deliberately. The owner has not supplied calibrated coefficients
# yet, and a guessed line would silently manufacture thicknesses that feed the
# lead's PIIP volumes. While a row's entry is ABSENT the UI degrades to two
# plain manual inputs for that row -- no derivation, no one-source rule -- and
# shows a quiet "TWT <-> thickness conversion pending configuration" note.
# Populate a row here and the client picks the derivation up on its next
# /api/meta read (GET /api/meta serves this map as twt_thickness_coefficients);
# there is no code change and no migration.
TWT_THICKNESS_COEFFICIENTS = {}


# ---------------------------------------------------------------------------
# Windows share roots and directory maps (consumed by folders.py)
# ---------------------------------------------------------------------------
# Shared/root directories only. Do NOT put a field name or well name here.
# The app automatically builds: ROOT / [Field Name] / [Well Name] / [Section Folder]
#
# Main well directory buttons:
#   Open Lead Folder, Open Well Folder, Segmentation, PDA, MTR
# Server/FastX path is optional and used only if the share is mounted on Linux.
# Windows client path is what folder buttons return to users' browsers.
# Example client PDA path: \\aramco.com\ecc\data\NAUGAD\Wells\MDFT\MDFT-3\PDA
WELL_OVERVIEW_DIRECTORY_ROOT = Path("/mnt/wells")
WINDOWS_WELL_SHARE_ROOT = r"\\aramco.com\ecc\data\NAUGAD\Wells"

# Supporting files for Prospect Maturation components live under the parallel
# Leads share. BP Execution component folders continue to use the Wells roots
# above. folders.get_component_folder_link selects between them from the task's
# stage group, so a promoted well's historical prospect components still point
# to Leads.
LEAD_COMPONENT_DIRECTORY_ROOT = Path("/mnt/leads")
WINDOWS_LEAD_COMPONENT_SHARE_ROOT = r"\\aramco.com\ecc\data\NAUGAD\Leads"

# Separate lead-workflow directory used only by the Task Update stage buttons:
#   Open Identification Folder, Open Risking Folder, Open Segmentation Folder
# Example client path: \\aramco.com\ecc\data\NAUGAD\Lead_Workflow\MDFT\MDFT-3\Leads\Identification
LEAD_WORKFLOW_DIRECTORY_ROOT = Path("/mnt/lead_workflow")
WINDOWS_LEAD_WORKFLOW_SHARE_ROOT = r"\\aramco.com\ecc\data\NAUGAD\Lead_Workflow"

# Easy-to-change folder placeholders. Keys are used by frontend buttons; values are
# subfolders under the resolved ROOT / Field / Well path.
WELL_OVERVIEW_DIRECTORY_MAP = {
    # Main well directory buttons.
    "lead": "Leads",
    "well": "",
    "segmentation": "Segmentation",
    "pda": "PDA",
    "mtr": "MTR",

    # Task Update stage buttons in the separate lead-workflow directory.
    "identification_workflow": "Leads/Identification",
    "risking_workflow": "Leads/Risking",
    "segmentation_workflow": "Leads/Segmentation",

    # Card 2B. The consolidated Lead Assessment page's ONE folder row: where the
    # lead's interpreted polygons and surfaces are filed. Lead-scoped (see
    # LEAD_COMPONENT_SECTION_KEYS below), so it resolves under the Leads share
    # -- \\...\Leads\<field>\<lead>\Polygons__Surfaces -- not the Wells one.
    "polygons": "Polygons__Surfaces",
}

LEAD_WORKFLOW_SECTION_KEYS = {
    "identification_workflow",
    "risking_workflow",
    "segmentation_workflow",
}

# Sections that resolve under the LEADS share (LEAD_COMPONENT_DIRECTORY_ROOT /
# WINDOWS_LEAD_COMPONENT_SHARE_ROOT) rather than the Wells or Lead_Workflow
# roots. A third root-selection bucket rather than a special case: a lead's own
# deliverables sit beside its component files, which folders.py already files
# under this share for every prospect-stage step.
LEAD_COMPONENT_SECTION_KEYS = {
    "polygons",
}

# Components where users typically need a physical/share location for supporting files.
# The app automatically generates: Leads-or-Wells ROOT / Field / Well /
# Component Files / Component Name
COMPONENT_FILE_SECTIONS = {
    # The Prospect Maturation components that require generated file locations.
    # (v18 removed the Presence CoS Evaluation step.)
    #
    # BOTH the v5 names and the names they replaced stay in this set, for the
    # same reason both quicklook spellings do below: a lead's share folder was
    # created on disk under whatever the step was called at the time, so the
    # folder card must keep resolving for leads foldered before v5. The v5
    # renames ("Reservoir Area Definition" -> "Area Definition",
    # "Lead Resource Assessment" -> "Resource Assessment",
    # "Prospect Evaluation Presentation" -> "Segmentation Slides",
    # "Pre-Drilling Resource Assessment" -> "Pre-Drilling GeoX Assessment") are
    # in-place task_name rewrites, so the old entries are reachable only through
    # that historical path -- and the retired "Trap CoS" / "Seal CoS" halves
    # only through it too, now that "Trap and Seal CoS" is the live step.
    # v7's live consolidated component, followed by its retired source folders
    # so links created before the merge remain resolvable.
    "Lead Assessment", "Area Definition", "Thickness Estimation", "Resource Assessment",
    "Seismic Signature Validation", "Reservoir CoS", "Trap and Seal CoS",
    "Segmentation Slides", "Pre-Drilling GeoX Assessment",
    # Their pre-v5 spellings (legacy on-disk folders).
    "Reservoir Area Definition", "Lead Resource Assessment", "Trap CoS", "Seal CoS",
    "Prospect Evaluation Presentation",
    # Additional components with supporting-file requirements.
    # BOTH quicklook names stay in this set on purpose. The v3 migration
    # renamed the step to "Quicklook Logs", but a well's share folder was
    # created on disk under whatever name the step had at the time -- keeping
    # the old name here is what makes the folder card keep resolving for wells
    # foldered before the rename (and for any row the migration's
    # both-names guard skipped).
    "Approval to Stake", "Well Proposal", "GHEER", "Quicklook Logs Interpretation", "Quicklook Logs", "SAD Model",
    "Executive Summary", "Flowback Results",
    "SAD Update", "Final Log Analysis", "PVAD Structural MTR",
    "PDA", "Pre-Drilling Resource Assessment",
    # The four names the v4 merges retired stay in this set for the same reason
    # the old quicklook name does: a well's share folder was created on disk
    # under whatever the step was called at the time, so the folder card must
    # keep resolving for wells foldered before the merge. Retired steps no
    # longer render as components, so these entries are reachable only through
    # that historical path.
    "URED Update", "Executive Summary Final",
    "Resource Assessment Update", "Post-Drilling Resource Assessment",
}

# Business Plan Execution configuration.  The approved year selector is fixed;
# historical values outside it remain stored and are reported by the API as a
# data-quality condition rather than being silently coerced.  The three
# unresolved production values intentionally ship empty: deployment can supply
# them without a code change, while the UI shows "Not configured" instead of a
# fabricated destination or calculation input.
BPE_YEAR_MIN = 1999
BPE_YEAR_MAX = 2035


def _env_list(name: str):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return ()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = [part.strip() for part in raw.split(",")]
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


# ---------------------------------------------------------------------------
# User-maintained pick lists (config/lists.yaml)
# ---------------------------------------------------------------------------
# Formation names and wellbore/hole sizes are lists an ASAS user extends, not
# code. They live in ONE YAML file so nobody has to find them in two source
# files and keep them in step. The file is optional: a missing file, a missing
# key, an unreadable file or a malformed one all fall back to the built-in
# default, so a deployment that never touches it behaves exactly as before.


def user_lists_path() -> Path:
    """Path to the user-maintained pick lists (config/lists.yaml)."""
    raw = os.environ.get("SEGMENT_TRACKER_LISTS_PATH", str(BASE_DIR / "config" / "lists.yaml"))
    return Path(raw).expanduser().resolve()


def user_list(name: str, default) -> tuple:
    """One list from config/lists.yaml, or ``default`` when it is unusable.

    Read on every call rather than cached at import: the tests re-point the
    file per case, and these lists are read a handful of times per request at
    most. Entries are stripped and blanks dropped; duplicates are collapsed
    keeping first position, so a hand-edited file cannot produce a dropdown
    with the same option twice.
    """
    try:
        import yaml  # local import: config.py must stay importable without it

        with user_lists_path().open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except Exception:
        return tuple(default)
    if not isinstance(data, dict):
        return tuple(default)
    raw = data.get(name)
    if not isinstance(raw, list):
        return tuple(default)
    seen = {}
    for item in raw:
        text = str(item).strip()
        if text and text not in seen:
            seen[text] = True
    return tuple(seen) or tuple(default)


# The canonical formation trio is the fallback, so an absent lists.yaml leaves
# the app exactly where it was.
DEFAULT_FORMATIONS = ("SARH", "QASM", "QWRH")


def formations() -> tuple:
    """Formation names offered wherever a formation is picked."""
    return user_list("formations", DEFAULT_FORMATIONS)


def hole_sections() -> tuple:
    """Ordered wellbore/hole sizes for the BP Gate's interval From/To pair.

    The environment variable still wins where a deployment sets it; otherwise
    the list comes from config/lists.yaml. Before both existed this was empty,
    which is why those dropdowns offered formations only.
    """
    return _env_list("SEGMENT_TRACKER_BPE_HOLE_SECTIONS") or user_list("hole_sections", ())


# Kept as a module-level name because callers imported it directly; it now
# reflects the environment only. Read hole_sections() instead -- it also sees
# config/lists.yaml.
BPE_HOLE_SECTIONS = _env_list("SEGMENT_TRACKER_BPE_HOLE_SECTIONS")


def business_plan_vsp_url() -> str:
    return os.environ.get("SEGMENT_TRACKER_BPE_VSP_URL", "").strip()


def business_plan_structural_mtr_url() -> str:
    return os.environ.get("SEGMENT_TRACKER_BPE_STRUCTURAL_MTR_URL", "").strip()
