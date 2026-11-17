"""Forecast GTA VI's launch-day Twitch viewers hour by hour (make.fun gta6-twitch-ccu-forecast-01).

The model: past big launches' ratio curves.

A launch day's shape is driven by the unlock rolling around the world at local
midnight: Europe and Oceania are already playing when the UTC day starts, the
US East Coast unlocks at 05:00Z and the West Coast at 08:00Z, and the audience
peaks in the US/European evening. That shape is stable across launches once
each launch is scaled by its own hour-1 print, so the forecast is:

    hour h viewers  =  hour-1 print  x  ratio_h

where ratio_h comes from the comparable launches in the case file, each
adjusted to this case before they are combined:

  * Selection: only launches whose hour 1 already carries a real audience
    (peak / hour-1 <= 15 and no single-hour jump above 3x). Launches that
    unlock all at once later in the day have a near-zero hour 1 and a wildly
    different shape; GTA VI unlocks at rolling local midnight, so they are
    not analogues. Early-access-contaminated, weak and target-history
    comparables are left out too.
  * Weighting: console-only launches weigh 1, launches with a PC version
    weigh 0.5 (a simultaneous PC unlock distorts the curve), times a
    Gaussian kernel on log hour-1 size, so launches of a similar scale
    matter most.
  * Level adjustment: bigger hour-1 prints mean flatter days (a huge first
    hour already holds most of the launch audience), so each comparable's
    log ratio is shifted by SLOPE * (log p1 - log h1_comp).
  * Front-loading adjustment: a launch whose hour 1 towers over the hour
    before it (the UK unlock step) tends to stay flatter afterwards, so the
    log ratios shift by GAMMA * (uk_step_case - uk_step_comp).
  * Centre and spread per hour: the weighted median of the adjusted log
    ratios is the median; the weighted standard deviation around it, with a
    floor, gives the five quantiles through the normal quantiles.

Everything is deterministic: no randomness, sorted iteration, fixed rounding.
Standard library only, no network. Runs in well under a second per case.
"""

import json
import math
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]

# Selection: a launch is an h1-informative analogue when its day peak is at
# most 15x its hour-1 print and no single hour jumps more than 3x.
MAX_PEAK_RATIO = 15.0
MAX_HOUR_JUMP = 3.0
# A launch with a PC version gets half weight (simultaneous PC unlocks bend
# the curve); console-only launches get full weight.
PC_WEIGHT = 0.5
# Gaussian kernel bandwidth on log hour-1 size: launches near the case's
# scale weigh most.
H1_KERNEL_BW = 1.0
# Bigger hour-1 prints -> flatter ratio curves (log-log slope).
LEVEL_SLOPE = -0.2
# A bigger UK unlock step (hour 1 vs the hour before) -> flatter day.
UK_STEP_GAMMA = -0.3
# The front-loading adjustment is capped, so an extreme case cannot be
# dragged far by one feature.
UK_STEP_CAP = 0.35
# Spread floor in log units: the forecast never claims more precision than
# the launches disagree by, and never less than this.
SD_FLOOR = 0.3
SD_WIDEN = 1.1
# Fallback spread (the house persistence baseline's), used only when no
# comparable survives selection.
FALLBACK_SIGMA = 0.15


def parse_ts(text):
    """Parse an ISO-8601 UTC timestamp like 2026-11-19T00:00:00Z."""
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def tier_of(comp):
    """The comparability tier recorded in the comparable's unlock note."""
    note = comp.get("unlock") or ""
    if "Tier: early-access-contaminated" in note:
        return "early-access"
    if "Tier: weak" in note:
        return "weak"
    if "Tier: target-history" in note:
        return "target-history"
    return "comparable"


def nearest_hour(points, target, window=3):
    """Viewers at the point nearest hour-offset `target`, within `window` hours.

    `points` maps hour offsets (ints, negative before day start) to viewers.
    Returns None when nothing is close enough.
    """
    best_off = None
    best_val = None
    for off in sorted(points):
        val = points[off]
        if not isinstance(val, (int, float)) or val <= 0:
            continue
        if abs(off - target) > window:
            continue
        if best_off is None or abs(off - target) < abs(best_off - target):
            best_off = off
            best_val = val
    return best_val


