# City Walkability

A geospatial analytics pipeline that measures how walkable a city actually
is — not just how walkable its street map makes it look.

Most walkability tools (WalkScore and similar) score a location by
distance to amenities over a road network, implicitly assuming every
street is equally easy to walk. This project builds a **"True
Walkability" graph** from OpenStreetMap data that accounts for the things
that actually make a route harder: missing sidewalks, steep grades,
staircases, and high-traffic roads with no pedestrian infrastructure —
then compares that "Actual" walkability against the naive "Planned" view
to surface the gap between them.

**Status: Phase 2 (the core scoring algorithm) is in progress.** Phase 1
(data pipeline, five study cities processed and cached) is complete. See
[ROADMAP.md](ROADMAP.md) for what's built vs. still open, and
[REPORT.md](REPORT.md) for the methodology decisions and open questions
behind the numbers.

---

## For planners & policymakers

The question this project answers: **for a given city, where does the
street network quietly fail pedestrians, even though the map makes it
look walkable?**

A road network can look fully connected on a map while being genuinely
unpleasant or unsafe to walk — a "shortest path" that routes someone
along a high-speed arterial with no sidewalk, or up a slope steep enough
to be a real barrier for anyone with limited mobility. This project builds
two versions of the same city's walking network — one that assumes every
street is equally walkable ("Planned"), and one that penalizes routes for
missing sidewalks, steep terrain, and stairs based on real OpenStreetMap
infrastructure data ("Actual") — and compares them to find specific
streets and intersections where the gap is largest. That gap is where
infrastructure investment (a sidewalk, a crossing, an accessible ramp)
would do the most good.

**What exists today**: five study locations have been fully mapped and
enriched with elevation and terrain-grade data — Maastricht (Netherlands),
Matosinhos (Portugal), Sabancı University campus (Istanbul, Turkey),
Lanaken (Belgium), and Mindelo (Portugal). The friction-based scoring
model (the "Actual" side of the comparison) is implemented and validated;
the Planned-vs-Actual comparison itself, and the single comparable
"walkability score" per city, are the next pieces being built — see
[ROADMAP.md](ROADMAP.md).

**A caveat worth stating plainly**: the specific penalty values used to
say "this street is 40% harder to walk than that one" are a documented
design choice informed by an established framework (Pedestrian Level of
Service grading), not a number pulled from a calibration study specific
to these cities. That's disclosed throughout rather than hidden, and is
open to refinement — see [REPORT.md](REPORT.md).

## For technical reviewers

### Pipeline

```
pedestrian_nodes.py        Phase 1 — OSMnx graph extraction, pedestrian-
                             tag filtering, elevation enrichment (Open-
                             Elevation API), grade calculation, export to
                             GraphML / GeoPackage / CSV, with caching.

friction_weighting.py       Phase 2 — converts the enriched graph into a
                             friction-weighted graph: 
                                friction_weight = length * (1 + slope_penalty
                                                             + infra_penalty)
                             slope_penalty derived from Tobler's hiking
                             function; infra_penalty from a PLOS-inspired
                             categorical tier system. See module docstring
                             for the full derivation, including a subtlety
                             in Tobler's function that required a
                             deliberate modeling decision (documented in
                             REPORT.md).

visualise_node_maps.py      Static visualization — per-city and combined
                             node maps, colour-configurable (currently
                             degree; grade/elevation/friction ready to
                             wire in once #007 lands).
```

Planned-vs-Actual routing and connectivity metrics (circuity, betweenness
centrality) are implemented — see [ROADMAP.md](ROADMAP.md). The composite
index and interactive visualization are still open work, tracked on
[GitHub Issues](https://github.com/ChristianCrivelli/city_walkability/issues).

### Design decisions worth knowing about before reading the code

- The friction formula is **additive** (`1 + penalties`), matching the
  project's original design — not multiplicative. See
  [GitHub issue #1](https://github.com/ChristianCrivelli/city_walkability/issues/1).
- A real data-quality bug was found and fixed during implementation: the
  original pedestrian-edge filter treated an explicit `sidewalk=no` OSM
  tag as evidence a sidewalk *exists*, because it only checked tag
  presence, not value. See the same issue for details.
- GraphML round-trips through OSMnx don't preserve dtypes for
  non-standard attribute names (custom fields silently come back as
  strings on reload) — verified case-by-case rather than assumed; see
  commit history / issue notes if extending the schema further.

### Tech stack

`OSMnx` / `NetworkX` (network extraction & graph algorithms) ·
`GeoPandas` (spatial dataframes) · `Matplotlib` (static visualization,
`Folium` planned for interactive — see
[GitHub issue #7](https://github.com/ChristianCrivelli/city_walkability/issues/7)) · Open-Elevation
API (elevation data, no key required).

### Running it

```bash
pip install -r requirements.txt
python pedestrian_nodes.py       # Phase 1 — builds/caches the 5 study graphs
python friction_weighting.py      # Phase 2 step 1 — friction-weights them
python visualise_node_maps.py     # static PNG maps
```

Both pipeline scripts cache their graph output under `locations/<city>/`
and reuse it on subsequent runs rather than re-downloading from OSM.

## Study locations

| Location | Nodes | Edges | Network length (km) | Elevation range (m) |
|---|---:|---:|---:|---:|
| Maastricht, NL | 12,506 | 35,090 | 1,948.8 | 39–158 |
| Matosinhos, PT | 15,230 | 39,988 | 2,025.9 | −6–125 |
| Sabancı University, TR | 364 | 958 | 41.9 | 126–187 |
| Lanaken, BE | 3,552 | 9,614 | 1,024.7 | 39–112 |
| Mindelo, PT | 504 | 1,320 | 106.3 | 0–59 |

*(from `locations/all_locations_summary.csv`, Phase 1 output)*

## Project docs

- [`ROADMAP.md`](ROADMAP.md) — phased plan, what's done vs. open
- [`REPORT.md`](REPORT.md) — running methodology notes, open questions,
  and findings, feeding the eventual write-up
- [GitHub Issues](https://github.com/ChristianCrivelli/city_walkability/issues) —
  tracked backlog and per-issue resolution notes (formerly mirrored under
  `docs/issues/`; that folder has been retired in favor of Issues as the
  single source of truth)
