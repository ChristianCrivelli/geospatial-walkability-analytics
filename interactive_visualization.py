"""
interactive_visualization.py
=============================
Phase 3 / issue #7: interactive (non-static) walkability maps — the
minimum-viable scope from the issue, per the decision recorded in
REPORT.md/ROADMAP.md: Folium-only, no dashboard. The final report leads
with the technical audience, and a self-contained per-city HTML map (no
server, no build step) is the artifact that fits that choice — cheap to
host in the repo or embed in the report, meets the issue's acceptance
criterion of "at least one interactive map artifact per city" without
taking on the stretch scope's added effort.

WHAT EACH MAP SHOWS
--------------------
1. Every pedestrian edge, coloured by `infra_tier` (from
   `friction_weighting.classify_infrastructure` — see that module and
   METHODOLOGY.md for the full tier definitions and penalty values). Tiers
   are an ORDERED severity scale (dedicated -> low -> moderate -> high
   traffic exposure without a sidewalk), so they're rendered as a single-
   hue ordinal ramp (light = easiest, dark = hardest), not unordered
   categorical hues — that's the accurate encoding for a variable with a
   real best-to-worst order, and it reads as a difficulty gradient at a
   glance. Edges additionally tagged with a steps penalty (stairs) are
   drawn dashed, on top of whatever base-tier colour they'd otherwise
   have — steps are a distinct kind of obstacle (impassable for a
   wheelchair user, not just "less pleasant"), not more of the same
   traffic-exposure variable, so it gets its own visual channel rather
   than folding into the colour ramp.
2. The top-10 betweenness "choke points" from issue #3, marked as circle
   callouts with a popup giving rank, betweenness score, and which method
   (exact vs. k-sample) produced it — same "record the method, don't hide
   it" principle #3 itself established.

WHERE THE DATA COMES FROM
--------------------------
Choke points are loaded directly from #3's already-exported
`<city>_betweenness_top10.csv` rather than recomputed — reusing a
validated result instead of risking a silent divergence from a second
computation, the same principle #4 applied when it reused #1-#3's outputs
rather than rerunning them. `infra_tier` is recomputed by calling
`add_friction_weights` on the freshly loaded base graph (cheap, no
network calls, no randomness) rather than trusting a second cached copy
of the same derived attribute.

OUTPUT
------
One self-contained HTML file per city:
    figures/interactive/<slug>_interactive_map.html
plus a plain index page linking all five:
    figures/interactive/index.html
"""

import logging
import os

import branca.element
import folium
import pandas as pd

log = logging.getLogger(__name__)

# === 1. COLOUR ENCODING ======================================================
# Ordinal, single-hue (blue) ramp: light = easiest tier, dark = hardest.
# Validated with the project's dataviz skill (validate_palette.js --ordinal):
# lightness monotone, adjacent steps >= 0.06 apart, light end clears the
# map-tile surface. Order matches friction_weighting.INFRA_PENALTY's own
# best-to-worst ordering, not chosen independently.
TIER_COLORS = {
    "dedicated": "#86b6ef",
    "low_traffic_no_sidewalk": "#3987e5",
    "moderate_traffic_no_sidewalk": "#1c5cab",
    "high_traffic_no_sidewalk": "#0d366b",
}
TIER_LABELS = {
    "dedicated": "Dedicated pedestrian infrastructure",
    "low_traffic_no_sidewalk": "Low-traffic road, no sidewalk",
    "moderate_traffic_no_sidewalk": "Moderate-traffic road, no sidewalk",
    "high_traffic_no_sidewalk": "High-traffic road, no sidewalk",
}
DEFAULT_COLOR = "#898781"   # unclassified fallback — should be rare/never
CHOKE_POINT_COLOR = "#d03b3b"  # status/critical red — a point of concern,
                                 # deliberately distinct from the tier ramp


def _split_tier(infra_tier: str) -> tuple[str, bool]:
    """'high_traffic_no_sidewalk+steps' -> ('high_traffic_no_sidewalk', True)."""
    if infra_tier.endswith("+steps"):
        return infra_tier[: -len("+steps")], True
    return infra_tier, False


