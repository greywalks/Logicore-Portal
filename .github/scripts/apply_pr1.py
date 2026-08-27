from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def replace_once(text, old, new, label):
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text, pattern, replacement, label):
    if isinstance(replacement, str) and replacement in text:
        return text
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    return updated


def update_app():
    path = ROOT / "app.py"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        'OUTPUT_DIR.mkdir(exist_ok=True)\n\nsys.path.insert(0, str(Path(__file__).parent))',
        '''OUTPUT_DIR.mkdir(exist_ok=True)

# Generated files are authorized by the invoice subsection that created them.
# This mirrors the application's existing process-local output/session model.
_OUTPUT_ACCESS = {}
_OUTPUT_ACCESS_LOCK = threading.Lock()


def _register_output(path_or_name, subsection):
    """Register one generated filename to the client subsection that owns it."""
    if not path_or_name or not subsection:
        return
    filename = Path(path_or_name).name
    with _OUTPUT_ACCESS_LOCK:
        _OUTPUT_ACCESS[filename] = subsection


def _registered_output_subsection(filename):
    with _OUTPUT_ACCESS_LOCK:
        return _OUTPUT_ACCESS.get(Path(filename).name)


sys.path.insert(0, str(Path(__file__).parent))''',
        "output registry",
    )

    route_block = '''ROUTE_SECTIONS = {
    # Promethean — Workshop Invoice
    "sanitize_route": ("invoice-generator", "promethean"),
    "generate": ("invoice-generator", "promethean"),
    "stream": ("invoice-generator", "promethean"),
    # Promethean — Storage Invoice
    "analyze_storage_route": ("invoice-generator", "promethean"),
    "confirm_storage": ("invoice-generator", "promethean"),
    "stream_storage": ("invoice-generator", "promethean"),
    # Promethean — FedEx Shipment Upload
    "analyze_fedex_shipment_route": ("invoice-generator", "promethean"),
    "build_fedex_shipment": ("invoice-generator", "promethean"),
    "stream_fedex_shipment": ("invoice-generator", "promethean"),
    # Philips
    "analyze_philips_route": ("invoice-generator", "philips"),
    "confirm_philips": ("invoice-generator", "philips"),
    "stream_philips": ("invoice-generator", "philips"),
    "generate_report_and_analyze": ("invoice-generator", "philips"),
    # TCL
    "analyze_tcl_route": ("invoice-generator", "tcl"),
    "confirm_tcl": ("invoice-generator", "tcl"),
    "stream_tcl": ("invoice-generator", "tcl"),
    # AMC
    "analyze_amc_route": ("invoice-generator", "amc"),
    "confirm_amc": ("invoice-generator", "amc"),
    "stream_amc": ("invoice-generator", "amc"),
    # Config — all pricing/reference administration is gated here.
    "get_serial_rules": ("invoice-generator", "config"),
    "save_serial_rules": ("invoice-generator", "config"),
    "get_storage_prices": ("invoice-generator", "config"),
    "set_storage_prices": ("invoice-generator", "config"),
    "get_fedex_shipment_defaults": ("invoice-generator", "config"),
    "set_fedex_shipment_defaults": ("invoice-generator", "config"),
    "get_philips_dimensions": ("invoice-generator", "config"),
    "upload_philips_dimensions": ("invoice-generator", "config"),
    "download_philips_dimensions": ("invoice-generator", "config"),
    "get_philips_repair_cost": ("invoice-generator", "config"),
    "set_philips_repair_cost": ("invoice-generator", "config"),
    "get_amc_dimensions": ("invoice-generator", "config"),
    "upload_amc_dimensions": ("invoice-generator", "config"),
    "download_amc_dimensions": ("invoice-generator", "config"),
    "get_amc_prices": ("invoice-generator", "config"),
    "set_amc_prices": ("invoice-generator", "config"),
    # SMS NonConforming
    "nc_list_items": ("sms-nonconforming", None),
    "nc_create_item": ("sms-nonconforming", None),
    "nc_get_item": ("sms-nonconforming", None),
    "nc_update_item": ("sms-nonconforming", None),
    "nc_delete_item": ("sms-nonconforming", None),
    "nc_export": ("sms-nonconforming", None),
    "nc_label": ("sms-nonconforming", None),
    "nc_next_number": ("sms-nonconforming", None),
}'''
    text = regex_once(
        text,
        r'ROUTE_SECTIONS = \{\n.*?\n\}\n\n\n(?=@app\.before_request)',
        route_block + "\n\n\n",
        "route permission map",
    )

    session_store = '''class SessionStore:
    """Per-module, per-browser-session analysis state and SSE queue."""

    def __init__(self, output_subsection=None):
        self._data: dict[str, dict] = {}
        self._queues: dict[str, "queue.Queue"] = {}
        self.output_subsection = output_subsection

    def _make_queue(self):
        q = queue.Queue()
        q.output_subsection = self.output_subsection
        return q

    def get(self, sid: str) -> dict:
        """The session's analysis dict — created empty on first access."""
        return self._data.setdefault(sid, {})

    def replace(self, sid: str, data: dict) -> dict:
        """Overwrite the session's analysis dict wholesale."""
        self._data[sid] = data
        return data

    def new_queue(self, sid: str) -> queue.Queue:
        """Start a fresh SSE queue for this session."""
        q = self._make_queue()
        self._queues[sid] = q
        return q

    def queue(self, sid: str) -> queue.Queue:
        """Fetch or create this session's current SSE queue."""
        if sid not in self._queues:
            self._queues[sid] = self._make_queue()
        return self._queues[sid]

    @staticmethod
    def make_logger(q: "queue.Queue"):
        """Bind log/done callbacks to one browser session's queue."""
        def log(msg: str):
            q.put({"type": "log", "msg": msg})

        def done(success: bool, payload: dict):
            subsection = getattr(q, "output_subsection", None)
            if success and subsection:
                for key, value in payload.items():
                    if value and (key == "filename" or key.endswith("_filename")):
                        _register_output(value, subsection)
            q.put({"type": "done", "success": success, **payload})

        return log, done'''
    text = regex_once(
        text,
        r'class SessionStore:\n.*?        return log, done',
        session_store,
        "SessionStore output registration",
    )

    for old, new, label in (
        ('_workshop = SessionStore()', '_workshop = SessionStore("promethean")', "workshop store"),
        ('_storage = SessionStore()', '_storage = SessionStore("promethean")', "storage store"),
        ('_philips = SessionStore()', '_philips = SessionStore("philips")', "philips store"),
        ('_tcl = SessionStore()', '_tcl = SessionStore("tcl")', "tcl store"),
        ('_amc = SessionStore()', '_amc = SessionStore("amc")', "amc store"),
        ('_fedex_shipment = SessionStore()', '_fedex_shipment = SessionStore("promethean")', "fedex store"),
    ):
        text = replace_once(text, old, new, label)

    cnt_anchor = '''def _cnt(df, type2, size, prev_triaged=None):
    m = (df["Type2"] == type2) & (df["Size"] == size)
    if prev_triaged is not None and "was_prev_triaged" in df.columns:
        m &= (df["was_prev_triaged"] == prev_triaged)
    return int(m.sum())
'''
    rebuild_helper = cnt_anchor + '''

def _rebuild_workshop_clean_df(raw_df, corrections=None, issue_indices=None):
    """Apply review decisions to complete source rows, then rebuild billing rows."""
    from sanitizer import apply_corrections, BILLABLE_SIZES

    corrections = corrections or {}
    issue_indices = {int(i) for i in (issue_indices or [])}
    working_df = apply_corrections(raw_df.copy(), corrections)

    resolved_indices = set()
    for idx, correction in corrections.items():
        if correction == "EXCLUDE":
            resolved_indices.add(int(idx))
        elif isinstance(correction, dict):
            if correction.get("value") not in (None, ""):
                resolved_indices.add(int(idx))
        elif correction not in (None, ""):
            resolved_indices.add(int(idx))

    unresolved_indices = issue_indices - resolved_indices
    if unresolved_indices:
        working_df = working_df[~working_df.index.isin(unresolved_indices)].copy()

    clean_df = working_df.copy()
    if "_exclude" in clean_df.columns:
        clean_df = clean_df[~clean_df["_exclude"].fillna(False)].copy()

    clean_df = clean_df[clean_df["_size"].isin(BILLABLE_SIZES)].copy()
    clean_df["Type"] = clean_df["_Type"]
    clean_df["Type2"] = clean_df["_Type2"]
    clean_df["Actual Model"] = clean_df["_clean_model"]
    clean_df = clean_df[clean_df["Type"].isin(("Depot Repair Tab", "Triage Tab"))].copy()
    return clean_df, working_df
'''
    text = replace_once(text, cnt_anchor, rebuild_helper, "workshop clean rebuild helper")

    text = replace_once(
        text,
        'def apply_dedup(repair_df, prev_path, shipping_path, log=print, billing_start=None):\n    from sanitizer import BILLABLE_SIZES\n\n',
        'def apply_dedup(repair_df, prev_path, shipping_path, log=print, billing_start=None):\n',
        "remove unused dedup import",
    )

    dedup_anchor = '''    depot  = df[df["Type"] == "Depot Repair Tab"].copy()
    triage = df[df["Type"] == "Triage Tab"].copy()

    log(f"Excluded as duplicates — Depot: {int(depot['is_duplicate'].sum())}, "
'''
    dedup_audit = '''    depot  = df[df["Type"] == "Depot Repair Tab"].copy()
    triage = df[df["Type"] == "Triage Tab"].copy()

    excluded_rows = []
    for _, row in df[df["is_duplicate"]].iterrows():
        serial = row.get("Actual Serial", "")
        prev_date = row.get("prev_invoice_date")
        last_ship = ship_lookup.get(serial, pd.NaT)
        prev_label = (pd.Timestamp(prev_date).strftime("%Y-%m-%d")
                      if pd.notna(prev_date) else "an unknown date")
        if pd.isna(last_ship):
            reason = (f"Previously invoiced on {prev_label}; no later shipment "
                      "was found, so the serial was not billed again.")
        else:
            ship_label = pd.Timestamp(last_ship).strftime("%Y-%m-%d")
            reason = (f"Previously invoiced on {prev_label}; last shipped on "
                      f"{ship_label}, which was not after the prior invoice.")
        excluded_rows.append({
            "Model": row.get("Actual Model", ""),
            "Serial": serial,
            "Source Tab": ("Depot Repair" if row.get("Type") == "Depot Repair Tab"
                           else "Triage Units"),
            "Category": row.get("Category", ""),
            "Result": row.get("Result", ""),
            "Original Date": row.get("Date Integer", ""),
            "Reason": reason,
        })
    excluded_df = pd.DataFrame(excluded_rows, columns=[
        "Model", "Serial", "Source Tab", "Category", "Result", "Original Date", "Reason"
    ])

    log(f"Excluded as duplicates — Depot: {int(depot['is_duplicate'].sum())}, "
'''
    text = replace_once(text, dedup_anchor, dedup_audit, "dedup audit rows")
    text = replace_once(
        text,
        '    return depot_clean, triage_clean\n\n\n# ══════════════════════════════════════════════════════════════════════════════\n# Legacy process()',
        '    return depot_clean, triage_clean, excluded_df\n\n\n# ══════════════════════════════════════════════════════════════════════════════\n# Legacy process()',
        "dedup return audit dataframe",
    )

    text = replace_once(
        text,
        '''        depot_clean, triage_clean = apply_dedup(repair, prev_path, shipping_path, log, billing_start=date_from)
        _finish(depot_clean, triage_clean, output_path,
                invoice_date, completed_date, call_id, customer,
                log=log, done=done)''',
        '''        depot_clean, triage_clean, excluded_df = apply_dedup(
            repair, prev_path, shipping_path, log, billing_start=date_from)
        _finish(depot_clean, triage_clean, output_path,
                invoice_date, completed_date, call_id, customer,
                excluded_df=excluded_df, log=log, done=done)''',
        "legacy excluded flow",
    )

    text = replace_once(
        text,
        '''            programming_df=None, part_prices=None, part_type_totals=None,
            log=print, done=None):''',
        '''            programming_df=None, part_prices=None, part_type_totals=None,
            excluded_df=None, log=print, done=None):''',
        "finish signature",
    )
    text = replace_once(
        text,
        '''        call_id=call_id, customer=customer,
        programming_df=programming_df, part_prices=part_prices,
    )''',
        '''        call_id=call_id, customer=customer,
        programming_df=programming_df, part_prices=part_prices,
        excluded_df=excluded_df,
    )''',
        "finish passes excluded dataframe",
    )

    text = replace_once(
        text,
        '''            "clean_df":     clean_df,
            "raw_df":       raw_df,
            "date_from":    date_from,''',
        '''            "clean_df":     clean_df,
            "raw_df":       raw_df,
            "issue_indices": [int(iss["row_index"]) for iss in issues],
            "date_from":    date_from,''',
        "store workshop issue indices",
    )

    old_generate = '''        from sanitizer import apply_corrections, export_corrected_workbook, BILLABLE_SIZES

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

        log(f"Records after corrections: {len(clean_df)}")'''
    new_generate = '''        from sanitizer import export_corrected_workbook

        if corrections:
            log(f"Applying {len(corrections)} correction(s)…")
        clean_df, raw_df = _rebuild_workshop_clean_df(
            sess["raw_df"], corrections, sess.get("issue_indices", []))

        log(f"Records after corrections: {len(clean_df)}")'''
    text = replace_once(text, old_generate, new_generate, "raw workshop reconstruction")
    text = replace_once(
        text,
        '''        depot_clean, triage_clean = apply_dedup(
            clean_df, sess["prev_path"], sess["ship_path"], log,
            billing_start=sess["date_from"])''',
        '''        depot_clean, triage_clean, excluded_df = apply_dedup(
            clean_df, sess["prev_path"], sess["ship_path"], log,
            billing_start=sess["date_from"])''',
        "raw dedup audit return",
    )
    text = replace_once(
        text,
        '''                part_prices=part_prices,
                part_type_totals=part_type_totals,
                log=log, done=done)''',
        '''                part_prices=part_prices,
                part_type_totals=part_type_totals,
                excluded_df=excluded_df,
                log=log, done=done)''',
        "raw finish audit argument",
    )

    download_replacement = '''@app.route("/download/<path:filename>")
def download(filename):
    subsection = _registered_output_subsection(filename)
    if subsection is None:
        return "File not found", 404

    user = portal_auth.get_current_user()
    if not portal_auth.has_access(user, "invoice-generator", subsection):
        flash("You don't have permission to download that output.", "error")
        return redirect(url_for("index"))

    path = (OUTPUT_DIR / filename).resolve()
    if path.parent != OUTPUT_DIR.resolve():
        return "File not found", 404
    if not path.exists():
        return "File not found", 404
    return send_file(path, as_attachment=True, download_name=path.name,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")'''
    text = regex_once(
        text,
        r'@app\.route\("/download/<path:filename>"\)\ndef download\(filename\):\n.*?mimetype="application/vnd\.openxmlformats-officedocument\.spreadsheetml\.sheet"\)',
        download_replacement,
        "module-authorized download route",
    )

    text = replace_once(
        text,
        '''        build_month_end_report(report_analysis, report_path, log=logs.append)

        invoice_analysis = analyze_philips''',
        '''        build_month_end_report(report_analysis, report_path, log=logs.append)
        _register_output(report_path, "philips")

        invoice_analysis = analyze_philips''',
        "register immediate Philips report",
    )

    path.write_text(text, encoding="utf-8")


