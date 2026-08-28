import csv
import io
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from flask import Response, flash, redirect, render_template, request

from inventory_management.promethean_quality import REFERENCE_FILENAME, canonical_part, load_reference, validate_reference
from inventory_management.promethean_quality_v2 import (
    _details,
    _ensure_override_table,
    _grade_code,
    _inventory_rows,
    _latest_inventory_batch,
    _load_overrides,
    _norm_serial,
    _reference_path,
    _reference_summary,
    _text,
    canonical_model,
    parts_model_key,
)
from inventory_management.promethean_serial_rules_v2 import decode_serial


_AUDIT_CACHE = {}
_AUDIT_LOCK = threading.Lock()


def _normalize(value):
    value = _text(value).upper()
    value = value.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    value = value.replace("\ufffe", "-").replace("\ufffd", "-")
    value = re.sub(r"\s+", "", value)
    return value.strip(". ,;:")


def _ensure_whitelist_table(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS promethean_whitelist (
            kind TEXT NOT NULL CHECK(kind IN ('part','model')),
            normalized_value TEXT NOT NULL,
            display_value TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(kind, normalized_value)
        )
        """
    )
    db.commit()


def _load_whitelist(db):
    rows = db.execute(
        "SELECT * FROM promethean_whitelist ORDER BY kind, display_value"
    ).fetchall()
    result = {"part": {}, "model": {}}
    for row in rows:
        result[row["kind"]][row["normalized_value"]] = dict(row)
    return result


def _whitelist_key(kind, value):
    if kind == "part":
        return canonical_part(value)
    return canonical_model(value)


def _expected_full_model(core, grade):
    if not core:
        return ""
    if grade in {"B", "R"}:
        return f"{core}-NA-R"
    if grade == "A":
        return f"{core}-NA"
    return core


def _model_check(serial, recorded_model, reference):
    recorded_core = canonical_model(recorded_model)
    decoded = decode_serial(serial, reference) if serial else None
    if not recorded_model:
        return {"status": "missing_model", "message": "No recorded model number was supplied.", "decoded": decoded, "recorded_core": recorded_core}
    if decoded:
        expected = canonical_model(decoded.get("model"))
        if recorded_core == expected:
            return {"status": "ok", "message": "Recorded model agrees with the serial number.", "decoded": decoded, "recorded_core": recorded_core}
        return {"status": "mismatch", "message": f"Serial decodes to {decoded.get('model')}, not {recorded_model}.", "decoded": decoded, "recorded_core": recorded_core}
    if parts_model_key(recorded_model, reference):
        return {"status": "not_decoded", "message": "Model is recognized, but this serial format is not covered by the documented decoder.", "decoded": None, "recorded_core": recorded_core}
    return {"status": "unknown_model", "message": "Recorded value is not recognized as a Promethean panel model.", "decoded": None, "recorded_core": recorded_core}


def _part_check(part, serial, recorded_model, reference):
    part_core = canonical_part(part)
    decoded = decode_serial(serial, reference) if serial else None
    compatibility_model = decoded.get("model") if decoded else canonical_model(recorded_model)
    model_key = parts_model_key(compatibility_model, reference) if compatibility_model else None
    known_on = (reference.get("part_models") or {}).get(part_core) if reference else None
    if not part_core:
        return {"status": "missing_part", "message": "No part number was supplied.", "known_on": [], "parts_model": model_key}
    if not known_on:
        return {"status": "unknown_part", "message": "Part number is not present in the installed Promethean parts reference.", "known_on": [], "parts_model": model_key}
    if not model_key:
        return {"status": "known_part", "message": "Part number is recognized, but a compatible panel model could not be determined.", "known_on": known_on, "parts_model": None}
    if model_key in known_on:
        return {"status": "ok", "message": f"Part is listed for {model_key}.", "known_on": known_on, "parts_model": model_key}
    return {"status": "incompatible", "message": f"Part is not listed for {model_key}.", "known_on": known_on, "parts_model": model_key}


def _audit_signature(db, reference_path, batch_id):
    try:
        ref_mtime = reference_path.stat().st_mtime_ns
    except OSError:
        ref_mtime = 0
    override_stats = db.execute("SELECT COUNT(*) AS c, COALESCE(MAX(updated_at),'') AS u FROM promethean_model_overrides").fetchone()
    whitelist_stats = db.execute("SELECT COUNT(*) AS c, COALESCE(MAX(updated_at),'') AS u FROM promethean_whitelist").fetchone()
    return (batch_id or 0, ref_mtime, override_stats["c"], override_stats["u"], whitelist_stats["c"], whitelist_stats["u"])


def run_inventory_audit(db, reference, reference_path):
    _ensure_override_table(db)
    _ensure_whitelist_table(db)
    batch = _latest_inventory_batch(db)
    overrides = _load_overrides(db)
    whitelist = _load_whitelist(db)
    if not batch:
        return {
            "batch": None, "inventory_records": 0, "units_checked": 0, "recognized_parts": 0,
            "whitelisted_parts": 0, "whitelisted_models": 0, "issues": [], "errors": 0,
            "warnings": 0, "unmatched": [], "unmatched_count": 0,
            "overrides": list(overrides.values()), "whitelist": whitelist,
        }

    signature = _audit_signature(db, reference_path, batch["id"])
    with _AUDIT_LOCK:
        cached = _AUDIT_CACHE.get(signature)
        if cached is not None:
            return cached

    issues = []
    unmatched = []
    units_checked = 0
    recognized_parts = 0
    whitelisted_parts = 0
    whitelisted_models = 0
    rows = _inventory_rows(db, batch["id"])
    part_models = reference.get("part_models") or {}

    for row in rows:
        item = dict(row)
        details = _details(row)
        item.pop("details_json", None)
        item["grade"] = _grade_code(details.get("grade"))
        item["rack"] = _text(details.get("rack"))
        item["bin"] = _text(details.get("bin"))
        item["warehouse"] = _text(details.get("warehouse"))
        item["item_type"] = _text(details.get("item_type"))

        serial = _text(row["serial_number"])
        recorded_model = _text(row["model_number"])
        serial_key = _norm_serial(serial)
        recorded_core = canonical_model(recorded_model)
        part_key = canonical_part(recorded_model)
        grade = item["grade"]

        # A globally approved part/value is intentionally excluded from panel-model auditing.
        if part_key and part_key in whitelist["part"]:
            whitelisted_parts += 1
            continue

        override = overrides.get(serial_key)
        decoded = decode_serial(serial, reference) if serial else None
        known_model_key = parts_model_key(recorded_model, reference)
        globally_approved_model = whitelist["model"].get(recorded_core)
        known_part = part_key in part_models if recorded_model else False

        expected_core = ""
        expectation_source = ""
        if override:
            expected_core = canonical_model(override["approved_model"])
            expectation_source = "Approved serial mapping"
        elif decoded:
            expected_core = canonical_model(decoded.get("model"))
            expectation_source = "Serial decoder"
        elif globally_approved_model:
            expected_core = canonical_model(globally_approved_model["display_value"])
            expectation_source = "Approved model whitelist"
            whitelisted_models += 1

        is_unit = bool(expected_core or known_model_key or globally_approved_model)
        if not expected_core and not known_model_key and known_part:
            recognized_parts += 1
            continue

        if is_unit:
            units_checked += 1

        expected_full = _expected_full_model(expected_core or recorded_core, grade)
        normalized_recorded = _normalize(recorded_model)

        if expected_core:
            if not recorded_model:
                issue = dict(item)
                issue.update(issue_type="Missing model", severity="error", expected_model=expected_full or expected_core,
                             expectation_source=expectation_source, message="Inventory record has no model number.")
                issues.append(issue)
            elif recorded_core != expected_core:
                issue = dict(item)
                issue.update(issue_type="Model mismatch" if not override else "Approved model mismatch", severity="error",
                             expected_model=expected_full or expected_core, expectation_source=expectation_source,
                             message=f"Recorded model {recorded_model} does not match {_expected_full_model(expected_core, grade)}.")
                issues.append(issue)
        elif not known_part and not globally_approved_model:
            review = dict(item)
            review.update(
                decoded=None,
                approved_model=None,
                known_model=bool(known_model_key),
                message=(
                    "Model is recognized, but this serial format cannot be decoded. Approve the model globally or map this serial specifically."
                    if known_model_key
                    else "Recorded value is not recognized as a panel model or part number, and the serial cannot be decoded. Approve it as a part/value or model if it is valid."
                ),
            )
            unmatched.append(review)
            continue

        # Grade suffix is always validated against the full expected model.
        if is_unit:
            if grade in {"B", "R"} and expected_full and normalized_recorded != _normalize(expected_full):
                # Avoid duplicating the same row when a core-model mismatch already explains it.
                if recorded_core == (expected_core or recorded_core):
                    issue = dict(item)
                    issue.update(issue_type=f"{grade}-grade suffix", severity="error", expected_model=expected_full,
                                 expectation_source="Grade rule", message=f"{grade}-grade inventory units must use the full model ending -NA-R.")
                    issues.append(issue)
            elif grade == "A" and expected_full and normalized_recorded != _normalize(expected_full):
                if recorded_core == (expected_core or recorded_core):
                    issue = dict(item)
                    issue.update(issue_type="A-grade suffix", severity="warning", expected_model=expected_full,
                                 expectation_source="Grade rule", message="A-stock units normally use the full model ending -NA.")
                    issues.append(issue)

    audit = {
        "batch": dict(batch),
        "inventory_records": len(rows),
        "units_checked": units_checked,
        "recognized_parts": recognized_parts,
        "whitelisted_parts": whitelisted_parts,
        "whitelisted_models": whitelisted_models,
        "issues": issues,
        "errors": sum(1 for i in issues if i["severity"] == "error"),
        "warnings": sum(1 for i in issues if i["severity"] == "warning"),
        "unmatched": unmatched,
        "unmatched_count": len(unmatched),
        "overrides": list(overrides.values()),
        "whitelist": whitelist,
    }
    with _AUDIT_LOCK:
        _AUDIT_CACHE.clear()
        _AUDIT_CACHE[signature] = audit
    return audit


def _manual_check(args, reference):
    serial = _text(args.get("serial"))
    model = _text(args.get("model"))
    part = _text(args.get("part"))
    if not any((serial, model, part)):
        return None
    return {
        "model": _model_check(serial, model, reference) if model else None,
        "part": _part_check(part, serial, model, reference) if part else None,
        "serial_only": decode_serial(serial, reference) if serial and not model else None,
    }


def _clear_cache():
    with _AUDIT_LOCK:
        _AUDIT_CACHE.clear()


def register_quality_checker(app, db_connect, clean, data_dir):
    reference_path = _reference_path(data_dir)
    db = db_connect()
    _ensure_override_table(db)
    _ensure_whitelist_table(db)
    db.close()

    @app.route("/quality", methods=["GET", "POST"])
    def quality_checker():
        if request.method == "POST":
            uploaded = request.files.get("reference")
            if not uploaded or not uploaded.filename:
                flash("Choose a Promethean reference JSON file.", "error")
                return redirect("quality")
            if not uploaded.filename.lower().endswith(".json"):
                flash("Promethean reference data must be a .json file.", "error")
                return redirect("quality")
            try:
                data = validate_reference(json.loads(uploaded.read().decode("utf-8")))
                Path(data_dir).mkdir(parents=True, exist_ok=True)
                tmp = reference_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
                os.replace(tmp, reference_path)
                _clear_cache()
                flash("Promethean model/parts reference installed on persistent storage.", "success")
            except Exception as exc:
                flash(f"Reference file could not be installed: {exc}", "error")
            return redirect("quality")

        reference = load_reference(data_dir)
        manual = _manual_check(request.args, reference) if reference else None
        audit = None
        db = db_connect()
        _ensure_override_table(db)
        _ensure_whitelist_table(db)
        overrides = list(_load_overrides(db).values())
        whitelist = _load_whitelist(db)
        if reference and request.args.get("audit") == "1":
            audit = run_inventory_audit(db, reference, reference_path)
        db.close()
        return render_template(
            "quality.html", reference=_reference_summary(reference), reference_path=str(reference_path),
            manual=manual, audit=audit, overrides=overrides, whitelist=whitelist,
        )

    @app.route("/quality/override", methods=["POST"])
    def quality_override():
        serial = clean(request.form.get("serial"))
        approved_model = clean(request.form.get("approved_model"))
        note = clean(request.form.get("note"))
        if not serial or not approved_model:
            flash("Serial number and approved model are required.", "error")
            return redirect("quality?audit=1")
        now = datetime.utcnow().replace(microsecond=0).isoformat(sep=" ")
        db = db_connect()
        _ensure_override_table(db)
        db.execute(
            """INSERT INTO promethean_model_overrides
               (normalized_serial, serial_number, approved_model, note, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(normalized_serial) DO UPDATE SET serial_number=excluded.serial_number,
               approved_model=excluded.approved_model, note=excluded.note, updated_at=excluded.updated_at""",
            (_norm_serial(serial), serial, approved_model, note or None, now, now),
        )
        db.commit(); db.close(); _clear_cache()
        flash(f"Approved {approved_model} for serial {serial}.", "success")
        return redirect("quality?audit=1")

    @app.route("/quality/override/delete", methods=["POST"])
    def quality_override_delete():
        serial = clean(request.form.get("serial"))
        if serial:
            db = db_connect(); _ensure_override_table(db)
            db.execute("DELETE FROM promethean_model_overrides WHERE normalized_serial=?", (_norm_serial(serial),))
            db.commit(); db.close(); _clear_cache()
            flash(f"Removed approved model mapping for {serial}.", "success")
        return redirect("quality?audit=1")

    @app.route("/quality/whitelist", methods=["POST"])
    def quality_whitelist_add():
        kind = clean(request.form.get("kind")).lower()
        value = clean(request.form.get("value"))
        note = clean(request.form.get("note"))
        if kind not in {"part", "model"} or not value:
            flash("Choose Part / Value or Model and enter a value to approve.", "error")
            return redirect("quality?audit=1")
        key = _whitelist_key(kind, value)
        if not key:
            flash("The whitelist value could not be normalized.", "error")
            return redirect("quality?audit=1")
        now = datetime.utcnow().replace(microsecond=0).isoformat(sep=" ")
        db = db_connect(); _ensure_whitelist_table(db)
        db.execute(
            """INSERT INTO promethean_whitelist(kind, normalized_value, display_value, note, created_at, updated_at)
               VALUES(?,?,?,?,?,?) ON CONFLICT(kind, normalized_value) DO UPDATE SET
               display_value=excluded.display_value, note=excluded.note, updated_at=excluded.updated_at""",
            (kind, key, value, note or None, now, now),
        )
        db.commit(); db.close(); _clear_cache()
        flash(f"Approved {value} globally as a {kind}.", "success")
        return redirect("quality?audit=1")

    @app.route("/quality/whitelist/delete", methods=["POST"])
    def quality_whitelist_delete():
        kind = clean(request.form.get("kind")).lower()
        value = clean(request.form.get("value"))
        if kind in {"part", "model"} and value:
            db = db_connect(); _ensure_whitelist_table(db)
            db.execute("DELETE FROM promethean_whitelist WHERE kind=? AND normalized_value=?", (kind, _whitelist_key(kind, value)))
            db.commit(); db.close(); _clear_cache()
            flash(f"Removed {value} from the global whitelist.", "success")
        return redirect("quality?audit=1")

    @app.route("/quality/export.csv")
    def quality_export():
        reference = load_reference(data_dir)
        if not reference:
            flash("Install the Promethean reference data before running an audit.", "error")
            return redirect("quality")
        db = db_connect(); _ensure_override_table(db); _ensure_whitelist_table(db)
        audit = run_inventory_audit(db, reference, reference_path); db.close()
        out = io.StringIO(); writer = csv.writer(out)
        writer.writerow(["Record Type", "Issue Type", "Severity", "Serial Number", "Grade", "Recorded Model", "Expected / Approved Model", "Warehouse", "Rack", "Bin", "Source File", "Source Row", "Detail"])
        for item in audit["issues"]:
            writer.writerow(["Issue", item.get("issue_type"), item.get("severity"), item.get("serial_number"), item.get("grade"), item.get("model_number"), item.get("expected_model"), item.get("warehouse"), item.get("rack"), item.get("bin"), item.get("original_filename"), item.get("source_row_key"), item.get("message")])
        for item in audit["unmatched"]:
            writer.writerow(["Needs model mapping", "Unmatched serial/model", "review", item.get("serial_number"), item.get("grade"), item.get("model_number"), "", item.get("warehouse"), item.get("rack"), item.get("bin"), item.get("original_filename"), item.get("source_row_key"), item.get("message")])
        return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": 'attachment; filename="Promethean_Current_Inventory_Audit.csv"'})
