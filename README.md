# ENGIE L3 Take-Home — Solar & Wind Dashboard

> This README is a decision log, not a setup guide. It records what was built and why, in the
> order a 15-minute walkthrough would cover it.

## 1. What this is / how to run it

A pipeline that pulls 30 days of hourly solar radiation and wind speed for three contrasting
sites from Open-Meteo, cleans it, flags anomalies with a two-tier detector, and serves it in a
Streamlit dashboard with a second tab: a tool-calling AI agent over the same store (§8).

```bash
make install   # venv + pinned deps
make dev       # rebuilds the store if needed, then serves the dashboard
```

A raw-data snapshot is committed under `data/raw/` and the built store is committed too, so
`make dev` works offline — no API key or network access needed for the dashboard tab. The AI
tab works too, without a key, via its keyless fallback (§8); drop an `ANTHROPIC_API_KEY` in
`.env` (see `.env.example`) for the live model path. `make pipeline` re-runs fetch → clean →
anomaly → store by hand; `make test` runs invariant checks against the store; `make inject-faults`
builds the fault-injection demo DB (§3, §6).

## 2. Site selection and why

**Atacama Desert, CL** (-23.4, -68.0), **North Sea Coast, UK** (56.5, -2.0), and **Luzon, PH**
(14.6, 121.0) — one site each that is solar-dominant, wind-dominant, and tropical-mixed, chosen
by probing the live API for contrast rather than guessing. The three also span opposite
hemispheres, so the same calendar month is a different season at each site — the strongest
available argument for why the anomaly detector's fences must be conditioned per site rather
than computed globally (see §6).

## 3. Data source notes

- **Endpoint:** `https://api.open-meteo.com/v1/forecast` — confirmed live against the running
  API rather than trusting the build spec's or the docs page's hypothesized names, per the
  spec's own instruction to verify at build time.
- **Window:** explicit `start_date`/`end_date` (today−30 to today−1) rather than `past_days=30`,
  which pulls in today's partial/forecast day and breaks the "past 30 days" requirement.
- **Variables:** `shortwave_radiation` (global horizontal irradiance, W/m²), `terrestrial_radiation`
  (top-of-atmosphere radiation, W/m² — used for the daylight boundary, see §6), `wind_speed_100m`
  (turbine hub height; requested in m/s via `wind_speed_unit=ms` — Open-Meteo's default is km/h,
  so the API converts server-side rather than us converting client-side). `wind_speed_100m` is not
  listed on the public docs page (which shows 10/80/120/180m) but resolves correctly on the live
  endpoint.
- **Timezone:** each site is fetched in its own IANA timezone (`America/Santiago`,
  `Europe/London`, `Asia/Manila`), not a fixed UTC offset — hour-of-day bucketing in the anomaly
  detector depends entirely on this being correct, since a timezone bug would silently shift every
  site's "noon" and destroy the conditioning logic in §6.
- **Model output, not sensor telemetry.** Open-Meteo serves reanalysis/model output, which is
  already quality-controlled and smooth. Verified directly: the real 30-day window across all
  three sites has zero null values and zero Tier-1 physical-validity violations. This means the
  anomalies this dashboard surfaces are *meteorological events* (cloud cover, calm spells), not
  instrument faults — a production system pointed at SCADA or inverter telemetry is where the
  physical-validity tier would actually earn its keep. To make that tier demonstrable anyway,
  `src/inject_faults.py` is an explicit, opt-in script that writes a clearly separate,
  gitignored `data/demo_faulty.duckdb` with 6 hand-injected faults — it never runs automatically
  and never touches the real dataset. The dashboard shows an unmistakable banner whenever that
  file is loaded.

  **Scope note:** the two-tier detector and this fault-injection script go beyond the original
  brief's literal ask (a single anomaly flag, z-score or IQR). They were added deliberately, in a
  prior planning session, specifically because a more sophisticated detector creates a problem
  the brief doesn't warn about — Tier 1 would sit empty in a live demo on Open-Meteo's clean
  model output. Kept because it demonstrates the judgment the brief says it's grading, stays off
  by default, and is unmistakably labeled synthetic — not because bigger is better.

## 4. Cleaning and normalisation decisions

