import csv
import io
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Response, render_template, request


PRESET_LABELS = {
    "today": "Today",
    "yesterday": "Yesterday",
    "last7": "Last 7 Days",
    "last30": "Last 30 Days",
    "custom": "Custom Range",
}


def _today():
    tz_name = os.environ.get("INVENTORY_TIMEZONE", "America/New_York")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("America/New_York")
    return datetime.now(tz).date()


def _parse_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def resolve_range(args):
    preset = (args.get("preset") or "last7").strip().lower()
    if preset not in PRESET_LABELS:
        preset = "last7"

    today = _today()
    if preset == "today":
        start = end = today
    elif preset == "yesterday":
        start = end = today - timedelta(days=1)
    elif preset == "last30":
        start, end = today - timedelta(days=29), today
    elif preset == "custom":
        start = _parse_date(args.get("start"))
        end = _parse_date(args.get("end"))
        if not start or not end:
            start, end = today - timedelta(days=6), today
        if start > end:
            start, end = end, start
    else:
        start, end = today - timedelta(days=6), today

    return {
        "preset": preset,
        "preset_label": PRESET_LABELS[preset],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "start_display": start.strftime("%b %d, %Y"),
        "end_display": end.strftime("%b %d, %Y"),
    }


def _shipping_rows(db, start, end, model=None):
    where = [
        "e.source_type = 'shipping'",
        "e.event_type = 'shipped'",
        "e.event_at IS NOT NULL",
        "DATE(e.event_at) BETWEEN ? AND ?",
    ]
    params = [start, end]
    if model:
        where.append("UPPER(TRIM(COALESCE(e.model_number, ''))) = UPPER(TRIM(?))")
        params.append(model)

    rows = db.execute(
        f"""
        SELECT e.event_at, e.model_number, e.details_json,
               a.serial_number, c.mso_number, c.case_number,
               b.original_filename
        FROM events e
        LEFT JOIN assets a ON a.id = e.asset_id
        LEFT JOIN cases c ON c.id = e.case_id
        JOIN import_batches b ON b.id = e.batch_id
        WHERE {' AND '.join(where)}
        ORDER BY e.event_at DESC, e.id DESC
        """,
        params,
    ).fetchall()

    import json
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["details"] = json.loads(item.pop("details_json") or "{}")
        except Exception:
            item["details"] = {}
        result.append(item)
    return result


def _models(db):
    rows = db.execute(
        """
        SELECT DISTINCT TRIM(model_number) AS model
        FROM events
        WHERE source_type='shipping' AND event_type='shipped'
          AND model_number IS NOT NULL AND TRIM(model_number) != ''
        ORDER BY UPPER(TRIM(model_number))
        """
    ).fetchall()
    return [r["model"] for r in rows]


def _summary(rows):
    return {
        "shipments": len(rows),
        "models": len({(r.get("model_number") or "").strip().upper() for r in rows if r.get("model_number")}),
        "serials": len({(r.get("serial_number") or "").strip().upper() for r in rows if r.get("serial_number")}),
        "msos": len({(r.get("mso_number") or "").strip().upper() for r in rows if r.get("mso_number")}),
    }


def _csv_response(rows, filename):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Shipped Date", "Model", "Serial Number", "MSO", "Case Number", "Tracking Number", "Sales Order Number", "Pickup Date", "Source File"])
    for r in rows:
        d = r.get("details") or {}
        writer.writerow([
            r.get("event_at") or "",
            r.get("model_number") or "",
            r.get("serial_number") or "",
            r.get("mso_number") or "",
            r.get("case_number") or "",
            d.get("tracking_number") or "",
            d.get("sales_order_number") or "",
            d.get("pickup_date") or "",
            r.get("original_filename") or "",
        ])
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def register_shipping_history(app, db_connect, clean):
    @app.route("/shipping/model")
    def shipping_model_history():
        date_range = resolve_range(request.args)
        model = clean(request.args.get("model"))
        db = db_connect()
        models = _models(db)
        rows = _shipping_rows(db, date_range["start"], date_range["end"], model=model) if model else []
        db.close()
        return render_template(
            "shipping_history.html",
            report_kind="model",
            model=model,
            models=models,
            rows=rows,
            summary=_summary(rows),
            date_range=date_range,
            preset_labels=PRESET_LABELS,
        )

    @app.route("/shipping/all")
    def shipping_all_history():
        date_range = resolve_range(request.args)
        db = db_connect()
        rows = _shipping_rows(db, date_range["start"], date_range["end"])
        db.close()
        return render_template(
            "shipping_history.html",
            report_kind="all",
            model="",
            models=[],
            rows=rows,
            summary=_summary(rows),
            date_range=date_range,
            preset_labels=PRESET_LABELS,
        )

    @app.route("/shipping/model/export.csv")
    def shipping_model_export():
        date_range = resolve_range(request.args)
        model = clean(request.args.get("model"))
        db = db_connect()
        rows = _shipping_rows(db, date_range["start"], date_range["end"], model=model) if model else []
        db.close()
        safe_model = "".join(c if c.isalnum() or c in "-_" else "_" for c in model) or "Model"
        return _csv_response(rows, f"Shipping_History_{safe_model}_{date_range['start']}_to_{date_range['end']}.csv")

    @app.route("/shipping/all/export.csv")
    def shipping_all_export():
        date_range = resolve_range(request.args)
        db = db_connect()
        rows = _shipping_rows(db, date_range["start"], date_range["end"])
        db.close()
        return _csv_response(rows, f"Shipping_History_All_Models_{date_range['start']}_to_{date_range['end']}.csv")
