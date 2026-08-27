import os
import time
import logging
import requests
import numpy as np
import osmnx as ox
import pandas as pd
import networkx as nx
from typing import Optional, Union

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── OSMnx global settings ────────────────────────────────────────────────────
ox.settings.log_console = False
ox.settings.useful_tags_way = list(
    set(ox.settings.useful_tags_way)
    | {
        "sidewalk",
        "sidewalk:left",
        "sidewalk:right",
        "sidewalk:both",
        "footway",
        "crossing",
        "surface",
        "smoothness",
        "incline",
        "lit",
        "tactile_paving",
        "wheelchair",
        "kerb",
        "width",
        "maxspeed",
    }
)

# ── Study locations ───────────────────────────────────────────────────────────
LOCATIONS = {
    "Maastricht": {
        "query": "Maastricht, Netherlands",
        "network_type": "walk",
    },
    "Matosinhos": {
        "query": "Matosinhos, Portugal",
        "network_type": "walk",
    },
    "Sabanci_University": {
        "query": "Sabancı University, Tuzla, Istanbul, Turkey",
        "network_type": "walk",
        # campus is small — use a point+distance fallback if place query fails
        "fallback": {"lat": 40.8903, "lon": 29.3763, "dist": 1500},
    },
    "Lanaken": {
        "query": "Lanaken, Belgium",
        "network_type": "walk",
    },
    "Mindelo": {
        "query": "Mindelo, Vila do Conde, Portugal",
        "network_type": "walk",
        # Small parish — point+distance fallback in case Nominatim doesn't resolve it
        "fallback": {"lat": 41.3456, "lon": -8.6845, "dist": 1200},
    },
}


# === 1. NETWORK EXTRACTION ===

def fetch_walk_graph(
    query: str,
    network_type: str = "walk",
    retain_all: bool = True,
    simplify: bool = True,
    fallback: Optional[dict] = None,
) -> nx.MultiDiGraph:
    """
    Download the pedestrian OSM graph for a named place.

    Parameters
    ----------
    query        : OSMnx place query string  e.g. "Maastricht, Netherlands"
    network_type : OSMnx network type — keep 'walk' for pedestrian networks
    retain_all   : keep all graph components (important for campus / island graphs)
    simplify     : consolidate nodes (recommended)
    fallback     : dict with keys lat, lon, dist — used when place query fails
                   (useful for university campuses not in OSM Nominatim)

    Returns
    -------
    G : MultiDiGraph with pedestrian edges and extended OSM tags
    """
    log.info(f"Fetching walk graph for: {query}")
    try:
        G = ox.graph_from_place(
            query,
            network_type=network_type,
            retain_all=retain_all,
            simplify=simplify,
        )
    except Exception as e:
        if fallback:
            log.warning(f"Place query failed ({e}). Using point+distance fallback.")
            G = ox.graph_from_point(
                (fallback["lat"], fallback["lon"]),
                dist=fallback["dist"],
                network_type=network_type,
                retain_all=retain_all,
                simplify=simplify,
            )
        else:
            raise

    log.info(
        f"  → {G.number_of_nodes()} nodes | {G.number_of_edges()} edges"
    )
    return G


# === 2. INFRASTRUCTURE FILTERING === 

# OSM highway values that represent pedestrian-accessible infrastructure
PEDESTRIAN_HIGHWAY_VALUES = {
    "footway",
    "pedestrian",
    "path",
    "living_street",
    "steps",
    "crossing",
    "sidewalk",
    "track",
    "residential",        # usually has footpaths
    "service",
    "unclassified",
    "tertiary",
    "secondary",
    "primary",
    "trunk",
    # motorway / motorway_link excluded intentionally
}


