"""
Promethean Workshop Invoice Generator — Flask Backend
"""

import os, sys, shutil, queue, threading, json, uuid, webbrowser
from pathlib import Path
from datetime import datetime

import pandas as pd
from flask import Flask, render_template, request, jsonify, Response, send_file, session as flask_session, redirect, url_for, flash
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.security import check_password_hash

import portal_auth

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

UPLOAD_DIR    = Path(__file__).parent / "uploads"
OUTPUT_DIR    = Path(__file__).parent / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(Path(__file__).parent))

# ── Training Tracker mount ──────────────────────────────────────────────────
# training_tracker/app.py is a complete, separate Flask app (own login/
# session handling, own SQLite DB) — not a blueprint of this app, because it
# needs its own multi-page navigation and auth that don't fit the Invoice
# Generator's single-page-with-hidden-divs pattern. It's composed in at the
# WSGI level via DispatcherMiddleware rather than imported as a blueprint,
# so its ~1700 lines of routes/session logic didn't need to be rewritten —
# it runs exactly as it does standalone, just mounted under a path prefix.
# Its sidebar entry in templates/index.html is a plain <a href="/training-tracker/">
# (full page navigation), not one of the showPortalPage() JS-toggled tabs.
from training_tracker.app import app as training_tracker_app, init_db as _tt_init_db
_tt_init_db()
app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {
    "/training-tracker": training_tracker_app.wsgi_app,
})

# ══════════════════════════════════════════════════════════════════════════════
# Per-browser-session state
# ══════════════════════════════════════════════════════════════════════════════
# Every module (Workshop, Storage, Philips, TCL, AMC) uses a two-step
# analyze→confirm flow: step 1 stashes an in-progress result, step 2 reads it
# back. That "in-progress result" used to live in a single bare module-level
# dict per module (e.g. `_session = {}`), shared by every request the process
# ever handles. That's fine for one person on one laptop, but two people
# hitting the same server (or even one person with two tabs open) will
# silently overwrite each other's analysis mid-flow — the second person's
# /sanitize response replaces the first person's, and the first person's
# /generate then builds the wrong invoice with no error at all.
#
# SESSION_SECRET_FILE persists a signed-cookie secret across restarts so a
# browser's session id — and therefore its in-progress analysis — survives
# the app being relaunched (as opposed to `app.secret_key = os.urandom(...)`,
# which would invalidate every open session on every restart). On a host
# with an ephemeral filesystem (Render's free tier, etc.) this file doesn't
# survive a restart/redeploy either — set a FLASK_SECRET_KEY environment
# variable there instead and it's preferred over the file automatically.
# Shared with training_tracker/app.py (see portal_auth.py docstring) so a
# session started at the top-level /login is readable by the mounted
# Training Tracker app too — one login, one session cookie, both apps.
app.secret_key = portal_auth.load_or_create_secret()

portal_auth.init_db()
app.teardown_appcontext(portal_auth.close_db)


# ── Portal-wide login gate ──────────────────────────────────────────────────
# Every route requires a logged-in user except the handful below. Section-
# level access (which nav items / API routes a user may reach) is enforced
# per-route via ROUTE_SECTIONS + the before_request hook further down, so a
# direct link to a page/API a user isn't permissioned for 404s/redirects
# instead of rendering.
PUBLIC_ENDPOINTS = {"login", "healthz", "version", "static"}

# Maps each route's endpoint (view function name) to the (section, subsection)
# it belongs to, per portal_auth.SECTIONS. "index" and anything not listed
# just requires being logged in (no specific section) — index.html does its
# own conditional rendering of nav items based on the user's permissions.
ROUTE_SECTIONS = {
    # Promethean — Workshop Invoice
    "sanitize_route": ("invoice-generator", "promethean"),
    "generate": ("invoice-generator", "promethean"),
    "stream": ("invoice-generator", "promethean"),
    "download": ("invoice-generator", "promethean"),
    # Promethean — Storage Invoice
    "analyze_storage_route": ("invoice-generator", "promethean"),
    "confirm_storage": ("invoice-generator", "promethean"),
    "stream_storage": ("invoice-generator", "promethean"),
    "get_storage_prices": ("invoice-generator", "promethean"),
    "set_storage_prices": ("invoice-generator", "promethean"),
    # Promethean — FedEx Shipment Upload
    "analyze_fedex_shipment_route": ("invoice-generator", "promethean"),
    "build_fedex_shipment": ("invoice-generator", "promethean"),
    "stream_fedex_shipment": ("invoice-generator", "promethean"),
    "get_fedex_shipment_defaults": ("invoice-generator", "promethean"),
    "set_fedex_shipment_defaults": ("invoice-generator", "promethean"),
    # Philips
    "analyze_philips_route": ("invoice-generator", "philips"),
    "confirm_philips": ("invoice-generator", "philips"),
    "stream_philips": ("invoice-generator", "philips"),
    "generate_report_and_analyze": ("invoice-generator", "philips"),
    "get_philips_dimensions": ("invoice-generator", "philips"),
    "upload_philips_dimensions": ("invoice-generator", "philips"),
    "download_philips_dimensions": ("invoice-generator", "philips"),
    "get_philips_repair_cost": ("invoice-generator", "philips"),
    "set_philips_repair_cost": ("invoice-generator", "philips"),
    # TCL
    "analyze_tcl_route": ("invoice-generator", "tcl"),
    "confirm_tcl": ("invoice-generator", "tcl"),
    "stream_tcl": ("invoice-generator", "tcl"),
    # AMC
    "analyze_amc_route": ("invoice-generator", "amc"),
    "confirm_amc": ("invoice-generator", "amc"),
    "stream_amc": ("invoice-generator", "amc"),
    "get_amc_dimensions": ("invoice-generator", "amc"),
    "upload_amc_dimensions": ("invoice-generator", "amc"),
    "download_amc_dimensions": ("invoice-generator", "amc"),
    "get_amc_prices": ("invoice-generator", "amc"),
    "set_amc_prices": ("invoice-generator", "amc"),
    # Config
    "get_serial_rules": ("invoice-generator", "config"),
    "save_serial_rules": ("invoice-generator", "config"),
}


@app.before_request
def _enforce_login_and_permissions():
    endpoint = request.endpoint
    if endpoint is None or endpoint in PUBLIC_ENDPOINTS:
        return None
    user = portal_auth.get_current_user()
    if not user:
        return redirect(url_for("login", next=request.path))
    section = ROUTE_SECTIONS.get(endpoint)
    if section and not portal_auth.has_access(user, section[0], section[1]):
        flash("You don't have permission to view that.", "error")
        return redirect(url_for("index"))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = portal_auth.get_db()
        row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            flask_session.clear()
            flask_session["user_id"] = row["id"]
            flask_session.permanent = True
            next_url = request.form.get("next") or request.args.get("next") or url_for("index")
            return redirect(next_url)
        flash("Incorrect username or password.", "error")
    next_url = request.args.get("next", "")
    return render_template("login.html", next_url=next_url)


@app.route("/logout", methods=["POST"])
def logout():
    flask_session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("login"))


@app.route("/admin/permissions")
def admin_permissions():
    user = portal_auth.get_current_user()
    if not user or not user["is_superadmin"]:
        flash("You don't have permission to view that.", "error")
        return redirect(url_for("index"))
    users = portal_auth.list_users()
    perms_by_user = {}
    for u in users:
        raw = portal_auth.get_user_permissions(u["id"])  # {(section, subsection): role_or_'access'}
        shaped = {}
        for section, meta in portal_auth.SECTIONS.items():
            if meta.get("children"):
                whole = (section, None) in raw
                children = {c for (s, sub) in raw if s == section and sub is not None for c in [sub]}
                shaped[section] = {"whole": whole, "children": children}
            elif meta.get("roles"):
                shaped[section] = raw.get((section, None))  # role string or None
            else:
                shaped[section] = (section, None) in raw
        perms_by_user[u["id"]] = shaped
    return render_template(
        "admin_permissions.html",
        users=users,
        perms_by_user=perms_by_user,
        sections=portal_auth.SECTIONS,
    )


@app.route("/admin/permissions/users/new", methods=["POST"])
def admin_permissions_new_user():
    user = portal_auth.get_current_user()
    if not user or not user["is_superadmin"]:
        flash("You don't have permission to do that.", "error")
        return redirect(url_for("index"))
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    is_superadmin = request.form.get("is_superadmin") == "on"
    if not username or not password:
        flash("Username and password are required.", "error")
    else:
        try:
            portal_auth.create_user(username, password, is_superadmin)
            flash(f"Created user {username}.", "success")
        except Exception:
            flash("That username is already taken.", "error")
    return redirect(url_for("admin_permissions"))


