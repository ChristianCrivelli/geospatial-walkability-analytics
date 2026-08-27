# Methodology

This document collects every modeling decision in the pipeline that is a
**documented judgment call, not a citation** — a choice made because a
decision had to be made, backed by reasoning and (where possible) a
sanity check, but not derived from calibrated survey/GPS-trace data the
way a published Pedestrian Level of Service study would be. Each one is
flagged inline in the code's docstrings and discussed as it arose in
`REPORT.md`; this file exists per issue #8 to put them all in one place a
reader can find without digging through source, now that the algorithm
issues (#1–#5) have stabilized enough for the list to be final rather
than a moving target.

Two things this document is *not*: a restatement of what each script does
(see the module docstrings and `README.md` for that), and a duplicate of
issue tracking (see [GitHub Issues](https://github.com/ChristianCrivelli/geospatial-walkability-analytics/issues)
for status, and `REPORT.md` for the fuller narrative and numbers behind
each item below).

---

## 1. Friction weighting (`friction_weighting.py`, issue #1)

**Formula is additive, not multiplicative.**
`friction_weight = length * (1 + slope_penalty + infrastructure_penalty)`
— matching the project's original plan. Additive and multiplicative
combinations aren't equivalent (multiplicative introduces a cross-term
between slope and infrastructure penalties); additive is what was
actually designed and implemented.

**Slope penalty floors Tobler's hiking function at zero.**
The penalty is derived from Tobler's hiking function
(`r(S) = 6 * exp(-3.5 * |S + 0.05|)` km/h), the standard model for how
grade affects walking speed. The function doesn't peak at flat ground —
it peaks at a gentle 5% downhill, where a literal speed-ratio penalty
would be *negative* (a "discount"), which contradicts a strictly-additive
`1 + penalties` formula. Resolved by flooring the slope penalty at 0:
gentle downhill costs nothing extra, but is never treated as cheaper than
flat. This is a deliberate simplification of Tobler's function, not a
property of the function itself. Grades are also clipped to ±45%
(`MAX_ABS_GRADE`) to guard against elevation-data noise producing
physically implausible slopes.

**Infrastructure penalty tiers are PLOS-inspired; the values are not.**
The five tiers below are inspired by the Pedestrian Level of Service
(Highway Capacity Manual-style A–F grading of sidewalk presence and
traffic exposure), but PLOS gives *categories*, not universal numeric
constants — published studies calibrate the actual numbers locally via
survey or GPS-trace data, which this project doesn't have. The values
below are a first-pass, documented model open to recalibration, not an
empirical finding:

| Tier | Additive penalty |
|---|---|
| Dedicated pedestrian infrastructure (footway, pedestrian, path, living_street, track) | 0.00 |
| Low-traffic road, no sidewalk (residential, service, unclassified) | 0.15 |
| Moderate-traffic road, no sidewalk (tertiary, secondary) | 0.40 |
| High-traffic road, no sidewalk (primary, trunk) | 0.90 |
| Steps (added on top of whatever tier penalty applies) | +0.60 |

A sensitivity check — does the city ranking change materially if these
values move ±30%? — is on the report's open-questions list, now
actionable since the index code exists to re-run with perturbed inputs,
but not yet done.

**A real data bug was found and fixed while building this tier logic.**
The original edge filter in `pedestrian_nodes.py`
(`bool(data.get("sidewalk"))`) treated `sidewalk=no` — an explicit OSM
tag meaning "there is no sidewalk here" — as if it meant a sidewalk was
present, because it only checked whether the tag existed, not what it
said. Mindelo alone had 30 street segments affected. Fixed via
`_tag_is_positive()`, which excludes `"no"`/`"none"`/`""` values. This
one isn't a judgment call — it's a straightforward correctness bug — but
it's recorded here because it changed real output.

---

## 2. Planned vs. Actual (`planned_vs_actual.py`, issue #2)

**"Planned" is defined as the OSM graph with zero penalties, not sourced
from municipal data.** `planned_weight == length`: every pedestrian-
eligible edge is treated as equally walkable, mirroring what a naive tool
like WalkScore assumes. The alternative — pulling official street-grid
data from each city's own municipal GIS system — was rejected as a scope
tradeoff: 5 study locations across 4 countries would mean 5 different
data sources and 5 different schemas, undermining a consistent
comparison. This is a scope/consistency tradeoff, not obviously the
"correct" definition of Planned, and is still an open question for the
final report to either justify explicitly or name as a limitation.

**O-D sampling is random-pairs only for this pass; POI-based sampling is
deferred, not dropped.** POI-based pairs (e.g. "nearest residential node
→ nearest amenity") would need a live Overpass API call the pipeline
doesn't otherwise depend on. Issue #5's real, human-picked routes are a
stronger source of human-meaningful O-D pairs than synthetic
nearest-amenity sampling would have been anyway, so POI sampling is
deferred rather than rushed — `poi_based_od_pairs` exists as an explicit
stub in the code so the option isn't silently lost. Random pairs are
drawn from each graph's largest strongly-connected component, so every
sampled pair is guaranteed reachable and no samples are wasted.

**Route overlap is edge-count based, not length-weighted.** Reported as
the percentage of the shorter route's edges also used by the other route
— simple and symmetric for a v1. Flagged as a candidate refinement if a
future example shows edge-count overlap reading misleadingly against a
length-weighted version.

**Length delta is reported in physical metres, not friction-cost units.**
The interesting comparison is "how much further, physically, does the
friction-aware route go to avoid painful terrain" — a different question
from the friction-cost delta, which is also reported but kept separate
rather than conflated.

---

## 3. Connectivity metrics (`connectivity_metrics.py`, issue #3)

**Betweenness centrality: exact for 3 cities, k-sample approximation for
2.** Exact betweenness is O(V·E·log V) with NetworkX's pure-Python
Dijkstra — fine for Sabancı (364 nodes), Mindelo (504), and Lanaken
(3,552), impractical at reasonable runtime for Maastricht (12,506) and
Matosinhos (15,230). Those two use k=500 sampling with a fixed seed
(`seed=42`) for reproducibility. Which method was used is recorded
per-city in the output (`betweenness_method` column), not applied as one
silent blanket method. Total runtime across all 5 cities: ~3 minutes.
Betweenness is computed on a simplified DiGraph (parallel edges collapsed
to the cheapest by `friction_weight`) but kept directed, since
friction-weight is direction-dependent (uphill vs. downhill) and a
routing-relevant choke point can genuinely differ by direction of travel.

**Circuity uses physical route length, not friction cost.** Circuity is
a geometric detour ratio (network distance ÷ straight-line distance), so
it's computed against `actual_length_m` (physical metres), not
`friction_weight` (effort units) — the two answer different questions
and shouldn't be conflated.

**Median, not mean, is the headline circuity figure per city.** Checked,
not assumed: Sabancı's max per-pair circuity hit 11.8, traced to a real
O-D pair only 12m apart in a straight line but 143m apart on the network
(two points close across a building/obstacle with no direct path) — a
known property of circuity ratios at short distances, where a small
absolute detour produces a huge ratio. Median absorbs this kind of
outlier; mean doesn't. This same short-distance sensitivity turned out to
matter at the city level too — see the #4 follow-up below.

---

## 4. Composite True Walkability Index (`true_walkability_index.py`, issue #4)

**Four components, chosen to span different failure modes a naive score
would miss:** `friction_ratio` (effort), `median_circuity` (detour, later
revised — see follow-up below), `dead_end_density` (physical dead-ends, a
new metric introduced for this index), and `friction_savings_pct` (the
size of the Planned/Actual gap). All four are framed "higher = worse"
before normalization.

**Normalization is min-max across the 5-city set, not z-score.** Z-score
needs a larger, more stable sample than 5 cities to be meaningful; 0–1
min-max also reads more intuitively for the planner/policy audience the
README targets. **This has a real consequence worth restating plainly:
because every component is normalized only against these 5 cities, the
index is a relative ranking within this set, not an absolute or portable
score.** Adding a 6th city (issue #6) would shift every city's normalized
values and therefore its index score — a limitation of a 5-city v1, not a
bug, and worth an explicit caveat wherever the index is presented so a
reader doesn't mistake "82/100" for a universal grade.

**Weights are equal (0.25 each) — a documented first-pass judgment call,
not a derived or empirically-justified weighting**, for the same reason
the infrastructure penalty values aren't: no calibration data. Weights
are a plain dict specifically so a future sensitivity check (does the
ranking change much if weights move ±30%?) is a trivial re-run rather
than a rewrite.

### Follow-up: distance bias in `median_circuity`, found and fixed post-close

After #4 closed, a hands-on-the-ground sanity check pushed back on
Sabancı's score (29.4/100, second-worst of 5): the campus is uphill and
stair-heavy, but genuinely easy to navigate day-to-day, which didn't
square with the number. Investigating found the gut check right, not the
score. The culprit was two structural metrics, not effort — Sabancı's
`friction_ratio` was mid-pack (1.219 vs. Maastricht's 1.170).

- **`median_circuity` is measurably distance-decayed, confirmed in this
  project's own pooled data**, not just asserted from the literature: an
  OLS fit across all 5 cities' 1,000 O-D pairs gives
  `circuity ≈ 2.082 − 0.0923·ln(distance_m)` (r = −0.234, p = 6.3e-14,
  n = 1000) — a real, statistically significant effect, though noisy at
  the individual-pair level (r² = 0.055; reliable at the per-city-median
  level over 200 pairs, not as a single-pair predictor). Sabancı's median
  O-D distance is 342m — the whole campus fits in roughly a 1km bounding
  box — versus 1,300–3,500m for the other four cities, so every one of
  its 200 sampled pairs fell in the "short trip → mechanically higher
  ratio" zone purely because the study area is small, not because its
  path network is worse.

  **Fix:** a distance-detrended `median_excess_circuity` (observed minus
  the pooled model's expected value) was added as a second, additional
  index component, computed by `fit_circuity_distance_model` /
  `add_excess_circuity`. The original index using raw `median_circuity`
  is kept computed and exported unchanged — the fix is additive, not a
  silent replacement — so both are available for comparison
  (`all_locations_true_walkability_index_comparison.csv`).

  Effect: Sabancı moves from 29.4 (original) to 54.4 (adjusted) — from
  second-worst to solidly mid-pack, the largest move of any city and in
  the direction the ground-truth intuition predicted.

- **`dead_end_density` has the same kind of scale sensitivity and was
  deliberately left unfixed.** 72 of Sabancı's 101 degree-1 nodes (71%)
  sit on `footway`-tagged edges — almost certainly building-entrance
  spurs on a richly-mapped campus, not places a pedestrian would actually
  get stuck. There is no clean, non-arbitrary way to filter these out
  without OSM building-footprint data this pipeline doesn't load, and
  picking an edge-length cutoff by eye would just stack a second designed
  judgment call on top of the first one it's trying to correct. Recorded
  here as a known, un-corrected limitation rather than "fixed" with an
  unjustified number.

Both indexes are exported side by side deliberately: the original stays
available as the pre-fix baseline, and the adjusted version is not
presented as strictly "more correct" everywhere — Matosinhos, for
example, actually drops further in the adjusted index (23.8 → 6.7), not
because of a circuity artifact but because of a genuine infrastructure
difference (26.3% dedicated pedestrian infrastructure vs. Sabancı's
61.8%, and 20% of its network on traffic-exposed, sidewalk-free roads vs.
0% for Sabancı) that the circuity fix doesn't touch.

---

## 5. Personal path validation (`personal_path_validation.py`, issue #5)

**Named places were geocoded by hand via web search, not a live
geocoder call.** The sandbox's direct network access to
`nominatim.openstreetmap.org` is blocked, and both Nominatim and Photon's
API endpoints are blocked for the fetch tooling available — so resolving
a restaurant, sports centre, or beach name to coordinates was done via
web search rather than a reproducible geocoder call. Resolved coordinates
are hardcoded in `PERSONAL_PATHS` with a `source` note per point;
re-running the script alone does not re-derive them. Wherever possible,
a named place was instead matched directly against the target city's own
cached graph (OSM `name` tags on edges) — preferred over external
geocoding because it's guaranteed in-bbox and typically more precise than
a third-party lookup (used for both Maastricht points and Mindelo's
origin).

**Two real network-coverage gaps were surfaced rather than smoothed
over**, each resolved by an explicit choice rather than a silent
default:

- **Lanaken → Maasmechelen (LIDL)**: the destination sits roughly 2.5–3km
  outside the cached Lanaken graph's extent entirely. Run as a "partial
  route to the network edge" — `nearest_nodes` snaps to whichever node is
  closest within the Lanaken graph, and the straight-line gap to the real
  destination is reported explicitly. This is not a real validation of
  that walk; it describes how far the model's own network reaches in
  that direction.
- **Matosinhos → Castelo do Queijo (basketball hoops)**: the best
  available coordinate for this landmark sits ~390m outside the
  Matosinhos graph's boundary — the site is technically in Porto's
  Nevogilde parish, not Matosinhos. Run anyway using the nearest node
  within the Matosinhos graph, with the resulting ~611m combined gap
  (parish-boundary distance plus ordinary snap distance) reported
  per-row rather than hidden.

**One destination is an unverified best-effort placement.** No source
gave a precise address for Sabancı's "Sport Center" — consistent with
#1–#4's finding that the campus is thinly tagged in OSM generally.
Placed near campus centre per the user's own description ("the large
building by the lake") and flagged as unverified in the output, not
presented as a precise coordinate.

**One destination name was resolved by inference.** Mindelo's "the
Beach" was not a specific place name the user gave a source for; deferred
to best judgment, which used "Rua da Praia" ("Beach Street") — a
literal, well-connected name match found directly in the graph (54
edges) — over the alternative candidate "Avenida Marginal (Norte)" (a
coastal avenue, 4 edges). Noted in the code as an inference, not a given.

**A geography mix-up was caught before it reached output.** This
project's "Mindelo" is the small parish of Mindelo in Vila do Conde,
Portugal (per `LOCATIONS["Mindelo"]["query"]`), not Mindelo, Cape Verde.
An initial pass researched the wrong Mindelo before this was checked
against the project's own config; caught and redone correctly before any
result was generated. Recorded here as a reminder that place names are
not unique, not because it affected any delivered number.

---

## 6. Repo hygiene & data storage (issue #8)

**Git history bloat is a recorded decision, not an oversight.**
`locations/` and `figures/` together are ~108MB of generated, regenerable
data committed directly to git. Three options were considered: (a) leave
as-is — simplest, but a heavy clone for a portfolio repo; (b) migrate to
Git LFS; (c) `.gitignore` the data and document "run
`pedestrian_nodes.py` to regenerate" instead. Both (b) and (c) require
rewriting git history (`git filter-repo` + force-push) to actually shrink
the repository — a destructive, irreversible-by-default operation not
undertaken as part of an automated pass. **Decision: leave the data
as-is (option a)** — the simplest option, revisited manually later if the
clone size becomes a real problem rather than a theoretical one.

---

## What's still open

This document is a snapshot once #1–#5 stabilized; it does not track
status. For what's still undecided or in progress, see the "Open
questions" section of `REPORT.md` (report audience/format, the
`friction_ratio` sensitivity check across weight/penalty variation, and
whether the Planned/OSM-only definition needs a stronger justification or
an explicit limitations paragraph) and `ROADMAP.md` for what's next.