The brief asks to both "handle outliers" during cleaning and "flag anomalies" on the same data —
taken literally, cleaning would delete the very thing the flag exists to surface. **Resolution:
never delete a row.** Two separate columns carry the two separate concerns:
- `quality_flag` — was this value repaired during cleaning? (`ok` / `interpolated` / `missing`)
- `is_anomaly` — was this value flagged by the detector? (see §6)

Null handling: gaps of **2 consecutive hours or fewer** are linearly interpolated and marked
`quality_flag='interpolated'`. Longer gaps are left null (`quality_flag='missing'`) rather than
fabricated. The 2-hour threshold is the spec's own example value, adopted directly. On real data
this path never fires (zero nulls, see §3); it's exercised via the fault-injection script.

## 5. Storage choice

**DuckDB**, embedded, single file. Matches the analytical query pattern here (aggregate,
filter, group-by over a few thousand rows) with no server to run and a trivial dependency for a
reviewer's clean clone. At production scale — real telemetry at higher cadence, many more
sites, continuous ingestion instead of one 30-day batch — this would move to partitioned Parquet
on object storage with DuckDB or a warehouse querying over it, or a purpose-built time-series
store (TimescaleDB, InfluxDB) if sub-minute ingestion and retention policies became a
requirement; the batch-refresh pipeline here would become an incremental/streaming load.

**Schema (critical):** anomaly flags, tiers, reasons, and fence bounds are computed once by
`src/anomaly.py` and persisted as columns — never recomputed at render time. The dashboard and
the AI agent (Part 2) read the same `observations` table, so they cannot disagree with each
other about what's anomalous.

```
timestamp, site_id, site_name, metric, value, unit,
quality_flag, is_anomaly, anomaly_tier, anomaly_reason,
fence_lower, fence_upper
```

Long format (one row per timestamp × site × metric) rather than wide — simpler to query
generically across both metrics from the AI agent's tool functions in Part 2.

## 6. Anomaly detection

### Method: IQR, not z-score

- **Masking.** Mean and standard deviation are computed from data that includes the anomalies —
  a single spike inflates σ enough to pull the 3σ fence out past itself. IQR's quartiles have a
  ~25% breakdown point; the mean's is 1/n.
- **The 3σ threshold has no meaning here.** Its "99.7%" justification comes from the normal
  distribution. Wind speed is Weibull-shaped (right-skewed, non-negative); solar over a full 24h
  cycle is zero-inflated and bimodal. Neither is normal, so 3σ is an arbitrary number for either.
- **Symmetry is wrong for wind.** A symmetric fence on right-skewed data places the upper fence
  too far out to catch real events and the lower fence below zero, where it can never fire.
- **Global z-score inverts the truth on solar.** 950 W/m² at noon (a normal clear day) sits well
  above a global mean dragged down by twelve hours of nightly zeros and gets flagged. 300 W/m² at
  02:00 (physically impossible) sits inside 1σ and passes. The detector flags the normal thing and
  misses the impossible thing.
- **Modified z-score (median + MAD), considered and rejected.** Fixes masking but still fails
  here: on solar including night hours, the majority of values are exactly 0, so MAD → 0 and the
  statistic degenerates.

### The conditioning decision

The detector is constrained by the brief. The *input* to the detector is not. IQR fences are
computed **per site, per metric, per hour-of-day**, with solar additionally restricted to rows
where top-of-atmosphere radiation (TOA) > 0 — the actual per-row daylight boundary, not a fixed
hour label (sunrise/sunset drift a few minutes across the 30-day window, so a fixed hour near
dawn/dusk is daylight on some days and not others; TOA catches this correctly where an `is_day`
flag does not — see §3/build-log for the 26-case verification). A noon reading is compared only
against other noon readings at that site.

Without this, night zeros poison the daytime statistics for solar: Q1 = Q3 = 0, IQR = 0, and
every nonzero daytime value becomes an "outlier." Wind is still right-skewed within an hour
bucket, so it uses the wider **3× IQR "far out" fence** rather than a log transform — a genuine
18 m/s hour is the most operationally interesting data a wind farm produces, not an error to
suppress. Solar uses the standard 1.5× Tukey fence. ~30 observations land in each bucket (30
days), thin but workable for quartiles, and a further argument against z-score — an SD estimated
from 30 contaminated points is noise. Buckets with fewer than 4 valid observations (after
excluding Tier-1 violations and nulls) get no fence rather than one computed from too few points.

### Two-tier output