@app.route("/admin/permissions/users/<int:user_id>/delete", methods=["POST"])
def admin_permissions_delete_user(user_id):
    user = portal_auth.get_current_user()
    if not user or not user["is_superadmin"]:
        flash("You don't have permission to do that.", "error")
        return redirect(url_for("index"))
    if user_id == user["id"]:
        flash("You can't delete your own account while logged in as it.", "error")
        return redirect(url_for("admin_permissions"))
    portal_auth.delete_user(user_id)
    flash("User deleted.", "success")
    return redirect(url_for("admin_permissions"))


@app.route("/admin/permissions/users/<int:user_id>/password", methods=["POST"])
def admin_permissions_reset_password(user_id):
    user = portal_auth.get_current_user()
    if not user or not user["is_superadmin"]:
        flash("You don't have permission to do that.", "error")
        return redirect(url_for("index"))
    password = request.form.get("password", "")
    if len(password) < 4:
        flash("Password must be at least 4 characters.", "error")
    else:
        portal_auth.update_user(user_id, password=password)
        flash("Password updated.", "success")
    return redirect(url_for("admin_permissions"))


@app.route("/admin/permissions/users/<int:user_id>/set", methods=["POST"])
def admin_permissions_set(user_id):
    user = portal_auth.get_current_user()
    if not user or not user["is_superadmin"]:
        flash("You don't have permission to do that.", "error")
        return redirect(url_for("index"))

    # Top-level sections without children: checkbox "on"/absent.
    for section, meta in portal_auth.SECTIONS.items():
        if meta.get("children"):
            continue
        if meta.get("roles"):
            role = request.form.get(f"role__{section}", "")
            if role in meta["roles"]:
                portal_auth.set_permission(user_id, section, None, role)
            else:
                portal_auth.clear_permission(user_id, section, None)
        else:
            if request.form.get(f"access__{section}") == "on":
                portal_auth.set_permission(user_id, section, None, "access")
            else:
                portal_auth.clear_permission(user_id, section, None)

    # Sections with children: either "whole section" or a set of specific
    # children.
    for section, meta in portal_auth.SECTIONS.items():
        children = meta.get("children")
        if not children:
            continue
        if request.form.get(f"access__{section}") == "on":
            portal_auth.set_permission(user_id, section, None, "access")
            for child in children:
                portal_auth.clear_permission(user_id, section, child)
        else:
            portal_auth.clear_permission(user_id, section, None)
            for child in children:
                if request.form.get(f"access__{section}__{child}") == "on":
                    portal_auth.set_permission(user_id, section, child, "access")
                else:
                    portal_auth.clear_permission(user_id, section, child)

    flash("Permissions updated.", "success")
    return redirect(url_for("admin_permissions"))


def _sid() -> str:
    """Stable id for the current browser session, backed by a signed cookie.
    Must only be called from inside a Flask request (it touches the request-
    bound `session` object) — background build threads don't have a request
    context, so routes capture `_sid()` up front and pass the id down."""
    if "sid" not in flask_session:
        flask_session["sid"] = uuid.uuid4().hex
        flask_session.permanent = True
    return flask_session["sid"]


class SessionStore:
    """Per-module, per-browser-session storage: an analysis dict plus an SSE
    log queue, keyed by session id instead of shared globally. One instance
    per invoice module (Workshop, Storage, Philips, TCL, AMC)."""

    def __init__(self):
        self._data:   dict[str, dict] = {}
        self._queues: dict[str, "queue.Queue"] = {}

    def get(self, sid: str) -> dict:
        """The session's analysis dict — created empty on first access."""
        return self._data.setdefault(sid, {})

    def replace(self, sid: str, data: dict) -> dict:
        """Overwrite the session's analysis dict wholesale (mirrors the old
        `global _session; _session = {...}` pattern)."""
        self._data[sid] = data
        return data

    def new_queue(self, sid: str) -> queue.Queue:
        """Start a fresh SSE queue for this session (call at the top of the
        confirm/generate step, before spawning the background thread)."""
        q = queue.Queue()
        self._queues[sid] = q
        return q

    def queue(self, sid: str) -> queue.Queue:
        """Fetch this session's current queue for the /stream endpoint. If a
        client opens /stream before /generate has run (or after a restart),
        hand back a fresh empty queue rather than raising — it will just sit
        there emitting pings until the real one is created."""
        return self._queues.setdefault(sid, queue.Queue())

    @staticmethod
    def make_logger(q: "queue.Queue"):
        """Bind a (log, done) callable pair to one specific queue, so
        background-thread code logs to the browser session that kicked it
        off instead of a shared/ambiguous global."""
        def log(msg: str):
            q.put({"type": "log", "msg": msg})
        def done(success: bool, payload: dict):
            q.put({"type": "done", "success": success, **payload})
        return log, done


# ══════════════════════════════════════════════════════════════════════════════
# Upload validation
# ══════════════════════════════════════════════════════════════════════════════
# Every route trusted uploads based on filename alone — a .csv accidentally
# selected for an "xlsx" field (or any non-Excel file renamed to .xlsx) would
# sail past `file.save()` and only fail once pandas/openpyxl hit it deep
# inside the analyze/build pipeline, surfacing a raw Python traceback to the
# user instead of a plain "wrong file type" message. `MAX_CONTENT_LENGTH`
# (set above) already rejects oversized uploads at the Flask layer; this
# adds the matching check for file *type*.

ALLOWED_UPLOAD_EXTS = {
    "xlsx": {".xlsx", ".xlsm"},
    "csv":  {".csv"},
}


def _save_upload(file_storage, field_label: str, kind: str) -> Path:
    """Validate an uploaded file's extension and basic content shape, save it
    to UPLOAD_DIR, and return its path. Raises ValueError with a message
    meant to be shown directly to the user — every route already wraps its
    body in `except Exception as e: return jsonify({"error": str(e)}), 400`,
    so this slots in without changing any route's error-handling shape.

    kind: 'xlsx' (or 'xlsm') for Excel uploads, 'csv' for CSV uploads.
    """
    if file_storage is None or not getattr(file_storage, "filename", None):
        raise ValueError(f"No file uploaded for '{field_label}'.")

    allowed = ALLOWED_UPLOAD_EXTS[kind]
    ext = Path(file_storage.filename).suffix.lower()
    if ext not in allowed:
        raise ValueError(
            f"'{field_label}' must be a {'/'.join(sorted(allowed))} file "
            f"— got '{file_storage.filename}'.")

    dest = UPLOAD_DIR / file_storage.filename
    file_storage.save(dest)

    # Light content sniff, cheap enough to run on every upload: catches a
    # renamed/corrupt file before it reaches pandas/openpyxl with an opaque
    # traceback. Not a full parse — analyze_*/sanitize() still do the real
    # validation (right sheet names, right columns, etc).
    try:
        if kind == "xlsx":
            import zipfile
            if not zipfile.is_zipfile(dest):
                raise ValueError(
                    f"'{field_label}' ({file_storage.filename}) doesn't look like "
                    f"a valid Excel file — it may be corrupt, empty, or a "
                    f"different file type renamed to .xlsx.")
        elif kind == "csv":
            with open(dest, "rb") as fh:
                head = fh.read(8192)
            for enc in ("utf-8", "latin-1"):
                try:
                    head.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise ValueError(
                    f"'{field_label}' ({file_storage.filename}) doesn't look like "
                    f"a valid CSV file — it isn't readable as text.")
    except ValueError:
        dest.unlink(missing_ok=True)
        raise

    return dest

# ── Pricing ───────────────────────────────────────────────────────────────────
PRICES = {
    ("Depot Repair Tab", "Basic",  "Small", False): 110,
    ("Depot Repair Tab", "Basic",  "Large", False): 135,
    ("Depot Repair Tab", "Heavy",  "Small", False): 220,
    ("Depot Repair Tab", "Heavy",  "Large", False): 268,
    ("Depot Repair Tab", "Basic",  "Small", True):   64,
    ("Depot Repair Tab", "Basic",  "Large", True):   74,
    ("Depot Repair Tab", "Heavy",  "Small", True):  108,
    ("Depot Repair Tab", "Heavy",  "Large", True):  127,
    ("Depot Repair Tab", "Salvage of Hardware and Scrap", "Small", False): 28,
    ("Depot Repair Tab", "Salvage of Hardware and Scrap", "Large", False): 28,
    ("Depot Repair Tab", "Salvage of Hardware and Scrap", "Small", True):  28,
    ("Depot Repair Tab", "Salvage of Hardware and Scrap", "Large", True):  28,
    ("Triage Tab", "Basic", "Small", False):  86,
    ("Triage Tab", "Basic", "Large", False): 101,
    ("Triage Tab", "Heavy", "Small", False): 152,
    ("Triage Tab", "Heavy", "Large", False): 181,
}
TAX_RATE = 0.07

