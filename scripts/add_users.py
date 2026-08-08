"""Batch-add login users from ``name:role[:password]`` specs.

Adds rows to the ``users`` table the same idempotent way migrations seeds
config.SEED_USERS (INSERT OR IGNORE keyed on the UNIQUE name), so reruns never
clobber existing rows. Pass ``--update`` to instead overwrite the role (and
reactivate) names that already exist; a spec WITH a password also replaces the
stored password, while a password-less spec keeps whatever password the row
already has (clearing a password is out of scope -- set a new one instead).

Spec format (one user per argument, or per line with ``--file``):
    Name:role
    Name:role:password

- ``Name``   1-80 chars (the POST /api/login limit); may contain spaces.
- ``role``   supervisor | staff | employee (any casing).
- ``password``  optional; may itself contain ``:``. When present it is stored
  as a werkzeug hash in ``users.password_hash`` and POST /api/login then
  requires it for that user (typed into the login form's Passcode box). When
  absent the user logs in by name alone, exactly like the SEED_USERS rows.

Usage:
    .venv/bin/python scripts/add_users.py "Alice Smith:supervisor:s3cret" "Bob:employee"
    .venv/bin/python scripts/add_users.py --file team_roster.txt
    SEGMENT_TRACKER_DB_PATH=/tmp/seed.db .venv/bin/python scripts/add_users.py --file roster.txt --update

``--file`` lines are stripped; blank lines and ``#`` comments are ignored.
The whole batch is validated before anything is written: one bad spec aborts
the run with no partial insert.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from werkzeug.security import generate_password_hash

import config
import db
from helpers import utc_now_str

VALID_ROLES = ("supervisor", "staff", "employee")

# Werkzeug's default hash method (scrypt) needs OpenSSL scrypt support, which
# some Python builds (including this project's dev interpreter) lack. pbkdf2 is
# available everywhere and check_password_hash auto-detects the method, so
# hashes stay verifiable if the default ever changes.
PASSWORD_HASH_METHOD = "pbkdf2:sha256"


def parse_user_spec(spec: str):
    """Parse one ``name:role[:password]`` spec into (name, role, password|None).

    Raises ValueError with a spec-quoting message on any problem, so a batch
    failure always names the offending entry.
    """
    parts = str(spec or "").split(":", 2)
    if len(parts) < 2:
        raise ValueError(f"Bad user spec {spec!r}: expected name:role or name:role:password.")
    name = parts[0].strip()
    role = parts[1].strip().lower()
    password = parts[2] if len(parts) == 3 else None
    if not name or len(name) > 80:
        raise ValueError(f"Bad user spec {spec!r}: name must be 1 to 80 characters.")
    if role not in VALID_ROLES:
        raise ValueError(f"Bad user spec {spec!r}: role must be one of {', '.join(VALID_ROLES)}.")
    if password is not None and not password.strip():
        raise ValueError(f"Bad user spec {spec!r}: password may not be blank (omit the third field for a name-only login).")
    return name, role, password


def read_spec_file(path: Path):
    """Return the non-blank, non-comment lines of a roster file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines
            if line.strip() and not line.strip().startswith("#")]


def add_users(session, users, update_existing=False):
    """Insert (or with ``update_existing``, upsert) parsed (name, role, password) triples.

    Returns (added_names, updated_names, skipped_names). Runs in one write
    transaction: the batch lands atomically.
    """
    added, updated, skipped = [], [], []
    with db.write_transaction(session):
        for name, role, password in users:
            password_hash = (generate_password_hash(password, method=PASSWORD_HASH_METHOD)
                             if password is not None else None)
            existing = db.fetch_one(session, "SELECT user_id FROM users WHERE LOWER(name) = LOWER(:name)",
                                    {"name": name})
            if existing is None:
                db.execute(session, """
                    INSERT INTO users (name, role, password_hash, created_at)
                    VALUES (:name, :role, :password_hash, :now)
                """, {"name": name, "role": role, "password_hash": password_hash, "now": utc_now_str()})
                added.append(name)
            elif update_existing:
                if password_hash is not None:
                    db.execute(session, """
                        UPDATE users
                        SET role = :role, password_hash = :password_hash, is_active = 1
                        WHERE user_id = :user_id
                    """, {"role": role, "password_hash": password_hash, "user_id": existing["user_id"]})
                else:
                    # A password-less spec updates role/activation ONLY: the
                    # stored hash is kept, so re-running --update against a
                    # roster file that omits passwords can never silently
                    # downgrade an account to name-only login. Clearing a
                    # password is deliberately out of scope for this tool
                    # (set a new one instead).
                    db.execute(session, """
                        UPDATE users SET role = :role, is_active = 1
                        WHERE user_id = :user_id
                    """, {"role": role, "user_id": existing["user_id"]})
                updated.append(name)
            else:
                skipped.append(name)
    return added, updated, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("specs", nargs="*", metavar="NAME:ROLE[:PASSWORD]",
                        help="User specs; role is supervisor, staff or employee.")
    parser.add_argument("--file", type=Path,
                        help="Roster file with one spec per line (# comments and blank lines ignored).")
    parser.add_argument("--update", action="store_true",
                        help="Overwrite role/password (and reactivate) names that already exist "
                             "instead of skipping them.")
    args = parser.parse_args()

    raw_specs = list(args.specs)
    if args.file:
        if not args.file.exists():
            print(f"Roster file not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        raw_specs += read_spec_file(args.file)
    if not raw_specs:
        parser.error("No user specs given (pass NAME:ROLE[:PASSWORD] arguments and/or --file).")

    # Validate the WHOLE batch before touching the database.
    try:
        users = [parse_user_spec(spec) for spec in raw_specs]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    names_seen = set()
    for name, _role, _password in users:
        key = name.lower()
        if key in names_seen:
            print(f"Duplicate name in batch: {name!r} appears more than once.", file=sys.stderr)
            sys.exit(1)
        names_seen.add(key)

    print(f"Target database: {config.db_path()}")
    db.init_db()  # Same bootstrap main.py runs: create_all + seed config.SEED_USERS.
    session = db.new_session()
    try:
        added, updated, skipped = add_users(session, users, update_existing=args.update)
    finally:
        session.close()

    for name in added:
        print(f"  added   {name}")
    for name in updated:
        print(f"  updated {name}")
    for name in skipped:
        print(f"  skipped {name} (already exists; pass --update to overwrite)")
    print(f"Done: {len(added)} added, {len(updated)} updated, {len(skipped)} skipped.")


if __name__ == "__main__":
    main()
