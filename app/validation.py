"""Input validation for the BTMS prediction service."""
from __future__ import annotations

import math

# Accepted ranges = ranges covered by the synthetic training data.
NUMERIC_FIELDS = {
    "c_rate":       (0.5, 3.0,   "C"),
    "soc_start":    (0.05, 0.98, "fraction"),
    "soh":          (0.70, 1.00, "fraction"),
    "ambient_temp": (15.0, 45.0, "°C"),
    "inlet_temp":   (18.0, 35.0, "°C"),
    "start_temp":   (15.0, 48.0, "°C"),
    "reynolds":     (400.0, 700.0, "-"),
    "phi_pct":      (0.0, 2.0,   "vol %"),
    "duration_min": (5.0, 30.0,  "min"),
}
MODES = ("charge", "discharge")
MAX_CHARGE_C = 2.0
MAX_BATCH = 500


class ValidationError(Exception):
    def __init__(self, errors: list[dict]):
        super().__init__("invalid input")
        self.errors = errors


def validate_record(rec, required=None) -> dict:
    """Return a clean dict or raise ValidationError listing every problem found."""
    if not isinstance(rec, dict):
        raise ValidationError([{"field": None, "error": "request body must be a JSON object"}])
    required = list(NUMERIC_FIELDS) if required is None else required
    errors, clean = [], {}

    mode = rec.get("mode")
    if mode not in MODES:
        errors.append({"field": "mode", "error": f"must be one of {list(MODES)}"})
    else:
        clean["mode"] = mode

    for f in required:
        lo, hi, unit = NUMERIC_FIELDS[f]
        v = rec.get(f)
        if v is None:
            errors.append({"field": f, "error": "missing"})
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errors.append({"field": f, "error": f"must be a number, got {type(v).__name__}"})
            continue
        if not math.isfinite(v):
            errors.append({"field": f, "error": "must be a finite number"})
            continue
        if not (lo <= v <= hi):
            errors.append({"field": f, "error": f"out of supported range [{lo}, {hi}] {unit}",
                           "value": v})
            continue
        clean[f] = float(v)

    if clean.get("mode") == "charge" and clean.get("c_rate", 0) > MAX_CHARGE_C:
        errors.append({"field": "c_rate", "error": f"charging above {MAX_CHARGE_C}C is outside the supported envelope"})

    unknown = sorted(set(rec) - set(NUMERIC_FIELDS) - {"mode", "optimise_phi", "target_t_max", "max_delta_t"})
    if unknown:
        errors.append({"field": ",".join(unknown), "error": "unknown field(s)"})

    if errors:
        raise ValidationError(errors)
    return clean
