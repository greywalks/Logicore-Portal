import csv
import io
import json
import os
import re
import threading
from pathlib import Path

from flask import Response, flash, redirect, render_template, request


REFERENCE_FILENAME = "promethean_reference.json"
_AUDIT_CACHE = {}
_AUDIT_LOCK = threading.Lock()


def _text(value):
    return "" if value is None else str(value).strip()


def _normalize_token(value):
    value = _text(value).upper()
    value = value.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    value = value.replace("\ufffe", "-").replace("\ufffd", "-")
    value = re.sub(r"\s+", "", value)
    return value.strip(". ,;:")


def canonical_model(value):
    model = _normalize_token(value)
    if not model:
        return ""
    model = re.sub(r"-(?:NA|US|EU|UK|CN|ANZ|APAC)-R$", "", model)
    model = re.sub(r"-R$", "", model)
    return model


def canonical_part(value):
    part = _normalize_token(value)
    if not part:
        return ""
    part = re.sub(r"\([^)]*\)$", "", part).strip()
    return part.strip(".")


def _compact_serial(value):
    return re.sub(r"[^A-Z0-9]", "", _text(value).upper())


def _reference_path(data_dir):
    return Path(data_dir) / REFERENCE_FILENAME


def load_reference(data_dir):
    path = _reference_path(data_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict) or not isinstance(data.get("part_models"), dict):
        return None
    return data


def validate_reference(data):
    if not isinstance(data, dict):
        raise ValueError("Reference file must contain a JSON object.")
    models = data.get("models")
    part_models = data.get("part_models")
    rules = data.get("serial_rules")
    if not isinstance(models, dict) or not models:
        raise ValueError("Reference file is missing the model-to-parts map.")
    if not isinstance(part_models, dict) or not part_models:
        raise ValueError("Reference file is missing the part-to-model map.")
    if not isinstance(rules, dict) or not rules.get("modern_prefixes") or not rules.get("year_codes"):
        raise ValueError("Reference file is missing the serial decoding rules.")
    return data


def decode_serial(serial, reference):
    """Return a documented model inference, or None for unsupported formats.

    The checker deliberately prefers 'not decoded' over guessing so older or
    undocumented serial formats do not create false model-error flags.
    """
    if not reference:
        return None
    s = _compact_serial(serial)
    rules = reference.get("serial_rules") or {}
    years = rules.get("year_codes") or {}
    if len(s) < 6:
        return None

    modern_prefixes = rules.get("modern_prefixes") or {}
    prefix_rule = modern_prefixes.get(s[:2])
    if prefix_rule:
        cfg = rules.get("modern") or {}
        try:
            size = s[int(cfg.get("size_start", 2)):int(cfg.get("size_end", 4))]
            year_char = s[int(cfg.get("year_index", 5))]
            marker = s[int(cfg.get("designation_index", 4))]
            variant = s[int(cfg.get("variant_index", 9))]
        except (IndexError, TypeError, ValueError):
            return None
        if not size.isdigit() or len(size) != 2:
            return None
        model = f"{prefix_rule.get('model_prefix', '')}{size}"
        kind = prefix_rule.get("kind")
        designations = []
        if kind == "ap9" and marker == cfg.get("ap9_02_marker", "G"):
            designations.append("02")
        if kind == "ap9" and variant == cfg.get("variant_marker", "V"):
            designations.append("V")
        elif variant in set(cfg.get("mexico_markers") or ["M", "A"]):
            designations.append("M")
        for designation in designations:
            model += f"-{designation}"
        return {
            "model": model,
            "model_core": canonical_model(model),
            "year_code": year_char,
            "year": years.get(year_char),
            "family": prefix_rule.get("model_prefix"),
            "confidence": "documented",
        }

    ap7 = rules.get("ap7") or {}
    try:
        u_start = int(ap7.get("u_signature_start", 2))
        u_end = int(ap7.get("u_signature_end", 5))
        u_signature = s[u_start:u_end]
    except (TypeError, ValueError):
        u_signature = ""
    if u_signature in set(ap7.get("u_signatures") or []):
        size = s[:2]
        if not size.isdigit():
            return None
        try:
            year_char = s[int(ap7.get("u_year_index", 5))]
        except (IndexError, TypeError, ValueError):
            return None
        year = years.get(year_char)
        model = f"AP7-U{size}"
        if year is not None and year >= int(ap7.get("dash02_start_year", 2021)):
            model += "-02"
        return {
            "model": model,
            "model_core": canonical_model(model),
            "year_code": year_char,
            "year": year,
            "family": "AP7-U",
            "confidence": "documented",
        }

    for signature, model_base in (ap7.get("b_signatures") or {}).items():
        if s.startswith(signature):
            try:
                year_char = s[int(ap7.get("b_year_index", 4))]
            except (IndexError, TypeError, ValueError):
                return None
            year = years.get(year_char)
            model = model_base
            if year is not None and year >= int(ap7.get("dash02_start_year", 2021)):
                model += "-02"
            return {
                "model": model,
                "model_core": canonical_model(model),
                "year_code": year_char,
                "year": year,
                "family": "AP7-B",
                "confidence": "documented",
            }
    return None


