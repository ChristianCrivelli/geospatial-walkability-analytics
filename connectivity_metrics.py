"""
connectivity_metrics.py
========================
Phase 2 / Step 3 of the True Walkability pipeline: connectivity metrics —
circuity and betweenness centrality — on the friction-weighted graph, to
surface dead-ends and choke points (issue #3).

1. CIRCUITY — network distance / straight-line distance, per O-D pair.
   Reuses the exact O-D sampling from #002 (`random_od_pairs`, same seed)
   so circuity and the Planned-vs-Actual comparison are computed over the
   same pairs. "Network distance" here is the *physical* length (metres)
   of the Actual (friction-weighted) shortest path — same quantity as
   `actual_length_m` in `planned_vs_actual.py` — not the friction-cost
   value, since circuity is a geometric detour ratio, not an effort ratio.
   Aggregated both as a per-city mean and as a coarse per-grid-cell view
   (bucketing each pair's origin into a projected-metre grid), per the
   issue's "even a coarse grid-cell bucket is fine for v1" note.

2. BETWEENNESS CENTRALITY — on the friction-weighted graph, to find
   choke points. Exact computation is O(V*E*log V) with Dijkstra
   (networkx's pure-Python weighted implementation) — fine for the three
   smaller study locations, impractical for the two largest. Scope
   decision (recorded, not silently applied): EXACT for Sabancı
   University, Mindelo, Lanaken; k-SAMPLE APPROXIMATION (fixed seed, for
   reproducibility) for Maastricht and Matosinhos. Which method was used
   per city is recorded in the output, not hidden — same principle as
   #001/#002's "documented judgment call, not a citation" caveats.

   Betweenness is computed on a *simplified DiGraph*, not the raw
   MultiDiGraph: parallel edges are collapsed to the cheapest (by
   friction_weight) — the same parallel-edge resolution rule
   `nx.shortest_path` itself applies, and the one `planned_vs_actual.py`
   uses for its own per-path cost calculations. Kept directed (not
   collapsed to undirected) because friction_weight is direction-
   dependent (uphill vs. downhill), so a routing-relevant choke point can
   genuinely differ by direction of travel.
"""

import logging
import math
from typing import Optional

import networkx as nx
import pandas as pd

log = logging.getLogger(__name__)

# === 1. CIRCUITY ==============================================================

EARTH_RADIUS_M = 6_371_000.0


def great_circle_distance_m(lat1, lon1, lat2, lon2) -> float:
    """Haversine great-circle distance in metres between two (lat, lon) points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def compute_circuity(G: nx.MultiDiGraph, comparison_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add `great_circle_m` and `circuity` (actual_length_m / great_circle_m)
    to a copy of a Planned-vs-Actual comparison DataFrame (from
    `planned_vs_actual.compare_od_pairs`) — reuses its `o`, `d`, and
    `actual_length_m` columns rather than re-running shortest paths.

    Pairs whose great-circle distance is ~0 (o and d snap to the same
    point) are dropped — circuity is undefined there, not infinite.
    """
    df = comparison_df.copy()
    gc = []
    for o, d in zip(df["o"], df["d"]):
        lat1, lon1 = G.nodes[o]["y"], G.nodes[o]["x"]
        lat2, lon2 = G.nodes[d]["y"], G.nodes[d]["x"]
        gc.append(great_circle_distance_m(lat1, lon1, lat2, lon2))
    df["great_circle_m"] = gc

    n_zero = int((df["great_circle_m"] < 1.0).sum())
    if n_zero:
        log.warning(f"  Dropping {n_zero} pair(s) with near-zero great-circle distance (circuity undefined)")
    df = df[df["great_circle_m"] >= 1.0].copy()

    df["circuity"] = df["actual_length_m"] / df["great_circle_m"]
    return df


