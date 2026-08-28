import csv
import io
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from flask import Response, flash, redirect, render_template, request

from inventory_management.promethean_quality import (
    REFERENCE_FILENAME,
    canonical_part,
    decode_serial,
    load_reference,
    validate_reference,
)


_AUDIT_CACHE = {}
_AUDIT_LOCK = threading.Lock()


def _text(value):
    return "" if value is None else str(value).strip()


def _normalize(value):
    value = _text(value).upper()
    value = value.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    value = value.replace("\ufffe", "-").replace("\ufffd", "-")
    value = re.sub(r"\s+", "", value)
    return value.strip(". ,;:")


def _norm_serial(value):
    return re.sub(r"[^A-Z0-9]", "", _text(value).upper())


def canonical_model(value):
    """Operational model without region/repair-stock suffixes.

    Examples:
      AP9-B75-02-M-NA-R -> AP9-B75-02-M
      AP10-A65-M-NA    -> AP10-A65-M
    """
    model = _normalize(value)
    if not model:
        return ""
    model = re.sub(r"-(?:NA|US|EU|UK|CN|ANZ|APAC)-R$", "", model)
    model = re.sub(r"-(?:NA|US|EU|UK|CN|ANZ|APAC)$", "", model)
    model = re.sub(r"-R$", "", model)
    return model


def parts_model_key(model, reference):
    if not reference:
        return None
    models = reference.get("models") or {}
    current = canonical_model(model)
    if current in models:
        return current

    candidates = [current]
    if current.endswith("-M"):
        candidates.append(current[:-2])
    for candidate in list(candidates):
        if candidate.startswith("AP9-") and candidate.endswith("-02"):
            candidates.append(candidate[:-3])
        if candidate.startswith("AP9-") and "-02-" in candidate:
            candidates.append(candidate.replace("-02-", "-"))
    for candidate in candidates:
        if candidate in models:
            return candidate
    return None


def _reference_path(data_dir):
    return Path(data_dir) / REFERENCE_FILENAME


