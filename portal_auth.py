"""
Shared authentication & permissions for the Logicore Portal.

One login gate, one users table, one permissions table — used by the portal
shell (app.py) AND by Training Tracker (training_tracker/app.py), which used
to have its own separate login/users table. Training Tracker no longer
authenticates anyone itself; it trusts the portal session and asks this
module "does this user have a Training Tracker role?" instead.

Sharing works because both are separate Flask() apps mounted in the same
process via DispatcherMiddleware, but Flask sessions are just signed cookies
— as long as both apps use the identical `secret_key` (see SECRET_FILE
below, read by both app.py and training_tracker/app.py) and the same session
key names, a session started in one is readable by the other with zero
extra plumbing.
"""
import sqlite3
from pathlib import Path
from functools import wraps

from flask import session, redirect, url_for, request, g, flash
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "portal_auth.db"

# Shared secret so a session cookie set by the portal app is readable inside
# the training_tracker app (and vice versa) — see module docstring.
SECRET_FILE = BASE_DIR / ".flask_secret"


def load_or_create_secret():
    import os
    env_secret = os.environ.get("FLASK_SECRET_KEY")
    if env_secret:
        return env_secret.encode() if isinstance(env_secret, str) else env_secret
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes()
    secret = os.urandom(32)
    try:
        SECRET_FILE.write_bytes(secret)
    except OSError:
        pass
    return secret


# ---------------------------------------------------------------------------
# Section registry — the single source of truth for what can be permissioned
# and shown in the admin "Users & Permissions" screen. Every top-level nav
# item is a "section". Sections with "children" can be granted either as a
# whole (subsection=NULL) or narrowed to specific children (e.g. just AMC).
# Sections with "roles" (only Training Tracker today) support admin/editor/
# viewer instead of a flat yes/no.
# ---------------------------------------------------------------------------
SECTIONS = {
    "invoice-generator": {
        "label": "Invoice Generator",
        "children": {
            "promethean": "Promethean",
            "amc": "AMC",
            "tcl": "TCL",
            "philips": "Philips",
            "config": "Config",
        },
    },
    "sms-nonconforming": {"label": "SMS NonConforming"},
    "training-tracker": {"label": "Training Tracker", "roles": ["admin", "editor", "viewer"]},
    "tbd2": {"label": "TBD 2"},
}

TRAINING_TRACKER_ROLES = ("admin", "editor", "viewer")


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def get_db():
    """Per-request connection, cached on flask.g. Safe to call from either
    app since each has its own request/app context."""
    if "_auth_db" not in g:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        g._auth_db = db
    return g._auth_db