# === 2. BUILD THE EDGE GEOJSON ===============================================


def _edge_coords(G, u, v, data) -> list:
    """Prefer the edge's stored curve geometry (OSMnx round-trips this
    through GraphML) over a straight line between endpoints, so bends in
    the real street aren't flattened."""
    geom = data.get("geometry")
    if geom is not None and hasattr(geom, "coords"):
        return [[round(float(x), 6), round(float(y), 6)] for x, y in geom.coords]
    return [
        [round(G.nodes[u]["x"], 6), round(G.nodes[u]["y"], 6)],
        [round(G.nodes[v]["x"], 6), round(G.nodes[v]["y"], 6)],
    ]


def build_edges_geojson(G) -> dict:
    """One GeoJSON FeatureCollection for the whole graph — a single vector
    layer renders far faster in the browser than one Folium object per
    edge, which matters for the two ~35-40k-edge cities."""
    features = []
    for u, v, data in G.edges(data=True):
        tier, has_steps = _split_tier(data.get("infra_tier", "dedicated"))
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": _edge_coords(G, u, v, data)},
                "properties": {"tier": tier, "steps": has_steps},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _edge_style(feature: dict) -> dict:
    props = feature["properties"]
    style = {
        "color": TIER_COLORS.get(props["tier"], DEFAULT_COLOR),
        "weight": 1.6,
        "opacity": 0.78,
    }
    if props.get("steps"):
        style["dashArray"] = "4,4"
        style["weight"] = 2.2
    return style


# === 3. LEGEND ================================================================

_LEGEND_HTML = """
<div style="
    position: fixed; bottom: 24px; left: 24px; z-index: 9999;
    background: #fcfcfb; padding: 12px 14px; border-radius: 6px;
    border: 1px solid rgba(11,11,11,0.10);
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
    font-size: 12px; color: #0b0b0b; line-height: 1.5; max-width: 260px;">
  <div style="font-weight: 600; margin-bottom: 6px;">Infrastructure tier</div>
  {tier_rows}
  <div style="margin-top: 6px; display: flex; align-items: center;">
    <div style="width:18px; border-top: 2.2px dashed #52514e; margin-right:8px;"></div>
    <span>Steps (stairs) — additional obstacle</span>
  </div>
  <div style="margin-top: 6px; display: flex; align-items: center;">
    <div style="width:11px; height:11px; border-radius:50%; background:{choke_color};
                margin-right:8px; flex-shrink:0;"></div>
    <span>Top-10 betweenness choke point (#3)</span>
  </div>
</div>
"""

_TIER_ROW = """
  <div style="display: flex; align-items: center; margin-top: 3px;">
    <div style="width:18px; height:4px; background:{color}; margin-right:8px; flex-shrink:0;"></div>
    <span>{label}</span>
  </div>
"""


def _legend_html() -> str:
    rows = "".join(
        _TIER_ROW.format(color=TIER_COLORS[t], label=TIER_LABELS[t])
        for t in ["dedicated", "low_traffic_no_sidewalk", "moderate_traffic_no_sidewalk", "high_traffic_no_sidewalk"]
    )
    return _LEGEND_HTML.format(tier_rows=rows, choke_color=CHOKE_POINT_COLOR)


# === 4. MAP ASSEMBLY ==========================================================


