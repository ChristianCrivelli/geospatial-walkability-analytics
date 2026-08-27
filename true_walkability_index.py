"""
true_walkability_index.py
==========================
Phase 2 / Step 4 of the True Walkability pipeline: the composite "True
Walkability Index" (issue #4) — a single 0-100 score per city (higher =
more walkable) built from four components already produced by #001-#003,
plus one new one (dead-end density).

COMPONENTS (all framed consistently as "higher = worse/harder to walk"
before normalization):

  1. friction_ratio      — sum(friction_weight) / sum(length) over all
                            edges. Reused from #001's validation approach;
                            already known per city from REPORT.md, recomputed
                            here directly from the graph rather than trusted
                            as a hardcoded constant.
  2. median_circuity     — median network-distance / great-circle-distance
                            over the #002/#003 200-pair O-D sample. Median,
                            not mean, per #003's documented Sabancı short-
                            distance-outlier caveat (see connectivity_metrics
                            module docstring and REPORT.md).
  3. dead_end_density    — fraction of degree-1 nodes in a simplified,
                            undirected, simple graph (self-loops dropped,
                            parallel edges collapsed) — a NEW metric for #4,
                            not computed anywhere upstream. A degree-1 node
                            is a physical dead end: nowhere to go but back
                            the way you came.
  4. friction_savings_pct (mean) — the mean % effort saved by the Actual
                            route over the Planned route, reused directly
                            from #002's `compare_od_pairs` output. Framed
                            here as "how big is the Planned/Actual gap" —
                            a LARGER gap means the naive/Planned model is a
                            worse stand-in for what's actually walkable,
                            i.e. more hidden friction the Planned view
                            misses. (Not "how much better Actual routing
                            makes things" — same number, opposite framing,
                            which is why it's treated as a "higher = worse"
                            input here even though #002/REPORT.md discuss it
                            as a savings.)

WHY THESE FOUR: the issue asks for >= 4 components spanning effort
(friction_ratio), detour (median_circuity), physical dead-ends
(dead_end_density), and the Planned/Actual gap (friction_savings_pct) —
four different failure modes a naive walkability score would miss, matching
the project's core "Planned vs Actual" thesis.

NORMALIZATION: min-max across THIS 5-city set (0 = best/easiest city on
that axis, 1 = worst), not z-score — z-score needs a larger, more stable
sample than 5 cities to be meaningful, and 0-1 min-max reads more
intuitively for the planner audience README.md targets. IMPORTANT CAVEAT
(documented here and in REPORT.md, not hidden): because normalization is
relative to only these 5 cities, the index is a RANKING within this set,
not an absolute/universal score — adding a 6th city (issue #6) would shift
every city's normalized values and therefore its index score. This is a
real limitation of a 5-city v1, not a bug.

WEIGHTS: equal-weighted (0.25 each) by default — a documented first-pass
judgment call, not a derived or empirically-justified weighting, exactly
like #001's infrastructure penalty values. `DEFAULT_WEIGHTS` is a plain
dict specifically so it's trivial to override for a sensitivity check
(REPORT.md floats "does the ranking change much if weights/penalties move
±30%?" as a future report exercise).

FINAL SCORE: true_walkability_index = 100 * (1 - difficulty_score), so
higher = more walkable, 0-100 scale — chosen to read intuitively ("82/100
walkable") for the planner/policy audience rather than a raw 0-1 difficulty
number.

--------------------------------------------------------------------------
FOLLOW-UP (issue #4, post-close): SCALE BIAS IN median_circuity
--------------------------------------------------------------------------
The first-pass index above initially ranked Sabancı University near the
bottom (29.4/100), which read as unbelievable against a hand-on-the-ground
sense that the campus — while uphill and stair-heavy — is genuinely easy
to get around. Investigating confirmed the gut check, not the number:
Sabancı's low score was being driven almost entirely by dead_end_density
and median_circuity, NOT friction_ratio (which was mid-pack, 1.219 vs.
Maastricht's 1.170) — i.e. not by effort, by two structural metrics.

  - dead_end_density: 72 of Sabancı's 101 degree-1 nodes (71%) sit on
    `footway`-tagged edges — almost certainly building-entrance spurs on a
    richly-mapped campus, not places you'd actually get stuck. No clean,
    non-arbitrary way to filter these out of the current graph (would need
    OSM building-footprint data this pipeline doesn't load) — left as-is,
    documented as a known limitation rather than "fixed" with a made-up
    edge-length threshold that would be just as much a designed judgment
    call as the thing it's trying to correct.

  - median_circuity: circuity ratios are a well-documented distance-decayed
    quantity (a fixed detour matters more, proportionally, on a short trip
    than a long one). Confirmed in this project's own data: pooled across
    all 5 cities' 1,000 O-D pairs, circuity ~ -0.0923 * ln(distance_m) +
    2.082 (OLS, r=-0.234, p=6.3e-14, n=1000) — a real, statistically
    significant decay, and the same downward slope shows up within Mindelo
    alone (the one city with both short and long trips to compare). Sabancı's
    median O-D distance is 342m — the whole campus fits in roughly a 1km
    bounding box — versus 1,300-3,500m for the other four cities. Every one
    of its 200 sampled pairs lands in the "short trip -> mechanically higher
    ratio" zone purely because the study area is small, not because its path
    network is worse. FIXED here via `excess_circuity` (observed - expected,
    from the pooled distance model above) as the adjusted component in
    place of raw `median_circuity` — see `fit_circuity_distance_model` and
    `ADJUSTED_COMPONENT_COLUMNS` below. The original index (issue #4, as
    closed) is left computed and exported unchanged; the adjusted index is
    a second, additional output, not a replacement — see REPORT.md for the
    side-by-side comparison and discussion.
"""

