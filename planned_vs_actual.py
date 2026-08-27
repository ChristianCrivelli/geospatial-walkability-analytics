"""
planned_vs_actual.py
=====================
Phase 2 / Step 2 of the True Walkability pipeline: the project's stated
"unique angle" — comparing "Planned" walkability (what a naive tool like
WalkScore assumes: every non-motorway edge is equally walkable) against
"Actual" walkability (the friction-weighted graph from #001 / see
`friction_weighting.py`), on the same origin-destination pairs.

    Planned route  = nx.shortest_path(G, o, d, weight="planned_weight")
    Actual route   = nx.shortest_path(G, o, d, weight="friction_weight")

where `planned_weight == length` (see `add_planned_weights` below) — i.e.
the Planned graph assumes zero slope penalty and zero infrastructure
penalty. This is intentionally derived from the same OSM pull as the
Actual graph rather than sourced from 5 separate municipal GIS datasets,
so the comparison is consistent across all 5 (very different) study
locations. This is a scope/consistency tradeoff, not obviously the
"correct" definition of "Planned" — flagged as an open question in
REPORT.md, not presented as settled.

Scope of this pass (see issue #2 and REPORT.md):
  - O-D sampling: RANDOM pairs only. POI-based sampling (e.g. "nearest
    residential node -> nearest amenity") is stubbed below
    (`poi_based_od_pairs`) rather than implemented, since it requires a
    live Overpass API call this pipeline doesn't otherwise depend on.
    Real, human-picked start/end points are issue #005's job instead
    (personal-path validation) — that's a stronger source of
    human-meaningful O-D pairs than synthetic POI sampling would be, so
    POI-based sampling is deferred rather than rushed.
  - "Path length delta" is reported in *physical* metres (sum of the
    `length` attribute along each route), not in friction-cost units —
    the interesting comparison is "how much further, physically, does
    the friction-aware route go to avoid painful terrain," not a
    difference between two incomparable cost units. Friction-cost columns
    are included too (see `compare_od_pairs`), for the complementary
    question "how much *effort* would the naive route have cost you."
"""

import logging
import random
from typing import Optional

import networkx as nx
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# === 1. PLANNED WEIGHTS ======================================================
# "Planned" = naive walkability assumption: every pedestrian-eligible edge is
# equally walkable, i.e. no slope penalty, no infrastructure penalty. This is
# literally just the raw `length`, kept as its own edge attribute (rather than
# routing on `length` directly) so the Planned/Actual comparison reads
# symmetrically in code and so `planned_weight` survives a graphml round-trip
# alongside `friction_weight` for later reuse (#003, #004).


