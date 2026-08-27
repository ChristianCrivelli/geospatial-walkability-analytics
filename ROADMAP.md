# Roadmap

This project has two audiences by design (see README): a planner/
policymaker who wants the findings, and a technical reviewer who wants to
see the engineering hold up. The roadmap below is sequenced so that the
*methodology* (how "walkability" gets scored) stabilizes before the
project scales out to more cities or more polished visuals — validating
five cities properly beats generalizing to fifty prematurely.

Each phase links to its tracked issue(s) on
[GitHub Issues](https://github.com/ChristianCrivelli/city_walkability/issues)
— the single source of truth for issue status and resolution notes.

## Phase 1 — Data pipeline ✅ Done

Pedestrian network extraction (OSMnx), infrastructure-tag filtering,
elevation enrichment, grade calculation, GraphML/GeoPackage/CSV export,
and a first static visualization — all five study locations (Maastricht,
Matosinhos, Sabancı University, Lanaken, Mindelo) processed and cached.

_Code: `pedestrian_nodes.py`, `visualise_node_maps.py`_

## Phase 2 — The True Walkability Algorithm (in progress)

The project's actual differentiator. Order matters here — each step
depends on the graph the previous step produced:

1. ✅ [**001 — Friction-weighted edges**](https://github.com/ChristianCrivelli/city_walkability/issues/1)
   (slope penalty + infrastructure penalty combined into `friction_weight`)
2. ✅ [**002 — Planned vs. Actual comparison**](https://github.com/ChristianCrivelli/city_walkability/issues/2)
   (`planned_vs_actual.py` — random O-D sampling + per-pair comparison, run against all 5 cities; POI-based sampling deferred to #5)
3. ✅ [**003 — Connectivity metrics**](https://github.com/ChristianCrivelli/city_walkability/issues/3)
   (`connectivity_metrics.py` — circuity reusing #2's O-D sample, betweenness centrality with exact/k-sample split by graph size, top-10 choke points per city)
4. ✅ [**004 — Composite True Walkability Index**](https://github.com/ChristianCrivelli/city_walkability/issues/4)
   (`true_walkability_index.py` — 4 components: friction_ratio, median_circuity, dead_end_density (new), friction_savings_pct; min-max normalized across the 5 cities, equal-weighted, `100*(1-difficulty_score)`; run against all 5 cities. Follow-up after close: median_circuity was scale-biased against small study areas — fixed with a distance-detrended `median_excess_circuity` variant, exported alongside the original rather than replacing it. See REPORT.md.)

Running alongside Phase 2, not blocking it:

- ✅ [**005 — Personal-path validation**](https://github.com/ChristianCrivelli/city_walkability/issues/5)
  (`personal_path_validation.py` — 5 real user-supplied routes run through the same Planned-vs-Actual comparison as #2. 3 of 5 show zero Planned/Actual divergence, a useful contrast with #2's random-sample averages; 2 of 5 carry real data-availability gaps — Lanaken's destination is outside the cached network entirely, Matosinhos's is ~390-610m outside it — run anyway with the gap reported, not hidden. See REPORT.md.)

## Phase 3 — Scale & polish (after Phase 2 stabilizes)

- 🔲 [**006 — Generalize to an arbitrary city**](https://github.com/ChristianCrivelli/city_walkability/issues/6)
  — deliberately sequenced last; see that issue for why.
- 🔲 [**007 — Interactive visualization**](https://github.com/ChristianCrivelli/city_walkability/issues/7)
- ✅ [**008 — Repo hygiene & portfolio polish**](https://github.com/ChristianCrivelli/city_walkability/issues/8)
  — data-storage decision recorded (leave the ~108MB as-is); filename typo
  fixed (`pedestraian_nodes.py` → `pedestrian_nodes.py`, all imports
  updated); [`METHODOLOGY.md`](METHODOLOGY.md) written, collecting every
  documented judgment call from #1–#5 in one place now that they've all
  stabilized.

## Phase 4 — The report

Not an issue — tracked as running notes in [`REPORT.md`](REPORT.md) as we
go, then written up once Phase 2's numbers exist to report on. Audience
and format (written report vs. interactive companion, see #7) still
open — see REPORT.md.

## Explicitly not yet decided

A few open methodology questions block clean progress on Phase 2 and are
tracked in detail in REPORT.md rather than buried in issue text:
what "Planned" means precisely, whether/how municipal data factors in,
and how the infrastructure-penalty and index weights get chosen and
justified (#4's equal weighting is a documented first pass, not derived —
a sensitivity check is on the report's open-questions list).

With #001-#005 all done, Phase 2 (including the personal-path validation
running alongside it) is now feature-complete. #008 (repo hygiene) is
also done. Phase 3's remaining items — #006 (generalize to an arbitrary
city) and #007 (interactive visualization) — are next up.
