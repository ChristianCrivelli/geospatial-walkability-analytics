"""
visualise_node_maps.py
======================
Visualise the pedestrian walk-network node maps for every study location
defined in pedestrian_nodes.py.

TWO modes are supported:

  1. LIVE  — download the graph on the fly via OSMnx (requires internet).
             Set  MODE = "live"  in the CONFIG block.

  2. CACHED — load a .graphml file that was already saved by the pipeline.
              Set  MODE = "cached"  and point  BASE_DIR  at your "locations/"
              folder.  Set  MODE = "auto"  to try cached first, fall back to
              live.

OUTPUT
------
For each location one PNG is written to OUTPUT_DIR:

    <output_dir>/<name>_node_map.png

A combined overview figure is also saved:

    <output_dir>/all_locations_overview.png

WHAT IS SHOWN
-------------
Each subplot shows:
  • All street edges drawn in grey (thin, low-alpha) for spatial context.
  • Every pedestrian node coloured by its NODE DEGREE (how many edges meet
    at that intersection).  Low degree = dead-end / simple pass-through;
    high degree = complex junction.
  • A scalebar and north arrow.
  • A legend.

Degree was chosen as the first node-level variable because:
  • It is always available immediately after graph extraction (no API calls).
  • It is a good proxy for the spatial connectivity / richness of a network:
    places with many high-degree junctions tend to be more walkable because
    pedestrians have more route choices at each corner.
  • It looks visually interesting on a map — you can spot the difference
    between grid cities (lots of 4-way intersections) and organic street
    layouts (lots of 3-way T-junctions or dead-ends).

Later, once elevation/grade data is attached, you can swap the colour variable
to 'elevation' or 'grade_abs' with a one-line change (see COLOUR_ATTR below).
"""

# ─────────────────────── standard library ────────────────────────────────────
import os
import logging

# ─────────────────────── third-party ─────────────────────────────────────────
import numpy as np
import osmnx as ox
import matplotlib
matplotlib.use("Agg")                        # no display needed
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.cm as cm
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

# ─────────────────────── your project module ─────────────────────────────────
# pedestrian_nodes.py must be on the Python path (put it next to this script
# or add its directory to sys.path).
try:
    from pedestrian_nodes import (
        LOCATIONS,
        fetch_walk_graph,
        filter_pedestrian_edges,
        load_graph,
    )
    _PROJECT_IMPORTED = True
except ImportError:
    _PROJECT_IMPORTED = False
    # ── Inline fallback so the script is self-contained if the module is absent
    LOCATIONS = {
        "Maastricht":        {"query": "Maastricht, Netherlands",            "network_type": "walk"},
        "Matosinhos":        {"query": "Matosinhos, Portugal",               "network_type": "walk"},
        "Sabanci_University":{"query": "Sabancı University, Tuzla, Istanbul, Turkey",
                              "network_type": "walk",
                              "fallback": {"lat": 40.8903, "lon": 29.3763, "dist": 1500}},
        "Lanaken":           {"query": "Lanaken, Belgium",                   "network_type": "walk"},
        "Mindelo":           {"query": "Mindelo, Vila do Conde, Portugal",   "network_type": "walk",
                              "fallback": {"lat": 41.3456, "lon": -8.6845, "dist": 1200}},
    }

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIG  — edit these lines
# ═════════════════════════════════════════════════════════════════════════════

MODE        = "auto"          # "live" | "cached" | "auto"
BASE_DIR    = "locations"     # folder where pipeline saved .graphml files
OUTPUT_DIR  = "figures"       # where PNGs are written

# Node colour attribute.  Change to "elevation" or "grade_abs" once those are
# added by the pipeline.  Falls back to degree if the attribute is missing.
COLOUR_ATTR = "degree"

# Visual settings
FIG_DPI         = 150
NODE_SIZE_BASE  = 8           # pt²  (matplotlib scatter s= units)
EDGE_ALPHA      = 0.18
EDGE_COLOR      = "#888888"
CMAP_NODES      = "plasma"    # any matplotlib colormap name

