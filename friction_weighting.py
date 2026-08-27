"""
friction_weighting.py
======================
Phase 2 / Step 1 of the True Walkability pipeline: convert the enriched
pedestrian graph produced by `pedestrian_nodes.py` (OSM tags + elevation
+ grade) into a friction-weighted graph suitable for realistic routing.

This implements the Friction Factor formula from the project plan:

    W = d * (1 + slope_penalty + infrastructure_penalty)

as a new edge attribute, `friction_weight` (units: "effort-metres"),
which later routing/index code (see issue #2 and issue #4)
uses instead of raw `length`.

1. SLOPE PENALTY — derived from Tobler's hiking function (Tobler, 1993),
   the standard model for how ground slope affects walking speed:

       r(S) = 6 * exp(-3.5 * |S + 0.05|)      [km/h]

   Source: https://en.wikipedia.org/wiki/Tobler%27s_hiking_function

   IMPLEMENTATION NOTE — a subtlety worth documenting: Tobler's function
   does not peak at S=0 (flat ground). It peaks at a gentle S=-0.05
   (5%) downhill, where people walk fastest. A naive ratio r(0)/r(S)
   is therefore < 1 for S near -0.05 — i.e. it *rewards* gentle
   downhill relative to flat ground. That contradicts the project's
   own friction formula above, which is a strictly additive penalty
   (1 + penalties, never less than 1). We resolve this by flooring the
   slope penalty at 0: gentle downhill costs nothing extra, but we
   never treat it as *cheaper* than flat ground. This is a deliberate
   modeling decision, not a property of Tobler's function itself —
   flagged in REPORT.md for discussion.

2. INFRASTRUCTURE PENALTY — a categorical additive penalty whose
   *tiers* are inspired by the Pedestrian Level of Service (PLOS)
   framework (Highway Capacity Manual-style A-F grading of sidewalk
   presence, buffer, and traffic exposure). IMPORTANT: there is no
   single agreed-upon numeric constant for this in the published
   literature — PLOS studies calibrate locally per city. The specific
   penalty VALUES below are a documented modeling choice, not an
   empirical estimate — see REPORT.md ("Open questions for the
   report") before treating them as more than a first pass.

Bug fixed while writing this module: the original edge filter in
`pedestrian_nodes.py` (`bool(data.get("sidewalk"))`) treated
`sidewalk="no"` as a truthy "sidewalk present" signal, because it's a
non-empty string. This is a real, present-in-data bug — Mindelo alone
has 30 edges explicitly tagged `sidewalk=no`. Fixed here via
`_tag_is_positive()`.
"""

import logging

import numpy as np
import networkx as nx

log = logging.getLogger(__name__)

# === 1. SLOPE PENALTY — Tobler's hiking function ============================

TOBLER_K = 3.5
TOBLER_OFFSET = 0.05
MAX_ABS_GRADE = 0.45  # clip physically-implausible grades (elevation-data noise)


def tobler_speed_ratio(grade) -> float:
    """
    r(0)/r(S) from Tobler's hiking function — the flat-ground speed
    divided by the slope-adjusted speed. Can be < 1 near the function's
    gentle-downhill speed peak (see module docstring); callers that want
    a pure "added effort" penalty should use `slope_penalty()` instead,
    which floors this at 0.
    """
    try:
        s = float(grade)
    except (TypeError, ValueError):
        return 1.0
    if np.isnan(s):
        return 1.0
    s = max(min(s, MAX_ABS_GRADE), -MAX_ABS_GRADE)
    return float(np.exp(TOBLER_K * (abs(s + TOBLER_OFFSET) - TOBLER_OFFSET)))


def slope_penalty(grade) -> float:
    """
    Additive slope penalty (>= 0) for the W = d*(1 + penalties) formula.
    Missing/invalid grade -> 0.0 (no penalty; caller should track
    coverage separately if that matters).
    """
    return max(0.0, tobler_speed_ratio(grade) - 1.0)


# === 2. INFRASTRUCTURE PENALTY — PLOS-inspired tiers =========================

DEDICATED_INFRA = {"footway", "pedestrian", "path", "living_street", "track"}
LOW_TRAFFIC_NO_SIDEWALK = {"residential", "service", "unclassified"}
MODERATE_TRAFFIC_NO_SIDEWALK = {"tertiary", "secondary"}
HIGH_TRAFFIC_NO_SIDEWALK = {"primary", "trunk"}
STEPS_VALUES = {"steps"}

SIDEWALK_KEYS = ("sidewalk", "sidewalk:left", "sidewalk:right", "sidewalk:both")
NEGATIVE_TAG_VALUES = {"no", "none", ""}

# See module docstring: tiers are PLOS-inspired, penalty VALUES are a
# documented modeling choice — revisit in the report, not a citation.
INFRA_PENALTY = {
    "dedicated": 0.0,
    "low_traffic_no_sidewalk": 0.15,
    "moderate_traffic_no_sidewalk": 0.4,
    "high_traffic_no_sidewalk": 0.9,
}
STEPS_PENALTY = 0.6  # added on top of whatever tier penalty applies