def _projected_node_coords(G: nx.MultiDiGraph) -> dict:
    """Project G to a local metric CRS and return {node_id: (x_m, y_m)} —
    used only for grid bucketing, not for routing (routing weights are
    already in metres regardless of the graph's stored projection)."""
    import osmnx as ox

    Gp = ox.project_graph(G)
    return {n: (data["x"], data["y"]) for n, data in Gp.nodes(data=True)}


def circuity_by_grid_cell(
    G: nx.MultiDiGraph,
    circuity_df: pd.DataFrame,
    cell_size_m: float = 500.0,
) -> pd.DataFrame:
    """
    Coarse per-region circuity view: bucket each O-D pair by its *origin*
    node's projected coordinates into `cell_size_m` square cells, and
    report mean circuity + pair count per non-empty cell. A v1-level
    substitute for a real spatial density surface — enough to show
    circuity isn't uniform across a city without building a full raster.
    """
    coords = _projected_node_coords(G)
    cells = []
    for o in circuity_df["o"]:
        x, y = coords[o]
        cells.append((int(x // cell_size_m), int(y // cell_size_m)))

    df = circuity_df.copy()
    df["cell_x"], df["cell_y"] = zip(*cells)

    grid = (
        df.groupby(["cell_x", "cell_y"])
        .agg(mean_circuity=("circuity", "mean"), n_pairs=("circuity", "size"))
        .reset_index()
        .sort_values("mean_circuity", ascending=False)
    )
    return grid


# === 2. BETWEENNESS CENTRALITY ================================================


def simplify_to_digraph(G: nx.MultiDiGraph, weight: str = "friction_weight") -> nx.DiGraph:
    """
    Collapse a MultiDiGraph to a simple DiGraph, keeping only the cheapest
    parallel edge per (u, v) by `weight` — the same parallel-edge
    resolution rule `nx.shortest_path` applies implicitly, and the one
    `planned_vs_actual.py`'s `_path_cost` uses explicitly. Kept directed:
    friction_weight is direction-dependent (slope), so collapsing to
    undirected would blur a real asymmetry.
    """
    H = nx.DiGraph()
    H.add_nodes_from(G.nodes(data=True))
    for u, v, data in G.edges(data=True):
        w = data.get(weight, float("inf"))
        if not H.has_edge(u, v) or w < H[u][v].get(weight, float("inf")):
            H.add_edge(u, v, **{weight: w, "length": data.get("length")})
    return H


def compute_betweenness(
    G: nx.MultiDiGraph,
    weight: str = "friction_weight",
    k: Optional[int] = None,
    seed: Optional[int] = 42,
) -> tuple[dict, str]:
    """
    Betweenness centrality on the friction-weighted graph.

    `k=None` -> exact (all nodes as sources). `k=<int>` -> approximate,
    sampling k source nodes (fixed `seed` for reproducibility). Returns
    (centrality_dict, method_label) — method is recorded by the caller,
    never silently applied.
    """
    H = simplify_to_digraph(G, weight=weight)
    method = "exact" if k is None else f"k-sample (k={k}, seed={seed})"
    log.info(f"  Computing betweenness centrality ({method}) on {H.number_of_nodes()} nodes, {H.number_of_edges()} edges...")
    centrality = nx.betweenness_centrality(H, k=k, weight=weight, seed=seed, normalized=True)
    return centrality, method


def top_choke_points(G: nx.MultiDiGraph, centrality: dict, n: int = 10) -> pd.DataFrame:
    """Top-n highest-betweenness nodes: node id, lat/lon, betweenness score, rank."""
    ranked = sorted(centrality.items(), key=lambda kv: kv[1], reverse=True)[:n]
    rows = [
        {
            "rank": i + 1,
            "node": node,
            "lat": G.nodes[node]["y"],
            "lon": G.nodes[node]["x"],
            "betweenness": score,
        }
        for i, (node, score) in enumerate(ranked)
    ]
    return pd.DataFrame(rows)


# === 3. EXPORT ================================================================


def export_connectivity_outputs(
    circuity_df: pd.DataFrame,
    grid_df: pd.DataFrame,
    choke_points_df: pd.DataFrame,
    name: str,
    base_dir: str = "locations",
) -> dict:
    import os

    slug = name.replace(" ", "_")
    loc_dir = os.path.join(base_dir, slug)
    paths = {}

    p = os.path.join(loc_dir, f"{slug}_circuity.csv")
    circuity_df.to_csv(p, index=False)
    paths["circuity"] = p
    log.info(f"  Saved circuity -> {p}")

    p = os.path.join(loc_dir, f"{slug}_circuity_by_grid_cell.csv")
    grid_df.to_csv(p, index=False)
    paths["grid"] = p
    log.info(f"  Saved circuity-by-grid-cell -> {p}")

    p = os.path.join(loc_dir, f"{slug}_betweenness_top10.csv")
    choke_points_df.to_csv(p, index=False)
    paths["choke_points"] = p
    log.info(f"  Saved top-10 choke points -> {p}")

    return paths


# === 4. CLI ====================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from pedestrian_nodes import LOCATIONS, load_graph
    from friction_weighting import add_friction_weights
    from planned_vs_actual import random_od_pairs, add_planned_weights, compare_od_pairs

    N_PAIRS = 200
    SEED = 42

    # Betweenness scope decision (see module docstring) — recorded here, not
    # silently applied: exact for the three smaller graphs, k-sample
    # approximation for the two largest (~12.5k and ~15.2k nodes).
    BETWEENNESS_K = {
        "Maastricht": 500,
        "Matosinhos": 500,
        "Sabanci_University": None,
        "Lanaken": None,
        "Mindelo": None,
    }

    all_summaries = []

    for loc_name in LOCATIONS:
        G = load_graph(loc_name)
        if G is None:
            log.warning(f"No cached graph for '{loc_name}' — run pedestrian_nodes.py first. Skipping.")
            continue

        print(f"\n{'=' * 60}\n  {loc_name}\n{'=' * 60}")

        add_friction_weights(G, name=loc_name)
        add_planned_weights(G)

        # --- Circuity: reuse #002's exact O-D sampling ---
        od_pairs = random_od_pairs(G, n=N_PAIRS, seed=SEED)
        comparison_df = compare_od_pairs(G, od_pairs, city=loc_name)
        circuity_df = compute_circuity(G, comparison_df)
        grid_df = circuity_by_grid_cell(G, circuity_df)

        log.info(
            f"  Circuity ({len(circuity_df)} pairs): mean={circuity_df['circuity'].mean():.3f} "
            f"median={circuity_df['circuity'].median():.3f} max={circuity_df['circuity'].max():.3f}"
        )
        log.info(f"  Grid cells with data: {len(grid_df)}")

        # --- Betweenness centrality ---
        k = BETWEENNESS_K.get(loc_name)
        centrality, method = compute_betweenness(G, k=k, seed=SEED)
        choke_points_df = top_choke_points(G, centrality, n=10)
        choke_points_df["betweenness_method"] = method
        log.info(f"  Top choke point: node {choke_points_df.iloc[0]['node']} "
                 f"(betweenness={choke_points_df.iloc[0]['betweenness']:.5f})")

        export_connectivity_outputs(circuity_df, grid_df, choke_points_df, loc_name)

        all_summaries.append({
            "city": loc_name,
            "n_pairs": len(circuity_df),
            "mean_circuity": circuity_df["circuity"].mean(),
            "median_circuity": circuity_df["circuity"].median(),
            "max_circuity": circuity_df["circuity"].max(),
            "betweenness_method": method,
            "top_choke_node": choke_points_df.iloc[0]["node"],
            "top_choke_betweenness": choke_points_df.iloc[0]["betweenness"],
        })

    if all_summaries:
        combined = pd.DataFrame(all_summaries)
        out_path = "locations/all_locations_connectivity_summary.csv"
        combined.to_csv(out_path, index=False)
        print(f"\nAll-location connectivity summary saved -> {out_path}")
        print(combined.to_string(index=False))