def parts_model_key(model, reference):
    """Resolve a full operational model to its parts-reference hardware row."""
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


def check_model(serial, recorded_model, reference):
    observed = canonical_model(recorded_model)
    decoded = decode_serial(serial, reference)
    result = {
        "serial": _text(serial),
        "recorded_model": _text(recorded_model),
        "recorded_core": observed,
        "decoded": decoded,
        "status": "not_decoded",
        "message": "Serial format is not covered by the installed decoding reference.",
    }
    if not observed:
        result.update(status="missing_model", message="No recorded model number was supplied.")
        return result
    if decoded:
        if observed == decoded["model_core"]:
            result.update(status="ok", message="Recorded model agrees with the serial number.")
        else:
            result.update(status="mismatch", message=f"Serial decodes to {decoded['model']}, not {observed}.")
        return result
    ref_key = parts_model_key(observed, reference)
    if ref_key:
        result.update(status="known_model", message="Model is in the Promethean model reference, but this serial format cannot be decoded confidently.")
    else:
        result.update(status="unknown_model", message="Recorded model is not recognized in the Promethean model reference.")
    return result


def check_part(part, serial, recorded_model, reference):
    p = canonical_part(part)
    decoded = decode_serial(serial, reference) if serial else None
    compatibility_model = decoded["model"] if decoded else canonical_model(recorded_model)
    model_key = parts_model_key(compatibility_model, reference) if compatibility_model else None
    part_models = (reference or {}).get("part_models") or {}
    known_on = part_models.get(p)
    result = {
        "part": _text(part),
        "part_core": p,
        "serial": _text(serial),
        "recorded_model": _text(recorded_model),
        "decoded": decoded,
        "compatibility_model": compatibility_model,
        "parts_model": model_key,
        "known_on": known_on or [],
        "status": "unknown_part",
        "message": "Part number is not present in the installed Promethean parts reference.",
    }
    if not p:
        result.update(status="missing_part", message="No part number was supplied.")
        return result
    if not known_on:
        return result
    if not model_key:
        result.update(status="known_part", message="Part number is recognized, but a compatible panel model could not be determined.")
        return result
    if model_key in known_on:
        result.update(status="ok", message=f"Part is listed for {model_key}.")
    else:
        result.update(status="incompatible", message=f"Part is not listed for {model_key}.")
    return result


def _model_audit_rows(db):
    return db.execute(
        """
        SELECT e.id, e.event_at, e.event_type, e.model_number, e.source_type,
               e.source_row_key, a.serial_number, c.mso_number,
               b.original_filename
        FROM events e
        LEFT JOIN assets a ON a.id=e.asset_id
        LEFT JOIN cases c ON c.id=e.case_id
        JOIN import_batches b ON b.id=e.batch_id
        WHERE a.serial_number IS NOT NULL
          AND e.model_number IS NOT NULL AND TRIM(e.model_number) != ''
          AND (
            (e.source_type='receiving' AND e.event_type='received') OR
            (e.source_type='shipping' AND e.event_type='shipped') OR
            (e.source_type='inventory' AND e.event_type='inventory_observed') OR
            (e.source_type='repair' AND e.event_type IN ('repair','repair_pending_parts','repair_scrapped'))
          )
        ORDER BY e.id
        """
    ).fetchall()


def _fedex_part_rows(db):
    return db.execute(
        """
        SELECT e.id, e.event_at, e.details_json, e.source_row_key,
               a.serial_number, c.mso_number, b.original_filename
        FROM events e
        LEFT JOIN assets a ON a.id=e.asset_id
        LEFT JOIN cases c ON c.id=e.case_id
        JOIN import_batches b ON b.id=e.batch_id
        WHERE e.source_type='fedex'
          AND e.id IN (
            SELECT MIN(e2.id) FROM events e2
            WHERE e2.source_type='fedex'
            GROUP BY e2.batch_id, e2.source_row_key
          )
        ORDER BY e.id
        """
    ).fetchall()