# ── Per-session state + SSE log queue ──────────────────────────────────────────
# Holds sanitizer output between /sanitize and /generate calls, keyed per browser
# session (see SessionStore above) rather than one dict shared by every user.
_workshop = SessionStore()


def _cnt(df, type2, size, prev_triaged=None):
    m = (df["Type2"] == type2) & (df["Size"] == size)
    if prev_triaged is not None and "was_prev_triaged" in df.columns:
        m &= (df["was_prev_triaged"] == prev_triaged)
    return int(m.sum())


# ══════════════════════════════════════════════════════════════════════════════
# Duplicate filtering (shared by both flows)
# ══════════════════════════════════════════════════════════════════════════════

def apply_dedup(repair_df, prev_path, shipping_path, log=print, billing_start=None):
    from sanitizer import BILLABLE_SIZES

    prev_repair = pd.read_excel(prev_path, sheet_name="Repair Log")
    prev_triage = pd.read_excel(prev_path, sheet_name="Triage Log")
    shipping    = pd.read_csv(shipping_path)

    def clean(s):
        return s.replace('="','').replace('""','').replace('"','').strip() if isinstance(s,str) else s
    shipping["Serial Number"] = shipping["Serial Number"].apply(clean)
    shipping["Shipped Date"]  = pd.to_datetime(shipping["Shipped Date"], format="%m-%d-%Y", errors="coerce")
    ship_lookup = shipping.groupby("Serial Number")["Shipped Date"].max()

    # Only consider master entries STRICTLY BEFORE the billing period start.
    # Entries from the same month or later are the current invoice or future ones
    # and would incorrectly cancel out current period records.
    if billing_start is not None:
        cutoff = pd.Timestamp(billing_start)
        prev_repair = prev_repair[pd.to_datetime(prev_repair["Month"], errors="coerce") < cutoff]
        prev_triage = prev_triage[pd.to_datetime(prev_triage["Date"],  errors="coerce") < cutoff]
        log(f"Master filtered to entries before {cutoff.date()} — "
            f"Repair: {len(prev_repair)}, Triage: {len(prev_triage)}")

    prev_repair_lkp = prev_repair.groupby("Serial")["Month"].max()
    prev_triage_lkp = prev_triage.groupby("Serial")["Date"].max()

    # For the previously-triaged discount: load the FULL triage history (no cutoff).
    # A unit triaged in any prior period — including the current billing month —
    # should receive the lower repair-only rate when it comes back as a completed repair.
    prev_triage_all = pd.read_excel(prev_path, sheet_name="Triage Log")
    prev_triage_all_lkp = prev_triage_all.groupby("Serial")["Date"].max()

    def prev_inv_date(serial):
        dates = [d for d in [prev_repair_lkp.get(serial), prev_triage_lkp.get(serial)] if pd.notna(d)]
        return max(dates) if dates else pd.NaT

    def is_dup(serial, prev_date, record_type):
        if pd.isna(prev_date): return False
        # If the prior invoice was TRIAGE ONLY (in triage master, not in repair master)
        # and the current record is a completed REPAIR, this is legitimate new billing.
        # The unit was triaged before, is now being repaired — not a duplicate.
        only_triaged_before = (
            serial in prev_triage_lkp.index and
            serial not in prev_repair_lkp.index
        )
        if only_triaged_before and record_type == "Depot Repair Tab":
            return False
        if serial not in ship_lookup.index:
            return True
        return ship_lookup[serial] <= prev_date

    df = repair_df.copy()
    df["prev_invoice_date"] = df["Actual Serial"].apply(prev_inv_date)
    df["is_duplicate"]      = df.apply(
        lambda r: is_dup(r["Actual Serial"], r["prev_invoice_date"], r["Type"]), axis=1)

    depot  = df[df["Type"] == "Depot Repair Tab"].copy()
    triage = df[df["Type"] == "Triage Tab"].copy()

    log(f"Excluded as duplicates — Depot: {int(depot['is_duplicate'].sum())}, "
        f"Triage: {int(triage['is_duplicate'].sum())}")

    depot_clean  = depot[~depot["is_duplicate"]].copy()
    triage_clean = triage[~triage["is_duplicate"]].copy()

    # Use the full (unfiltered) triage history for the discount check.
    # A unit counts as "previously triaged" if it appears anywhere in the triage master,
    # including the current billing period — it was triaged before it was repaired.
    depot_clean["was_prev_triaged"]  = depot_clean["Actual Serial"].isin(prev_triage_all_lkp.index)
    triage_clean["was_prev_triaged"] = False

    depot_clean["Unit Price"]  = depot_clean.apply(
        lambda r: PRICES.get((r["Type"], r["Type2"], r["Size"], r["was_prev_triaged"]), 0), axis=1)
    triage_clean["Unit Price"] = triage_clean.apply(
        lambda r: PRICES.get((r["Type"], r["Type2"], r["Size"], False), 0), axis=1)

    log(f"Final rows — Depot: {len(depot_clean)}, Triage: {len(triage_clean)}")
    return depot_clean, triage_clean


# ══════════════════════════════════════════════════════════════════════════════
# Legacy process() — for pre-sanitized Repair_Data.xlsx uploads
# ══════════════════════════════════════════════════════════════════════════════

def process_legacy(repair_path, prev_path, shipping_path,
                   date_from, date_to, call_id, customer,
                   invoice_date, completed_date, output_path,
                   log, done):
    try:
        log("Loading source files…")
        repair = pd.read_excel(repair_path)
        repair["Date Integer"] = pd.to_datetime(repair["Date Integer"], errors="coerce")
        repair = repair[
            (repair["Date Integer"] >= pd.Timestamp(date_from)) &
            (repair["Date Integer"] <= pd.Timestamp(date_to))
        ].copy()
        log(f"Records in date range: {len(repair)}")

        # Expect Type and Size already present (pre-sanitized)
        repair["Size"] = repair["Derive Size"].apply(
            lambda x: "Large" if str(x).strip() == "86" else "Small")

        depot_clean, triage_clean = apply_dedup(repair, prev_path, shipping_path, log, billing_start=date_from)
        _finish(depot_clean, triage_clean, output_path,
                invoice_date, completed_date, call_id, customer,
                log=log, done=done)
    except Exception as e:
        import traceback
        log(f"Error: {e}")
        done(False, {"error": str(e), "trace": traceback.format_exc()})


# ══════════════════════════════════════════════════════════════════════════════
# Shared finish step
# ══════════════════════════════════════════════════════════════════════════════

def _finish(depot_clean, triage_clean, output_path,
            invoice_date, completed_date, call_id, customer,
            corrected_filename=None, master_filename=None,
            programming_df=None, part_prices=None, part_type_totals=None,
            log=print, done=None):
    from builder import build, TEMPLATE_FILE as TPL
    if not TPL.exists():
        raise FileNotFoundError(f"Template not found: {TPL}")

    log("Building invoice workbook…")
    build(
        depot_df=depot_clean, triage_df=triage_clean,
        output_path=output_path,
        invoice_date=invoice_date, completed_date=completed_date,
        call_id=call_id, customer=customer,
        programming_df=programming_df, part_prices=part_prices,
    )

    subtotal = sum(
        _cnt(depot_clean, t, s, pt) * p
        for (tab, t, s, pt), p in PRICES.items() if tab == "Depot Repair Tab"
    ) + sum(
        _cnt(triage_clean, t, s, False) * p
        for (tab, t, s, pt), p in PRICES.items() if tab == "Triage Tab"
    )

    # Parts Testing & Configuration (rows 34-43 on the Breakdown sheet) is part
    # of the workbook's own "=SUM(E11:E43)" subtotal formula, so it must be
    # included here too — otherwise the subtotal/tax/total returned to the UI
    # silently under-reports whenever a FedEx Master Sheet was uploaded.
    pt_subtotal = 0
    if part_type_totals and part_prices:
        pt_subtotal = sum(
            qty * part_prices.get(ptype, 0)
            for ptype, qty in part_type_totals.items()
        )
    subtotal += pt_subtotal

    tax   = round(subtotal * TAX_RATE, 2)
    total = round(subtotal + tax, 2)

    log(f"Invoice saved — {len(depot_clean)} depot repairs, {len(triage_clean)} triage units"
         + (f", Parts Testing subtotal ${pt_subtotal:,.2f}" if pt_subtotal else ""))
    payload = {
        "filename":           Path(output_path).name,
        "depot_count":        len(depot_clean),
        "triage_count":       len(triage_clean),
        "subtotal":           subtotal,
        "tax":                tax,
        "total":              total,
    }
    if corrected_filename:
        payload["corrected_filename"] = corrected_filename
    if master_filename:
        payload["master_filename"] = master_filename
    if done is None:
        raise RuntimeError("_finish() requires an explicit done= callback (per-session queue)")
    done(True, payload)



