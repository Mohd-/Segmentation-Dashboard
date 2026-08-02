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
]


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
    "Area Definition", "Thickness Estimation", "Resource Assessment",
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