def _db_signature(db, reference_path):
    event_stats = db.execute("SELECT COUNT(*) AS c, COALESCE(MAX(id),0) AS m FROM events").fetchone()
    batch_max = db.execute("SELECT COALESCE(MAX(id),0) FROM import_batches").fetchone()[0]
    try:
        ref_mtime = reference_path.stat().st_mtime_ns
    except OSError:
        ref_mtime = 0
    return (event_stats["c"], event_stats["m"], batch_max, ref_mtime)


def run_audit(db, reference, reference_path):
    signature = _db_signature(db, reference_path)
    with _AUDIT_LOCK:
        cached = _AUDIT_CACHE.get(signature)
        if cached is not None:
            return cached

    model_issues = []
    model_checked = 0
    undecodable = 0
    for row in _model_audit_rows(db):
        result = check_model(row["serial_number"], row["model_number"], reference)
        if result["decoded"]:
            model_checked += 1
        else:
            undecodable += 1
        if result["status"] in {"mismatch", "unknown_model"}:
            item = dict(row)
            item.update(result)
            item["issue_type"] = "Model mismatch" if result["status"] == "mismatch" else "Unknown model"
            model_issues.append(item)

    part_issues = []
    part_checked = 0
    for row in _fedex_part_rows(db):
        try:
            details = json.loads(row["details_json"] or "{}")
        except Exception:
            details = {}
        serial = row["serial_number"] or details.get("used_serial_number") or ""
        seen = set()
        for field, label in (("reported_product_code", "Reported part"), ("used_product_code", "Used part")):
            raw_part = details.get(field)
            part = canonical_part(raw_part)
            if not part or part in seen:
                continue
            seen.add(part)
            part_checked += 1
            result = check_part(raw_part, serial, "", reference)
            if result["status"] in {"unknown_part", "incompatible"}:
                item = dict(row)
                item.pop("details_json", None)
                item.update(result)
                item["part_field"] = label
                item["issue_type"] = "Unknown part" if result["status"] == "unknown_part" else "Part/model mismatch"
                part_issues.append(item)

    audit = {
        "model_checked": model_checked,
        "model_issues": model_issues,
        "model_issue_count": len(model_issues),
        "model_issue_serials": len({i.get("serial_number") or i.get("serial") for i in model_issues}),
        "undecodable_records": undecodable,
        "part_checked": part_checked,
        "part_issues": part_issues,
        "part_issue_count": len(part_issues),
        "part_issue_msos": len({i.get("mso_number") for i in part_issues if i.get("mso_number")}),
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
        "model": check_model(serial, model, reference) if model else None,
        "part": check_part(part, serial, model, reference) if part else None,
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


def register_quality_checker(app, db_connect, clean, data_dir):
    reference_path = _reference_path(data_dir)

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
                raw = uploaded.read()
                data = validate_reference(json.loads(raw.decode("utf-8")))
                Path(data_dir).mkdir(parents=True, exist_ok=True)
                tmp = reference_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
                os.replace(tmp, reference_path)
                with _AUDIT_LOCK:
                    _AUDIT_CACHE.clear()
                flash("Promethean model/parts reference installed on persistent storage.", "success")
            except Exception as exc:
                flash(f"Reference file could not be installed: {exc}", "error")
            return redirect("quality")

        reference = load_reference(data_dir)
        manual = _manual_check(request.args, reference) if reference else None
        audit = None
        if reference and request.args.get("audit") == "1":
            db = db_connect()
            audit = run_audit(db, reference, reference_path)
            db.close()
        return render_template(
            "quality.html",
            reference=_reference_summary(reference),
            reference_path=str(reference_path),
            manual=manual,
            audit=audit,
        )

    @app.route("/quality/export.csv")
    def quality_export():
        reference = load_reference(data_dir)
        if not reference:
            flash("Install the Promethean reference data before running an audit.", "error")
            return redirect("quality")
        db = db_connect()
        audit = run_audit(db, reference, reference_path)
        db.close()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["Issue Type", "Source", "Source File", "Source Row", "Serial Number", "MSO", "Recorded Model / Part", "Expected / Compatible Model", "Detail"])
        for item in audit["model_issues"]:
            expected = (item.get("decoded") or {}).get("model") or ""
            writer.writerow([
                item.get("issue_type"), item.get("source_type"), item.get("original_filename"), item.get("source_row_key"),
                item.get("serial_number"), item.get("mso_number"), item.get("recorded_model"), expected, item.get("message"),
            ])
        for item in audit["part_issues"]:
            writer.writerow([
                item.get("issue_type"), "fedex", item.get("original_filename"), item.get("source_row_key"),
                item.get("serial_number") or item.get("serial"), item.get("mso_number"), item.get("part"),
                item.get("parts_model") or item.get("compatibility_model") or "", item.get("message"),
            ])
        return Response(
            out.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="Promethean_Data_Quality_Issues.csv"'},
        )