def _build_updated_master(prev_path, depot_df, triage_df, billing_date, output_path):
    """
    Appends this month's invoiced units to the Previously Invoiced Master
    and saves as a new file ready for next month's run.
    """
    import shutil
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    billing_ts = pd.Timestamp(billing_date)
    billing_month = billing_ts.replace(day=1)

    # Load existing master sheets
    prev_repair = pd.read_excel(prev_path, sheet_name="Repair Log")
    prev_triage = pd.read_excel(prev_path, sheet_name="Triage Log")

    # Build new repair rows from this month's depot repairs
    # Type2 label matching what the master stores
    def repair_type_label(row):
        t2 = row["Type2"]
        sz = row["Size"]
        pt = row.get("was_prev_triaged", False)
        if t2 == "Salvage of Hardware and Scrap":
            return "Salvage of Hardware and Scrap"
        if pt:
            return f"{'Heavy' if t2=='Heavy' else 'Basic'}{'Small' if sz=='Small' else 'Large'} - Previously Triaged"
        return t2   # "Basic" or "Heavy"

    new_repair_rows = []
    for _, r in depot_df.iterrows():
        new_repair_rows.append({
            "Month":  billing_month,
            "Model":  r["Actual Model"],
            "Serial": r["Actual Serial"],
            "Type":   repair_type_label(r),
        })

    new_triage_rows = []
    for _, r in triage_df.iterrows():
        t2 = r["Type2"]
        sz = r["Size"]
        new_triage_rows.append({
            "Date":   billing_month,
            "Model":  r["Actual Model"],
            "Serial": r["Actual Serial"],
            "Type":   f"Triage - {'Heavy' if t2=='Heavy' else 'Basic'}",
        })

    # Append to existing data
    if new_repair_rows:
        new_repair_df = pd.DataFrame(new_repair_rows)
        prev_repair = pd.concat([prev_repair, new_repair_df], ignore_index=True)
    if new_triage_rows:
        new_triage_df = pd.DataFrame(new_triage_rows)
        prev_triage = pd.concat([prev_triage, new_triage_df], ignore_index=True)

    # Write to a copy of the original master
    shutil.copy(prev_path, output_path)
    with pd.ExcelWriter(str(output_path), engine="openpyxl", mode="a",
                        if_sheet_exists="replace") as writer:
        prev_repair.to_excel(writer, sheet_name="Repair Log",  index=False)
        prev_triage.to_excel(writer, sheet_name="Triage Log",  index=False)


# ══════════════════════════════════════════════════════════════════════════════
# Flask routes
# ══════════════════════════════════════════════════════════════════════════════

APP_VERSION = "9.0"


@app.route("/version")
def version():
    return jsonify({"version": APP_VERSION, "modules": ["workshop", "storage", "philips", "tcl", "amc"]})


@app.route("/healthz")
def healthz():
    """Render (and similar hosts) hit this to confirm the service is alive."""
    return jsonify({"ok": True}), 200

@app.route("/")
def index():
    user = portal_auth.get_current_user()
    ig_children = set(portal_auth.accessible_children(user, "invoice-generator"))
    can_ig = bool(ig_children)
    can_sms = portal_auth.has_access(user, "sms-nonconforming")
    can_tbd2 = portal_auth.has_access(user, "tbd2")
    tt_role = portal_auth.get_role(user, "training-tracker")
    can_tt = tt_role is not None

    default_portal = None
    for p, ok in (("invoice-generator", can_ig), ("sms-nonconforming", can_sms), ("tbd2", can_tbd2)):
        if ok:
            default_portal = p
            break

    default_client = None
    if can_ig:
        for c in ("promethean", "amc", "tcl", "philips", "config"):
            if c in ig_children:
                default_client = c
                break

    return render_template(
        "index.html",
        auth_user=user,
        can_invoice_generator=can_ig,
        ig_children=ig_children,
        can_sms_nonconforming=can_sms,
        can_tbd2=can_tbd2,
        can_training_tracker=can_tt,
        default_portal=default_portal,
        default_client=default_client,
        has_any_access=bool(default_portal or can_tt),
    )


# ── SANITIZE: validate raw production file ────────────────────────────────────
@app.route("/sanitize", methods=["POST"])
def sanitize_route():
    try:
        raw_file   = request.files["raw_file"]
        prev_file  = request.files["prev_invoiced"]
        ship_file  = request.files["shipping"]
        fedex_file = request.files.get("fedex")   # optional — Parts Testing & Configuration

        raw_path  = _save_upload(raw_file,  "Raw Production File", "xlsx")
        prev_path = _save_upload(prev_file, "Previously Invoiced Master", "xlsx")
        ship_path = _save_upload(ship_file, "Shipping History", "csv")

        fedex_path = None
        if fedex_file and fedex_file.filename:
            fedex_path = _save_upload(fedex_file, "FedEx Master Sheet", "xlsx")

        date_from = datetime.strptime(request.form["date_from"], "%Y-%m-%d")
        date_to   = datetime.strptime(request.form["date_to"],   "%Y-%m-%d")

        from sanitizer import sanitize
        clean_df, issues, raw_df, auto_corrections = sanitize(raw_path, date_from, date_to)

        # Store in this browser session for the generate step (not a global —
        # see SessionStore: two people sanitizing at once now get independent
        # in-progress state instead of overwriting each other's).
        _workshop.replace(_sid(), {
            "raw_path":     str(raw_path),
            "prev_path":    str(prev_path),
            "ship_path":    str(ship_path),
            "fedex_path":   str(fedex_path) if fedex_path else None,
            "clean_df":     clean_df,
            "raw_df":       raw_df,
            "date_from":    date_from,
            "date_to":      date_to,
            "call_id":      request.form.get("call_id", "C1413671"),
            "customer":     request.form.get("customer", "Promethean"),
            "invoice_date": request.form.get("invoice_date", str(date_to.date())),
            "completed_date": request.form.get("completed_date", str(date_to.date())),
        })

        # Serialize issues for JSON
        issues_out = []
        for iss in issues:
            issues_out.append({k: str(v) for k, v in iss.items()
                                if k not in ('suggested_values',)} |
                               {"suggested_values": iss.get("suggested_values", [])})

        # Serialize auto_corrections for UI
        auto_out = []
        for ac in auto_corrections:
            auto_out.append({k: str(v) for k, v in ac.items()})

        return jsonify({
            "ok":               True,
            "total_records":    len(raw_df),
            "clean_records":    len(clean_df),
            "issue_count":      len(issues),
            "issues":           issues_out,
            "auto_count":       len(auto_corrections),
            "auto_corrections": auto_out,
            "fedex_uploaded":   fedex_path is not None,
        })

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()}), 400


# ── GENERATE: apply corrections and build invoice ─────────────────────────────
@app.route("/generate", methods=["POST"])
def generate():
    sid = _sid()
    q = _workshop.new_queue(sid)
    log, done = SessionStore.make_logger(q)

    try:
        corrections = json.loads(request.form.get("corrections", "{}"))
        mode = request.form.get("mode", "raw")  # "raw" or "legacy"

        if mode == "legacy":
            # Old flow: user uploads pre-sanitized Repair_Data.xlsx
            repair_file   = request.files["repair"]
            prev_file     = request.files["prev_invoiced"]
            shipping_file = request.files["shipping"]

            repair_path   = _save_upload(repair_file,   "Repair Data", "xlsx")
            prev_path     = _save_upload(prev_file,     "Previously Invoiced Master", "xlsx")
            shipping_path = _save_upload(shipping_file, "Shipping History", "csv")

            date_from      = datetime.strptime(request.form["date_from"], "%Y-%m-%d")
            date_to        = datetime.strptime(request.form["date_to"],   "%Y-%m-%d")
            call_id        = request.form.get("call_id", "C1413671")
            customer       = request.form.get("customer", "Promethean")
            invoice_date   = datetime.strptime(request.form.get("invoice_date",   str(date_to.date())), "%Y-%m-%d")
            completed_date = datetime.strptime(request.form.get("completed_date", str(date_to.date())), "%Y-%m-%d")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            leg_fname = request.form.get("output_filename", "").strip()
            invoice_name = f"{leg_fname}.xlsx" if leg_fname else f"Promethean_Invoice_{ts}.xlsx"
            output_path = OUTPUT_DIR / invoice_name

            threading.Thread(
                target=process_legacy,
                kwargs=dict(
                    repair_path=repair_path, prev_path=prev_path,
                    shipping_path=shipping_path,
                    date_from=date_from, date_to=date_to,
                    call_id=call_id, customer=customer,
                    invoice_date=invoice_date, completed_date=completed_date,
                    output_path=output_path,
                    log=log, done=done,
                ), daemon=True
            ).start()

        else:
            # New flow: use this browser session's sanitizer output
            sess = _workshop.get(sid)
            if not sess:
                return jsonify({"ok": False, "error": "No sanitized data in session. "
                                "Please run the sanitize step first."}), 400

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            raw_fname = request.form.get("output_filename", "").strip()
            invoice_name = f"{raw_fname}.xlsx" if raw_fname else f"Promethean_Invoice_{ts}.xlsx"
            output_path   = OUTPUT_DIR / invoice_name
            corrected_path = OUTPUT_DIR / f"Promethean_Production_Corrected_{ts}.xlsx"

            threading.Thread(
                target=_run_raw_generate,
                args=(sess, corrections, output_path, corrected_path, log, done),
                daemon=True
            ).start()

        return jsonify({"ok": True, "output": str(output_path)})

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e),
                        "trace": traceback.format_exc()}), 400