def filter_pedestrian_edges(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """
    Remove edges that are not accessible to pedestrians.

    Strategy
    --------
    - Keep edges whose 'highway' value is in PEDESTRIAN_HIGHWAY_VALUES.
    - Always keep edges that have an explicit 'footway' or 'sidewalk' tag.
    - Remove isolated nodes left behind after pruning.
    """
    log.info("Filtering pedestrian edges …")

    edges_to_remove = []
    for u, v, k, data in G.edges(keys=True, data=True):
        highway = data.get("highway", "")
        # highway can be a list (multiple values in OSM)
        if isinstance(highway, list):
            hw_set = set(highway)
        else:
            hw_set = {highway}

        has_footway_tag = bool(data.get("footway") or data.get("sidewalk"))
        in_pedestrian_set = bool(hw_set & PEDESTRIAN_HIGHWAY_VALUES)

        if not (in_pedestrian_set or has_footway_tag):
            edges_to_remove.append((u, v, k))

    G.remove_edges_from(edges_to_remove)

    # Remove nodes with no remaining edges
    isolated = list(nx.isolates(G))
    G.remove_nodes_from(isolated)

    log.info(
        f"  → After filter: {G.number_of_nodes()} nodes | {G.number_of_edges()} edges "
        f"(removed {len(edges_to_remove)} edges, {len(isolated)} isolated nodes)"
    )
    return G

# === 3. ELEVATION INTEGRATION === 

# ── 3a. Open-Elevation (free, no API key) ───

OPEN_ELEVATION_URL = "https://api.open-elevation.com/api/v1/lookup"
OPEN_ELEVATION_BATCH = 500   # max locations per request


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def get_elevation_open_elevation(
    coords: list[tuple[float, float]],
    batch_size: int = OPEN_ELEVATION_BATCH,
    pause: float = 1.0,
) -> list[float]:
    """
    Query Open-Elevation for a list of (lat, lon) pairs.
    Returns a list of elevation values in metres (same order as input).
    Falls back to NaN on failure.
    """
    elevations = []
    for chunk in _chunks(coords, batch_size):
        payload = {"locations": [{"latitude": lat, "longitude": lon} for lat, lon in chunk]}
        try:
            r = requests.post(OPEN_ELEVATION_URL, json=payload, timeout=30)
            r.raise_for_status()
            results = r.json()["results"]
            elevations.extend([pt["elevation"] for pt in results])
        except Exception as e:
            log.warning(f"Open-Elevation request failed: {e}. Filling with NaN.")
            elevations.extend([float("nan")] * len(chunk))
        time.sleep(pause)
    return elevations


# ── 3b. Google Maps Elevation API ──-

GOOGLE_ELEVATION_URL = "https://maps.googleapis.com/maps/api/elevation/json"
GOOGLE_ELEVATION_BATCH = 512   # max per request (Google limit)


def get_elevation_google(
    coords: list[tuple[float, float]],
    api_key: str,
    batch_size: int = GOOGLE_ELEVATION_BATCH,
    pause: float = 0.1,
) -> list[float]:
    """
    Query Google Maps Elevation API for a list of (lat, lon) pairs.
    Requires a valid API key with the Elevation API enabled.
    Returns elevations in metres (same order as input).
    """
    elevations = []
    for chunk in _chunks(coords, batch_size):
        locations = "|".join(f"{lat},{lon}" for lat, lon in chunk)
        params = {"locations": locations, "key": api_key}
        try:
            r = requests.get(GOOGLE_ELEVATION_URL, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            if data["status"] != "OK":
                raise ValueError(f"Google Elevation API status: {data['status']}")
            elevations.extend([pt["elevation"] for pt in data["results"]])
        except Exception as e:
            log.warning(f"Google Elevation request failed: {e}. Filling with NaN.")
            elevations.extend([float("nan")] * len(chunk))
        time.sleep(pause)
    return elevations


# ── 3c. Attach elevations to graph nodes ───

def add_elevation_to_graph(
    G: nx.MultiDiGraph,
    provider: str = "open-elevation",
    google_api_key: Optional[str] = None,
) -> nx.MultiDiGraph:
    """
    Attach elevation (Z) coordinates to every node in G.

    Parameters
    ----------
    G               : the pedestrian walk graph
    provider        : 'open-elevation' (free) or 'google' (requires API key)
    google_api_key  : required when provider='google'

    Node attributes added
    ---------------------
    elevation : float  metres above sea level (NaN if query failed)
    """
    nodes = list(G.nodes(data=True))
    coords = [(data["y"], data["x"]) for _, data in nodes]   # (lat, lon)

    log.info(f"Fetching elevation for {len(coords)} nodes via '{provider}' …")

    if provider == "google":
        if not google_api_key:
            raise ValueError("google_api_key is required for provider='google'")
        elevations = get_elevation_google(coords, google_api_key)
    else:
        elevations = get_elevation_open_elevation(coords)

    for (node_id, _), elev in zip(nodes, elevations):
        G.nodes[node_id]["elevation"] = elev

    n_missing = sum(1 for e in elevations if np.isnan(e))
    log.info(f"  → Elevation attached. Missing: {n_missing}/{len(elevations)}")
    return G


# === 4. GRADE (SLOPE) CALCULATION === 

def add_grade_to_edges(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """
    Compute grade (rise/run) for every directed edge and attach as attributes.

    Edge attributes added
    ---------------------
    grade        : float  rise/run  (positive = uphill, negative = downhill)
    grade_abs    : float  |grade|   (for undirected slope analysis)
    elevation_change : float  metres  (end_elev − start_elev)
    """
    log.info("Computing edge grades …")
    grades, grades_abs, elev_changes = [], [], []

    for u, v, data in G.edges(data=True):
        z_u = G.nodes[u].get("elevation", float("nan"))
        z_v = G.nodes[v].get("elevation", float("nan"))
        length = data.get("length", 0)

        if np.isnan(z_u) or np.isnan(z_v) or length == 0:
            grade = float("nan")
        else:
            dz = z_v - z_u
            grade = dz / length

        grades.append(grade)
        grades_abs.append(abs(grade) if not np.isnan(grade) else float("nan"))
        elev_changes.append(
            z_v - z_u if not (np.isnan(z_u) or np.isnan(z_v)) else float("nan")
        )

    edge_keys = list(G.edges(keys=False))
    for (u, v), g, ga, dz in zip(edge_keys, grades, grades_abs, elev_changes):
        # update all parallel edges between u→v
        for k in G[u][v]:
            G[u][v][k]["grade"] = g
            G[u][v][k]["grade_abs"] = ga
            G[u][v][k]["elevation_change"] = dz

    valid = [g for g in grades if not np.isnan(g)]
    if valid:
        log.info(
            f"  → Grade stats — mean: {np.mean(valid):.4f} | "
            f"max_abs: {max(abs(g) for g in valid):.4f} | "
            f"missing: {len(grades) - len(valid)}"
        )
    return G


# === 5. EXPORT === 

def export_graph(G: nx.MultiDiGraph, name: str, base_dir: str = "locations") -> dict:
    """
    Save the enriched graph for one location into:

        locations/
        └── <name>/
            ├── <name>.graphml        <- full graph with all attributes
            ├── <name>.gpkg           <- GIS-ready GeoPackage (nodes + edges)
            ├── <name>_nodes.csv      <- node table (no geometry)
            └── <name>_edges.csv      <- edge table (no geometry)

    Returns a dict of file paths.
    """
    slug = name.replace(" ", "_")
    loc_dir = os.path.join(base_dir, slug)
    os.makedirs(loc_dir, exist_ok=True)
    paths = {}

    # GraphML — preserves every node/edge attribute for reloading
    graphml_path = os.path.join(loc_dir, f"{slug}.graphml")
    ox.save_graphml(G, graphml_path)
    paths["graphml"] = graphml_path
    log.info(f"  Saved GraphML  -> {graphml_path}")

    # GeoPackage — GIS-ready (QGIS, ArcGIS, GeoPandas)
    gpkg_path = os.path.join(loc_dir, f"{slug}.gpkg")
    ox.save_graph_geopackage(G, filepath=gpkg_path, directed=True)
    paths["gpkg"] = gpkg_path
    log.info(f"  Saved GeoPackage -> {gpkg_path}")

    # CSV tables (geometry dropped for plain tabular use)
    nodes_gdf, edges_gdf = ox.graph_to_gdfs(G)
    nodes_csv = os.path.join(loc_dir, f"{slug}_nodes.csv")
    edges_csv = os.path.join(loc_dir, f"{slug}_edges.csv")
    nodes_gdf.drop(columns="geometry").to_csv(nodes_csv)
    edges_gdf.drop(columns="geometry").to_csv(edges_csv)
    paths["nodes_csv"] = nodes_csv
    paths["edges_csv"] = edges_csv
    log.info(f"  Saved nodes CSV -> {nodes_csv}")
    log.info(f"  Saved edges CSV -> {edges_csv}")

    return paths


def load_graph(name: str, base_dir: str = "locations") -> Optional[nx.MultiDiGraph]:
    slug = name.replace(" ", "_")
    graphml_path = os.path.join(base_dir, slug, f"{slug}.graphml")
    if not os.path.exists(graphml_path):
        return None
    log.info(f"Loading cached graph for '{name}' from {graphml_path}")
    return ox.load_graphml(graphml_path)


#  === 6. ONE-STOP PIPELINE FUNCTION === 

def build_pedestrian_graph(
    query: str,
    name: Optional[str] = None,
    network_type: str = "walk",
    retain_all: bool = True,
    simplify: bool = True,
    fallback: Optional[dict] = None,
    elevation_provider: str = "open-elevation",
    google_api_key: Optional[str] = None,
    filter_edges: bool = True,
    base_dir: str = "locations",
) -> nx.MultiDiGraph:
    
    label = name or query

    # 1. Fetch raw walk graph
    G = fetch_walk_graph(query, network_type, retain_all, simplify, fallback)

    # 2. Filter to pedestrian infrastructure
    if filter_edges:
        G = filter_pedestrian_edges(G)

    # 3. Attach elevation
    G = add_elevation_to_graph(G, provider=elevation_provider, google_api_key=google_api_key)

    # 4. Compute grade on each edge
    G = add_grade_to_edges(G)

    # 5. Save graph to disk (always, so it can be reloaded next run)
    export_graph(G, label, base_dir)

    log.info(f"Pipeline complete for '{label}'.")
    return G


# === 7. QUICK SUMMARY HELPER === 

def summarise_graph(G: nx.MultiDiGraph, name: str = "") -> pd.DataFrame:
    """
    Print and return a summary DataFrame of node/edge statistics.
    """
    nodes_gdf, edges_gdf = ox.graph_to_gdfs(G)

    summary = {
        "location": name,
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "total_length_km": edges_gdf["length"].sum() / 1000,
        "avg_edge_length_m": edges_gdf["length"].mean(),
        "elev_min_m": nodes_gdf["elevation"].min() if "elevation" in nodes_gdf else None,
        "elev_max_m": nodes_gdf["elevation"].max() if "elevation" in nodes_gdf else None,
        "elev_mean_m": nodes_gdf["elevation"].mean() if "elevation" in nodes_gdf else None,
        "grade_mean_abs": edges_gdf["grade_abs"].mean() if "grade_abs" in edges_gdf else None,
        "grade_max_abs": edges_gdf["grade_abs"].max() if "grade_abs" in edges_gdf else None,
    }

    df = pd.DataFrame([summary])
    print(f"\n{'='*60}")
    print(f"  Summary: {name}")
    print(f"{'='*60}")
    for k, v in summary.items():
        if k == "location":
            continue
        if isinstance(v, float):
            print(f"  {k:<25} {v:.3f}")
        else:
            print(f"  {k:<25} {v}")
    return df


# === 8. MAIN — run all five study locations === 

if __name__ == "__main__":
    # ── Configuration ────────────────────────────────────────────────────────
    ELEVATION_PROVIDER = "open-elevation"   # change to "google" if you have a key
    GOOGLE_API_KEY = os.getenv("GOOGLE_ELEVATION_API_KEY", None)
    BASE_DIR = "locations"

    all_summaries = []

    for loc_name, cfg in LOCATIONS.items():
        print(f"\n{'#'*60}")
        print(f"  Processing: {loc_name}")
        print(f"{'#'*60}")

        try:
            # Re-use saved graph if it already exists
            G = load_graph(loc_name, BASE_DIR)
            if G is not None:
                log.info(f"  Cache hit — skipping download for {loc_name}")
            else:
                G = build_pedestrian_graph(
                    query=cfg["query"],
                    name=loc_name,
                    network_type=cfg.get("network_type", "walk"),
                    fallback=cfg.get("fallback"),
                    elevation_provider=ELEVATION_PROVIDER,
                    google_api_key=GOOGLE_API_KEY,
                    filter_edges=True,
                    base_dir=BASE_DIR,
                )

            summary_df = summarise_graph(G, loc_name)
            all_summaries.append(summary_df)

        except Exception as exc:
            log.error(f"Failed to process {loc_name}: {exc}", exc_info=True)

    # Combined summary table
    if all_summaries:
        combined = pd.concat(all_summaries, ignore_index=True)
        summary_path = os.path.join(BASE_DIR, "all_locations_summary.csv")
        os.makedirs(BASE_DIR, exist_ok=True)
        combined.to_csv(summary_path, index=False)
        print(f"\nAll-location summary saved → {summary_path}")
        print(combined.to_string(index=False))