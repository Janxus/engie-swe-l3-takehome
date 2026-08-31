# ENGIE L3 Take-Home — Build Specification & Handoff

> **Purpose.** This file is the authoritative build brief for the coding agent. All architectural
> and analytical decisions below were settled in a prior planning session. Do **not** re-litigate
> them. If you believe a decision is wrong, flag it and stop — do not silently substitute an
> alternative.
>
> **Audience:** VS Code coding agent.
> **Author of decisions:** Aldjan W. Mararac (candidate).
> **Source brief:** `swe-l3-takehome_rev1.pdf` — Senior Software Engineer (Backend) L3,
> IS Data & Model Services, ENGIE Services Philippines.

---

## 0. Non-negotiable submission requirements

These are graded independently of code quality. Verify each before submission.

- [ ] GitHub repository. Public, **or** private with access granted to `bongvaldozjr@gmail.com`.
- [ ] **Commit all AI-assist artifacts** — prompts, instructions, agent configs, this spec file.
      The brief asks for this explicitly. It is a scored dimension, not a compliance checkbox:
      they are evaluating *how the candidate works with AI tools*. Put them in `/ai-artifacts/`.
- [ ] README written as a **decision log**, not a setup guide. See §7.
- [ ] Known issues documented in the README. The brief states this is "useful signal."
- [ ] Repo must run from a clean clone. Pin dependencies. Include a data-fetch step or committed
      snapshot so a reviewer without an API key still sees a working dashboard.
- [ ] **Commit incrementally, with real messages, throughout the build — not one paste-in commit
      at the end.** Reviewers read commit history as part of the assessment, especially given they
      explicitly asked for AI-assist artifacts to be committed. A single "initial commit" tells
      them nothing about process; a real history does.

**Reviewer context:** a 15-minute walkthrough follows. Optimise for *explainability in 15 minutes*,
not for feature count.

---

## 1. Scope

### Part 1 — mandatory
1. Pull hourly **solar radiation** and **wind speed** from the Open-Meteo API for **three
   locations**, **past 30 days**.
2. Clean and normalise: nulls, unit consistency, outliers.
3. Persist to a local store (SQLite / DuckDB / Parquet).
4. Dashboard with: site selector, date-range selector, time-series chart, anomaly flag.
5. README justification of the anomaly method chosen.

### Part 2 — bonus, **in scope, build it**
Conversational interface over the same store. Grounded answers only.

### Hard sequencing rule
**Part 2 does not begin until Part 1 is complete, working, committed, and its README sections
drafted.** Not "nearly done." If Part 2 goes sideways, it is deleted and a complete mandatory
deliverable ships. A half-finished bonus bolted onto a rushed Part 1 is the worst outcome.

### Explicit non-goals
- No authentication, no user accounts, no deployment/hosting.
- No ML-based anomaly detection (Isolation Forest, LOF, autoencoders). Rejected — see §4.5.
- No text-to-SQL. Rejected — see §5.1.
- No forecasting. The tool reports on historical data only.
- Do not over-polish. The brief says so directly.

---

## 2. Data acquisition

### Source
Open-Meteo. **Verify endpoint and variable names against current docs at build time** — do not
trust the names below without checking, they are a starting hypothesis only.

Expected shape:
- Historical hourly data. Note that the ERA5 archive endpoint has a multi-day ingestion lag,
  which may make a strict "past 30 days" window impossible from that endpoint alone. The
  forecast endpoint's `past_days` parameter is the likely correct route. **Confirm this.**
- Solar: global horizontal irradiance, W/m². Likely `shortwave_radiation`.
- Wind: likely `wind_speed_10m` and/or `wind_speed_100m`. Pick one and state which in the README;
  100m is more relevant to turbine hub height if available.
- Optional for §4.4: top-of-atmosphere / extraterrestrial radiation, likely `terrestrial_radiation`.

Record in the README: exact endpoint, exact variable names, units as returned, and timezone
handling.

### Site selection
Three locations, chosen deliberately, **not arbitrarily**. Select for contrast — e.g. differing
climate regimes or differing latitudes — so that cross-site differences in the dashboard are
meaningful rather than noise. State the rationale in the README in two sentences.

### Timezone
Fetch in a fixed, explicit timezone and store timestamps unambiguously. Hour-of-day bucketing
(§4.2) depends entirely on this being correct. A timezone bug silently destroys the anomaly logic.

---

## 3. Cleaning, normalisation, storage

### The brief's internal contradiction — resolve it explicitly
Part 1 asks to "handle outliers" during cleaning **and** to flag anomalies on the data. Taken
literally, cleaning would delete the very thing the flag exists to surface.

**Resolution: never delete a row.** Flag and retain. Maintain separate columns:
- `quality_flag` — was this value repaired/imputed/interpolated during cleaning?
- `is_anomaly` — was this value flagged by the detector? (see §4)