def _run_raw_generate(sess, corrections, output_path, corrected_path, log, done):
    try:
        from sanitizer import apply_corrections, export_corrected_workbook, BILLABLE_SIZES

        clean_df = sess["clean_df"].copy()
        raw_df   = sess["raw_df"].copy()

        # Apply user corrections to the clean_df
        if corrections:
            log(f"Applying {len(corrections)} correction(s)…")
            clean_df = apply_corrections(clean_df, corrections)
            raw_df   = apply_corrections(raw_df,   corrections)

        # Drop excluded rows
        if '_exclude' in clean_df.columns:
            clean_df = clean_df[~clean_df['_exclude'].fillna(False)].copy()

        # Re-filter to billable sizes after corrections
        clean_df = clean_df[clean_df['_size'].isin(BILLABLE_SIZES)].copy()

        log(f"Records after corrections: {len(clean_df)}")

        # Dedup against previously invoiced
        # Pass billing_start so master entries from the current period are excluded
        depot_clean, triage_clean = apply_dedup(
            clean_df, sess["prev_path"], sess["ship_path"], log,
            billing_start=sess["date_from"])

        # Parts Testing & Configuration (FedEx Master Sheet) — optional upload.
        # Same parsing/pricing logic the Storage invoice uses, shared via
        # storage_builder.analyze_fedex() so both invoices agree on the numbers.
        programming_df    = None
        part_prices       = None
        part_type_totals  = None
        fedex_path = sess.get("fedex_path")
        if fedex_path:
            log("Processing FedEx Master Sheet…")
            import storage_builder
            part_prices, line_prices = storage_builder.load_prices()
            fedex_result = storage_builder.analyze_fedex(
                fedex_path,
                sess["date_from"].replace(day=1), sess["date_to"],
                part_prices, line_prices["small_part_pick"], log=log)
            programming_df   = fedex_result["programming_df"]
            part_type_totals = fedex_result["part_type_totals"]
            log(f"Parts Testing & Configuration: {len(programming_df)} row(s) from FedEx file")
        else:
            log("No FedEx Master Sheet uploaded — Parts Testing & Configuration will be blank")

        # Build invoice
        inv_date  = datetime.strptime(sess["invoice_date"],   "%Y-%m-%d") if isinstance(sess["invoice_date"], str) else sess["invoice_date"]
        comp_date = datetime.strptime(sess["completed_date"], "%Y-%m-%d") if isinstance(sess["completed_date"], str) else sess["completed_date"]

        # Build corrected production file BEFORE _finish so filenames go in done payload
        log("Exporting corrected production file…")
        export_corrected_workbook(sess["raw_path"], raw_df, corrected_path)
        log(f"Corrected file saved: {corrected_path.name}")

        # Build updated Previously Invoiced Master
        log("Building updated invoiced master…")
        master_path = corrected_path.parent / corrected_path.name.replace(
            "Promethean_Production_Corrected_", "Previously_Invoiced_Master_Updated_")
        _build_updated_master(
            prev_path=sess["prev_path"],
            depot_df=depot_clean,
            triage_df=triage_clean,
            billing_date=sess["date_from"],
            output_path=master_path,
        )
        log(f"Updated master saved: {master_path.name}")

        # _finish sends the done event — pass filenames so they arrive in the same payload
        _finish(depot_clean, triage_clean, output_path,
                inv_date, comp_date, sess["call_id"], sess["customer"],
                corrected_filename=corrected_path.name,
                master_filename=master_path.name,
                programming_df=programming_df,
                part_prices=part_prices,
                part_type_totals=part_type_totals,
                log=log, done=done)

    except Exception as e:
        import traceback
        log(f"Error: {e}")
        done(False, {"error": str(e), "trace": traceback.format_exc()})


@app.route("/stream")
def stream():
    q = _workshop.queue(_sid())
    def event_stream():
        while True:
            try:
                item = q.get(timeout=60)
                yield f"data: {json.dumps(item)}\n\n"
                if item.get("type") == "done":
                    break
            except queue.Empty:
                yield 'data: {"type":"ping"}\n\n'
    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/download/<path:filename>")