**Tier 1 — physical validity.** Deterministic rules, no statistics — these are *certainly*
wrong: negative radiation; radiation > TOA × 1.05 (5% margin for interpolation/model noise at
the boundary, not 1.0, so the rule doesn't fire on rounding); non-zero radiation while TOA = 0;
negative wind; wind > 60 m/s (generous ceiling for a reanalysis product); and a flatline/
stuck-sensor rule — an identical value repeated for ≥6 consecutive *valid* hours (daylight hours
for solar, all hours for wind, so a legitimate night-zero run is never flagged). This is a
collective anomaly, invisible to any point detector, and cheap to implement via a run-length
check.

**Tier 2 — contextual statistical anomaly.** The conditional IQR fences above. Semantics:
"unusual for this site at this hour, worth a review" — exactly the brief's own language.

**Why two tiers:** an analyst needs to distinguish a broken sensor from a weather event, because
those have different owners and different responses. A single boolean can't express that.
`anomaly_reason` carries a human-readable explanation for every flagged row, because the AI
agent in Part 2 surfaces it directly to the user rather than recomputing anything.

### Rejected approaches

- **Isolation Forest / LOF / autoencoders:** ignores the brief's own instruction; unexplainable
  ("the model says so" is not actionable for an analyst); ungroundable for Part 2, since the
  agent could not explain *why* a point was flagged; would underperform well-conditioned IQR on
  ~720 rows per site anyway.
- **STL / seasonal decomposition:** textbook-correct for daily seasonality, but hour-of-day
  bucketing achieves the same stationarity in a few lines with no library and no explanation cost
  in a 15-minute walkthrough. Named as the production upgrade path (§10).

## 7. Anomaly taxonomy and out-of-scope

- **Point** anomalies — handled by conditional IQR.
- **Contextual** anomalies — handled by conditioning on site and hour-of-day.
- **Collective** anomalies (flatlines, stuck sensors, drift) — flatlines and stuck sensors
  handled by the Tier-1 delta/run-length rule; slow drift is acknowledged as not handled.
- **Spatial** anomalies (one site diverging from neighbours under the same weather system) —
  out of scope here, and the highest-value next addition (§10).

## 8. AI agent design

### Architecture: tool calling, not text-to-SQL

The model is constrained to intent classification and parameter extraction; all computation is
deterministic. The answer set is therefore closed, and hallucination is structurally excluded
rather than prompted against. `src/agent.py` runs a manual tool-use loop against
`claude-sonnet-5` (the Anthropic Python SDK) over exactly 4 typed Python functions in
`src/tools.py` — the model never writes a query, never touches a number, and every figure the
user sees was produced by a `duckdb` query in this repo, not generated text.

**Rejected: text-to-SQL.** Most failure-prone pattern on a small schema; requires a guard layer
(read-only enforcement, injection defence, syntax retries, query timeouts); and fails
*invisibly* — a subtly wrong `WHERE` clause returns plausible numbers that are simply not the
answer.

**Rejected: RAG over pre-computed summaries.** Adds a retrieval layer that can miss, and still
cannot answer arithmetic questions reliably.

### Tool surface — exactly 4

| Function | Answers | Parameters |
|---|---|---|
| `get_site_ranking` | "Which site had the highest average solar radiation last week?" | metric, aggregation, date range, sort direction |
| `get_anomalies` | "Were there anomalous wind readings at a site in the last 7 days?" | site, metric, date range |
| `compare_sites` | "Compare generation potential across all three sites for the past month." | metric, date range, site list |
| `get_summary_stats` | min/max/mean/count for one site, one metric, one window | site, metric, date range |

`get_summary_stats` drops the aggregation choice and always returns all four statistics together
— it's the catch-all stats lookup, and there's no reason to make the model pick one when the
query computes all four for free.

Every aggregate (`get_site_ranking`, `compare_sites`, `get_summary_stats`) excludes Tier-1-flagged
rows — physically impossible readings must never enter a mean or min/max — but keeps Tier-2 rows,
since those are real meteorological events (§6), not noise. `get_anomalies` is the one exception:
it reads the persisted `is_anomaly`/`anomaly_tier`/`anomaly_reason` columns directly and never
recomputes them, which is the actual payoff of §3/§6's storage decision — Parts 1 and 2 were
designed as one system, not bolted together.

**"Generation potential" is a documented proxy, not a power estimate.** Wind power scales with
the cube of velocity, and solar output depends on a capacity/efficiency assumption neither raw
metric captures alone — so `compare_sites` reports relative irradiance/wind-speed as a proxy for
generation potential; a true estimate requires turbine power curves and panel capacity/efficiency
data, which are out of scope here. This sentence is stated once in the tool's own response
payload (so the model surfaces it verbatim, not paraphrased away) and once here.

### Two rules that make this look deliberate

1. **Date resolution is a deterministic tool, not a prompt instruction.** "Last week," "the past 7
   days" resolve to one of a fixed enum (`last_7_days`/`last_14_days`/`last_30_days`/`all`), which
   `src/dates.py` resolves against `MAX(timestamp)` in the store — never `datetime.now()`, never
   left to the LLM. The model classifies phrasing onto the enum; it performs no date arithmetic at
   all. An absolute-date question ("August 5 to August 10") falls through to the refusal path
   below — a real limit, demonstrated rather than hidden.
2. **`get_anomalies` reads the persisted flag columns, full stop.**

### Refusal is a feature

Every tool returns a structured result plus an explicit `no_data` signal — see the North Sea /
wind transcript entry below, a real no-anomalies case in the live data, not a hypothetical. When
no tool matches the question at all, the system prompt instructs the model to say so plainly
rather than guess or force-fit a tool. See the last transcript entry.

### Presentation and the keyless fallback

Second tab in `app.py` (`st.tabs(["Dashboard", "AI Assistant"])`), same app, not a separate
service. Preset buttons cover all 4 tools; free text is available alongside them. Every answer
renders an expander with the resolved tool call (function name + parameters) beneath it — the
grounding claim made visible, not just asserted.

**Without a key**, the app doesn't degrade to broken: a banner explains the missing key, and the
preset buttons are wired directly to the tools (`src/tools.py`'s `TOOL_DISPATCH`) with no model
involved — a reviewer cloning this repo with no `ANTHROPIC_API_KEY` still sees the tools work,
just without a model turning the result into a sentence. Free text is disabled in this mode, since
parsing arbitrary phrasing genuinely needs the model.

**A key being present doesn't mean it still works** — credit can run out mid-session. `app.py`'s
`handle_question()` tries the live call first, and if it fails on a preset question (bad key,
exhausted credit, rate limit, network), it falls back to the same direct-tool path used when
there's no key at all, with a caption naming the failure instead of a repeated error. Verified by
simulating a dead key end-to-end: the preset still returned the correct real numbers via the
fallback path. Free text has no fixed tool mapping to fall back to, so it shows a plain error
message rather than crashing.

### Transcript (live, `claude-sonnet-5`, real API key)

**Q: "Which site had the highest average solar radiation last week?"**
Resolved tool call: `get_site_ranking(metric=solar_radiation, aggregation=mean, date_range=last_7_days, sort_direction=desc)`
> Over the last 7 days (Aug 24–30, 2026), **Atacama Desert, CL** had the highest average solar
> radiation at **266.55 W/m²**, followed by Luzon, PH (180.52 W/m²) and North Sea Coast, UK
> (140.32 W/m²).

**Q: "Were there anomalous wind readings at the North Sea Coast site in the last 7 days?"**
Resolved tool call: `get_anomalies(site=northsea_uk, metric=wind_speed, date_range=last_7_days)`
> No anomalous wind speed readings were flagged at the North Sea Coast, UK site over the last 7
> days.

This is `get_anomalies`' `no_data: true` path firing for real — North Sea/wind has zero anomalies
in the live 30-day window (all its Tier-2 flags belong to solar and to wind at the other two
sites). Nothing was hidden or suppressed to manufacture this example.

**Q: "Compare generation potential across all three sites for the past month."**
Raw model output *before* the tool call ran (distinct from the final answer below — shown here
per the requirement to log raw output alongside a resolved call at least once):
> I'll compare both solar radiation and wind speed across all three sites for the past 30 days,
> since "generation potential" could apply to either solar or wind resources at each location.

Resolved tool call: `compare_sites(metric=solar_radiation, date_range=last_30_days)` — the model
called `compare_sites` twice in parallel (once per metric, its own initiative, not prompted) and
synthesized both results into one answer:
> **Solar Radiation (avg, W/m² proxy):** Atacama Desert 234.57, Luzon 181.07, North Sea Coast
> 177.74 → Atacama Desert has the strongest solar generation potential by a clear margin.
> **Wind Speed (avg, m/s proxy):** North Sea Coast 7.88, Luzon 7.71, Atacama Desert 3.28 → North
> Sea Coast and Luzon show similar, much higher wind potential than Atacama Desert.
> *Note: these figures are relative irradiance/wind-speed averages used as a proxy for generation
> potential, not actual power output estimates.*

This question also caught a real bug: the manual loop's first pass only executed one `tool_use`
block per turn. The API's parallel-tool-use default means one turn can carry several — leaving the
second unresolved gets the next request rejected with a 400. Fixed by executing every `tool_use`
block in a turn and returning all `tool_result` blocks together in one message, per the API's own
contract (`ai-artifacts/build-log.md` has the full account).

**Q: "What's the min, max, mean and count of wind speed at the Atacama Desert site this week?"**
Resolved tool call: `get_summary_stats(site=atacama_cl, metric=wind_speed, date_range=last_7_days)`
> Min: 0.1 m/s · Max: 11.94 m/s · Mean: 3.17 m/s · Count: 168 readings

**Q: "What will solar radiation be tomorrow?"** (out-of-scope, demonstrating the refusal path)
Resolved tool call: none — no tool matches a forecast question.
> I can't answer that — the tools I have only report historical/observed solar radiation and wind
> speed data (summary stats, rankings, comparisons, and anomalies) for the three sites. None of
> them provide forecasts, so I can't tell you what solar radiation will be tomorrow.

## 9. Known issues and limitations

- Real Open-Meteo data over the actual 30-day window has zero Tier-1 violations — the dashboard's
  physical-validity tier is demonstrated only via the explicit, clearly-labelled fault-injection
  script (`src/inject_faults.py`), not on the real data a reviewer sees by default.
- Fixed numeric thresholds (2h short-gap cutoff, 6h flatline window, 60 m/s wind ceiling, 1.05×
  TOA margin, 4-observation minimum bucket size) are stated and justified in §4/§6 but are not
  tuned against any ground truth — there is no labeled fault dataset to validate against, only
  physical reasoning.
- Hour-of-day IQR buckets have ~30 observations each (one 30-day window) — workable for
  quartiles but thin; a longer history would give materially more stable fences.
- The dashboard shows one site and one metric at a time by design (§ dashboard build-log entry)
  for walkthrough clarity; it does not offer a cross-site overlay view.
- No seasonal validation: the 30-day window is a single season at each site, so the hour-of-day
  conditioning is not tested across a full annual cycle at any site.
- Interpolated values are not exempted from Tier-1 checks (confirmed via fault injection — an
  interpolated pre-dawn value at Atacama itself breached the TOA ceiling and was correctly
  flagged). This is intentional, not a bug, but worth naming since it means `quality_flag` and
  `is_anomaly` can both be set on the same row.

## 10. What I'd do differently with more time

- **Clearness index (§4.4 of the build spec):** divide GHI by TOA radiation to get a
  site/time-independent index (~0–1), apply IQR to that instead of raw GHI, and get a free hard
  rule (index > 1 is impossible) alongside the existing physical-validity checks.
- **Spatial cross-site consistency:** flag a site diverging from its neighbours under the same
  regional weather system — the taxonomy gap named in §7, and probably the single highest-value
  next addition for an operator running many sites.
- **STL/seasonal decomposition** once enough history exists to make daily and seasonal
  components separable — hour-of-day bucketing is the right lightweight substitute for a 30-day
  window, not a long-term replacement.
- **Real telemetry ingestion** (SCADA/inverter data) so Tier 1 has something to actually catch
  without needing the fault-injection script.

## 11. AI usage

This project was built with Claude Code (Opus 5, then Sonnet 5) against a build spec
(`ai-artifacts/ENGIE_Takehome_Build_Spec.md`) authored by the candidate in a prior planning
session. All prompts, redirections, and the resulting build decisions — including the ones that
override or add detail beyond what the spec assumed — are recorded verbatim in `/ai-artifacts/`:

- `ENGIE_Takehome_Build_Spec.md` — the settled design brief this session executed against.
- `prompts.md` — every instruction and redirection given during the build session, verbatim.
- `build-log.md` — the distilled decision record: what was built, and for anything not pinned
  down explicitly in the spec, the specific choice made and why.