def build_city_map(G, choke_points_df: pd.DataFrame, name: str) -> folium.Map:
    """Assemble the full Folium map for one city: base tiles, the edge
    layer (styled by infra tier), choke-point callouts, legend, title."""
    lats = [d["y"] for _, d in G.nodes(data=True)]
    lons = [d["x"] for _, d in G.nodes(data=True)]
    center = [sum(lats) / len(lats), sum(lons) / len(lons)]

    m = folium.Map(location=center, zoom_start=15, tiles="OpenStreetMap", prefer_canvas=True)

    geojson = build_edges_geojson(G)
    folium.GeoJson(
        geojson,
        name="Pedestrian network",
        style_function=_edge_style,
        tooltip=None,
    ).add_to(m)

    for _, row in choke_points_df.iterrows():
        popup_html = (
            f"<b>Choke point, rank {int(row['rank'])}</b><br>"
            f"Betweenness: {row['betweenness']:.4f}<br>"
            f"Method: {row['betweenness_method']}<br>"
            f"Node: {row['node']}"
        )
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=7,
            color="#ffffff",
            weight=1.5,
            fill=True,
            fill_color=CHOKE_POINT_COLOR,
            fill_opacity=0.95,
            popup=folium.Popup(popup_html, max_width=250),
        ).add_to(m)

    title_html = (
        f'<div style="position: fixed; top: 12px; left: 50%; transform: translateX(-50%); '
        f'z-index: 9999; background: #fcfcfb; padding: 6px 16px; border-radius: 6px; '
        f'border: 1px solid rgba(11,11,11,0.10); box-shadow: 0 2px 8px rgba(0,0,0,0.15); '
        f'font-family: system-ui, -apple-system, \'Segoe UI\', sans-serif; '
        f'font-size: 15px; font-weight: 600; color: #0b0b0b;">'
        f"{name.replace('_', ' ')} — pedestrian infrastructure & choke points</div>"
    )
    m.get_root().html.add_child(branca.element.Element(title_html))
    m.get_root().html.add_child(branca.element.Element(_legend_html()))

    return m


# === 5. EXPORT ================================================================


def export_city_map(m: folium.Map, name: str, base_dir: str = "figures/interactive") -> str:
    os.makedirs(base_dir, exist_ok=True)
    slug = name.replace(" ", "_")
    out_path = os.path.join(base_dir, f"{slug}_interactive_map.html")
    m.save(out_path)
    size_kb = os.path.getsize(out_path) / 1024
    log.info(f"  Saved interactive map -> {out_path} ({size_kb:.0f} KB)")
    return out_path


_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>City Walkability — interactive maps</title>
<style>
  body {{ font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
          background: #f9f9f7; color: #0b0b0b; max-width: 640px;
          margin: 48px auto; padding: 0 20px; line-height: 1.5; }}
  h1 {{ font-size: 22px; }}
  p.sub {{ color: #52514e; margin-top: -8px; }}
  ul {{ padding-left: 20px; }}
  li {{ margin: 8px 0; }}
  a {{ color: #2a78d6; text-decoration: none; font-weight: 600; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>City Walkability — interactive maps</h1>
<p class="sub">Issue #7, minimum-viable scope. Each map shows the pedestrian
network coloured by infrastructure tier, plus #3's top-10 betweenness choke
points. See <code>METHODOLOGY.md</code> for tier definitions and
<code>REPORT.md</code> for the Folium-vs-dashboard scope decision.</p>
<ul>
{items}
</ul>
</body>
</html>
"""


def export_index(names: list, base_dir: str = "figures/interactive") -> str:
    items = "\n".join(
        f'  <li><a href="{n.replace(" ", "_")}_interactive_map.html">{n.replace("_", " ")}</a></li>'
        for n in names
    )
    out_path = os.path.join(base_dir, "index.html")
    with open(out_path, "w") as f:
        f.write(_INDEX_HTML.format(items=items))
    log.info(f"  Saved index -> {out_path}")
    return out_path


# === 6. CLI ====================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from pedestrian_nodes import LOCATIONS, load_graph
    from friction_weighting import add_friction_weights

    built = []
    for loc_name in LOCATIONS:
        G = load_graph(loc_name)
        if G is None:
            log.warning(f"No cached graph for '{loc_name}' — run pedestrian_nodes.py first. Skipping.")
            continue

        print(f"\n{'=' * 60}\n  {loc_name}\n{'=' * 60}")
        add_friction_weights(G, name=loc_name)

        cp_path = os.path.join("locations", loc_name, f"{loc_name}_betweenness_top10.csv")
        if not os.path.exists(cp_path):
            log.warning(f"  No betweenness_top10.csv for '{loc_name}' — run connectivity_metrics.py first. Skipping.")
            continue
        choke_points_df = pd.read_csv(cp_path)

        m = build_city_map(G, choke_points_df, loc_name)
        export_city_map(m, loc_name)
        built.append(loc_name)

    if built:
        export_index(built)
        print(f"\n✓ Done. {len(built)} interactive map(s) written to figures/interactive/")
