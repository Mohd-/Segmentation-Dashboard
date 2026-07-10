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
APP_VERSION = "v17"

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
SEED_USERS = [
    ("Supervisor", "supervisor"),
    ("Staff Member", "staff"),
    ("Employee", "employee"),
]


# ---------------------------------------------------------------------------
# Seismic block dictionary (consumed by reporting.get_portfolio_rows)
# ---------------------------------------------------------------------------
# !!! PLACEHOLDER DICT -- EDIT BEFORE DEPLOYING !!!
# Maps seismic volume AR numbers (format "AR-XXXXXXX", as entered in the
# Reservoir CoS evaluations) to the human-readable field names shown in the
# Portfolio "Seismic Block" column. AR numbers not found here fall back to
# displaying the raw AR number, so an incomplete dictionary degrades gracefully.
SEISMIC_BLOCK_NAMES = {
    "AR-0000001": "JOHN 4",  # placeholder example -- replace with real entries
}


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
# Example client PDA path: \\YOUR_WINDOWS_SERVER\YOUR_SHARE\Wells\MDFT\MDFT-3\PDA
WELL_OVERVIEW_DIRECTORY_ROOT = Path("/mnt/wells")
WINDOWS_WELL_SHARE_ROOT = r"\\YOUR_WINDOWS_SERVER\YOUR_SHARE\Wells"

# Separate lead-workflow directory used only by the Task Update stage buttons:
#   Open Identification Folder, Open Risking Folder, Open Segmentation Folder
# Example client path: \\YOUR_WINDOWS_SERVER\YOUR_SHARE\Lead_Workflow\MDFT\MDFT-3\Leads\Identification
LEAD_WORKFLOW_DIRECTORY_ROOT = Path("/mnt/lead_workflow")
WINDOWS_LEAD_WORKFLOW_SHARE_ROOT = r"\\YOUR_WINDOWS_SERVER\YOUR_SHARE\Lead_Workflow"

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
}

LEAD_WORKFLOW_SECTION_KEYS = {
    "identification_workflow",
    "risking_workflow",
    "segmentation_workflow",
}

# Components where users typically need a physical/share location for supporting files.
# The app automatically generates: ROOT / Field / Well / Component Files / Component Name
COMPONENT_FILE_SECTIONS = {
    # First eight Prospect Maturation components require generated file
    # locations. (v18 removed the Presence CoS Evaluation step.)
    "Reservoir Area Definition", "Thickness Estimation", "Lead Resource Assessment",
    "Seismic Signature Validation", "Reservoir CoS", "Trap CoS", "Seal CoS",
    "Prospect Evaluation Presentation",
    # Additional components with supporting-file requirements.
    "Approval to Stake", "Well Proposal", "GHEER", "Quicklook Logs Interpretation", "Quicklook Logs", "SAD Model",
    "Executive Summary", "URED Update", "Aramco Picks", "Aramco Approved Picks", "Flowback Results",
    "SAD Update", "Executive Summary Final", "Final Log Analysis", "PVAD Structural MTR",
    "Resource Assessment Update", "PDA", "Pre-Drilling Resource Assessment",
    "Post-Drilling Resource Assessment",
}