Document this resolution in the README. Noticing and resolving the contradiction is worth points.

### Null handling
Short gaps: interpolate, and mark `quality_flag`. Long gaps: leave null, do not fabricate.
Define "short" explicitly (e.g. ≤2 consecutive hours) and state the threshold in the README.

### Storage
Any of SQLite / DuckDB / Parquet is acceptable. **DuckDB is the recommended default** — analytical
query patterns, trivially embedded, no server. State the choice and the reason in one paragraph.

### Schema requirement — critical
The store must persist the anomaly results as **columns**, computed once in the pipeline:

```
timestamp, site_id, site_name, metric, value, unit,
quality_flag, is_anomaly, anomaly_tier, anomaly_reason,
fence_lower, fence_upper
```

(Exact shape is the agent's call — long vs. wide is fine. The requirement is that flags and
fence bounds are **persisted, not computed at render time**.)

**Why this is non-negotiable:** the dashboard and the AI agent must read the same flags from the
same source. If the dashboard computes flags on render, the agent in Part 2 has to recompute
them, and can then disagree with the chart on screen — which is precisely the ungrounded
behaviour the brief warns against. One computation, one truth, two consumers.

---

## 4. Anomaly detection — the centrepiece

This is the highest-value section of the submission. The brief names it as the thing to justify.

### 4.1 Method: **IQR**, not z-score. Settled.

Reasoning to reproduce in the README:

- **Masking.** Mean and standard deviation are computed from data that includes the anomalies.
  A single spike inflates σ enough to pull the 3σ fence out past itself. IQR's quartiles have a
  ~25% breakdown point; the mean's is 1/n.
- **The 3σ threshold has no meaning here.** Its "99.7%" justification comes from the normal
  distribution. Wind speed is Weibull-shaped (right-skewed, non-negative). Solar over a full
  24h cycle is zero-inflated and bimodal. Neither is normal, so 3σ is an arbitrary number.
- **Symmetry is wrong for wind.** A symmetric fence on right-skewed data places the upper fence
  too far out to catch real events, and the lower fence below zero — where it can never fire.
- **Global z-score inverts the truth on solar.** 950 W/m² at noon (a normal clear day) sits well
  above a global mean dragged down by twelve hours of nightly zeros, and gets flagged.
  300 W/m² at 02:00 (physically impossible) sits inside 1σ and passes. The detector flags the
  normal thing and misses the impossible thing. **Put this example in the README verbatim.**
- **Modified z-score (median + MAD)** fixes masking but still fails here: on solar including
  night hours the majority of values are exactly 0, so MAD → 0 and the statistic degenerates.
  Mention as considered-and-rejected; do not implement.

### 4.2 The conditioning decision — more important than the method

> The detector is constrained by the brief. The *input* to the detector is not.

Compute IQR fences **per site, per metric, per hour-of-day**, with solar restricted to daylight
hours. A noon reading is compared only against other noon readings at that site.

This single change removes both of the failure modes above:
- Solar: night zeros no longer poison the daytime statistics. (Without this, Q1 = Q3 = 0,
  IQR = 0, and *every* nonzero daytime value becomes an "outlier" — the method degenerates
  completely.)
- Wind: still right-skewed within an hour bucket, so **either** use the 3×IQR "far out" fence for
  wind, **or** apply IQR to log-transformed wind speed. Pick one, justify in one sentence.
  Rationale: for a wind farm, a genuine 18 m/s hour is the most operationally interesting data
  there is, not an error to suppress.

Expect ~30 observations per bucket. That is thin but workable for quantiles, and it is a further
argument against z-score (an SD estimated from 30 contaminated points is noise).

A **rolling/windowed IQR (Hampel-style)** is an acceptable alternative or complement to hour-of-day
bucketing — it is still IQR and still inside the brief. Agent's call; document whichever is used.

### 4.3 Two-tier output — required

**Tier 1 — physical validity.** Deterministic rules, no statistics. These are *certainly* wrong:
- Negative radiation.
- Non-zero radiation when the sun is below the horizon.
- Negative wind speed.
- Values above a physical ceiling (state the ceilings used and where they came from).
- **Flatline / stuck-sensor check:** identical value repeated for N consecutive hours, or an
  all-zero wind day. This is a *collective* anomaly — invisible to any point detector, cheap to
  implement via an hour-over-hour delta rule. Include it.

**Tier 2 — contextual statistical anomaly.** The conditional IQR fences from §4.2.
Semantics: "unusual for this site at this hour, worth a review" — which is exactly the language
the brief uses ("flag anomalies for review").

**Why two tiers:** an analyst opening the dashboard needs to distinguish a broken sensor from a
weather event, because those have different owners and different responses. A single boolean
cannot express that. `anomaly_reason` should carry a human-readable explanation, because the AI
agent in Part 2 will surface it directly to the user.