def download(filename):
    path = OUTPUT_DIR / filename
    if not path.exists():
        return "File not found", 404
    return send_file(path, as_attachment=True, download_name=path.name,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")



# ── Serial config routes ───────────────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / "serial_rules.json"

@app.route("/config/serial_rules", methods=["GET"])
def get_serial_rules():
    try:
        import json as _json
        data = _json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {"rules": []}
        return jsonify(data)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/config/serial_rules", methods=["POST"])
def save_serial_rules():
    try:
        import json as _json, importlib
        data = request.get_json()
        CONFIG_FILE.write_text(_json.dumps(data, indent=2))
        return jsonify({"ok": True, "count": len(data.get("rules", []))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
# Storage & Small Parts Invoice — routes
# ══════════════════════════════════════════════════════════════════════════════

_storage = SessionStore()   # in-memory analysis result between analyze→confirm, per browser session


# ── Step 1: Analyze (upload files, process, return review lists) ──────────────

@app.route("/analyze_storage", methods=["POST"])
def analyze_storage_route():
    try:
        sid = _sid()
        storage_analysis = _storage.get(sid)
        inv_file   = request.files["inventory"]
        ship_file  = request.files["shipping"]
        recv_file  = request.files["receipt"]
        fedex_file = request.files["fedex"]
        wl_file    = request.files.get("whitelist")

        inv_path   = _save_upload(inv_file,   "Inventory", "csv")
        ship_path  = _save_upload(ship_file,  "Shipping", "csv")
        recv_path  = _save_upload(recv_file,  "Receipt Log", "csv")
        fedex_path = _save_upload(fedex_file, "FedEx Master Sheet", "xlsx")
        wl_path    = _save_upload(wl_file, "Whitelist", "xlsx") if wl_file and wl_file.filename else None

        pallet_count = int(request.form.get("pallet_count", 0))
        date_from    = datetime.strptime(request.form["date_from"], "%Y-%m-%d")
        date_to      = datetime.strptime(request.form["date_to"],   "%Y-%m-%d")

        # Stash invoice metadata for confirm step
        storage_analysis["_meta"] = {
            "invoice_date":   request.form.get("invoice_date", str(date_to.date())),
            "completed_date": request.form.get("completed_date", str(date_to.date())),
            "call_id":        request.form.get("call_id", "C1413671"),
            "customer":       request.form.get("customer", "Promethean"),
            "output_filename":request.form.get("output_filename", "").strip(),
        }

        from storage_builder import analyze_storage
        logs = []
        result = analyze_storage(
            inventory_path=str(inv_path),
            shipping_path=str(ship_path),
            receipt_path=str(recv_path),
            fedex_path=str(fedex_path),
            pallet_count=pallet_count,
            date_from=date_from,
            date_to=date_to,
            whitelist_path=str(wl_path) if wl_path else None,
            log=logs.append,
        )
        storage_analysis["data"] = result

        return jsonify({
            "ok": True,
            "logs": logs,
            "auto_spc_count":       len(result["auto_spc_rows"]),
            "unmatched_count":      len(result.get("unmatched_df", [])) if result.get("unmatched_df") is not None else 0,
            "unit_storage_count":   len(result["unit_storage_df"]),
            "units_received_count": len(result["units_received"]),
            "programming_rows":     len(result["programming_df"]),
        })
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 400


# ── Step 2: Confirm + build (accepts manual selections) ───────────────────────

@app.route("/confirm_storage", methods=["POST"])
def confirm_storage():
    sid = _sid()
    q = _storage.new_queue(sid)
    log, done = SessionStore.make_logger(q)

    try:
        body = request.get_json(force=True)  # noqa - body reserved for future params

        storage_analysis = _storage.get(sid)
        if "data" not in storage_analysis:
            return jsonify({"ok": False, "error": "No analysis loaded — run Analyze first"}), 400

        analysis = storage_analysis["data"]
        meta     = storage_analysis.get("_meta", {})

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_fname = meta.get("output_filename", "").strip()
        invoice_name = f"{out_fname}.xlsx" if out_fname else f"Promethean_Storage_Invoice_{ts}.xlsx"
        output_path  = OUTPUT_DIR / invoice_name

        def _run():
            try:
                from storage_builder import build_storage_invoice
                invoice_date   = datetime.strptime(meta["invoice_date"],   "%Y-%m-%d")
                completed_date = datetime.strptime(meta["completed_date"], "%Y-%m-%d")
                result = build_storage_invoice(
                    analysis=analysis,
                    invoice_date=invoice_date,
                    completed_date=completed_date,
                    call_id=meta.get("call_id",""),
                    customer=meta.get("customer",""),
                    output_path=output_path,
                    log=log,
                )
                done(True, {"filename": output_path.name, **result})
            except Exception as e:
                import traceback
                log(f"Error: {e}")
                done(False, {"error": str(e), "trace": traceback.format_exc()})

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"ok": True})

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 400


@app.route("/stream_storage")
def stream_storage():
    q = _storage.queue(_sid())
    def event_stream():
        while True:
            try:
                item = q.get(timeout=60)
                yield f"data: {json.dumps(item)}\n\n"
                if item.get("type") == "done":
                    break
            except queue.Empty:
                yield 'data: {"type":"ping"}\n\n'
    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Storage pricing admin endpoints ──────────────────────────────────────────

@app.route("/get_storage_prices")
def get_storage_prices():
    from storage_builder import load_prices, DEFAULT_PART_TYPE_PRICES, DEFAULT_LINE_PRICES
    part_prices, line_prices = load_prices()
    return jsonify({
        "ok": True,
        "part_type_prices": part_prices,
        "line_prices":      line_prices,
        "defaults": {
            "part_type_prices": DEFAULT_PART_TYPE_PRICES,
            "line_prices":      DEFAULT_LINE_PRICES,
        }
    })

@app.route("/set_storage_prices", methods=["POST"])
def set_storage_prices():
    try:
        from storage_builder import save_prices
        body = request.get_json(force=True)
        save_prices(body["part_type_prices"], body["line_prices"])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ══════════════════════════════════════════════════════════════════════════════
# Philips Warehouse & Repair Invoice — routes
# ══════════════════════════════════════════════════════════════════════════════

_philips = SessionStore()   # holds analysis + upload path between analyze→confirm, per browser session


def _philips_summary_payload(analysis):
    return {
        "inbound_count":          analysis["inbound_count"],
        "outbound_count":         analysis["outbound_count"],
        "repair_count":           analysis["repair_count"],
        "harvest_count":          analysis["harvest_count"],
        "missing_dimension_models": analysis["missing_dimension_models"],
        "demo_total_sqft":        round(analysis["demo_total_sqft"], 2),
        "service_total_sqft":     round(analysis["service_total_sqft"], 2),
    }


# ── Step 1: Analyze ────────────────────────────────────────────────────────────
@app.route("/analyze_philips", methods=["POST"])
def analyze_philips_route():
    try:
        report_file = request.files["report"]
        report_path = _save_upload(report_file, "Month End Report", "xlsx")

        parts_sqft    = float(request.form.get("parts_sqft", 0) or 0)
        invoice_title = request.form.get("invoice_title", "").strip() or "TPV-Philips Warehouse & Repair"
        out_fname     = request.form.get("output_filename", "").strip()

        from philips_builder import analyze_philips
        logs = []
        analysis = analyze_philips(report_path, parts_sqft_manual=parts_sqft, log=logs.append)

        _philips.replace(_sid(), {
            "report_path":     str(report_path),
            "parts_sqft":      parts_sqft,
            "invoice_title":   invoice_title,
            "output_filename": out_fname,
            "analysis":        analysis,
        })

        return jsonify({"ok": True, "logs": logs, **_philips_summary_payload(analysis)})
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 400


# ── Step 2: Confirm (supply any missing dimensions) + build ───────────────────
@app.route("/confirm_philips", methods=["POST"])
def confirm_philips():
    sid = _sid()
    q = _philips.new_queue(sid)
    log, done = SessionStore.make_logger(q)

    try:
        philips_session = _philips.get(sid)
        if "analysis" not in philips_session:
            return jsonify({"ok": False, "error": "No analysis loaded — run Analyze first"}), 400

        body = request.get_json(force=True) or {}
        supplied = body.get("dimensions", {})   # {model: sqft}

        sess = dict(philips_session)
        output_path = OUTPUT_DIR / (f"{sess['output_filename']}.xlsx" if sess["output_filename"]
                                     else f"TPV_Philips_Invoice_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")

        def _run():
            try:
                from philips_builder import analyze_philips, build_philips_invoice, add_dimension, load_dimensions, _compile_tiers, load_repair_cost_raw

                if supplied:
                    log(f"Adding {len(supplied)} new dimension(s) to the reference store…")
                    for model, sqft in supplied.items():
                        if sqft in (None, ""):
                            continue
                        add_dimension(model, float(sqft))

                log("Re-computing with the current reference data…")
                dims  = load_dimensions()
                tiers = _compile_tiers(load_repair_cost_raw())
                analysis = analyze_philips(sess["report_path"], parts_sqft_manual=sess["parts_sqft"],
                                           dims=dims, tiers=tiers, log=log)

                log("Building invoice workbook…")
                result = build_philips_invoice(analysis, sess["invoice_title"], output_path, log=log)
                done(True, {
                    "filename":        output_path.name,
                    "report_filename": sess.get("report_filename"),
                    "inbound_count":   analysis["inbound_count"],
                    "outbound_count":  analysis["outbound_count"],
                    "repair_count":    analysis["repair_count"],
                    "harvest_count":   analysis["harvest_count"],
                    "excluded_count":  len(analysis["excluded_df"]),
                    **result,
                })
            except Exception as e:
                import traceback
                log(f"Error: {e}")
                done(False, {"error": str(e), "trace": traceback.format_exc()})

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"ok": True})

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 400


@app.route("/stream_philips")
def stream_philips():
    q = _philips.queue(_sid())
    def event_stream():
        while True:
            try:
                item = q.get(timeout=60)
                yield f"data: {json.dumps(item)}\n\n"
                if item.get("type") == "done":
                    break
            except queue.Empty:
                yield 'data: {"type":"ping"}\n\n'
    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ══════════════════════════════════════════════════════════════════════════════
# Philips Month End Report — generate from raw data, then chain into invoice
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/generate_report_and_analyze", methods=["POST"])
def generate_report_and_analyze():
    try:
        raw_file = request.files["raw_data"]
        raw_path = _save_upload(raw_file, "Raw Data", "xlsx")

        period_start = request.form["period_start"]
        period_end   = request.form["period_end"]
        parts_sqft    = float(request.form.get("parts_sqft", 0) or 0)
        invoice_title = request.form.get("invoice_title", "").strip() or "TPV-Philips Warehouse & Repair"
        out_fname     = request.form.get("output_filename", "").strip()
        report_fname  = request.form.get("report_filename", "").strip()

        from report_builder import analyze_raw_data, build_month_end_report
        from philips_builder import analyze_philips

        logs = []
        report_analysis = analyze_raw_data(raw_path, period_start, period_end, log=logs.append)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_name = f"{report_fname}.xlsx" if report_fname else f"TPV_Philips_MonthEndReport_{ts}.xlsx"
        report_path = OUTPUT_DIR / report_name
        build_month_end_report(report_analysis, report_path, log=logs.append)

        invoice_analysis = analyze_philips(report_path, parts_sqft_manual=parts_sqft, log=logs.append)

        _philips.replace(_sid(), {
            "report_path":     str(report_path),
            "report_filename": report_name,
            "parts_sqft":      parts_sqft,
            "invoice_title":   invoice_title,
            "output_filename": out_fname,
            "analysis":        invoice_analysis,
        })

        return jsonify({
            "ok": True, "logs": logs,
            "report_filename": report_name,
            "flagged_received_count": len(report_analysis["flagged_received_df"]),
            "pending_repairs_count":  len(report_analysis["pending_repairs_df"]),
            **_philips_summary_payload(invoice_analysis),
        })
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 400


# ── Dimensions admin: view / upload / download ─────────────────────────────────
@app.route("/get_philips_dimensions")
def get_philips_dimensions():
    from philips_builder import load_dimensions
    dims = load_dimensions()
    return jsonify({"ok": True, "count": len(dims)})