def update_storage_builder():
    path = ROOT / "storage_builder.py"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '      - small_part_picks: unique MSO count in the period',
        '      - small_part_picks: sum of normalized Quantity values in the period',
        "FedEx result documentation",
    )
    text = replace_once(
        text,
        '''    fedex_period["Quantity"] = fedex_period["Quantity"].fillna(1)
    fedex_period.loc[fedex_period["Quantity"] == 0, "Quantity"] = 1

    small_part_picks = fedex_period["MSO"].dropna().nunique()
    log(f"Small Part Picks: {small_part_picks} unique MSO orders")''',
        '''    quantity = pd.to_numeric(fedex_period["Quantity"], errors="coerce").fillna(1)
    quantity = quantity.mask(quantity <= 0, 1)
    fractional = quantity[quantity.mod(1) != 0]
    if len(fractional):
        source_rows = ", ".join(str(int(i) + 2) for i in fractional.index[:10])
        raise ValueError(
            "FedEx Quantity must contain whole numbers; fractional value(s) "
            f"found on source row(s): {source_rows}")
    fedex_period["Quantity"] = quantity.astype(int)

    small_part_picks = int(fedex_period["Quantity"].sum())
    unique_msos = fedex_period["MSO"].dropna().nunique()
    log(f"Small Part Picks: {small_part_picks} total part(s) across {unique_msos} MSO order(s)")''',
        "quantity-based small part picks",
    )

    text = replace_once(
        text,
        '    part_prices, line_prices = load_prices()\n\n    unit_storage_df',
        '    _, line_prices = load_prices()\n\n    unit_storage_df',
        "storage line prices only",
    )
    text = replace_once(
        text,
        '    part_type_totals = analysis["part_type_totals"]\n',
        '',
        "remove storage part totals variable",
    )
    text = replace_once(
        text,
        '''                     small_parts_df, unit_picks_count, small_part_picks,
                     part_type_totals, line_prices, part_prices)''',
        '''                     small_parts_df, unit_picks_count, small_part_picks,
                     line_prices)''',
        "storage breakdown call",
    )
    text = replace_once(
        text,
        '''        line_prices["unit_pick"]       * unit_picks_count +
        line_prices["small_part_pick"] * small_part_picks +
        sum(qty * part_prices.get(pt, 0) for pt, qty in part_type_totals.items())''',
        '''        line_prices["unit_pick"]       * unit_picks_count +
        line_prices["small_part_pick"] * small_part_picks''',
        "storage subtotal excludes testing",
    )
    text = replace_once(
        text,
        '''                     small_parts_df, unit_picks_count, small_part_picks,
                     part_type_totals, line_prices, part_prices):''',
        '''                     small_parts_df, unit_picks_count, small_part_picks,
                     line_prices):''',
        "storage breakdown signature",
    )

    testing_block = '''    _line(ws, 17, "Small Part Picks","Order", line_prices["small_part_pick"],
          small_part_picks)

    # ── Parts Testing & Configuration ─────────────────────────────────────────
    section_hdr(19, "Parts Testing & Configuration")
    testing_lines = [
        (20, "PSU - Testing",                               "Each", "PSU"),
        (21, "Mainboard Configure for Dispatch - Testing",  "Each", "Mainboard Configure for Dispatch"),
        (22, "Mainboard - Testing",                         "Each", "Mainboard"),
        (23, "AC-PCA - Testing",                            "Each", "AC-PCA"),
        (24, "Keypad - Testing",                            "Each", "Keypad"),
        (25, "Maintouch - Testing",                         "Each", "Maintouch"),
        (26, "Ext-Input - Testing",                         "Each", "EXT-INPUT"),
        (27, "OPS-PCA - Testing",                           "Each", "OPS-PCA"),
        (28, "USB - Testing",                               "Each", "USB"),
        (29, "Speaker Testing",                             "Each", "SPEAKER"),
    ]
    for row, label, uom, ptype in testing_lines:
        qty   = part_type_totals.get(ptype, 0)
        price = part_prices.get(ptype, 0)
        _line(ws, row, label, uom, price, qty)   # total = =D{row}*C{row}
'''
    picks_only = '''    # Small Part Picks bill by summed Quantity on the retained FedEx detail sheet.
    _line(ws, 17, "Small Part Picks", "Each", line_prices["small_part_pick"],
          small_part_picks,
          qty_formula="=SUM('Part Testing & Programming'!G:G)")
'''
    text = replace_once(text, testing_block, picks_only, "remove storage testing charges")

    path.write_text(text, encoding="utf-8")


def main():
    update_app()
    update_storage_builder()
    print("PR 1 source updates applied successfully.")


if __name__ == "__main__":
    main()