def add_planned_weights(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Attach `planned_weight` (== `length`) to every edge in G (mutates and
    returns G)."""
    n_missing_length = 0
    for u, v, k, data in G.edges(keys=True, data=True):
        length = data.get("length", 0) or 0
        try:
            length = float(length)
        except (TypeError, ValueError):
            length = 0.0
            n_missing_length += 1
        data["planned_weight"] = length

    if n_missing_length:
        log.warning(f"  {n_missing_length} edges had missing/invalid length -> planned_weight=0.0")
    return G


# === 2. ORIGIN-DESTINATION SAMPLING ==========================================


def _largest_strongly_connected_nodes(G: nx.MultiDiGraph) -> set:
    """Restrict sampling to the largest strongly-connected component so every
    sampled pair is guaranteed reachable in both directions — avoids wasting
    samples (or silently biasing the sample) on pairs `nx.shortest_path`
    can't route between."""
    components = list(nx.strongly_connected_components(G))
    if not components:
        return set()
    return max(components, key=len)


def random_od_pairs(
    G: nx.MultiDiGraph,
    n: int = 200,
    seed: Optional[int] = 42,
) -> list[tuple]:
    """
    Sample `n` random (origin, destination) node pairs from G's largest
    strongly-connected component, for statistical coverage across the whole
    graph (per issue #2).

    Guarantees o != d and no duplicate pairs. Returns fewer than `n` pairs
    (with a warning) if the component is too small to support that many
    distinct pairs.
    """
    rng = random.Random(seed)
    nodes = sorted(_largest_strongly_connected_nodes(G))  # sorted -> deterministic given seed

    max_possible = len(nodes) * (len(nodes) - 1)
    if max_possible < n:
        log.warning(
            f"  Largest strongly-connected component has only {len(nodes)} nodes "
            f"({max_possible} possible ordered pairs) — requested n={n}, returning "
            f"{max_possible} instead."
        )
        n = max_possible

    pairs: set[tuple] = set()
    attempts = 0
    max_attempts = n * 50 + 100  # generous bound; component is fully connected so this won't loop forever
    while len(pairs) < n and attempts < max_attempts:
        o, d = rng.sample(nodes, 2)
        pairs.add((o, d))
        attempts += 1

    return list(pairs)


def poi_based_od_pairs(G: nx.MultiDiGraph, n: int = 50) -> list[tuple]:
    """
    NOT YET IMPLEMENTED.

    Intended to sample (residential node -> nearest amenity) pairs using
    live OSM POI data (`osmnx.features_from_place`, `amenity=*` / `shop=*`
    via the Overpass API) — see issue #2's proposed approach.

    Deferred deliberately for this pass (see module docstring): it needs an
    external, rate-limited network dependency this pipeline doesn't
    otherwise have, and issue #005 (personal-path validation) is about to
    supply real, human-picked start/end points, which are a stronger
    "human-meaningful O-D pair" source than synthetic nearest-amenity
    sampling would be. Revisit only if a report narrative specifically
    needs synthetic POI pairs in addition to #005's real ones.
    """
    raise NotImplementedError(
        "poi_based_od_pairs is a stub — see issue #2 and the module "
        "docstring. Use random_od_pairs for now; #005 supplies real O-D pairs."
    )


# === 3. ROUTE COMPARISON =====================================================


def _path_edges(path: list) -> list[tuple]:
    """Consecutive (u, v) node pairs along a path (undirected key, since
    overlap should count a shared street regardless of direction of travel)."""
    return [tuple(sorted((path[i], path[i + 1]))) for i in range(len(path) - 1)]


def _path_physical_length(G: nx.MultiDiGraph, path: list) -> float:
    """Sum of the `length` attribute along a path, picking the shortest
    parallel edge between each consecutive node pair (matches how
    nx.shortest_path itself resolves multi-edges when weight is a string
    attribute name)."""
    total = 0.0
    for u, v in zip(path[:-1], path[1:]):
        parallel = G.get_edge_data(u, v)
        best = min(parallel.values(), key=lambda d: d.get("length", float("inf")))
        total += float(best.get("length", 0) or 0)
    return total


def _path_cost(G: nx.MultiDiGraph, path: list, weight: str) -> float:
    """Sum of `weight` along a path, picking the cheapest parallel edge at
    each step (same resolution rule as `_path_physical_length`)."""
    total = 0.0
    for u, v in zip(path[:-1], path[1:]):
        parallel = G.get_edge_data(u, v)
        best = min(parallel.values(), key=lambda d: d.get(weight, float("inf")))
        total += float(best.get(weight, 0) or 0)
    return total


def compare_route(G: nx.MultiDiGraph, o, d) -> Optional[dict]:
    """
    Compute the Planned vs. Actual comparison for a single O-D pair.

    Returns None if either route doesn't exist (shouldn't happen when o, d
    come from `random_od_pairs`'s strongly-connected sample, but real-world
    graphs sometimes surprise you — fail soft per pair rather than aborting
    the whole run).

    Returns a dict with:
      planned_length_m, actual_length_m  : physical route length (metres)
      length_delta_m, length_delta_pct   : actual - planned, and as % of planned
      planned_friction_cost, actual_friction_cost : routes' cost under
          friction_weight (i.e. "how much effort would the Planned route
          have cost, vs. what the Actual route actually costs")
      overlap_pct        : % of the (physically shorter) route's edges that
          are also used by the other route — 100% means identical routes
      diverges_completely : True iff the two routes share zero edges
    """
    try:
        planned_path = nx.shortest_path(G, o, d, weight="planned_weight")
        actual_path = nx.shortest_path(G, o, d, weight="friction_weight")
    except nx.NetworkXNoPath:
        log.warning(f"  No path between {o} and {d} — skipping (unexpected for a strongly-connected sample).")
        return None

    planned_length = _path_physical_length(G, planned_path)
    actual_length = _path_physical_length(G, actual_path)

    planned_friction_cost = _path_cost(G, planned_path, "friction_weight")
    actual_friction_cost = _path_cost(G, actual_path, "friction_weight")

    planned_edges = set(_path_edges(planned_path))
    actual_edges = set(_path_edges(actual_path))
    shared_edges = planned_edges & actual_edges

    # Overlap as a fraction of edge *count* on the shorter (by edge count) of
    # the two routes — simple and symmetric enough for a v1 metric; a
    # length-weighted overlap is a reasonable future refinement if a
    # hand-checked example (see issue #2's acceptance criteria) suggests
    # edge-count overlap is misleading for very unequal-length route pairs.
    shorter_edge_count = min(len(planned_edges), len(actual_edges))
    overlap_pct = 100.0 * len(shared_edges) / shorter_edge_count if shorter_edge_count else 0.0

    length_delta_m = actual_length - planned_length
    length_delta_pct = 100.0 * length_delta_m / planned_length if planned_length else float("nan")

    return {
        "o": o,
        "d": d,
        "planned_length_m": planned_length,
        "actual_length_m": actual_length,
        "length_delta_m": length_delta_m,
        "length_delta_pct": length_delta_pct,
        "planned_friction_cost": planned_friction_cost,
        "actual_friction_cost": actual_friction_cost,
        "friction_savings_pct": (
            100.0 * (planned_friction_cost - actual_friction_cost) / planned_friction_cost
            if planned_friction_cost else float("nan")
        ),
        "overlap_pct": overlap_pct,
        "diverges_completely": len(shared_edges) == 0,
        "planned_n_edges": len(planned_edges),
        "actual_n_edges": len(actual_edges),
    }


def compare_od_pairs(G: nx.MultiDiGraph, od_pairs: list[tuple], city: str = "") -> pd.DataFrame:
    """Run `compare_route` over a list of (o, d) pairs and return a DataFrame,
    one row per pair (pairs with no path are dropped, with a warning already
    logged per-pair)."""
    rows = []
    for o, d in od_pairs:
        result = compare_route(G, o, d)
        if result is not None:
            result["city"] = city
            rows.append(result)

    df = pd.DataFrame(rows)
    if not df.empty:
        cols = ["city", "o", "d"] + [c for c in df.columns if c not in ("city", "o", "d")]
        df = df[cols]
    return df


def summarise_comparison(df: pd.DataFrame, city: str = "") -> None:
    """Log an aggregate summary for one city's comparison DataFrame."""
    if df.empty:
        log.warning(f"  No comparable O-D pairs for {city} — nothing to summarise.")
        return
    label = f" — {city}" if city else ""
    log.info(f"Planned vs. Actual summary{label} ({len(df)} O-D pairs):")
    log.info(f"  mean length delta:     {df['length_delta_pct'].mean():.1f}% (actual vs. planned)")
    log.info(f"  mean friction savings: {df['friction_savings_pct'].mean():.1f}% (actual vs. planned)")
    log.info(f"  mean route overlap:    {df['overlap_pct'].mean():.1f}%")
    n_diverge = int(df["diverges_completely"].sum())
    log.info(f"  fully divergent routes: {n_diverge}/{len(df)} ({100 * n_diverge / len(df):.1f}%)")


# === 4. EXPORT ================================================================


def export_comparison(df: pd.DataFrame, name: str, base_dir: str = "locations") -> str:
    """Save the comparison DataFrame as
    <base_dir>/<name>/<name>_planned_vs_actual.csv."""
    import os

    slug = name.replace(" ", "_")
    out_path = os.path.join(base_dir, slug, f"{slug}_planned_vs_actual.csv")
    df.to_csv(out_path, index=False)
    log.info(f"  Saved Planned vs. Actual comparison -> {out_path}")
    return out_path


# === 5. CLI ===================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from pedestrian_nodes import LOCATIONS, load_graph
    from friction_weighting import add_friction_weights

    N_PAIRS = 200
    SEED = 42

    all_summaries = []

    for loc_name in LOCATIONS:
        G = load_graph(loc_name)
        if G is None:
            log.warning(f"No cached graph for '{loc_name}' — run pedestrian_nodes.py first. Skipping.")
            continue

        print(f"\n{'=' * 60}\n  {loc_name}\n{'=' * 60}")

        add_friction_weights(G, name=loc_name)
        add_planned_weights(G)

        od_pairs = random_od_pairs(G, n=N_PAIRS, seed=SEED)
        log.info(f"  Sampled {len(od_pairs)} random O-D pairs")

        df = compare_od_pairs(G, od_pairs, city=loc_name)
        summarise_comparison(df, city=loc_name)
        export_comparison(df, loc_name)

        if not df.empty:
            # o/d are node IDs, not meaningful to average — drop before aggregating
            row = df.drop(columns=["o", "d"]).mean(numeric_only=True)
            row["city"] = loc_name
            row["n_pairs"] = len(df)
            all_summaries.append(row)

    if all_summaries:
        combined = pd.DataFrame(all_summaries)
        cols = ["city", "n_pairs"] + [c for c in combined.columns if c not in ("city", "n_pairs")]
        combined = combined[cols]
        out_path = "locations/all_locations_planned_vs_actual_summary.csv"
        combined.to_csv(out_path, index=False)
        print(f"\nAll-location Planned vs. Actual summary saved -> {out_path}")
        print(combined.to_string(index=False))