@app.route("/upload_philips_dimensions", methods=["POST"])
def upload_philips_dimensions():
    try:
        f = request.files["dimensions"]
        tmp_path = _save_upload(f, "Dimensions Workbook", "xlsx")
        from philips_builder import parse_dimensions_workbook, replace_dimensions_from_rows
        rows = parse_dimensions_workbook(tmp_path)
        if not rows:
            return jsonify({"ok": False, "error": "No Model/Sq Footage rows found in that file"}), 400
        dims = replace_dimensions_from_rows(rows)
        return jsonify({"ok": True, "count": len(dims)})
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 400


@app.route("/download_philips_dimensions")
def download_philips_dimensions():
    from philips_builder import load_dimensions, write_dimensions_workbook
    dims = load_dimensions()
    out_path = OUTPUT_DIR / "Philips_Box_Dimensions.xlsx"
    write_dimensions_workbook(out_path, dims)
    return send_file(out_path, as_attachment=True, download_name="Philips_Box_Dimensions.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── Repair Cost pricing admin ───────────────────────────────────────────────────
@app.route("/get_philips_repair_cost")
def get_philips_repair_cost():
    from philips_builder import load_repair_cost_raw
    return jsonify({"ok": True, "tiers": load_repair_cost_raw()})


@app.route("/set_philips_repair_cost", methods=["POST"])
def set_philips_repair_cost():
    try:
        from philips_builder import save_repair_cost_raw
        body = request.get_json(force=True)
        tiers = body.get("tiers", [])
        for t in tiers:
            str(t["size"])  # validate shape
            float(t["rb_price"]); float(t["harvest_price"])
        save_repair_cost_raw(tiers)
        return jsonify({"ok": True, "count": len(tiers)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ══════════════════════════════════════════════════════════════════════════════
# TCL (TTE Technology) Warehouse Invoice — routes
# ══════════════════════════════════════════════════════════════════════════════

_tcl = SessionStore()   # in-memory analysis result between analyze -> confirm, per browser session


# ── Step 1: Analyze (upload inventory file, split units/parts, return groups) ─

@app.route("/analyze_tcl", methods=["POST"])
def analyze_tcl_route():
    try:
        sid = _sid()
        tcl_analysis = _tcl.get(sid)

        inv_file = request.files["inventory"]
        inv_path = _save_upload(inv_file, "Inventory Export", "xlsx")

        date_from = datetime.strptime(request.form["date_from"], "%Y-%m-%d")
        date_to   = datetime.strptime(request.form["date_to"],   "%Y-%m-%d")

        tcl_analysis["_meta"] = {
            "invoice_number": request.form.get("invoice_number", "").strip(),
            "invoice_date":   request.form.get("invoice_date", str(date_to.date())),
            "due_date":       request.form.get("due_date", ""),
            "po_number":      request.form.get("po_number", "Contract"),
            "terms":          request.form.get("terms", "Net 30"),
            "bill_to":        request.form.get("bill_to", "").strip(),
            "ship_to":        request.form.get("ship_to", "").strip(),
            "output_filename":request.form.get("output_filename", "").strip(),
        }

        from tcl_builder import analyze_tcl
        logs = []
        result = analyze_tcl(
            inventory_path=str(inv_path),
            date_from=date_from, date_to=date_to,
            log=logs.append,
        )
        tcl_analysis["data"] = result

        def _grp_out(g, kind):
            out = {"key": g["key"], "quantity": g["quantity"], "received_date": g["received_date"]}
            if kind == "part":
                out["model"] = g["model"]
                out["grade"] = g["grade"]
            return out

        return jsonify({
            "ok": True,
            "logs": logs,
            "unit_count":  result["unit_count"],
            "part_count":  result["part_count"],
            "unit_groups": [_grp_out(g, "unit") for g in result["unit_groups"]],
            "part_groups": [_grp_out(g, "part") for g in result["part_groups"]],
        })
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 400


# ── Step 2: Confirm + build (accepts pallet/box breakdowns) ───────────────────

@app.route("/confirm_tcl", methods=["POST"])
def confirm_tcl():
    sid = _sid()
    q = _tcl.new_queue(sid)
    log, done = SessionStore.make_logger(q)

    try:
        body = request.get_json(force=True)
        unit_breakdowns = body.get("unit_breakdowns", {})
        box_breakdowns  = body.get("box_breakdowns", {})

        tcl_analysis = _tcl.get(sid)
        if "data" not in tcl_analysis:
            return jsonify({"ok": False, "error": "No analysis loaded — run Analyze first"}), 400

        analysis = tcl_analysis["data"]
        meta     = tcl_analysis.get("_meta", {})

        from tcl_builder import validate_breakdowns
        errors, parsed_unit, parsed_box = validate_breakdowns(analysis, unit_breakdowns, box_breakdowns)
        if errors:
            return jsonify({"ok": False, "error": "Fix the highlighted breakdowns", "field_errors": errors}), 400

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_fname = meta.get("output_filename", "").strip()
        invoice_name = f"{out_fname}.xlsx" if out_fname else f"TCL_Warehouse_Invoice_{ts}.xlsx"
        output_path  = OUTPUT_DIR / invoice_name

        def _run():
            try:
                from tcl_builder import build_tcl_invoice, DEFAULT_BILL_TO
                invoice_date = datetime.strptime(meta["invoice_date"], "%Y-%m-%d") if meta.get("invoice_date") else datetime.now()
                due_date     = datetime.strptime(meta["due_date"], "%Y-%m-%d") if meta.get("due_date") else None
                result = build_tcl_invoice(
                    analysis=analysis,
                    unit_breakdowns=parsed_unit,
                    box_breakdowns=parsed_box,
                    invoice_number=meta.get("invoice_number", ""),
                    invoice_date=invoice_date,
                    due_date=due_date,
                    po_number=meta.get("po_number") or "Contract",
                    terms=meta.get("terms") or "Net 30",
                    bill_to=meta.get("bill_to") or DEFAULT_BILL_TO,
                    ship_to=meta.get("ship_to") or None,
                    output_path=output_path,
                    log=log,
                )
                done(True, {"filename": output_path.name, **result})
            except Exception as e:
                import traceback
                log(f"Error: {e}")
                done(False, {"error": str(e), "trace": traceback.format_exc()})

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"ok": True})

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 400


@app.route("/stream_tcl")
def stream_tcl():
    q = _tcl.queue(_sid())
    def event_stream():
        while True:
            try:
                item = q.get(timeout=60)
                yield f"data: {json.dumps(item)}\n\n"
                if item.get("type") == "done":
                    break
            except queue.Empty:
                yield 'data: {"type":"ping"}\n\n'
    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ══════════════════════════════════════════════════════════════════════════════
# AMC Warehouse Invoice — routes
# ══════════════════════════════════════════════════════════════════════════════

_amc = SessionStore()   # holds analysis + upload paths between analyze→confirm, per browser session


def _amc_summary_payload(analysis):
    return {
        "receipt_count":  analysis["receipt_count"],
        "ship_count":     analysis["ship_count"],
        "total_sqft":     round(analysis["total_sqft"], 2),
        "additional_sqft": analysis["additional_sqft"],
        "missing_dimension_models": analysis["missing_dimension_models"],
    }


# ── Step 1: Analyze ────────────────────────────────────────────────────────────
@app.route("/analyze_amc", methods=["POST"])
def analyze_amc_route():
    try:
        recv_file = request.files["receiving"]
        ship_file = request.files["shipping"]
        inv_file  = request.files["inventory"]
        recv_path = _save_upload(recv_file, "Receiving Export", "xlsx")
        ship_path = _save_upload(ship_file, "Shipping Export", "xlsx")
        inv_path  = _save_upload(inv_file,  "Inventory Export", "xlsx")

        period_start  = request.form["period_start"]
        period_end    = request.form["period_end"]
        invoice_title = request.form.get("invoice_title", "").strip() or "AMC Warehouse Invoice"
        out_fname     = request.form.get("output_filename", "").strip()

        from amc_builder import analyze_amc
        logs = []
        analysis = analyze_amc(recv_path, ship_path, inv_path, period_start, period_end, log=logs.append)

        _amc.replace(_sid(), {
            "receiving_path":  str(recv_path),
            "shipping_path":   str(ship_path),
            "inventory_path":  str(inv_path),
            "period_start":    period_start,
            "period_end":      period_end,
            "invoice_title":   invoice_title,
            "output_filename": out_fname,
            "analysis":        analysis,
        })

        return jsonify({"ok": True, "logs": logs, **_amc_summary_payload(analysis)})
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 400


# ── Step 2: Confirm (supply any missing dimensions) + build ───────────────────
@app.route("/confirm_amc", methods=["POST"])
def confirm_amc():
    sid = _sid()
    q = _amc.new_queue(sid)
    log, done = SessionStore.make_logger(q)

    try:
        amc_session = _amc.get(sid)
        if "analysis" not in amc_session:
            return jsonify({"ok": False, "error": "No analysis loaded — run Analyze first"}), 400

        body = request.get_json(force=True) or {}
        supplied = body.get("dimensions", {})   # {model: sqft}

        sess = dict(amc_session)
        output_path = OUTPUT_DIR / (f"{sess['output_filename']}.xlsx" if sess["output_filename"]
                                     else f"AMC_Warehouse_Invoice_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")

        def _run():
            try:
                from amc_builder import analyze_amc, build_amc_invoice, add_dimension, load_dimensions, load_prices

                if supplied:
                    log(f"Adding {len(supplied)} new dimension(s) to the reference store…")
                    for model, sqft in supplied.items():
                        if sqft in (None, ""):
                            continue
                        add_dimension(model, float(sqft))

                log("Re-computing with the current reference data…")
                dims   = load_dimensions()
                prices = load_prices()
                analysis = analyze_amc(sess["receiving_path"], sess["shipping_path"], sess["inventory_path"],
                                        sess["period_start"], sess["period_end"], dims=dims, prices=prices, log=log)

                log("Building invoice workbook…")
                result = build_amc_invoice(analysis, sess["invoice_title"], output_path, prices=prices, log=log)
                done(True, {
                    "filename":       output_path.name,
                    "receipt_count":  analysis["receipt_count"],
                    "ship_count":     analysis["ship_count"],
                    "additional_sqft": analysis["additional_sqft"],
                    "excluded_count": len(analysis["excluded_df"]),
                    **result,
                })
            except Exception as e:
                import traceback
                log(f"Error: {e}")
                done(False, {"error": str(e), "trace": traceback.format_exc()})

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"ok": True})

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 400