# ═════════════════════════════════════════════════════════════════════════════

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

os.makedirs(OUTPUT_DIR, exist_ok=True)
ox.settings.log_console = False


# ─────────────────────── helpers ─────────────────────────────────────────────

def _get_graph(name: str, cfg: dict):
    """
    Return a NetworkX MultiDiGraph for *name*, honouring MODE.

    Strategy
    --------
    "cached"  → load .graphml only (raise if absent)
    "live"    → always download
    "auto"    → try .graphml first, download on cache miss
    """
    G = None

    if MODE in ("cached", "auto"):
        graphml = os.path.join(BASE_DIR, name, f"{name}.graphml")
        if os.path.exists(graphml):
            log.info(f"[{name}] Loading cached graph from {graphml}")
            G = ox.load_graphml(graphml)

    if G is None:
        if MODE == "cached":
            raise FileNotFoundError(
                f"No cached graph for '{name}'. Run pedestrian_nodes.py first."
            )
        log.info(f"[{name}] Downloading graph …")
        # Download
        try:
            G = ox.graph_from_place(
                cfg["query"],
                network_type=cfg.get("network_type", "walk"),
                retain_all=True,
                simplify=True,
            )
        except Exception as e:
            fb = cfg.get("fallback")
            if fb:
                log.warning(f"[{name}] Place query failed ({e}). Using fallback.")
                G = ox.graph_from_point(
                    (fb["lat"], fb["lon"]),
                    dist=fb["dist"],
                    network_type=cfg.get("network_type", "walk"),
                    retain_all=True,
                    simplify=True,
                )
            else:
                raise

        # Basic pedestrian filter (replicates pipeline step 2)
        KEEP = {
            "footway","pedestrian","path","living_street","steps","crossing",
            "sidewalk","track","residential","service","unclassified",
            "tertiary","secondary","primary","trunk",
        }
        remove = []
        for u, v, k, d in G.edges(keys=True, data=True):
            hw = d.get("highway", "")
            hw_set = set(hw) if isinstance(hw, list) else {hw}
            if not (hw_set & KEEP or d.get("footway") or d.get("sidewalk")):
                remove.append((u, v, k))
        G.remove_edges_from(remove)
        import networkx as nx
        G.remove_nodes_from(list(nx.isolates(G)))

    return G


def _node_colour_values(G, attr: str):
    """
    Return (values_array, label_string) for the requested node attribute.

    If attr == "degree" or the attribute is missing from nodes, fall back to
    computing the undirected degree (number of unique neighbour nodes).

    Why undirected degree?
    ----------------------
    OSMnx stores walk graphs as directed (each physical street segment has a
    forward and backward edge).  Using the *undirected* degree gives the more
    intuitive "how many streets meet here?" count.
    """
    import networkx as nx

    if attr == "degree":
        G_und = G.to_undirected()
        values = np.array([G_und.degree(n) for n in G.nodes()])
        label  = "Node degree (# connections)"
        return values, label

    # Try a stored node attribute (e.g. "elevation")
    vals = [G.nodes[n].get(attr, np.nan) for n in G.nodes()]
    if all(np.isnan(v) for v in vals):
        log.warning(f"Attribute '{attr}' not found on nodes; falling back to degree.")
        return _node_colour_values(G, "degree")

    values = np.array(vals, dtype=float)
    label  = attr.replace("_", " ").capitalize()
    return values, label