### 4.4 Optional upgrade — clearness index
If time permits: divide measured GHI by top-of-atmosphere radiation to get a clearness index
(≈0–1), comparable across all hours and all sites, then apply IQR to *that*. Gives a physically
meaningful normalisation plus a free hard rule (index > 1 is impossible → definite data error).
**Optional. §4.2 already captures most of the value.** Do not let this delay Part 1 completion.

### 4.5 Rejected approaches — one line each in the README
- **Isolation Forest / LOF / autoencoders:** ignores the brief's instruction; unexplainable
  ("the model says so" is not actionable for an analyst); ungroundable for Part 2, since the
  agent could not explain *why* a point was flagged; and would underperform well-conditioned
  IQR on ~720 rows per site anyway.
- **STL / seasonal decomposition:** textbook-correct for daily seasonality, but hour-of-day
  bucketing achieves the same stationarity in a few lines with no library and no explanation
  cost in a 15-minute walkthrough. Name it as the production upgrade path.

### 4.6 Anomaly taxonomy — include this paragraph in the README
State plainly what is and isn't covered:
- **Point** anomalies — handled by conditional IQR.
- **Contextual** anomalies — handled by conditioning on site and hour-of-day.
- **Collective** anomalies (flatlines, stuck sensors, drift) — flatlines handled by the Tier 1
  delta rule; slow drift acknowledged as not handled.
- **Spatial** anomalies (one site diverging from neighbours under the same weather system) —
  out of scope here, and named as the highest-value next addition.

This paragraph demonstrates command of the problem space and costs nothing to write.

### 4.7 Expectation management — important
Open-Meteo returns **model/reanalysis output, not sensor telemetry.** It is already
quality-controlled and smooth. Tier 1 rules will fire rarely or never, and the anomaly panel may
look empty during the walkthrough.

Two required responses:
1. **State it in the README.** Anomalies detected here are *meteorological events*, not
   instrument faults. A production version pointed at SCADA or inverter telemetry is where the
   physical-validity tier earns its keep. (ENGIE owns real generating assets — this distinction
   is domain-relevant, not a caveat.)
2. **Optionally ship a fault-injection script** — clearly labelled, toggleable, off by default —
   that corrupts a handful of rows so the detector can be demonstrated firing. Label it
   unmistakably so no reviewer could mistake synthetic data for real.

---

## 5. Part 2 — AI agent

### 5.1 Architecture: tool/function calling over typed functions. Settled.

The model's **only** responsibilities are intent classification and parameter extraction. It never
computes, never writes queries, never touches numbers. Every figure the user sees was produced by
deterministic Python.

README line to include, near-verbatim:
> The model is constrained to intent classification and parameter extraction; all computation is
> deterministic. The answer set is therefore closed, and hallucination is structurally excluded
> rather than prompted against.

**Rejected: text-to-SQL.** Most failure-prone pattern on a small schema; requires a guard layer
(read-only enforcement, injection defence, syntax retries, query timeouts); and fails
*invisibly* — a subtly wrong `WHERE` clause returns plausible numbers that are simply not the
answer. Note the rejection and the reason in the README.

**Rejected: RAG over pre-computed summaries.** Adds a retrieval layer that can miss, and still
cannot answer arithmetic questions reliably.

### 5.2 Tool surface — exactly these four. Do not add a fifth.

Derived directly from the sample questions in the brief.

| Function | Answers | Parameters |
|---|---|---|
| `get_site_ranking` | "Which site had the highest average solar radiation last week?" | metric, aggregation, date range, sort direction |
| `get_anomalies` | "Were there anomalous wind readings at Site B in the last 7 days?" | site, metric, date range |
| `compare_sites` | "Compare generation potential across all three sites for the past month." | metric, date range, site list |
| `get_summary_stats` | Catch-all: min/max/mean/count, one site, one metric, one window | site, metric, aggregation, date range |

### 5.2a `compare_sites` — the "generation potential" wording is a trap, resolve it explicitly

The sample question says *generation potential*, not "irradiance" or "wind speed." Those are not
the same thing: wind power scales with the **cube** of velocity, and solar output depends on a
capacity/efficiency assumption neither raw metric captures on its own. Silently reporting raw
GHI/wind-speed aggregates and calling it "generation potential" is imprecise; silently computing
a fabricated power number with no stated turbine curve or panel capacity is worse — it looks like
a made-up figure to a reviewer who works in energy.

**Resolution:** report the raw metric aggregates as a **documented proxy**, and say so in the
tool's response text and in the README — e.g. "reporting relative irradiance/wind-speed as a
proxy for generation potential; a true estimate requires turbine power curves and panel
capacity/efficiency data, which are out of scope here." One sentence, stated once, in both places.
This is a domain-credibility signal for an energy company — do not skip it.