import logging

import networkx as nx
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# === 1. COMPONENT METRICS ====================================================


def friction_ratio(G: nx.MultiDiGraph) -> float:
    """sum(friction_weight) / sum(length) across all edges — the same
    penalty-ratio quantity already reported per city in REPORT.md (#001),
    recomputed here from the live graph rather than hardcoded."""
    total_friction = 0.0
    total_length = 0.0
    for _, _, data in G.edges(data=True):
        length = float(data.get("length", 0) or 0)
        total_length += length
        total_friction += float(data.get("friction_weight", length) or length)
    if total_length == 0:
        return float("nan")
    return total_friction / total_length


def dead_end_density(G: nx.MultiDiGraph) -> float:
    """
    Fraction of degree-1 nodes in a simplified, undirected, simple graph
    (self-loops dropped, parallel edges collapsed to one) — a physical
    dead end is a node with exactly one street connecting to it, i.e.
    nowhere to go but back. New metric for #4; not reused from #001-#003.
    """
    H = nx.Graph()
    H.add_nodes_from(G.nodes())
    for u, v in G.edges():
        if u != v:
            H.add_edge(u, v)

    n_nodes = H.number_of_nodes()
    if n_nodes == 0:
        return float("nan")

    degrees = dict(H.degree())
    n_dead_ends = sum(1 for d in degrees.values() if d == 1)
    return n_dead_ends / n_nodes


# median_circuity and friction_savings_pct are NOT reimplemented here — they
# are read straight off #002's `compare_od_pairs` and #003's
# `compute_circuity` DataFrames by the CLI below, per the project's
# established "reuse existing module functions, don't depend on stale CSVs
# on disk" pattern (see #003's own CLI for precedent).


# === 2. DISTANCE-DETRENDED CIRCUITY (follow-up refinement, see module docstring) ==


