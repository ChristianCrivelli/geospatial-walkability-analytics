"""
personal_path_validation.py
============================
Phase 2 (running alongside it) / issue #5: anchor the Planned-vs-Actual
comparison in real, lived routes instead of only random O-D samples.

The 5 pairs below are the user's own routes, supplied in chat (2026-08-26)
as a mix of raw coordinates and named places/addresses. Named places were
resolved to coordinates via web search + Nominatim-style lookups (the
sandbox's direct network access to nominatim.openstreetmap.org is blocked,
so this was done by hand via WebSearch/WebFetch — not reproducible by
re-running this script alone; the resolved coordinates are hardcoded in
PERSONAL_PATHS with a `source` note per point) and, where possible, cross-
checked directly against each city's own cached graph (matching OSM `name`
tags on edges) rather than trusted from external geocoding alone.

DATA-QUALITY CAVEATS (see REPORT.md for the full discussion — these are
real, substantive limitations, not implementation footnotes):

  - Lanaken -> Maasmechelen (LIDL, Koninginnelaan 141): Maasmechelen's
    centre (~50.9655, 5.6945) is genuinely outside the cached Lanaken
    graph's extent (max lat 50.9420 — about 2.5-3km short). Per the user's
    choice, this pair is run as a "partial route to the network edge":
    `nearest_nodes` snaps the destination to whatever node is closest
    within the Lanaken graph (near its NE boundary, toward Maasmechelen),
    and the straight-line gap from that snapped node to the real LIDL is
    reported explicitly. This is NOT a real validation of that walk — it's
    "how far does the model's own network reach in that direction."
  - Matosinhos -> Castelo do Queijo Basketball Hoops: best available
    coordinate for this landmark (~41.16803, -8.68886) sits ~390m south of
    the Matosinhos graph's boundary — the fort/park is technically in
    Porto's Nevogilde parish, not Matosinhos, per Wikipedia. Per the
    user's choice, used anyway (nearest node within Matosinhos), with the
    ~390m gap reported per-pair rather than hidden.
  - Sabancı -> Sport Center: no verified precise address/coordinate was
    found via web search (the campus itself has zero OSM name tags to
    cross-check against — consistent with #001-#004's finding that
    Sabancı is thinly tagged). Per the user's description ("the large
    building by the lake"), placed near campus centre as a best-effort
    estimate — NOT independently verified. Flagged as such in the output,
    not presented as precise.
  - Mindelo (Vila do Conde, Portugal — NOT Mindelo, Cape Verde; a mix-up
    caught before it propagated into any output) -> "the Beach": the user
    deferred to best judgment. Used "Rua da Praia" ("Beach Street"), the
    literal name match found directly in the graph (54 edges, well-
    connected) over the alternative candidate "Avenida Marginal (Norte)"
    (4 edges, a coastal avenue) — noted as an inference, not a given.

Reuses `add_friction_weights`, `add_planned_weights`, and `compare_route`
unchanged from #001/#002 — a real O-D pair is handled identically to a
sampled one once it's snapped to graph nodes; only the *sampling* method
differs (a human, not `random_od_pairs`).
"""

import logging

import networkx as nx
import pandas as pd

log = logging.getLogger(__name__)

# === 1. THE 5 PERSONAL PATHS =================================================
# Each point: (lat, lon, label, source_note). Coordinates already in the
# form the graphs use (WGS84 degrees).

PERSONAL_PATHS = [
    {
        "city": "Sabanci_University",
        "origin": (40.891389, 29.381472, "given coordinates (40°53'29.0\"N 29°22'53.3\"E)", "user-supplied"),
        "destination": (40.893220, 29.379530, "Sport Center", "UNVERIFIED — no precise address found via search; placed near campus centre per user's description ('the large building by the lake'), not independently confirmed"),
    },
    {
        "city": "Matosinhos",
        "origin": (41.181690, -8.689580, "Sergio Crivelli (Italian restaurant, Rua de Brito Capelo 705)", "web search, precise address"),
        "destination": (41.168030, -8.688860, "Castelo do Queijo Basketball Hoops", "web search — ~390m outside Matosinhos graph boundary, see module docstring"),
    },
    {
        "city": "Maastricht",
        "origin": (50.846470, 5.687250, "Maastricht University (Minderbroedersberg, main building)", "matched directly in graph (OSM name tag)"),
        "destination": (50.834530, 5.711290, "UM Sports (P. Debyeplein 15)", "matched directly in graph (OSM name tag)"),
    },
    {
        "city": "Lanaken",
        "origin": (50.882667, 5.669361, "given coordinates (50°52'57.6\"N 5°40'09.7\"E)", "user-supplied"),
        "destination": (50.965500, 5.694500, "LIDL, Koninginnelaan 141/bus 2, 3630 Maasmechelen", "web search — OUTSIDE Lanaken graph extent, see module docstring"),
    },
    {
        "city": "Mindelo",
        "origin": (41.315090, -8.735850, "Rua Dos Descobrimentos, 111", "matched directly in graph (OSM name tag; exact house number not independently verified)"),
        "destination": (41.310230, -8.736140, "\"the Beach\" -> inferred as Rua da Praia", "inferred — see module docstring"),
    },
]


