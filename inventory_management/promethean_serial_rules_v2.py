import re

from inventory_management.promethean_quality import decode_serial as _base_decode_serial


def _compact_serial(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _insert_02(model):
    if not model or "-02" in model:
        return model
    if model.endswith("-M"):
        return model[:-2] + "-02-M"
    if model.endswith("-V"):
        return model[:-2] + "-02-V"
    return model + "-02"


def decode_serial(serial, reference):
    """Decode using the documented reference plus the AP9-B >=G -02 rule.

    For AP9-B serials, the fifth serial character (index 4) indicates the
    -02 hardware designation when it is G or any later alphabetic character.
    Example: 9B75GP521TB82A0005 -> AP9-B75-02 before stock-grade suffixes.
    """
    result = _base_decode_serial(serial, reference)
    s = _compact_serial(serial)
    if result and len(s) > 4 and s.startswith("9B"):
        marker = s[4]
        if marker.isalpha() and marker >= "G":
            result = dict(result)
            result["model"] = _insert_02(result.get("model"))
            # Keep model_core consistent with the base decoder's convention:
            # it is the decoded product model before -NA/-NA-R stock suffixes.
            result["model_core"] = result["model"]
    return result
