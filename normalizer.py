"""
normalizer.py — Result text normalization for the Depot Repair detail sheet.
Maps all known variants of harvest/scrap descriptions to canonical strings.
"""

import re

# ── Canonical result strings ───────────────────────────────────────────────────
# All variants of physical-damage harvests → one standard label
_HARVEST_PHYSICAL   = "Harvested: Physical Damage"
_HARVEST_LCD        = "Harvested: LCD Damage"
_HARVEST_LINES      = "Harvested: Lines in Screen"
_HARVEST_PANEL      = "Harvested: Panel Damage"
_HARVEST_SHIPPING   = "Harvested: Destroyed in Shipping"
_SALVAGE_SCRAP      = "Salvage of Hardware and Scrap"

# Order matters — more specific patterns first
_RULES = [
    # Physical damage variants (most common)
    (re.compile(r'harvest.*physical|physical.*harvest|physical damage.*harvest', re.I), _HARVEST_PHYSICAL),
    (re.compile(r'^HARVESTED?:\s*Physical Damage', re.I), _HARVEST_PHYSICAL),
    (re.compile(r'panel damaged.*harvest|harvest.*panel damage', re.I), _HARVEST_PANEL),
    (re.compile(r'harvest.*lcd|lcd.*harvest|lcd damage.*harvest', re.I), _HARVEST_LCD),
    (re.compile(r'harvest.*lines|lines.*harvest|horizontal lines.*harvest|vertical lines.*harvest', re.I), _HARVEST_LINES),
    (re.compile(r'harvest.*shipping|shipping.*harvest|destroyed in shipping', re.I), _HARVEST_SHIPPING),
    (re.compile(r'harvest.*due to|harvest', re.I), _HARVEST_PHYSICAL),  # catch-all for any remaining harvest
    (re.compile(r'^scrap$|immediate scrap', re.I), _SALVAGE_SCRAP),
]


def normalize_result(result: str) -> str:
    """Normalize a Result field value to a canonical string."""
    if not isinstance(result, str):
        return result
    s = result.strip()
    for pattern, canonical in _RULES:
        if pattern.search(s):
            return canonical
    return s