@app.route("/stream_amc")
def stream_amc():
    q = _amc.queue(_sid())
    def event_stream():
        while True:
            try:
                item = q.get(timeout=60)
                yield f"data: {json.dumps(item)}\n\n"
                if item.get("type") == "done":
                    break
            except queue.Empty:
                yield 'data: {"type":"ping"}\n\n'
    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Dimensions admin: view / upload / download ─────────────────────────────────
@app.route("/get_amc_dimensions")
def get_amc_dimensions():
    from amc_builder import load_dimensions
    dims = load_dimensions()
    return jsonify({"ok": True, "count": len(dims)})


@app.route("/upload_amc_dimensions", methods=["POST"])
def upload_amc_dimensions():
    try:
        f = request.files["dimensions"]
        tmp_path = _save_upload(f, "Dimensions Workbook", "xlsx")
        from amc_builder import parse_dimensions_workbook, replace_dimensions_from_rows
        rows = parse_dimensions_workbook(tmp_path)
        if not rows:
            return jsonify({"ok": False, "error": "No Model/Sq Ft rows found in that file"}), 400
        dims = replace_dimensions_from_rows(rows)
        return jsonify({"ok": True, "count": len(dims)})
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 400


@app.route("/download_amc_dimensions")
def download_amc_dimensions():
    from amc_builder import load_dimensions, write_dimensions_workbook
    dims = load_dimensions()
    out_path = OUTPUT_DIR / "AMC_Dimensions.xlsx"
    write_dimensions_workbook(out_path, dims)
    return send_file(out_path, as_attachment=True, download_name="AMC_Dimensions.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── Pricing admin ───────────────────────────────────────────────────────────────
@app.route("/get_amc_prices")
def get_amc_prices():
    from amc_builder import load_prices, DEFAULT_PRICES
    return jsonify({"ok": True, "prices": load_prices(), "defaults": DEFAULT_PRICES})


@app.route("/set_amc_prices", methods=["POST"])
def set_amc_prices():
    try:
        from amc_builder import save_prices
        body = request.get_json(force=True)
        prices = body.get("prices", {})
        for k, v in prices.items():
            float(v)  # validate shape
        save_prices(prices)
        return jsonify({"ok": True, "count": len(prices)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ══════════════════════════════════════════════════════════════════════════════
# FedEx Shipment Upload (Promethean) — not an invoice, a ticketing-system
# bulk import: turns FedEx's raw monthly billing export into one "call" per
# shipment, billed to Promethean at a markup over FedEx's own net charge.
# Single-step (analyze + build together) — the transform is deterministic,
# nothing needs human review/correction before it's safe to write. The
# analyze step still runs first and its counts/warnings come back before the
# file is written, so a bad upload (wrong file, no billable rows) surfaces
# before anything lands in outputs/.
# ══════════════════════════════════════════════════════════════════════════════
_fedex_shipment = SessionStore()   # holds the built rows between analyze→build, per browser session


@app.route("/analyze_fedex_shipment", methods=["POST"])
def analyze_fedex_shipment_route():
    try:
        raw_file = request.files["raw"]
        raw_path = _save_upload(raw_file, "Raw FedEx Export", "xlsx")

        period_label = request.form.get("period_label", "").strip()
        call_date    = request.form.get("call_date", "").strip()
        out_fname    = request.form.get("output_filename", "").strip()
        if not period_label:
            raise ValueError("Period label (e.g. 'June') is required.")
        if not call_date:
            raise ValueError("Call date is required.")

        from fedex_shipment_builder import analyze_fedex_shipment, load_defaults
        logs = []
        analysis = analyze_fedex_shipment(
            str(raw_path), period_label=period_label, call_date=call_date,
            defaults=load_defaults(), log=logs.append,
        )

        _fedex_shipment.replace(_sid(), {
            "raw_path":        str(raw_path),
            "period_label":    period_label,
            "call_date":       call_date,
            "output_filename": out_fname,
            "analysis":        analysis,
        })

        return jsonify({
            "ok": True,
            "logs": logs,
            "row_count":       len(analysis["rows"]),
            "total_price":     analysis["total_price"],
            "defaulted_rows":  analysis["defaulted_rows"],
            "skipped_rows":    analysis["skipped_rows"],
        })
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 400


@app.route("/build_fedex_shipment", methods=["POST"])
def build_fedex_shipment():
    sid = _sid()
    q = _fedex_shipment.new_queue(sid)
    log, done = SessionStore.make_logger(q)

    try:
        sess = _fedex_shipment.get(sid)
        if "analysis" not in sess:
            return jsonify({"ok": False, "error": "No analysis loaded — run Analyze first"}), 400

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_fname = sess.get("output_filename", "").strip()
        fname = f"{out_fname}.xlsx" if out_fname else f"FedEx_Shipment_Upload_{ts}.xlsx"
        output_path = OUTPUT_DIR / fname
        analysis = sess["analysis"]

        def _run():
            try:
                from fedex_shipment_builder import build_fedex_shipment_upload
                result = build_fedex_shipment_upload(analysis, output_path, log=log)
                done(True, {"filename": output_path.name, **result})
            except Exception as e:
                import traceback
                log(f"Error: {e}")
                done(False, {"error": str(e), "trace": traceback.format_exc()})

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"ok": True})

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 400


@app.route("/stream_fedex_shipment")
def stream_fedex_shipment():
    q = _fedex_shipment.queue(_sid())
    def event_stream():
        while True:
            try:
                item = q.get(timeout=60)
                yield f"data: {json.dumps(item, default=str)}\n\n"
                if item.get("type") == "done":
                    break
            except queue.Empty:
                yield 'data: {"type":"ping"}\n\n'
    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Defaults admin (site info + margin divisor + static call fields) ──────────
@app.route("/get_fedex_shipment_defaults")
def get_fedex_shipment_defaults():
    from fedex_shipment_builder import load_defaults, DEFAULT_DEFAULTS
    return jsonify({"ok": True, "defaults": load_defaults(), "bundled_defaults": DEFAULT_DEFAULTS})


@app.route("/set_fedex_shipment_defaults", methods=["POST"])
def set_fedex_shipment_defaults():
    try:
        from fedex_shipment_builder import save_defaults, DEFAULT_DEFAULTS
        body = request.get_json(force=True)
        cfg = body.get("defaults", {})
        unknown = set(cfg) - set(DEFAULT_DEFAULTS)
        if unknown:
            raise ValueError(f"Unknown defaults key(s): {', '.join(sorted(unknown))}")
        save_defaults(cfg)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    print("  Modules active: Workshop Invoice + Storage Invoice + Philips Warehouse Invoice + TCL Warehouse Invoice + AMC Warehouse Invoice + FedEx Shipment Upload + Training Tracker")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"\n  Logicore Portal running → {url}\n  Press Ctrl+C to stop.\n")
    app.run(debug=False, port=5000)