def fit_circuity_distance_model(pooled_circuity_df: pd.DataFrame) -> tuple[float, float, float]:
    """
    Fit circuity = intercept + slope * ln(great_circle_m) by OLS across ALL
    cities' O-D pairs pooled together (not per-city) — circuity is a known
    distance-decayed quantity, so the fit needs to span the full range of
    trip distances across the whole study set, not just one city's narrower
    range. Returns (intercept, slope, r_squared). Uses plain numpy (no
    scipy dependency) since this is a one-off diagnostic fit, not a
    reusable stats utility.
    """
    x = np.log(pooled_circuity_df["great_circle_m"].values)
    y = pooled_circuity_df["circuity"].values
    slope, intercept = np.polyfit(x, y, 1)
    pred = intercept + slope * x
    ss_res = np.sum((y - pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot else float("nan")
    return float(intercept), float(slope), float(r_squared)


def add_excess_circuity(circuity_df: pd.DataFrame, intercept: float, slope: float) -> pd.DataFrame:
    """
    Add `expected_circuity` (from the pooled distance model) and
    `excess_circuity` (observed - expected) to a copy of a per-city
    circuity DataFrame. Negative excess_circuity = less circuitous than
    expected for a trip of that distance; positive = more. This is the
    distance-controlled replacement for raw `circuity` in the adjusted
    index (see module docstring).
    """
    df = circuity_df.copy()
    df["expected_circuity"] = intercept + slope * np.log(df["great_circle_m"])
    df["excess_circuity"] = df["circuity"] - df["expected_circuity"]
    return df


# === 3. NORMALIZATION AND COMPOSITE SCORE ====================================

COMPONENT_COLUMNS = [
    "friction_ratio",
    "median_circuity",
    "dead_end_density",
    "friction_savings_pct",
]

# Equal-weighted first pass — a documented judgment call, not a derived
# result. Override this dict (or pass a different one into
# `compute_composite_index`) to run a sensitivity check.
DEFAULT_WEIGHTS = {
    "friction_ratio": 0.25,
    "median_circuity": 0.25,
    "dead_end_density": 0.25,
    "friction_savings_pct": 0.25,
}

# Adjusted variant (follow-up, see module docstring): median_circuity swapped
# for median_excess_circuity (distance-detrended). Same equal-weight
# philosophy, same weight value — only the circuity component's *definition*
# changes, not its relative importance.
ADJUSTED_COMPONENT_COLUMNS = [
    "friction_ratio",
    "median_excess_circuity",
    "dead_end_density",
    "friction_savings_pct",
]

ADJUSTED_DEFAULT_WEIGHTS = {
    "friction_ratio": 0.25,
    "median_excess_circuity": 0.25,
    "dead_end_density": 0.25,
    "friction_savings_pct": 0.25,
}


def _min_max_normalize(series: pd.Series) -> pd.Series:
    """0 = lowest ('best'/easiest) value in the set, 1 = highest ('worst').
    Degenerate case (all values equal, e.g. a 1-city run) -> 0.0 for every
    row rather than dividing by zero."""
    lo, hi = series.min(), series.max()
    if hi == lo:
        return pd.Series(0.0, index=series.index)
    return (series - lo) / (hi - lo)


def compute_composite_index(
    raw_df: pd.DataFrame,
    weights: dict = None,
    component_columns: list = None,
) -> pd.DataFrame:
    """
    Given a DataFrame with one row per city and the given raw component
    columns (default COMPONENT_COLUMNS), add per-component `<col>_norm`
    columns (min-max across the rows given — see module docstring's caveat
    about this being relative to whatever set of cities is passed in),
    `difficulty_score` (weighted mean of the normalized components), and
    `true_walkability_index` (= 100 * (1 - difficulty_score)).

    `component_columns` / `weights` are overridable together so this same
    function computes both the original index (COMPONENT_COLUMNS,
    DEFAULT_WEIGHTS) and the distance-adjusted variant
    (ADJUSTED_COMPONENT_COLUMNS, ADJUSTED_DEFAULT_WEIGHTS) — see module
    docstring's follow-up section.
    """
    component_columns = component_columns or COMPONENT_COLUMNS
    weights = weights or DEFAULT_WEIGHTS
    df = raw_df.copy()

    for col in component_columns:
        df[f"{col}_norm"] = _min_max_normalize(df[col])

    weight_sum = sum(weights[col] for col in component_columns)
    df["difficulty_score"] = sum(
        df[f"{col}_norm"] * (weights[col] / weight_sum) for col in component_columns
    )
    df["true_walkability_index"] = 100.0 * (1.0 - df["difficulty_score"])

    return df


# === 4. EXPORT ================================================================


def export_index(df: pd.DataFrame, filename: str, base_dir: str = "locations") -> str:
    import os

    out_path = os.path.join(base_dir, filename)
    df.to_csv(out_path, index=False)
    log.info(f"Saved composite index -> {out_path}")
    return out_path


# === 5. CLI ====================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from pedestrian_nodes import LOCATIONS, load_graph
    from friction_weighting import add_friction_weights
    from planned_vs_actual import random_od_pairs, add_planned_weights, compare_od_pairs
    from connectivity_metrics import compute_circuity

    N_PAIRS = 200
    SEED = 42

    rows = []
    circuity_frames = []  # per-city circuity_df, pooled below to fit the distance model

    for loc_name in LOCATIONS:
        G = load_graph(loc_name)
        if G is None:
            log.warning(f"No cached graph for '{loc_name}' — run pedestrian_nodes.py first. Skipping.")
            continue

        print(f"\n{'=' * 60}\n  {loc_name}\n{'=' * 60}")

        add_friction_weights(G, name=loc_name)
        add_planned_weights(G)

        # Reuse #002/#003's exact O-D sample (same seed) so median_circuity
        # and friction_savings_pct line up with the same 200 pairs.
        od_pairs = random_od_pairs(G, n=N_PAIRS, seed=SEED)
        comparison_df = compare_od_pairs(G, od_pairs, city=loc_name)
        circuity_df = compute_circuity(G, comparison_df)
        circuity_df["city"] = loc_name
        circuity_frames.append(circuity_df)

        f_ratio = friction_ratio(G)
        d_density = dead_end_density(G)
        med_circuity = circuity_df["circuity"].median()
        savings_pct = comparison_df["friction_savings_pct"].mean()

        log.info(f"  friction_ratio:        {f_ratio:.4f}")
        log.info(f"  dead_end_density:      {d_density:.4f}")
        log.info(f"  median_circuity:       {med_circuity:.4f}")
        log.info(f"  friction_savings_pct:  {savings_pct:.4f}")

        rows.append({
            "city": loc_name,
            "friction_ratio": f_ratio,
            "median_circuity": med_circuity,
            "dead_end_density": d_density,
            "friction_savings_pct": savings_pct,
        })

    if rows:
        raw_df = pd.DataFrame(rows)

        # --- Original index (issue #4, as closed) — unchanged ---
        result_df = compute_composite_index(raw_df)
        cols = (
            ["city"]
            + COMPONENT_COLUMNS
            + [f"{c}_norm" for c in COMPONENT_COLUMNS]
            + ["difficulty_score", "true_walkability_index"]
        )
        result_df = result_df[cols]
        export_index(result_df, "all_locations_true_walkability_index.csv")

        print("\nTrue Walkability Index — ORIGINAL (0-100, higher = more walkable):")
        print(
            result_df[["city", "true_walkability_index", "difficulty_score"]]
            .sort_values("true_walkability_index", ascending=False)
            .to_string(index=False)
        )

        # --- Adjusted index (follow-up: distance-detrended circuity) ---
        pooled_circuity = pd.concat(circuity_frames, ignore_index=True)
        intercept, slope, r_squared = fit_circuity_distance_model(pooled_circuity)
        log.info(
            f"\nPooled circuity-distance fit (n={len(pooled_circuity)}): "
            f"circuity = {intercept:.4f} + {slope:.6f} * ln(distance_m), r^2={r_squared:.4f}"
        )

        excess_rows = []
        for loc_name in raw_df["city"]:
            city_circuity = pooled_circuity[pooled_circuity["city"] == loc_name]
            city_circuity = add_excess_circuity(city_circuity, intercept, slope)
            excess_rows.append({
                "city": loc_name,
                "median_excess_circuity": city_circuity["excess_circuity"].median(),
            })
        excess_df = pd.DataFrame(excess_rows)

        adjusted_raw_df = raw_df.merge(excess_df, on="city")
        adjusted_result_df = compute_composite_index(
            adjusted_raw_df,
            weights=ADJUSTED_DEFAULT_WEIGHTS,
            component_columns=ADJUSTED_COMPONENT_COLUMNS,
        )
        adj_cols = (
            ["city"]
            + ADJUSTED_COMPONENT_COLUMNS
            + [f"{c}_norm" for c in ADJUSTED_COMPONENT_COLUMNS]
            + ["difficulty_score", "true_walkability_index"]
        )
        adjusted_result_df = adjusted_result_df[adj_cols]
        export_index(adjusted_result_df, "all_locations_true_walkability_index_adjusted.csv")

        print("\nTrue Walkability Index — ADJUSTED for circuity distance-decay (0-100, higher = more walkable):")
        print(
            adjusted_result_df[["city", "true_walkability_index", "difficulty_score"]]
            .sort_values("true_walkability_index", ascending=False)
            .to_string(index=False)
        )

        # --- Side-by-side comparison ---
        comparison = result_df[["city", "true_walkability_index"]].rename(
            columns={"true_walkability_index": "index_original"}
        ).merge(
            adjusted_result_df[["city", "true_walkability_index"]].rename(
                columns={"true_walkability_index": "index_adjusted"}
            ),
            on="city",
        )
        comparison["delta"] = comparison["index_adjusted"] - comparison["index_original"]
        comparison = comparison.sort_values("index_adjusted", ascending=False)
        export_index(comparison, "all_locations_true_walkability_index_comparison.csv")

        print("\nOriginal vs. Adjusted, side by side:")
        print(comparison.to_string(index=False))

        print("\nFull component breakdown — original:")
        print(result_df.to_string(index=False))
        print("\nFull component breakdown — adjusted:")
        print(adjusted_result_df.to_string(index=False))