def extract_comparable(comp):
    """Turn one comparable into launch-day shape data, or None if unusable.

    Needs a complete 24-hour launch-day curve with a positive hour 1, and a
    point near the hour before day start for the UK-step feature.
    """
    if tier_of(comp) != "comparable":
        return None
    try:
        start = parse_ts(comp["day_start"])
    except (KeyError, TypeError, ValueError):
        return None
    curve = {}
    before = {}
    for pt in comp.get("hourly") or []:
        try:
            t = parse_ts(pt["from"])
            viewers = pt["viewers"]
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(viewers, (int, float)) or viewers < 0:
            continue
        off = int((t - start).total_seconds() // 3600)
        if 0 <= off < 24:
            curve[off] = viewers
        elif -48 <= off < 0:
            before[off] = viewers
    if len(curve) < 24:
        return None
    h1 = curve.get(0)
    if h1 is None or h1 <= 0:
        return None
    peak = max(curve.values())
    peak_ratio = peak / h1
    if peak_ratio > MAX_PEAK_RATIO:
        return None
    for h in range(1, 24):
        prev = curve[h - 1]
        if prev > 0 and curve[h] / prev > MAX_HOUR_JUMP:
            return None
    prev_hour = nearest_hour(before, -1)
    uk_step = math.log(h1 / prev_hour) if prev_hour else None
    return {
        "log_h1": math.log(h1),
        "log_ratio": {h: math.log(curve[h] / h1) for h in range(1, 24)},
        "pc": "PC" in (comp.get("platforms") or ""),
        "uk_step": uk_step,
    }


def case_uk_step(case):
    """log(hour-1 print / viewers in the hour before day start), or None.

    The hour before day start is the CET unlock hour; hour 1 adds the UK.
    Read from history_hourly, the point nearest day_start - 1h.
    """
    try:
        start = parse_ts(case["day_start"])
        known = next(k for k in case["known"] if k["hour"] == 1)
        p1 = known["viewers"]
    except (KeyError, TypeError, ValueError, StopIteration):
        return None
    if p1 is None or p1 <= 0:
        return None
    before = {}
    for pt in case.get("history_hourly") or []:
        try:
            t = parse_ts(pt["from"])
            viewers = pt["viewers"]
        except (KeyError, TypeError, ValueError):
            continue
        off = int((t - start).total_seconds() // 3600)
        if -48 <= off < 0:
            before[off] = viewers
    prev_hour = nearest_hour(before, -1)
    if prev_hour is None:
        return None
    return math.log(p1 / prev_hour)


def weighted_median(pairs):
    """Median of [(value, weight)] pairs; pairs need not be sorted."""
    ordered = sorted(pairs)
    total = sum(w for _, w in ordered)
    if total <= 0:
        return statistics.median([v for v, _ in ordered])
    acc = 0.0
    for value, weight in ordered:
        acc += weight
        if acc >= 0.5 * total:
            return value
    return ordered[-1][0]


def weighted_sd(pairs, center):
    """Weighted standard deviation of values around `center`."""
    total = sum(w for _, w in pairs)
    if total <= 0 or len(pairs) < 2:
        return 0.0
    var = sum(w * (v - center) ** 2 for v, w in pairs) / total
    return math.sqrt(max(var, 0.0))


def round2(x):
    """Round to 2 decimals, half up; whole numbers come back as ints."""
    value = math.floor(x * 100 + 0.5) / 100
    return int(value) if float(value).is_integer() else value


def clean_quantiles(raw):
    """Clamp to (0, 1e9], round, and force non-decreasing order."""
    out = []
    prev = 0.01
    for q in raw:
        v = round2(min(max(q, 0.01), 1e9))
        if v < prev:
            v = prev
        out.append(v)
        prev = v
    return out


def persistence_rows(p1):
    """Fallback forecast: every hour stays at hour 1, spread widening slowly."""
    rows = {}
    for h in range(2, 25):
        rows[h] = clean_quantiles(
            [p1 * math.exp(z * FALLBACK_SIGMA * math.sqrt(h - 1)) for z in Z]
        )
    return rows


def hour1_print(case):
    """Hour 1's print, with fallbacks so a malformed case still forecasts.

    Prefers the `known` row for hour 1; falls back to the largest recent
    hourly point, then to 1. A print below 1 counts as 1.
    """
    for row in case.get("known") or []:
        if row.get("hour") == 1:
            viewers = row.get("viewers")
            if isinstance(viewers, (int, float)) and viewers > 0:
                return float(viewers)
    recent = []
    for pt in case.get("history_hourly") or []:
        viewers = pt.get("viewers")
        if isinstance(viewers, (int, float)) and viewers > 0:
            recent.append(viewers)
    if recent:
        return float(max(recent))
    return 1.0


def forecast_case(case):
    p1 = max(1.0, hour1_print(case))
    log_p1 = math.log(p1)
    step_case = case_uk_step(case)

    comps = []
    for comp in case.get("comparables") or []:
        data = extract_comparable(comp)
        if data is None:
            continue
        weight = PC_WEIGHT if data["pc"] else 1.0
        weight *= math.exp(-0.5 * ((log_p1 - data["log_h1"]) / H1_KERNEL_BW) ** 2)
        comps.append((data, weight))
    if not comps:
        rows = persistence_rows(p1)
        return {"hours": [{"hour": h, "q": rows[h]} for h in range(2, 25)]}

    rows = {}
    for h in range(2, 25):
        off = h - 1
        adjusted = []
        for data, weight in comps:
            value = data["log_ratio"][off] + LEVEL_SLOPE * (log_p1 - data["log_h1"])
            if step_case is not None and data["uk_step"] is not None:
                shift = UK_STEP_GAMMA * (step_case - data["uk_step"])
                value += max(-UK_STEP_CAP, min(UK_STEP_CAP, shift))
            adjusted.append((value, weight))
        center = weighted_median(adjusted)
        spread = max(weighted_sd(adjusted, center), SD_FLOOR) * SD_WIDEN
        rows[h] = clean_quantiles([p1 * math.exp(center + z * spread) for z in Z])

    # The median curve's peak cannot sit below hour 1 itself: the day's peak
    # ratio is at least 1 by definition. Rescale if the adjustments pushed
    # every median below the hour-1 print.
    peak_med = max(rows[h][2] for h in range(2, 25))
    if peak_med < p1:
        scale = p1 / peak_med
        for h in range(2, 25):
            rows[h] = clean_quantiles([q * scale for q in rows[h]])

    return {"hours": [{"hour": h, "q": rows[h]} for h in range(2, 25)]}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case)
        text = json.dumps(forecast, separators=(",", ":")) + "\n"
        (OUTPUT_DIR / (path.stem + ".out")).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
