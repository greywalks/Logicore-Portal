import pandas as pd

from philips_builder import analyze_philips


def _write_month_end_report(path, inventory):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        inventory.to_excel(writer, sheet_name="Inventory", index=False)
        pd.DataFrame(
            [{"Stock Level (Primary)": "Demo", "Model": "50KNOWN", "Serial": "SHIP-1"}]
        ).to_excel(writer, sheet_name="Shipping", index=False)
        pd.DataFrame([{"Model": "50KNOWN", "Serial": "RECV-1"}]).to_excel(
            writer, sheet_name="Recieved", index=False
        )
        pd.DataFrame(
            [{"Model": "50KNOWN", "Serial": "REP-1", "Status": "Repaired"}]
        ).to_excel(writer, sheet_name="Repairs", index=False)


def test_inventory_size_is_derived_when_month_end_report_has_no_size_column(tmp_path):
    report = tmp_path / "month_end_without_size.xlsx"
    _write_month_end_report(
        report,
        pd.DataFrame(
            [
                {"Type": "Demo", "Model": "50KNOWN", "Serial": "INV-1"},
                {"Type": "Service", "Model": "55UNKNOWN", "Serial": "INV-2"},
            ]
        ),
    )
    logs = []

    analysis = analyze_philips(
        report,
        dims={"50KNOWN": 2.5},
        tiers=[(50, 50, 100, 50, None)],
        log=logs.append,
    )

    assert analysis["demo_total_sqft"] == 5.0
    assert analysis["service_total_sqft"] == 0
    assert analysis["missing_dimension_models"] == ["55UNKNOWN"]
    assert any("no Size column" in message for message in logs)


def test_blank_or_non_numeric_inventory_size_falls_back_to_model_reference(tmp_path):
    report = tmp_path / "month_end_with_blank_size.xlsx"
    _write_month_end_report(
        report,
        pd.DataFrame(
            [
                {"Type": "Demo", "Model": "50KNOWN", "Size": None, "Serial": "INV-1"},
                {"Type": "Service", "Model": "50KNOWN", "Size": "not a number", "Serial": "INV-2"},
            ]
        ),
    )

    analysis = analyze_philips(
        report,
        dims={"50KNOWN": 2.5},
        tiers=[(50, 50, 100, 50, None)],
        log=lambda _message: None,
    )

    assert analysis["demo_total_sqft"] == 5.0
    assert analysis["service_total_sqft"] == 2.5
    assert analysis["missing_dimension_models"] == []