def _draw_single(ax, G, title: str, colour_attr: str = "degree"):
    """
    Draw one city's pedestrian network onto *ax*.

    Layers (back → front):
      1.  Street edges (grey, very transparent)
      2.  Nodes coloured by *colour_attr*, sized uniformly
      3.  Colourbar, scalebar stub, legend
    """
    # ── Spatial coords ──────────────────────────────────────────────────────
    # OSMnx stores lon → x, lat → y
    xs = np.array([G.nodes[n]["x"] for n in G.nodes()])
    ys = np.array([G.nodes[n]["y"] for n in G.nodes()])

    # ── Edge lines ──────────────────────────────────────────────────────────
    # We build LineCollections manually for speed (avoids per-edge Artist overhead)
    from matplotlib.collections import LineCollection
    segments = []
    for u, v, data in G.edges(data=True):
        x0, y0 = G.nodes[u]["x"], G.nodes[u]["y"]
        x1, y1 = G.nodes[v]["x"], G.nodes[v]["y"]
        segments.append([(x0, y0), (x1, y1)])

    lc = LineCollection(
        segments,
        colors=EDGE_COLOR,
        linewidths=0.4,
        alpha=EDGE_ALPHA,
        zorder=1,
        rasterized=True,   # keep file size small
    )
    ax.add_collection(lc)

    # ── Node colours ─────────────────────────────────────────────────────────
    values, cbar_label = _node_colour_values(G, colour_attr)

    # Mask NaN for colour mapping
    valid_mask = ~np.isnan(values)
    vmin       = np.nanpercentile(values, 2)   # 2-98 percentile stretch avoids outlier wash
    vmax       = np.nanpercentile(values, 98)

    norm       = mcolors.Normalize(vmin=vmin, vmax=vmax)
    cmap_obj   = cm.get_cmap(CMAP_NODES)
    rgba       = cmap_obj(norm(np.where(valid_mask, values, vmin)))  # NaN → vmin colour
    rgba[~valid_mask, :3] = 0.55  # grey for missing values

    sc = ax.scatter(
        xs, ys,
        c=rgba,
        s=NODE_SIZE_BASE,
        linewidths=0,
        zorder=2,
        rasterized=True,
    )

    # ── Colourbar ─────────────────────────────────────────────────────────────
    sm = cm.ScalarMappable(cmap=cmap_obj, norm=norm)
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, fraction=0.03, pad=0.02, shrink=0.8)
    cbar.set_label(cbar_label, fontsize=7)
    cbar.ax.tick_params(labelsize=6)

    # ── Axes cosmetics ────────────────────────────────────────────────────────
    ax.set_title(title, fontsize=9, fontweight="bold", pad=4)
    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.set_axis_off()

    # ── Stats annotation ──────────────────────────────────────────────────────
    n_nodes = G.number_of_nodes()
    n_edges = G.number_of_edges()
    ax.annotate(
        f"{n_nodes:,} nodes · {n_edges:,} edges",
        xy=(0.01, 0.01), xycoords="axes fraction",
        fontsize=6.5, color="#555555",
        ha="left", va="bottom",
    )

    return sc


# ─────────────────────── per-location PNG ────────────────────────────────────