def _tag_is_positive(value) -> bool:
    """True if an OSM tag value affirmatively indicates presence (handles
    lists, and excludes 'no'/'none'/'' which are negative/empty values that
    a naive `bool(value)` check would incorrectly treat as truthy)."""
    if value is None:
        return False
    values = value if isinstance(value, list) else [value]
    return any(str(v).strip().lower() not in NEGATIVE_TAG_VALUES for v in values)


def classify_infrastructure(data: dict) -> tuple[str, float, bool]:
    """
    Classify one edge's OSM tags into a PLOS-inspired infrastructure tier.

    Returns (tier_name, additive_penalty, wheelchair_ok).
    """
    highway = data.get("highway", "")
    hw_set = set(highway) if isinstance(highway, list) else {highway}

    has_sidewalk = any(_tag_is_positive(data.get(k)) for k in SIDEWALK_KEYS)
    is_steps = bool(hw_set & STEPS_VALUES)

    wheelchair_tag = str(data.get("wheelchair", "")).strip().lower()
    wheelchair_ok = wheelchair_tag != "no" and not is_steps

    if has_sidewalk or (hw_set & DEDICATED_INFRA):
        tier = "dedicated"
    elif hw_set & HIGH_TRAFFIC_NO_SIDEWALK:
        tier = "high_traffic_no_sidewalk"
    elif hw_set & MODERATE_TRAFFIC_NO_SIDEWALK:
        tier = "moderate_traffic_no_sidewalk"
    elif hw_set & LOW_TRAFFIC_NO_SIDEWALK:
        tier = "low_traffic_no_sidewalk"
    else:
        # Unrecognized/other pedestrian-eligible highway value: treat
        # charitably rather than silently mis-penalizing. Logged so these
        # stay visible instead of hiding in aggregate stats.
        tier = "dedicated"
        log.debug(f"Unclassified highway value(s) {hw_set} defaulted to 'dedicated' tier")

    penalty = INFRA_PENALTY[tier]
    if is_steps:
        penalty += STEPS_PENALTY
        tier = f"{tier}+steps"

    return tier, penalty, wheelchair_ok


# === 3. COMBINE INTO friction_weight =========================================

def add_friction_weights(G: nx.MultiDiGraph, name: str = "") -> nx.MultiDiGraph:
    """
    Attach `slope_penalty`, `infra_tier`, `infra_penalty`, `wheelchair_ok`,
    and `friction_weight` to every edge in G (mutates and returns G), per:

        friction_weight = length * (1 + slope_penalty + infra_penalty)

    Logs a coverage/tier-distribution summary.
    """
    n_missing_grade = 0
    tier_counts: dict[str, int] = {}

    for u, v, k, data in G.edges(keys=True, data=True):
        grade = data.get("grade", float("nan"))
        try:
            grade_f = float(grade)
        except (TypeError, ValueError):
            grade_f = float("nan")
        if np.isnan(grade_f):
            n_missing_grade += 1

        s_pen = slope_penalty(grade_f)
        tier, i_pen, wheelchair_ok = classify_infrastructure(data)
        length = data.get("length", 0) or 0
        try:
            length = float(length)
        except (TypeError, ValueError):
            length = 0.0

        data["slope_penalty"] = s_pen
        data["infra_tier"] = tier
        data["infra_penalty"] = i_pen
        data["wheelchair_ok"] = wheelchair_ok
        data["friction_weight"] = length * (1.0 + s_pen + i_pen)

        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    total = G.number_of_edges()
    label = f" — {name}" if name else ""
    log.info(f"Friction weighting complete{label}: {total} edges | missing grade: {n_missing_grade}")
    for tier, count in sorted(tier_counts.items(), key=lambda kv: -kv[1]):
        log.info(f"  {tier:<35} {count:>6} edges ({100 * count / total:.1f}%)")

    return G


def export_friction_graph(G: nx.MultiDiGraph, name: str, base_dir: str = "locations") -> str:
    """Save the friction-weighted graph as <base_dir>/<name>/<name>_friction.graphml
    (kept separate from the base graph so the raw pipeline output stays
    reproducible / untouched)."""
    import os
    import osmnx as ox

    slug = name.replace(" ", "_")
    out_path = os.path.join(base_dir, slug, f"{slug}_friction.graphml")
    ox.save_graphml(G, out_path)
    log.info(f"  Saved friction-weighted GraphML -> {out_path}")
    return out_path


# === 4. CLI ==================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from pedestrian_nodes import LOCATIONS, load_graph

    for loc_name in LOCATIONS:
        G = load_graph(loc_name)
        if G is None:
            log.warning(f"No cached graph for '{loc_name}' — run pedestrian_nodes.py first. Skipping.")
            continue

        print(f"\n{'=' * 60}\n  {loc_name}\n{'=' * 60}")
        add_friction_weights(G, name=loc_name)
        export_friction_graph(G, loc_name)