# === 2. SNAPPING AND VALIDATION ==============================================


def snap_to_graph(G: nx.MultiDiGraph, lat: float, lon: float) -> tuple:
    """Snap a (lat, lon) point to its nearest graph node. Returns
    (node_id, snap_distance_m) — the caller decides whether that distance
    is small enough to trust."""
    import osmnx as ox

    node, dist_m = ox.distance.nearest_nodes(G, X=lon, Y=lat, return_dist=True)
    return node, dist_m


def validate_personal_path(G: nx.MultiDiGraph, pair: dict) -> dict:
    """
    Run one personal O-D pair through the same Planned-vs-Actual comparison
    #002 uses for sampled pairs (`compare_route`), after snapping both
    ends to the graph. Returns a result dict (or an error dict if no path
    exists between the snapped nodes — a real possibility for hand-picked
    points that #002's largest-SCC restriction was specifically designed
    to avoid).
    """
    from planned_vs_actual import compare_route
    from connectivity_metrics import great_circle_distance_m

    o_lat, o_lon, o_label, o_source = pair["origin"]
    d_lat, d_lon, d_label, d_source = pair["destination"]

    o_node, o_snap_dist = snap_to_graph(G, o_lat, o_lon)
    d_node, d_snap_dist = snap_to_graph(G, d_lat, d_lon)

    result = {
        "city": pair["city"],
        "origin_label": o_label,
        "origin_source": o_source,
        "origin_snap_dist_m": o_snap_dist,
        "destination_label": d_label,
        "destination_source": d_source,
        "destination_snap_dist_m": d_snap_dist,
    }

    comparison = compare_route(G, o_node, d_node)
    if comparison is None:
        result["status"] = "NO_PATH"
        return result

    result["status"] = "OK"
    result.update(comparison)

    # Great-circle circuity for this single pair, using the snapped nodes'
    # actual coordinates (same convention as #003's compute_circuity).
    gc = great_circle_distance_m(
        G.nodes[o_node]["y"], G.nodes[o_node]["x"],
        G.nodes[d_node]["y"], G.nodes[d_node]["x"],
    )
    result["great_circle_m"] = gc
    result["circuity"] = result["actual_length_m"] / gc if gc >= 1.0 else float("nan")

    return result


# === 3. EXPORT ================================================================


def export_results(df: pd.DataFrame, base_dir: str = "locations") -> str:
    import os

    out_path = os.path.join(base_dir, "personal_path_validation.csv")
    df.to_csv(out_path, index=False)
    log.info(f"Saved personal path validation -> {out_path}")
    return out_path


# === 4. CLI ====================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from pedestrian_nodes import load_graph
    from friction_weighting import add_friction_weights
    from planned_vs_actual import add_planned_weights

    rows = []
    for pair in PERSONAL_PATHS:
        city = pair["city"]
        print(f"\n{'=' * 60}\n  {city}\n{'=' * 60}")

        G = load_graph(city)
        if G is None:
            log.warning(f"No cached graph for '{city}' — skipping.")
            continue

        add_friction_weights(G, name=city)
        add_planned_weights(G)

        result = validate_personal_path(G, pair)
        rows.append(result)

        if result["status"] == "NO_PATH":
            log.warning(f"  NO PATH between snapped nodes for {city} — {result['origin_label']} -> {result['destination_label']}")
            continue

        log.info(f"  {result['origin_label']} (snap {result['origin_snap_dist_m']:.0f}m) -> {result['destination_label']} (snap {result['destination_snap_dist_m']:.0f}m)")
        log.info(f"  planned: {result['planned_length_m']:.0f}m  |  actual: {result['actual_length_m']:.0f}m  ({result['length_delta_pct']:+.1f}%)")
        log.info(f"  friction savings: {result['friction_savings_pct']:.1f}%  |  overlap: {result['overlap_pct']:.1f}%  |  circuity: {result['circuity']:.3f}")

    if rows:
        df = pd.DataFrame(rows)
        export_results(df)
        print("\nFull results:")
        print(df.to_string(index=False))