### 5.3 Two rules that make this look deliberate

1. **Date resolution is a deterministic tool, not a prompt instruction.** "Last week," "past 7
   days," "the last month" are resolved by a Python helper **against the max timestamp present in
   the dataset**, not by the LLM and not against the system clock. LLMs are unreliable at date
   arithmetic, and the data ends whenever the last fetch ran. This eliminates a whole class of
   quiet wrong answers.
2. **`get_anomalies` reads the persisted flag columns from §3.** It does not recompute. This is
   the payoff of the storage decision and should be called out in the walkthrough as evidence
   that Parts 1 and 2 were designed as one system.

### 5.4 Refusal is a feature, not an edge case
Every tool returns structured results **plus an explicit empty/no-data signal**. When no tool
matches, or a tool returns nothing, the agent says so plainly — "I don't have data to answer
that" / "No anomalies were flagged at Site B in that window."

**Demonstrate it.** Include an out-of-scope question in the README transcript (e.g. "what will
solar radiation be tomorrow?") and show the refusal holding. Most submissions showcase only
successes; showing the boundary hold is the stronger signal.

### 5.5 Presentation
- Same app as the dashboard — second tab or side panel. Not a separate service, not a CLI.
- Include **preset example buttons** alongside free-text input, so a reviewer sees it work on
  first contact rather than gambling on their own phrasing.
- **Render the resolved tool call** (function name + parameters) beneath each answer. Auditability,
  and it makes the grounding claim visible rather than merely asserted.
- **Keyless fallback:** if no API key is present, the app must not crash. Degrade to the preset
  buttons wired directly to the tools, with a banner explaining the key is missing. A reviewer
  cloning without a key must not see a broken bonus section.
- Log raw model output alongside the resolved tool call at least once in the README transcript.

---

## 6. Dashboard requirements

Minimum, per the brief: site selection, date-range selection, time-series chart, anomaly flag.

Additions that are cheap and worth it:
- Visually distinguish **Tier 1** from **Tier 2** anomalies on the chart.
- Surface `anomaly_reason` on hover or in a side table.
- Show the IQR fence bounds as a shaded band behind the series — makes the detector's behaviour
  legible at a glance and is the single best visual for the walkthrough.

Framework is the agent's call. Prioritise something that runs from a clean clone with minimal
setup.

---

## 7. README structure — the primary artifact

The brief states they care more about decisions and explanation than clean code. Write
accordingly. Order:

1. **What this is / how to run it** — five lines maximum. Do not let setup dominate the top.
2. **Site selection and why.**
3. **Data source notes** — endpoint, variables, units, timezone, and the model-output-vs-telemetry
   observation (§4.7).
4. **Cleaning and normalisation decisions** — including the flag-don't-delete resolution (§3).
5. **Storage choice** — one paragraph, plus what would change at production scale.
6. **Anomaly detection** — the longest section. Method, why not z-score, why not modified
   z-score, the conditioning argument, the wind-skew adjustment, the two-tier structure.
7. **Anomaly taxonomy and out-of-scope** (§4.6).
8. **AI agent design** — the architecture claim, plus a transcript including one refusal.
9. **Known issues and limitations** — specific and unhedged. The brief asked for this by name.
10. **What I'd do differently with more time** — STL, spatial cross-site consistency, clearness
    index, real telemetry ingestion.
11. **AI usage** — where AI assistance was used, pointing to `/ai-artifacts/`.

Sections 6 and 7 are what the 15-minute walkthrough will be about. Write them to be read aloud.

**Tone instruction:** the candidate's known failure mode is underselling — describing owned
architecture as "I just built stuff." The README must state decisions in the active voice with
their reasoning, without hedging. Not "I tried using IQR" — "I selected IQR because [reason]."

---

## 8. Definition of done

- [ ] Pipeline fetches, cleans, and loads 3 sites × 2 metrics × 30 days.
- [ ] Anomaly flags, tiers, reasons, and fence bounds **persisted in the store**.
- [ ] Dashboard: site selector, date range, time-series chart, anomaly flags visible and
      tier-distinguished.
- [ ] README sections 1–7 and 9–11 written.
- [ ] Repo runs from a clean clone.
- [ ] **← Part 1 complete. Commit here. Everything below is bonus.**
- [ ] Four tools implemented against the persisted store.
- [ ] Deterministic date resolution against dataset max timestamp.
- [ ] Refusal path implemented and demonstrated.
- [ ] Keyless fallback implemented.
- [ ] README section 8 written with transcript.
- [ ] `/ai-artifacts/` committed, including this file.
- [ ] Access granted to `bongvaldozjr@gmail.com` if the repo is private.
