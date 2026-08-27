# Report notes (working document)

This is a running scratchpad, not the report itself. It exists so that
methodology caveats, open questions, and interesting findings get written
down the moment they surface — during implementation, not reconstructed
from memory once it's time to write the final report. Organized into three
sections: things we still need to decide, things we already decided (and
why, for the technical reader), and things worth telling a non-technical
reader.

Issue status and resolution notes live on
[GitHub Issues](https://github.com/ChristianCrivelli/city_walkability/issues)
— this file is where the *methodology* narrative accumulates across
issues, not a duplicate of issue tracking.

---

## Open questions — need a decision before the report can be finalized

**Who is the report actually for?** Still undecided. Options discussed:
a technical audience (methodology-first, findings second) vs. a
planner/policy audience (findings first, methodology in an appendix).
This also determines whether "interactive element" (#7) means a Folium
map dropped in the repo or a real dashboard — very different amounts of
work. #2 and #3 now have real numbers (see Findings below) — worth
revisiting this question sooner rather than after #4 too.

**What does "Planned" walkability mean, precisely?** Implemented for #2
as the working assumption: derive it from OSM too (naive, no penalty),
rather than sourcing 5 different municipal GIS datasets across 4
countries. This is a scope/consistency tradeoff, not obviously the
"correct" choice — worth a paragraph in the report either justifying it
or noting it as a limitation if we later decide it undersells the
"planned" side of the comparison. Implementing it didn't resolve the
framing question, just made the tradeoff concrete.

**Infrastructure penalty values (#1) and index component weights (#4)
are designed, not derived.** PLOS gives categories, not universal
numbers — most published penalty studies calibrate locally via survey or
GPS-trace data, which this project doesn't have. The report needs to be
explicit about this rather than presenting the multipliers as if they
were empirical findings. Candidate framing: "a first-pass, documented
model open to recalibration," possibly with a sensitivity check (does
the index ranking across the 5 cities change much if penalty values move
±30%?) as a way to show the choice isn't arbitrary even without hard
calibration data. #4 is now implemented with equal weights (0.25 each) as
the documented first pass — the sensitivity check itself is still open,
now actionable since the index code exists to re-run with perturbed
weights/penalties. Post-close, a hands-on sanity check flagged
median_circuity as scale-biased against the smaller study area (Sabancı);
fixed with a distance-detrended variant kept alongside the original — see
Findings below. dead_end_density has a similar, un-fixed limitation
(campus building-entrance spurs inflate it) — flagged, not corrected, for
lack of a non-arbitrary filter.

**#4's index is a relative ranking across these 5 cities, not an absolute
score.** Because every component is min-max normalized against only this
5-city set, adding a 6th city (#6) would shift everyone's normalized
values and therefore their index score — a real limitation of a v1 built
on 5 cities, not a bug. Worth a explicit caveat in the report so a reader
doesn't mistake "82/100" for a universal, portable walkability grade.

**Personal path anchors (#5)** — done; results in Findings below. Two of
the five pairs have real, unresolved data gaps rather than clean
validations (Lanaken's destination is ~3.6km outside the cached network;
Matosinhos's is ~390-610m outside it) — both run anyway per an explicit
choice, not silently smoothed over. The other three (Sabancı, Maastricht,
Mindelo) are genuine validations, and the standout finding is that 3 of
the 5 pairs show *zero* Planned/Actual divergence — the naive and
friction-aware routes are literally the same path. See Findings for the
likely mechanism (short trips have fewer alternate routes to diverge
onto) and how the Sabancı result corroborates the #4 follow-up above.

**Git history / data storage (#8)** — decided: leaving the ~108MB as-is
for now (simplest option). Migrating to LFS or gitignore+regenerate would
need a git filter-repo history rewrite plus a force-push — done locally
if/when revisited, not from an automated pass. Still worth one sentence
in the report's "methodology & reproducibility" section either way.

**#8 repo hygiene — closed.** The filename typo (`pedestraian_nodes.py`
→ `pedestrian_nodes.py`) is fixed, with every import across
`connectivity_metrics.py`, `friction_weighting.py`,
`personal_path_validation.py`, `planned_vs_actual.py`,
`true_walkability_index.py`, and `visualise_node_maps.py` updated to
match — verified by re-grepping for the old spelling. `METHODOLOGY.md`
is written: it pulls every "documented judgment call, not a citation"
caveat out of the module docstrings and this file into one place a
reader can find without digging through source, organized by pipeline
stage (#1 friction weighting through #5 personal-path validation, plus
#8's own data-storage decision). It doesn't track status — that's still
this file's and ROADMAP.md's job — it's a fixed snapshot of *what was
decided and why*, taken now that #1–#5 have all stabilized.

---

## Decisions made, and why (technical-reader material)

- **Friction formula is additive, not multiplicative**:
  `friction_weight = length * (1 + slope_penalty + infra_penalty)`,
  matching the project's own original plan (`W = d × (1 + slope_penalty +
  infrastructure_penalty)`) rather than a multiplicative combination
  (`length * slope_mult * infra_mult`) — the two aren't equivalent
  (multiplicative has a cross-term), and additive is what was actually
  designed.

- **Tobler's hiking function doesn't peak at flat ground** — it peaks at
  a gentle ~5% downhill. A literal speed-ratio penalty would therefore be
  *negative* (a "discount") right around there, which contradicts a
  strictly-additive `1 + penalties` formula. Resolved by flooring the
  slope penalty at 0 (gentle downhill costs nothing extra, but is never
  cheaper than flat). This is a deliberate simplification of Tobler's
  function, not a property of the function itself — worth a sentence in
  the report's methodology section since it's a subtle point a technical
  reviewer would likely ask about.

- **Elevation data source**: Open-Elevation (free tier) returned 0
  missing values across all 5 cities (3,552–15,230 nodes each) — no need
  to fall back to a local SRTM DEM. Worth noting as a "we checked, it held
  up" line rather than silently assuming it.

- **#2 O-D sampling scope: random pairs only, POI-based sampling
  deferred.** POI-based pairs (residential → nearest amenity) need a live
  Overpass API call the pipeline doesn't otherwise depend on, and #5
  (personal-path validation, real human-picked routes) is a stronger
  source of human-meaningful O-D pairs than synthetic nearest-amenity
  sampling would be anyway. Random sampling is drawn from each graph's
  largest strongly-connected component so every sampled pair is
  guaranteed reachable — no wasted samples. `poi_based_od_pairs` exists
  as an explicit stub rather than being silently dropped, in case a report
  narrative later wants synthetic POI pairs in addition to #5's real
  ones.

- **Route overlap metric (#2) is edge-count based**, not
  length-weighted: % of the shorter route's edges also used by the other
  route. Simple and symmetric for v1; flagged as a candidate refinement
  if a future example shows edge-count overlap reads misleadingly against
  a length-weighted version.

- **#3 betweenness centrality: exact for 3 cities, k-sample approximation
  for 2.** Exact is O(V·E·log V) with networkx's pure-Python Dijkstra —
  fine for Sabancı (364 nodes), Mindelo (504), Lanaken (3,552); impractical
  for Maastricht (12,506) and Matosinhos (15,230) at reasonable runtime.
  Used k=500 (fixed seed=42) for those two. Recorded per-row in each
  city's output (`betweenness_method` column), not silently applied as one
  blanket method. Total runtime for all 5 cities: ~3 minutes.

- **#3 circuity uses physical route length, not friction cost.** Circuity
  is a geometric detour ratio (network distance ÷ straight-line distance),
  so it's computed against `actual_length_m` (physical metres) from #2,
  not `friction_weight` (effort units) — the two answer different
  questions and shouldn't be conflated.

- **#4 composite index: 4 components, min-max normalized, equal-weighted.**
  `friction_ratio` (sum friction_weight / sum length, whole-graph — reused
  from #1's validation approach), `median_circuity` (reused from #3,
  median not mean per the Sabancı-outlier caveat above), `dead_end_density`
  (NEW metric — fraction of degree-1 nodes in a simplified undirected
  simple graph; not computed anywhere upstream), and `friction_savings_pct`
  (mean, reused from #2 — reframed here as "size of the Planned/Actual
  gap": a bigger gap means the naive Planned view is a worse stand-in for
  what's actually walkable). All four framed "higher = worse" before
  normalization. Min-max chosen over z-score given the small (5-city)
  sample and a more intuitive 0-1 reading. Final score =
  `100 * (1 - difficulty_score)`, so higher = more walkable, 0-100 scale.
  Weights are equal (0.25 each) — a documented first-pass judgment call,
  not derived (see Open questions above re: a future sensitivity check).

- **#4 follow-up: median_circuity swapped for median_excess_circuity in a
  second, additional index, original left unchanged.** Raw circuity is
  distance-decayed (confirmed via OLS on the pooled 1,000-pair dataset:
  r=−0.234, p=6.3e-14 — see Findings below for the full writeup and the
  numbers), so comparing raw circuity across study areas of very
  different physical scale (a ~1km campus vs. multi-km cities)
  systematically penalizes the smaller area regardless of actual path
  quality. `fit_circuity_distance_model` fits a single pooled log-linear
  model across all cities; `add_excess_circuity` gives each O-D pair an
  observed-minus-expected residual; each city's median residual replaces
  raw median_circuity in `ADJUSTED_COMPONENT_COLUMNS`. Deliberately did
  NOT also try to "fix" dead_end_density the same way — no non-arbitrary
  filter was available without additional data, so that one stays
  documented as a limitation instead (see Findings below).

- **#5 personal paths: named places geocoded by hand, cross-checked
  against each city's own graph where possible, gaps reported rather than
  hidden.** The sandbox's direct network access to nominatim.openstreetmap.org
  is blocked, so named places (a restaurant, a sports centre, a beach)
  were resolved via web search rather than a live geocoder call — not
  reproducible by re-running `personal_path_validation.py` alone; the
  resolved coordinates are hardcoded in `PERSONAL_PATHS` with a `source`
  note per point. Preferred matching a point directly against each
  graph's own OSM `name` tags (Maastricht's two points, Mindelo's origin)
  over external geocoding wherever possible — guaranteed in-graph and
  more precise than a third-party lookup. Two real data gaps surfaced and
  were escalated rather than papered over: Lanaken's destination
  (Maasmechelen) sits ~2.5-3km outside the cached Lanaken network
  entirely, and Matosinhos's destination sits ~390m outside the
  Matosinhos network (the landmark is technically in a different
  municipality's parish). Per explicit choice: Lanaken is run as a
  "partial route to the network edge" (reports how far the model's own
  network reaches, not a real validation of that specific walk);
  Matosinhos is run with the nearest available node and the gap reported
  per-row. Sabancı's destination ("Sport Center") has no independently
  verified coordinate at all — no source gave a precise address, so it's
  placed near campus centre per the user's description and flagged as
  unverified, not presented as precise.

---

## Findings worth telling a non-technical reader

- **A real bug, found and fixed**: the original filter treated
  `sidewalk=no` (an explicit "there is no sidewalk here" OSM tag) as if it
  meant a sidewalk was present, because the code only checked whether the
  tag existed, not what it said. Mindelo alone had 30 street segments
  affected. Good, concrete "we found and fixed a real data-quality issue"
  anecdote for a portfolio-facing readme/report.

- **Sabancı University has essentially no sidewalk tagging at all** — 0
  edges with an explicit sidewalk tag, out of 958. Two possible readings:
  the campus genuinely has no separated sidewalks (plausible — it's a
  small, mostly car-light campus), or OSM simply hasn't been mapped in
  that detail there. Can't distinguish the two from this data alone — an
  honest limitation to state, and the seed of #6's "tagging density"
  sanity-check idea for scaling to new cities later.

- **Penalty-ratio range across cities (friction-km ÷ raw-km)**: Lanaken
  1.16, Maastricht 1.17, Sabancı 1.22, Mindelo 1.21, Matosinhos 1.27 (run
  against the current, first-pass penalty values — expect these to move
  once #4's index and any recalibration land). Matosinhos having the
  highest ratio is now something #3's choke-point/circuity detail can
  help explain in the report — see below.

- **#2 Planned-vs-Actual results (200 random O-D pairs/city, seed=42)**:
  the friction-aware ("Actual") route is consistently a little physically
  *longer* than the naive ("Planned") route, but consistently *cheaper in
  effort* — exactly the behavior the model is supposed to produce.

  | City | length delta | friction savings | route overlap | fully divergent |
  |---|---|---|---|---|
  | Maastricht | +2.9% | 6.0% | 57.7% | 1/200 |
  | Matosinhos | +4.2% | 6.2% | 50.8% | 0/200 |
  | Sabancı University | +2.3% | 2.4% | 81.5% | 2/200 |
  | Lanaken | +3.2% | 6.0% | 65.0% | 6/200 |
  | Mindelo | +0.6% | 1.1% | 89.8% | 1/200 |

  Sabancı has the highest overlap (81.5%) — makes sense for a small,
  mostly-dedicated-path campus with few detour options. Lanaken has the
  highest full-divergence rate (3%).

- **Hand-checked validation route (Lanaken, node 2204882099 →
  13192415045)** — a strong, concrete anecdote for the report: the
  Planned route runs 8,804m, with 69% of that (6,073m across 80 of 127
  edges) on `high_traffic_no_sidewalk` roads, because a naive
  shortest-path has no way to know those roads are unsafe for a
  pedestrian. The Actual route is 9,967m (+13.2% distance) but cuts
  `high_traffic_no_sidewalk` exposure to just 500m — an 11.8x reduction —
  for 23.6% lower friction cost overall. In plain terms: the model routes
  you noticeably further to almost entirely avoid a stretch of dangerous,
  sidewalk-free arterial road. Worth a map figure once #7 (interactive
  visualization) exists.

- **#3 connectivity results (same 200-pair sample as #2)**: mean circuity
  across the 5 cities ranges 1.34–1.52 (a typical pedestrian route is
  34–52% longer than straight-line distance) — Sabancı highest (1.52),
  Mindelo lowest (1.34).

  | City | mean circuity | median circuity | top choke point (betweenness) |
  |---|---|---|---|
  | Maastricht | 1.448 | 1.352 | node 618628852 (0.281) |
  | Matosinhos | 1.358 | 1.320 | node 10024023734 (0.174) |
  | Sabancı University | 1.516 | 1.385 | node 2265527528 (0.260) |
  | Lanaken | 1.362 | 1.303 | node 1477629854 (0.137) |
  | Mindelo | 1.338 | 1.307 | node 1428182504 (0.409) |

  Mindelo's single top choke point carries unusually high betweenness
  (0.409, notably above the other cities' top scores) — plausibly a
  single bridge/bottleneck street that most cross-town routes are forced
  through, worth checking against a map once #7 exists. Good candidate
  policy anecdote: "here is the one intersection that would benefit most
  from a targeted fix."

  **Caveat, checked not a bug**: Sabancı's max per-pair circuity hit 11.8
  — traced to a real O-D pair only 12m apart in a straight line but 143m
  apart on the network (two points close across a building/obstacle with
  no direct path). Known property of circuity ratios at short distances:
  a small absolute detour produces a huge ratio. Use median, not mean, as
  the headline circuity figure per city for this reason.

- **#4 True Walkability Index (0-100, higher = more walkable)** — the
  project's headline number, combining all four components above:

  | City | Index | friction_ratio | median_circuity | dead_end_density | friction_savings_pct |
  |---|---|---|---|---|---|
  | Mindelo | 79.0 | 1.213 | 1.307 | 0.202 | 1.06 |
  | Lanaken | 69.1 | 1.161 | 1.303 | 0.200 | 6.04 |
  | Maastricht | 58.7 | 1.170 | 1.352 | 0.171 | 6.02 |
  | Sabancı University | 29.4 | 1.219 | 1.385 | 0.277 | 2.43 |
  | Matosinhos | 23.8 | 1.266 | 1.320 | 0.260 | 6.20 |

  **Sanity check the issue explicitly asks for — does Sabancı score
  believably against Maastricht?** Yes, and the component breakdown shows
  *why*, which is the more interesting result than the ranking itself.
  Sabancı isn't dragged down by friction_ratio (it's mid-pack, 1.219 vs.
  Maastricht's 1.170) — it's dragged down by having both the **highest
  circuity** (1.385) and the **highest dead-end density** (0.277) of all
  5 cities. That's a coherent story for a small, mostly-dedicated-path
  campus: lots of branching paths that dead-end at buildings, and few
  through-routes, so getting anywhere often means a real detour even
  though the paths themselves are pleasant to walk on. Maastricht, by
  contrast, has the lowest dead-end density (0.171 — a real street grid,
  not a campus) even though its circuity (1.352) and Planned/Actual gap
  (6.02%) aren't the best in the set. In other words: Sabancı is locally
  pleasant but globally awkward to navigate, and Maastricht is the
  reverse — exactly the kind of distinction a raw friction-ratio-only
  score would have missed, which is the whole point of a composite index.
  Mindelo's #1 rank is driven almost entirely by its tiny Planned/Actual
  gap (1.06%, by far the lowest) — worth flagging as a component the
  final report should discuss rather than let the aggregate score speak
  for itself, since a near-zero gap could mean "already walkable" or
  "OSM tagging too sparse to show a difference" (see the Sabancı
  sidewalk-tagging caveat below — the same ambiguity could apply here).

- **#4 follow-up: the "sanity check" above didn't survive a second, more
  skeptical look, and the fix changes the ranking meaningfully.** After
  closing #4, a hands-on-the-ground read pushed back: Sabancı is uphill
  and stair-heavy but genuinely easy to get around, which didn't square
  with a 29.4/100 score. Investigating found the gut check right, not the
  number — the culprit was two structural metrics, not effort:

  - **median_circuity is a distance-decayed quantity, confirmed in this
    project's own data.** Pooled across all 5 cities' 1,000 O-D pairs:
    `circuity ≈ 2.082 − 0.0923·ln(distance_m)` (OLS, r=−0.234, p=6.3e-14,
    n=1000) — short trips show measurably higher circuity ratios than
    long ones, a real and statistically significant effect (though noisy
    pair-to-pair: r² = 0.055, so it's reliable at the per-city-median
    level over 200 pairs, not as a single-pair predictor). Sabancı's
    median O-D distance is 342m — the whole campus fits in roughly a 1km
    bounding box — versus 1,300–3,500m for the other four cities. Every
    one of its 200 sampled pairs falls in the "short trip → mechanically
    higher ratio" zone purely because the study area is small, not
    because its path network is worse. **Fixed**: swapped raw
    `median_circuity` for `median_excess_circuity` (observed − expected,
    from the pooled distance model) in a second, additional index —
    `true_walkability_index.py`'s `fit_circuity_distance_model` /
    `add_excess_circuity`. The original index is left computed and
    exported unchanged, not overwritten.
  - **dead_end_density was left as-is.** 72 of Sabancı's 101 degree-1
    nodes (71%) sit on `footway`-tagged edges — almost certainly
    building-entrance spurs on a richly-mapped campus, not places you'd
    actually get stuck. There's no clean, non-arbitrary way to filter
    these out without OSM building-footprint data this pipeline doesn't
    load, and picking an edge-length cutoff by eye would just be a second
    designed judgment call stacked on the first one. Documented here as a
    known limitation rather than "corrected" with an unjustified number.

  **Adjusted results** (`all_locations_true_walkability_index_adjusted.csv`):

  | City | Original | Adjusted | Δ |
  |---|---|---|---|
  | Mindelo | 79.0 | 73.9 | −5.1 |
  | Sabancı University | 29.4 | 54.4 | **+25.0** |
  | Lanaken | 69.1 | 49.8 | −19.3 |
  | Maastricht | 58.7 | 48.7 | −10.0 |
  | Matosinhos | 23.8 | 6.7 | −17.2 |

  Sabancı moves from second-worst to solidly mid-pack — the single
  biggest move in either direction, and in the direction the ground-truth
  intuition predicted. Mindelo stays on top either way. Matosinhos,
  already worst in the original index, falls further once Sabancı's
  circuity penalty (previously anchoring the "worst" end of the min-max
  range) is removed — everyone else's normalized circuity score shifts
  as a result, which is the expected mechanical consequence of min-max
  normalization within a small city set (see the index's normalization
  caveat above), not a new finding about Matosinhos. **Matosinhos's low
  score is not a hill/circuity artifact**: it's 26.3% dedicated
  pedestrian infrastructure by length vs. Sabancı's 61.8%, and has 20% of
  its network on traffic-exposed, sidewalk-free roads (16.3%
  moderate-traffic + 3.6% high-traffic) vs. 0% for Sabancı — a genuine
  infrastructure-tagging difference driving `friction_ratio`, which this
  follow-up didn't touch. Both indexes (original and adjusted) are kept
  and exported side by side, not replaced — see
  `all_locations_true_walkability_index_comparison.csv`.

- **#5 Personal path validation** — five real, user-supplied routes run
  through the same Planned-vs-Actual comparison #2 uses for sampled
  pairs. Two carry real data-availability caveats (see Decisions above);
  all five below, snap distances included so nothing is hidden:

  | City | Route | Length (actual) | Δ vs. planned | Friction savings | Overlap | Circuity | Note |
  |---|---|---|---|---|---|---|---|
  | Sabancı | given point → Sport Center | 241m | +0.0% | 0.0% | 100% | 1.065 | dest. unverified, ~34m snap |
  | Matosinhos | Sergio Crivelli → Castelo do Queijo | 1,354m | +0.0% | 0.2% | 94.1% | 1.478 | dest. ~611m outside network |
  | Maastricht | UM → UM Sports | 3,303m | +0.7% | 2.4% | 86.8% | 1.538 | clean — both points matched in graph |
  | Lanaken | given point → Maasmechelen LIDL | 7,063m | +0.2% | 0.6% | 92.1% | 1.115 | dest. ~3.6km outside network — not a real validation, see caveat |
  | Mindelo | Rua Dos Descobrimentos → Rua da Praia | 541m | +0.0% | 0.0% | 100% | 1.000 | dest. inferred ("the Beach"), both points matched in graph |

  **The standout finding: 3 of the 5 pairs show *zero* Planned/Actual
  divergence** — the friction-aware route is the exact same path as the
  naive one (Sabancı, Matosinhos, Mindelo all show 0.0-0.2% friction
  savings and 94-100% edge overlap). That's a real and useful contrast
  with #2's 200-random-pair averages (6.0% savings for Maastricht, 2.4%
  for Sabancı, etc.) — the likely mechanism: these three are short,
  everyday trips (241-1,354m) in areas that are already mostly dedicated
  pedestrian infrastructure, so there simply isn't a meaningfully
  different route to divert onto. Random sampling draws from the *whole*
  graph, including longer cross-town pairs where a friction-aware detour
  has more opportunity to pay off; a person's actual daily walk is
  usually short and already near-optimal. Worth stating plainly in the
  report rather than as a null result: the model doesn't invent savings
  where there's no real alternative route to find.

  Maastricht is the one pair long and complex enough to look like #2's
  aggregate behavior (3.3km, 2.4% friction savings, 86.8% overlap) — the
  most apples-to-apples personal validation of the five.

  **Sabancı's result directly corroborates the #4 follow-up above**: a
  real 241m walk on campus, dead straight (circuity 1.065), zero friction
  cost to route around — exactly the "genuinely easy to get around" read
  that motivated distance-detrending the circuity component in the first
  place, now confirmed on an actual route rather than just an aggregate
  correction.

  The two caveated pairs are worth naming plainly rather than averaging
  in unremarked: Lanaken's 7,063m "route" stops ~3.6km short of the real
  LIDL (Maasmechelen is outside the cached network — this number
  describes how far the model's own network extends toward it, not a
  real walk to that store), and Matosinhos's Castelo do Queijo result
  lands on a node ~611m from the actual landmark (partly the ~390m
  parish-boundary gap, partly ordinary snap distance). Both are flagged
  in `personal_path_validation.csv`'s snap-distance columns, not silently
  averaged into a clean-looking number.