def close_db(exception=None):
    db = g.pop("_auth_db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables and seed one super-admin (admin/admin) on first run.
    Idempotent — safe to call on every startup of either app."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_superadmin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            section TEXT NOT NULL,
            subsection TEXT,
            role TEXT,
            UNIQUE(user_id, section, subsection)
        )
        """
    )
    db.commit()
    # Migration: `initials` was added after the users table already shipped
    # (used by SMS NonConforming to build the "Number" field, e.g. MS26-1).
    # ALTER TABLE ADD COLUMN has no "IF NOT EXISTS" in SQLite, so probe first.
    existing_cols = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    if "initials" not in existing_cols:
        db.execute("ALTER TABLE users ADD COLUMN initials TEXT")
        db.commit()
    # BEGIN IMMEDIATE + COUNT-check guards against a duplicate-insert race if
    # two workers boot at once (same pattern training_tracker's init_db used
    # for its own default admin, before that table existed here).
    db.execute("BEGIN IMMEDIATE")
    try:
        existing = db.execute("SELECT COUNT(*) c FROM users").fetchone()[0]
        if existing == 0:
            cur = db.execute(
                "INSERT INTO users (username, password_hash, is_superadmin) VALUES (?, ?, 1)",
                ("admin", generate_password_hash("admin")),
            )
            db.commit()
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.close()


# ---------------------------------------------------------------------------
# Current user / login gate
# ---------------------------------------------------------------------------

def get_current_user():
    if "_auth_user" in g:
        return g._auth_user
    user_id = session.get("user_id")
    if not user_id:
        g._auth_user = None
        return None
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        session.clear()
        g._auth_user = None
        return None
    g._auth_user = dict(row)
    return g._auth_user


def using_default_admin_password(user):
    return bool(
        user
        and user["username"] == "admin"
        and check_password_hash(user["password_hash"], "admin")
    )


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Permission lookups
# ---------------------------------------------------------------------------

def _permission_rows(user_id):
    db = get_db()
    return db.execute("SELECT * FROM permissions WHERE user_id = ?", (user_id,)).fetchall()


def has_access(user, section, subsection=None):
    """Does `user` have access to `section` (optionally narrowed to a
    specific `subsection`, e.g. 'promethean' within 'invoice-generator')?"""
    if not user:
        return False
    if user.get("is_superadmin"):
        return True
    db = get_db()
    # A row with subsection IS NULL grants the whole section (all children).
    whole = db.execute(
        "SELECT 1 FROM permissions WHERE user_id = ? AND section = ? AND subsection IS NULL",
        (user["id"], section),
    ).fetchone()
    if whole:
        return True
    if subsection:
        specific = db.execute(
            "SELECT 1 FROM permissions WHERE user_id = ? AND section = ? AND subsection = ?",
            (user["id"], section, subsection),
        ).fetchone()
        return bool(specific)
    return False


def get_role(user, section):
    """For role-based sections (Training Tracker): the user's role, or None
    if they have no access to that section at all. Superadmins are treated
    as 'admin' for role-based sections."""
    if not user:
        return None
    if user.get("is_superadmin"):
        return "admin"
    db = get_db()
    row = db.execute(
        "SELECT role FROM permissions WHERE user_id = ? AND section = ? AND subsection IS NULL",
        (user["id"], section),
    ).fetchone()
    return row["role"] if row else None


def accessible_children(user, section):
    """Which children of `section` this user can see — used to decide which
    sidebar sub-items / client tabs to render. Empty list + no whole-section
    grant means none."""
    children = SECTIONS.get(section, {}).get("children", {})
    if not children:
        return []
    if has_access(user, section):  # whole-section grant (or superadmin)
        return list(children.keys())
    db = get_db()
    rows = db.execute(
        "SELECT subsection FROM permissions WHERE user_id = ? AND section = ? AND subsection IS NOT NULL",
        (user["id"], section),
    ).fetchall()
    return [r["subsection"] for r in rows]


def section_required(section, subsection=None):
    """Route decorator for the portal app. Training Tracker uses
    role_required() below instead, since its access model is role-based."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = get_current_user()
            if not user:
                return redirect(url_for("login", next=request.path))
            if not has_access(user, section, subsection):
                flash("You don't have permission to view that.", "error")
                return redirect(url_for("index"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def role_required(section, *roles):
    """Route decorator for Training Tracker. Requires the user's role for
    `section` (normally 'training-tracker') to be one of `roles`."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = get_current_user()
            if not user:
                script_name = request.environ.get("SCRIPT_NAME", "")
                return redirect(f"/login?next={script_name}{request.path}")
            role = get_role(user, section)
            if role not in roles:
                flash("You don't have permission to do that.", "error")
                return redirect(request.environ.get("SCRIPT_NAME", "") + "/")
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---------------------------------------------------------------------------
# Admin (superadmin-only) helpers for the Users & Permissions screen
# ---------------------------------------------------------------------------

def list_users():
    db = get_db()
    return db.execute("SELECT * FROM users ORDER BY username ASC").fetchall()


def get_user_permissions(user_id):
    """Returns {(section, subsection_or_None): role_or_'access'}"""
    db = get_db()
    rows = db.execute("SELECT * FROM permissions WHERE user_id = ?", (user_id,)).fetchall()
    return {(r["section"], r["subsection"]): (r["role"] or "access") for r in rows}


def set_permission(user_id, section, subsection, role):
    """role='access' for flat sections, or one of TRAINING_TRACKER_ROLES."""
    db = get_db()
    db.execute(
        "INSERT INTO permissions (user_id, section, subsection, role) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, section, subsection) DO UPDATE SET role = excluded.role",
        (user_id, section, subsection, None if role == "access" else role),
    )
    db.commit()


def clear_permission(user_id, section, subsection=None):
    db = get_db()
    if subsection is None:
        db.execute(
            "DELETE FROM permissions WHERE user_id = ? AND section = ? AND subsection IS NULL",
            (user_id, section),
        )
    else:
        db.execute(
            "DELETE FROM permissions WHERE user_id = ? AND section = ? AND subsection = ?",
            (user_id, section, subsection),
        )
    db.commit()


def create_user(username, password, is_superadmin=False, initials=None):
    db = get_db()
    db.execute(
        "INSERT INTO users (username, password_hash, is_superadmin, initials) VALUES (?, ?, ?, ?)",
        (username, generate_password_hash(password), 1 if is_superadmin else 0,
         (initials or "").strip().upper() or None),
    )
    db.commit()


def delete_user(user_id):
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()


def update_user(user_id, password=None, is_superadmin=None, initials=None):
    db = get_db()
    if password:
        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), user_id),
        )
    if is_superadmin is not None:
        db.execute(
            "UPDATE users SET is_superadmin = ? WHERE id = ?",
            (1 if is_superadmin else 0, user_id),
        )
    if initials is not None:
        db.execute(
            "UPDATE users SET initials = ? WHERE id = ?",
            (initials.strip().upper() or None, user_id),
        )
    db.commit()


def initials_for(user):
    """Best-effort initials for a user — explicit `initials` column first,
    falling back to a derivation from the username so SMS NonConforming
    always has *something* to build a Number from even before an admin has
    set it explicitly. Used only as a fallback; admins should set it."""
    if not user:
        return "XX"
    explicit = (user.get("initials") or "").strip().upper() if isinstance(user, dict) else (user["initials"] or "").strip().upper()
    if explicit:
        return explicit
    username = user["username"] if not isinstance(user, dict) else user.get("username", "")
    parts = [p for p in username.replace(".", " ").replace("_", " ").replace("-", " ").split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (username[:2] or "XX").upper()