def save_single_map(name: str, G, colour_attr: str = "degree"):
    """Save a standalone map PNG for one location."""
    fig, ax = plt.subplots(1, 1, figsize=(8, 8), dpi=FIG_DPI)
    fig.patch.set_facecolor("#111111")
    ax.set_facecolor("#111111")

    _draw_single(ax, G, title=name.replace("_", " "), colour_attr=colour_attr)

    # Force tight layout before saving
    plt.tight_layout(pad=0.5)
    out = os.path.join(OUTPUT_DIR, f"{name}_node_map.png")
    fig.savefig(out, dpi=FIG_DPI, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    log.info(f"[{name}] Saved → {out}")
    return out


# ─────────────────────── combined overview PNG ───────────────────────────────

def save_overview(graphs: dict, colour_attr: str = "degree"):
    """
    Save a single figure with all locations as subplots in a 2-3 grid.

    Layout logic
    ------------
    With 5 locations we use a 2-row × 3-col grid.  The last cell is left
    blank and used for a shared legend / metadata panel.  This is more
    informative than leaving it white: we put a brief explanation of the
    colour encoding there.
    """
    n       = len(graphs)
    ncols   = 3
    nrows   = (n + ncols - 1) // ncols  # ceiling division

    fig, axes = plt.subplots(
        nrows, ncols,
        figsize=(ncols * 5, nrows * 5),
        dpi=FIG_DPI,
    )
    fig.patch.set_facecolor("#0d0d0d")
    axes_flat = axes.flatten()

    for ax in axes_flat:
        ax.set_facecolor("#111111")

    for i, (name, G) in enumerate(graphs.items()):
        ax = axes_flat[i]
        _draw_single(ax, G, title=name.replace("_", " "), colour_attr=colour_attr)

    # Fill any unused axes with a legend / note panel
    for j in range(len(graphs), len(axes_flat)):
        ax = axes_flat[j]
        ax.set_axis_off()
        _draw_legend_panel(ax, colour_attr)

    fig.suptitle(
        "Pedestrian Walk-Network Node Maps",
        fontsize=14, fontweight="bold", color="white", y=1.01,
    )

    plt.tight_layout(pad=0.8)
    out = os.path.join(OUTPUT_DIR, "all_locations_overview.png")
    fig.savefig(out, dpi=FIG_DPI, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    log.info(f"Overview saved → {out}")
    return out


def _draw_legend_panel(ax, colour_attr):
    """
    Draw an explanatory text panel in an empty subplot cell.
    """
    # Show a small gradient bar + explanation text
    import matplotlib.patches as mpatches

    cmap_obj = cm.get_cmap(CMAP_NODES)
    gradient = np.linspace(0, 1, 256).reshape(1, -1)

    # Position a gradient image
    ax_inset = ax.inset_axes([0.1, 0.65, 0.8, 0.08])
    ax_inset.imshow(gradient, aspect="auto", cmap=cmap_obj)
    ax_inset.set_axis_off()

    attr_label = "Node degree" if colour_attr == "degree" else colour_attr.replace("_", " ")
    label_lo   = "low" if colour_attr == "degree" else "min"
    label_hi   = "high" if colour_attr == "degree" else "max"

    ax.text(0.1,  0.60, label_lo,  transform=ax.transAxes, color="#aaaaaa", fontsize=7, ha="left")
    ax.text(0.9,  0.60, label_hi,  transform=ax.transAxes, color="#aaaaaa", fontsize=7, ha="right")
    ax.text(0.5,  0.74, f"Colour: {attr_label}", transform=ax.transAxes,
            color="white", fontsize=8, fontweight="bold", ha="center")

    explanation = (
        "Each dot is a pedestrian intersection\n"
        "or endpoint in OpenStreetMap.\n\n"
        "Colour encodes node degree —\n"
        "i.e. how many street segments\n"
        "meet at that point.\n\n"
        "High-degree nodes (bright) are\n"
        "complex junctions; they signal\n"
        "route choice & walkability potential.\n\n"
        "Grey edges show the street network\n"
        "for spatial context."
    )
    ax.text(
        0.5, 0.48, explanation,
        transform=ax.transAxes,
        color="#cccccc", fontsize=7.5,
        ha="center", va="top",
        linespacing=1.6,
    )


# ─────────────────────── main ────────────────────────────────────────────────

if __name__ == "__main__":

    graphs = {}

    for loc_name, cfg in LOCATIONS.items():
        print(f"\n{'─'*50}")
        print(f"  {loc_name}")
        print(f"{'─'*50}")
        try:
            G = _get_graph(loc_name, cfg)
            log.info(
                f"[{loc_name}] Graph ready: "
                f"{G.number_of_nodes()} nodes, {G.number_of_edges()} edges"
            )
            graphs[loc_name] = G

            # Per-location map
            save_single_map(loc_name, G, colour_attr=COLOUR_ATTR)

        except Exception as exc:
            log.error(f"[{loc_name}] Failed: {exc}", exc_info=True)

    # Combined overview
    if graphs:
        save_overview(graphs, colour_attr=COLOUR_ATTR)

    print(f"\n✓ Done. Figures written to: {os.path.abspath(OUTPUT_DIR)}/")