def _ensure_override_table(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS promethean_model_overrides (
            normalized_serial TEXT PRIMARY KEY,
            serial_number TEXT NOT NULL,
            approved_model TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.commit()


def _load_overrides(db):
    rows = db.execute(
        "SELECT * FROM promethean_model_overrides ORDER BY updated_at DESC, serial_number"
    ).fetchall()
    return {row["normalized_serial"]: dict(row) for row in rows}


def _latest_inventory_batch(db):
    return db.execute(
        """
        SELECT b.*
        FROM import_batches b
        JOIN events e ON e.batch_id=b.id
        WHERE e.source_type='inventory' AND e.event_type='inventory_observed'
        GROUP BY b.id
        ORDER BY b.id DESC
        LIMIT 1
        """
    ).fetchone()


def _inventory_rows(db, batch_id):
    return db.execute(
        """
        SELECT e.id, e.event_at, e.model_number, e.details_json, e.source_row_key,
               a.serial_number, b.original_filename, b.imported_at
        FROM events e
        LEFT JOIN assets a ON a.id=e.asset_id
        JOIN import_batches b ON b.id=e.batch_id
        WHERE e.batch_id=?
          AND e.source_type='inventory'
          AND e.event_type='inventory_observed'
        ORDER BY e.id
        """,
        (batch_id,),
    ).fetchall()


def _details(row):
    try:
        return json.loads(row["details_json"] or "{}")
    except Exception:
        return {}


def _grade_code(value):
    grade = _text(value).upper().strip()
    if grade in {"A", "B", "R"}:
        return grade
    match = re.match(r"^([ABR])(?:\b|[-_\s])", grade)
    return match.group(1) if match else grade


def _model_check(serial, recorded_model, reference):
    recorded_core = canonical_model(recorded_model)
    decoded = decode_serial(serial, reference) if serial else None
    if not recorded_model:
        return {
            "status": "missing_model",
            "message": "No recorded model number was supplied.",
            "decoded": decoded,
            "recorded_core": recorded_core,
        }
    if decoded:
        expected = canonical_model(decoded.get("model"))
        if recorded_core == expected:
            return {
                "status": "ok",
                "message": "Recorded model agrees with the serial number.",
                "decoded": decoded,
                "recorded_core": recorded_core,
            }
        return {
            "status": "mismatch",
            "message": f"Serial decodes to {decoded.get('model')}, not {recorded_model}.",
            "decoded": decoded,
            "recorded_core": recorded_core,
        }
    if parts_model_key(recorded_model, reference):
        return {
            "status": "not_decoded",
            "message": "Model is recognized, but this serial format is not covered by the documented decoder.",
            "decoded": None,
            "recorded_core": recorded_core,
        }
    return {
        "status": "unknown_model",
        "message": "Recorded value is not recognized as a Promethean panel model.",
        "decoded": None,
        "recorded_core": recorded_core,
    }


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


def _suffix_expectation(model_core, grade):
    if not model_core:
        return None
    if grade in {"B", "R"}:
        return f"{model_core}-NA-R"
    if grade == "A":
        return f"{model_core}-NA"
    return None


def _audit_signature(db, reference_path, batch_id):
    try:
        ref_mtime = reference_path.stat().st_mtime_ns
    except OSError:
        ref_mtime = 0
    override_stats = db.execute(
        "SELECT COUNT(*) AS c, COALESCE(MAX(updated_at),'') AS u FROM promethean_model_overrides"
    ).fetchone()
    return (batch_id or 0, ref_mtime, override_stats["c"], override_stats["u"])


def run_inventory_audit(db, reference, reference_path):
    batch = _latest_inventory_batch(db)
    if not batch:
        return {
            "batch": None,
            "inventory_records": 0,
            "units_checked": 0,
            "recognized_parts": 0,
            "issues": [],
            "errors": 0,
            "warnings": 0,
            "unmatched": [],
            "unmatched_count": 0,
            "overrides": list(_load_overrides(db).values()),
        }

    signature = _audit_signature(db, reference_path, batch["id"])
    with _AUDIT_LOCK:
        cached = _AUDIT_CACHE.get(signature)
        if cached is not None:
            return cached

    overrides = _load_overrides(db)
    issues = []
    unmatched = []
    units_checked = 0
    recognized_parts = 0
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
        override = overrides.get(serial_key)
        decoded = decode_serial(serial, reference) if serial else None
        recorded_core = canonical_model(recorded_model)
        known_model_key = parts_model_key(recorded_model, reference)
        known_part = canonical_part(recorded_model) in part_models if recorded_model else False

        expected_core = None
        expected_label = None
        expectation_source = None
        if override:
            expected_core = canonical_model(override["approved_model"])
            expected_label = override["approved_model"]
            expectation_source = "Approved override"
        elif decoded:
            expected_core = canonical_model(decoded.get("model"))
            expected_label = decoded.get("model")
            expectation_source = "Serial decoder"

        is_unit = bool(expected_core or known_model_key)
        if not expected_core and not known_model_key and known_part:
            recognized_parts += 1
            continue

        if is_unit:
            units_checked += 1

        if expected_core:
            if not recorded_model:
                issue = dict(item)
                issue.update(
                    issue_type="Missing model",
                    severity="error",
                    expected_model=expected_label,
                    expectation_source=expectation_source,
                    message="Inventory record has no model number.",
                )
                issues.append(issue)
            elif recorded_core != expected_core:
                issue = dict(item)
                issue.update(
                    issue_type="Model mismatch" if not override else "Approved model mismatch",
                    severity="error",
                    expected_model=expected_label,
                    expectation_source=expectation_source,
                    message=f"Recorded model {recorded_model} does not match {expected_label}.",
                )
                issues.append(issue)
        elif not known_part:
            review = dict(item)
            review.update(
                decoded=None,
                approved_model=None,
                known_model=bool(known_model_key),
                message=(
                    "Model is recognized, but this serial format cannot be decoded. Approve the correct model for this serial to teach the checker."
                    if known_model_key
                    else "Recorded value is not recognized as a panel model or part number, and the serial cannot be decoded."
                ),
            )
            unmatched.append(review)

        if is_unit:
            grade = item["grade"]
            normalized_recorded = _normalize(recorded_model)
            suffix_core = expected_core or recorded_core
            expected_full = _suffix_expectation(suffix_core, grade)
            if grade in {"B", "R"} and expected_full and not normalized_recorded.endswith("-NA-R"):
                issue = dict(item)
                issue.update(
                    issue_type=f"{grade}-grade suffix",
                    severity="error",
                    expected_model=expected_full,
                    expectation_source="Grade rule",
                    message=f"{grade}-grade inventory units must end in -NA-R.",
                )
                issues.append(issue)
            elif grade == "A" and expected_full and not normalized_recorded.endswith("-NA"):
                issue = dict(item)
                issue.update(
                    issue_type="A-grade suffix",
                    severity="warning",
                    expected_model=expected_full,
                    expectation_source="Grade rule",
                    message="A-stock units typically end in -NA rather than -NA-R.",
                )
                issues.append(issue)

    audit = {
        "batch": dict(batch),
        "inventory_records": len(rows),
        "units_checked": units_checked,
        "recognized_parts": recognized_parts,
        "issues": issues,
        "errors": sum(1 for i in issues if i["severity"] == "error"),
        "warnings": sum(1 for i in issues if i["severity"] == "warning"),
        "unmatched": unmatched,
        "unmatched_count": len(unmatched),
        "overrides": list(overrides.values()),
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


def _reference_summary(reference):
    if not reference:
        return None
    return {
        "models": len(reference.get("models") or {}),
        "parts": len(reference.get("part_models") or {}),
        "version": reference.get("version") or "Installed",
        "source": reference.get("source") or "Promethean reference",
    }


def _clear_cache():
    with _AUDIT_LOCK:
        _AUDIT_CACHE.clear()


def register_quality_checker(app, db_connect, clean, data_dir):
    reference_path = _reference_path(data_dir)
    db = db_connect()
    _ensure_override_table(db)
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
        overrides = list(_load_overrides(db).values())
        if reference and request.args.get("audit") == "1":
            audit = run_inventory_audit(db, reference, reference_path)
        db.close()
        return render_template(
            "quality.html",
            reference=_reference_summary(reference),
            reference_path=str(reference_path),
            manual=manual,
            audit=audit,
            overrides=overrides,
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
            """
            INSERT INTO promethean_model_overrides
                (normalized_serial, serial_number, approved_model, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_serial) DO UPDATE SET
                serial_number=excluded.serial_number,
                approved_model=excluded.approved_model,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            (_norm_serial(serial), serial, approved_model, note or None, now, now),
        )
        db.commit()
        db.close()
        _clear_cache()
        flash(f"Approved {approved_model} for serial {serial}.", "success")
        return redirect("quality?audit=1")

    @app.route("/quality/override/delete", methods=["POST"])
    def quality_override_delete():
        serial = clean(request.form.get("serial"))
        if serial:
            db = db_connect()
            _ensure_override_table(db)
            db.execute("DELETE FROM promethean_model_overrides WHERE normalized_serial=?", (_norm_serial(serial),))
            db.commit()
            db.close()
            _clear_cache()
            flash(f"Removed approved model mapping for {serial}.", "success")
        return redirect("quality?audit=1")

    @app.route("/quality/export.csv")
    def quality_export():
        reference = load_reference(data_dir)
        if not reference:
            flash("Install the Promethean reference data before running an audit.", "error")
            return redirect("quality")
        db = db_connect()
        _ensure_override_table(db)
        audit = run_inventory_audit(db, reference, reference_path)
        db.close()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "Record Type", "Issue Type", "Severity", "Serial Number", "Grade", "Recorded Model",
            "Expected / Approved Model", "Warehouse", "Rack", "Bin", "Source File", "Source Row", "Detail",
        ])
        for item in audit["issues"]:
            writer.writerow([
                "Issue", item.get("issue_type"), item.get("severity"), item.get("serial_number"), item.get("grade"),
                item.get("model_number"), item.get("expected_model"), item.get("warehouse"), item.get("rack"), item.get("bin"),
                item.get("original_filename"), item.get("source_row_key"), item.get("message"),
            ])
        for item in audit["unmatched"]:
            writer.writerow([
                "Needs model mapping", "Unmatched serial/model", "review", item.get("serial_number"), item.get("grade"),
                item.get("model_number"), "", item.get("warehouse"), item.get("rack"), item.get("bin"),
                item.get("original_filename"), item.get("source_row_key"), item.get("message"),
            ])
        return Response(
            out.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="Promethean_Current_Inventory_Audit.csv"'},
        )
