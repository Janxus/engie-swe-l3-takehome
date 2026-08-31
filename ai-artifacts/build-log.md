# Build Log

Dated, terse entries. For anything not pinned down explicitly in the build spec, states the
decision made and why. Not a narrative — see `prompts.md` for the verbatim conversation record
and the README for the polished decision log.

## 2026-08-31

- **Repo scaffold.** `src/`, `data/`, `ai-artifacts/`, `.streamlit/`. Python 3.14 via Homebrew
  (system `python3` is 3.9.6, too old for current duckdb/streamlit/pandas wheels). `.venv` pinned
  to 3.14.6.
- **Dependency pins.** Installed latest compatible versions and froze them into
  `requirements.txt`: duckdb 1.5.5, pandas 3.0.5, numpy 2.5.2, streamlit 1.62.0, plotly 7.0.0,
  anthropic 1.2.0, python-dotenv 1.2.3, requests 2.34.2. No version ceiling reasoning needed —
  these are the current releases at build time and all installed clean on 3.14.

- **Open-Meteo endpoint verification (live, not docs-only).** The build spec explicitly says not
  to trust its own hypothesized variable/parameter names. Probed the live API directly:
  - `start_date`/`end_date` on `/v1/forecast` used instead of `past_days` — `past_days=30`
    pulls in today's partial/forecast day; explicit dates give exactly 720 clean historical hours
    per site (30 days x 24h), zero nulls, no forecast contamination.
  - `wind_speed_100m` exists and resolves on the live forecast endpoint despite not being listed
    on the public docs page (which only shows 10/80/120/180m) — used it directly, since 100m is
    closer to turbine hub height than the documented options.
  - Wind's default unit is km/h, not m/s as the spec assumed. Used `wind_speed_unit=ms` on the
    request so conversion happens server-side rather than in our own code.
  - `terrestrial_radiation` (top-of-atmosphere, TOA) is a strictly better day/night boundary than
    `is_day`: found 26 hours in a 30-day sample where `is_day=1` but GHI is legitimately 0
    (dawn/dusk boundary). An `is_day`-based Tier-1 rule would false-positive on physically normal
    twilight values. `TOA==0` had zero such false positives across 4 probe sites. TOA is used for
    both the Tier-1 horizon rule and daylight-hour bucketing in Tier 2.
  - Real Open-Meteo data across all 4 probe sites has zero nulls and zero Tier-1 violations —
    confirms the spec's own §4.7 warning (model/reanalysis output is already quality-controlled).
    This is why a fault-injection path is planned separately later, to make Tier 1 demonstrable.

- **Site selection** (`src/config.py`): Atacama Desert CL (-23.4, -68.0), North Sea Coast UK
  (56.5, -2.0), Luzon PH (14.6, 121.0). Chosen for contrast on both metrics (solar-dominant /
  wind-dominant / tropical-mixed) and opposite hemispheres, so the same 30-day window is a
  different season at different sites — the strongest available argument for why IQR fences must
  be conditioned per-site-per-hour rather than computed globally. Decided with the user via
  AskUserQuestion; see `prompts.md`.

- **Thresholds adopted** (`src/config.py`), stated explicitly rather than left implicit:
  - Short-gap interpolation cutoff: <=2 consecutive hours (the spec's own example value).
  - Flatline / stuck-sensor rule: identical value for >=6 consecutive *valid* hours (daylight
    hours for solar via TOA>0, all hours for wind) — chosen so legitimate night-zero solar
    readings are never flagged as a flatline.
  - Physical ceiling, solar: GHI > TOA x 1.05 is impossible (5% margin allows for model/rounding
    noise right at the TOA boundary without weakening the rule's intent).
  - Physical ceiling, wind: wind_speed_100m > 60 m/s — generous ceiling for a reanalysis product,
    well above any real recorded surface wind, so only clearly erroneous values trip it.
  - IQR fence multiplier: 1.5x (standard Tukey fence) for solar, 3x ("far out" fence) for wind.
    Wind rationale (spec's own): a genuine 18 m/s hour is the most operationally interesting data
    a wind farm produces, not noise to suppress — the wider fence keeps it unflagged while still
    catching genuinely extreme values.

- **`src/fetch.py`.** Fetches all 3 sites from `/v1/forecast`, caches each site's raw JSON
  response under `data/raw/` so `pipeline.py` (and a reviewer's clean clone) can rebuild the store
  without a network call or API key — this raw snapshot is committed. `--refresh` flag forces a
  live re-fetch. Verified against the real API: 720 rows/site, zero nulls.

- **`src/clean.py`.** Converts each site's raw JSON into a wide per-timestamp DataFrame
  (`ghi`, `toa`, `wind`), then interpolates only *runs* of <= `SHORT_GAP_MAX_HOURS` consecutive
  nulls per site/metric (via a run-length groupby, not `pandas.Series.interpolate(limit=N)` alone
  — the built-in `limit` parameter caps the count of NaNs filled per call, not the gap length, so
  it would partially fill long gaps instead of leaving them fully null). Longer gaps and
  unbounded edge gaps are left null. Every value gets a `quality_flag` of `ok` / `interpolated` /
  `missing`. Verified against real data: all `ok` (no nulls present to interpolate) — matches the
  zero-null finding above; the interpolation path itself will be exercised via the fault-injection
  script later.

- **`src/anomaly.py`.** Tier 1 (solar: negative, non-zero-while-TOA==0, above TOA*1.05 ceiling,
  >=6h daylight flatline; wind: negative, above 60 m/s, >=6h flatline) computed per-row, no
  statistics. Tier 2 fences computed via a filtered-groupby-then-merge pattern: quartiles are
  computed only from rows that are non-null and not already Tier-1-flagged (so a physically
  impossible spike can't drag its own bucket's fence outward), grouped by (site_id, hour-of-day),
  solar additionally restricted to TOA>0 rows so the daylight boundary is evaluated per-row rather
  than per fixed hour label (sunrise/sunset drift a few minutes across the 30-day window, so a
  fixed hour near dawn/dusk can be daylight on some days and not others). Fence bounds are then
  merged back onto every row in the bucket, including Tier-1-flagged and null rows, so the
  dashboard's shaded fence band is continuous rather than punctured. Buckets with fewer than
  `IQR_MIN_BUCKET_N=4` valid observations get no fence (NaN, not flagged) rather than a fence
  computed from too few points. Tier 1 takes precedence over Tier 2 in the persisted
  `anomaly_tier`/`anomaly_reason` when both would fire on the same row.
  Verified against real data: 0 Tier-1 hits on either metric (consistent with the earlier
  zero-null-zero-violation finding); 51/4320 rows (1.2%) flagged Tier 2, all solar/wind
  contextual anomalies. Manually spot-checked one cluster (Atacama, 2026-08-10/11, several
  consecutive daylight hours flagged low relative to that site's hour-of-day fence) — reads as a
  multi-day cloud event, which is exactly the "unusual for this site at this hour, worth review"
  signal the two-tier design is meant to produce.

- **`src/store.py` + `src/pipeline.py`.** DuckDB table `observations`, one row per
  (timestamp, site, metric), schema matches the spec's required column list exactly.
  `pipeline.py` wires fetch -> clean -> anomaly -> store as a single idempotent CLI command
  (`CREATE OR REPLACE TABLE` on each run — this is a batch job, not an incremental load, so
  idempotent full-refresh is simpler than tracking upserts for no benefit at this data volume).
  Ran end-to-end: 4320 rows loaded (3 sites x 2 metrics x 720 hours), 51 anomalies (1.2%), all
  Tier 2, zero Tier 1 -- matches the anomaly.py verification above.

- **`app.py` (dashboard tab only -- Part 2 AI tab intentionally not added yet, per the spec's hard
  sequencing gate).** Streamlit, single page. Sidebar: site (selectbox, single at a time --
  chosen over multi-select/overlaid traces because each metric has a different fence band per
  site, and overlaying 3 site bands on one chart reads as clutter rather than insight in a
  15-minute walkthrough), metric (radio: solar vs wind, since they're different units/scales and
  showing both on one axis would be meaningless), date range (date_input, bounded to the data's
  actual min/max). Chart: Plotly, IQR fence band as a shaded region (two zero-width line traces,
  the second with `fill='tonexty'`), value as a line, Tier 1 (red X) and Tier 2 (orange
  circle) anomalies as distinct marker traces so they're visually separable, hover text carries
  `anomaly_reason` directly from the persisted column. Below the chart: 4 summary metric tiles
  (observation count, Tier 1 count, Tier 2 count, interpolated/missing count) and an expandable
  anomaly detail table.
  Verified with `streamlit.testing.v1.AppTest` (headless, no browser) rather than manual
  clicking: ran the script standalone (no exceptions, 4 metric tiles / 1 chart / 1 table
  rendered), then cycled every site x metric combination (3 x 2 = 6) via the sidebar widgets and
  asserted no exception on any combination. Also smoke-tested via `streamlit run` + curl for an
  HTTP 200 on the served page. Fixed one deprecation warning surfaced by the test run
  (`use_container_width` -> `width="stretch"`, Streamlit is retiring the old parameter after
  2025-12-31).

- **`src/inject_faults.py`.** Optional, explicit-run-only script (`python src/inject_faults.py`,
  never called from `pipeline.py`). Corrupts a copy of the *raw* wide frame (before `clean()`, not
  after -- corrupting post-clean data would leave `quality_flag` stuck at `ok` for the newly-nulled
  cells and the interpolation path would never actually run) with 6 deterministic, hand-picked
  faults, one per rule the real 30-day dataset can't exercise: a 2h null gap (interpolation path),
  a 5h null gap (missing path), one negative wind reading, one GHI-exceeds-TOA reading, an 8-hour
  daylight solar flatline, and a 24-hour all-zero wind day (the spec names this one explicitly).
  Writes to `data/demo_faulty.duckdb`, a separate file the real pipeline and the AI agent's
  default data source never touch; gitignored rather than committed, since it's regenerable and
  its whole purpose is to be obviously not the real dataset.
  Verified by inspecting the output table directly: all 6 injected faults fired with the expected
  `anomaly_tier=1` and a human-readable `anomaly_reason`, 35 Tier-1 rows total (1 short-gap
  interpolation side effect + 1 direct ceiling breach + 8 flatline hours for solar, 1 negative +
  24 flatline hours for wind), `quality_flag` counts exactly 2 `interpolated` / 5 `missing`.
  One genuine, unplanned finding worth keeping: the interpolated value at Atacama (linear fill
  across a fast pre-dawn irradiance ramp) itself overshoots that hour's TOA ceiling and gets
  correctly Tier-1-flagged -- not a bug, but a real illustration that Tier 1 must run *after*
  cleaning and must not exempt interpolated values from physical-validity checks.

- **Wired the demo-data toggle into `app.py`.** Sidebar checkbox ("Use fault-injected demo
  data"), only shown at all if `data/demo_faulty.duckdb` exists (so a reviewer who never ran
  `inject_faults.py` doesn't see a dead control); an unmissable `st.error` red banner renders
  whenever it's checked, naming the file and the script that produced it. `load_observations`
  and `get_connection` now take the db path as an explicit cache key rather than closing over a
  fixed path, so Streamlit's cache correctly treats real vs. demo data as separate cache entries.
  Verified via `AppTest`: toggled the checkbox on (banner appears, no exception) and off (banner
  disappears, no exception), then re-ran the full 3-site x 2-metric matrix against the demo
  dataset specifically, since it's the one most likely to break the chart renderer (it contains
  nulls, a negative value, and multi-hour flatlines that the real dataset never has) -- all 6
  combinations rendered with no exception.

- **README.md sections 1-7 and 9-11 written** (§8, AI agent, deliberately held back until Part 2
  is actually built -- writing it now would document a plan, not a result). Covers: how to run,
  site rationale, data source notes (endpoint/vars/units/tz/model-output caveat), the
  flag-don't-delete cleaning resolution, the DuckDB storage choice and production upgrade path,
  the full anomaly-detection reasoning (IQR vs z-score with the 950/300 solar example, the
  conditioning argument, the wind 3xIQR justification, the two-tier structure, rejected
  approaches), the anomaly taxonomy, known issues, and the AI-usage section pointing here.

- **Clean-clone verification (Part 1 gate).** Copied the repo (excluding `.venv`,
  `__pycache__`, `.env`, `demo_faulty.duckdb` -- everything the `.gitignore` excludes, i.e. what
  a real `git clone` would actually contain) to a scratch directory, built a fresh Python 3.14
  venv there, `pip install -r requirements.txt`, deleted the pre-built `openmeteo.duckdb` to
  force a real rebuild, and ran `pipeline.py`: rebuilt all 4320 rows using only the committed
  `data/raw/*.json` snapshots, no network fetch triggered (fetch.py's cache check short-circuits
  before any request). Then booted the dashboard there via `AppTest` -- no exceptions, and the
  demo-data checkbox correctly did not appear (since `demo_faulty.duckdb` is gitignored and
  wasn't present in the scratch copy), confirming a reviewer's clean clone won't show a dead
  control. This satisfies the §0 "runs from a clean clone, works without an API key" requirement.
  Scratch copy deleted after verification.

- **Git/GitHub identity resolution.** Deferred initially per the user's explicit instruction
  (prompts.md #6), then reopened when the spec's own hard gate ("Part 1 complete, working,
  *committed*") came due. Sequence actually taken, in order:
  1. Global git config and the active `gh` account both pointed at the user's work identity
     (`aldjan.m@loansone.com.au` / `gh` account `technology-dev`) -- wrong for a personal
     take-home repo that needs to be shareable to a reviewer's personal email.
  2. User's own `aldjanm-droid` `gh` login (already present on the machine) was confirmed *not*
     personal either.
  3. Resolved by having the user run the `gh auth login --web` device flow live (code
     `818E-8705`) to add their actual personal account, which authenticated as **`Janxus`**.
  4. **Isolation requirement:** the user explicitly required that this not become the machine's
     default `gh` identity for *other* projects. `gh`'s "active account" is a single global
     pointer (`~/.config/gh/hosts.yml`), not project-scoped, so logging in auto-activating
     Janxus would have silently flipped every other terminal/repo on the machine from
     `technology-dev` to Janxus. Verified `gh auth token --user <name>` (gh 2.89.0) can fetch a
     *specific* logged-in account's token without it being the active one -- so the fix is:
     restore `technology-dev` as the global active account immediately, and give *this repo
     only* a `git config --local credential.https://github.com.helper` that always resolves to
     `gh auth token --user Janxus` regardless of whatever the global active account is. Commit
     authorship is separately scoped via plain `git config --local user.name/user.email`, which
     has zero global-leakage risk by construction -- no special handling needed there.
  Local identity: `Aldjan W Mararac <aldjanw.mararac@gmail.com>`. GitHub account for this repo:
  `Janxus`, pinned via the repo-local credential helper described above, machine-wide default
  left untouched at `technology-dev`.

## Part 1 status: gate complete

Pipeline, storage, anomaly detection, dashboard, fault-injection demo, and README sections
1-7/9-11 are built and verified as of this point in the session. Per the build spec's hard
sequencing rule, Part 2 (AI agent) starts next -- handed off to a fresh agent/session for context
budget reasons; see `ai-artifacts/part2-handoff.md` for everything that session needs to pick up
cleanly. Git identity is now resolved (see entry above); the actual `git init` + first commits
happen immediately after this handoff doc is written.

- **Housekeeping / diligence pass.** Added `Makefile` (`install`, `pipeline`, `inject-faults`,
  `dev`, `test`) as the single local entry point -- `make dev` is the project's equivalent of
  `npm run dev`: rebuilds the store only if it's missing, then serves the dashboard. Added
  `tests/test_invariants.py`, an assert-based script (no new dependency) checking schema shape,
  row-count symmetry across site/metric groups, unit consistency, `fence_lower <= fence_upper`,
  and that every `is_anomaly` row carries both an `anomaly_tier` and an `anomaly_reason` -- the
  kind of check that would catch a schema regression or a Tier-1 rule silently breaking, run via
  `make test`. Re-verified the "runs from a clean clone" claim with a *real* `git clone` this
  time (the earlier check, before git existed, used a manual rsync approximation) --
  `git clone` -> `make install` -> `make test` -> `make dev` all passed against a scratch copy of
  the actual committed history, HTTP 200 from the served dashboard.
  Found and fixed two real gaps during this pass: (1) Streamlit's first-run onboarding prompt
  asks for an email on stdin and would hang a reviewer's first `make dev` -- fixed via
  `.streamlit/config.toml` (`server.showEmailPrompt = false`, `browser.gatherUsageStats = false`),
  confirmed against the installed package's own `credentials.py` rather than guessing the right
  config keys. (2) Two local-only artifacts weren't gitignored: `.claude/settings.local.json`
  (session-specific tool permissions) and the `.agents/skills/` + `.claude/skills/` symlinks
  Streamlit auto-installs on first run (point into `.venv`; Streamlit's own installer output
  recommends not committing them). Both added to `.gitignore`.

- **Fault-injection scope check against the original source PDF.** User supplied the original
  ENGIE brief (`swe-l3-takehome_rev1.pdf`) and asked whether the fault-injection feature was
  "mistakenly added." Compared directly: the original PDF's entire Part 1 anomaly requirement is
  one line -- "An anomaly flag on the data (z-score or IQR -- pick one and briefly justify your
  choice in the README)." No mention of tiers, physical-validity rules, or synthetic/fault data
  anywhere in the 3-page source document. The two-tier detector and the fault-injection script
  are both elaborations from the *derived build spec*, which the build spec's own header
  attributes to the candidate's own prior planning session, not to ENGIE. Conclusion: not a
  mistake or a hallucinated requirement -- a deliberate, labeled scope addition, made necessary by
  the candidate's own choice to build a more sophisticated detector than the brief strictly asked
  for (Open-Meteo being clean model output means Tier 1 would sit empty in a live demo without it).
  **Decision: keep it, as built.** Rationale: it maps directly to the original brief's own stated
  grading criteria ("how you make decisions under ambiguity, and how you communicate those
  decisions") rather than being decorative -- it demonstrates a real capability (the Tier-1
  detector genuinely fires, verified against synthetic faults) rather than adding cosmetic
  polish, which keeps it consistent with the brief's separate "no need to over-polish" line. It
  also already satisfies every constraint the original PDF's spirit would impose even without the
  build spec spelling it out: off by default, opt-in only, unmistakably labeled synthetic
  (`st.error` red banner naming the exact file and script) -- so a reviewer cloning the repo never
  sees or could mistake it for real data unless they deliberately opt in. Named tradeoff, not
  hidden: it's extra surface area to explain in a time-boxed 15-minute walkthrough, so the
  walkthrough should lead with "off by default, clearly labeled" rather than assume the reviewer
  reads the README section unprompted.

## Part 2 — AI agent

- **Session start / plan.** Picked up from `part2-handoff.md` in a fresh agent session. Explored
  the real store before planning: `data/openmeteo.duckdb` has 4,320 rows, `MAX(timestamp) =
  2026-08-30 23:00`, and — notably — **zero Tier-1 anomalies and zero nulls** in the real data,
  only 51 Tier-2 flags (0 of them for North Sea/wind_speed, a ready no-data test case). Plan
  written and approved via `AskUserQuestion` at three genuine forks:
  - **Anomalous rows in aggregates**: exclude Tier-1 (`anomaly_tier IS DISTINCT FROM 1`), keep
    Tier-2. Tier 1 is "certainly wrong" and must never enter a mean/min/max; Tier 2 is a real
    meteorological event per README §6 and stays in. Applied uniformly across all 4 tools'
    aggregate queries (`get_anomalies` itself is unaffected — it returns flagged rows directly,
    not an aggregate).
  - **Date-range parameter shape**: enum only (`last_7_days`/`last_14_days`/`last_30_days`/`all`),
    no absolute-date parameter. The model classifies phrasing onto an enum, never emits or
    computes a date itself; an absolute-date question falls to the refusal path, which is a fine
    demonstration of the boundary holding rather than a gap.
  - **API key funding**: user confirmed real Anthropic API credit (not just a Claude subscription)
    was available, generated a key at console.anthropic.com, and dropped it in `.env` — unblocking
    a real live-path demo instead of a keyless-only submission.
  - Settled without asking (documented per the handoff's "state the decision and why" rule):
    `get_summary_stats` drops the spec table's literal `aggregation` parameter and always returns
    `{count, min, max, mean}` together — the spec's own "Answers" column says it answers all four
    at once, and an unused/ignored parameter would be dead schema surface. `get_site_ranking` keeps
    `aggregation` since ranking genuinely needs a sort key.

- **`src/dates.py`.** Deterministic range resolver: `resolve_range(range_key, con)` reads
  `MAX(timestamp)` (and `MIN` for `all`) from the store and computes `[end − (N−1) days at
  midnight, end]`. Never touches `datetime.now()`. Self-check via `demo()`, asserting each key's
  span length and that `all` matches the store's true min/max.

- **`src/tools.py`.** The 4 tools (`get_site_ranking`, `get_anomalies`, `compare_sites`,
  `get_summary_stats`) as plain functions over a `duckdb` connection, each returning a structured
  dict with an explicit `no_data`/`message` signal (spec §5.4). `compare_sites` embeds the §5.2a
  "documented proxy for generation potential" sentence directly in its response payload so the
  model surfaces it verbatim rather than paraphrasing it away. `TOOL_SCHEMAS` (Anthropic tool
  defs, `site`/`metric` as JSON-schema enums against `config.SITES`/`METRIC_*`) and
  `PRESET_QUESTIONS` (5 canned NL questions each carrying a fixed `(tool, params)` for the keyless
  path) live alongside. Verified against the real store: `get_site_ranking` ranks all 3 sites;
  `get_anomalies` correctly hits the no-data path for North Sea/wind and correctly returns rows for
  solar; `get_summary_stats` produces internally consistent min ≤ mean ≤ max.

- **`tests/test_tools.py`** added (same assert-based style as `test_invariants.py`), wired into
  `make test` as a second line in the `Makefile`.

- **`src/agent.py`.** Manual tool-use loop (not the beta Tool Runner — matches the handoff's
  "keep it simple, 4 fixed tools" call), stateless per question (`ponytail:` comment marking this
  as a deliberate scope limit — no sample question in the brief is a follow-up). `has_api_key()`
  gates the whole live path via `.env`/`python-dotenv`. SDK errors (auth, rate limit, connection,
  other status errors) are caught and turned into a plain-language `error` field rather than
  surfacing a stack trace to the Streamlit UI.

  **Bug found and fixed during live verification**: the first pass only executed
  `tool_use_blocks[0]` per turn. Parallel tool use is on by default in the API — asking to
  "compare generation potential across all three sites" made the model call `compare_sites` twice
  in one turn (once per metric, unprompted — a reasonable interpretation of "generation
  potential" spanning both solar and wind). Only feeding back one `tool_result` left the other
  `tool_use` id unresolved, which the API rejects on the next turn with a 400
  (`tool_use ids were found without tool_result blocks`). Fixed by executing every `tool_use`
  block in a turn and returning all `tool_result` blocks together in a single user message, per
  the API's own parallel-tool-use contract. Re-verified live — the model's dual-metric answer now
  correctly synthesizes both `compare_sites` calls into one response, proxy disclaimer included.

- **Second tab in `app.py`.** Existing Part 1 body extracted unchanged into `render_dashboard_tab`;
  new `render_agent_tab` added inside `st.tabs(["Dashboard", "AI Assistant"])`. Reuses the existing
  sidebar `db_path` (real vs `demo_faulty.duckdb`) rather than adding a second data-source
  selector, so the agent tab follows whichever dataset the dashboard tab is showing. Preset
  buttons and free text both route through one `handle_question()`: with a key, both go through
  `agent.ask()`; without one, presets resolve via their fixed `(tool, params)` mapping called
  directly (no NL parsing, matching spec §5.5's "preset buttons still wired directly to the
  tools"), and free text is disabled with an explanatory caption. Every answer renders an
  expander with the resolved tool call (function name + parameters) beneath it, satisfying the
  "grounding claim made visible" requirement.

- **Live verification (real key, real spend).** All 4 preset questions plus the out-of-scope
  refusal question ("what will solar radiation be tomorrow?") run against `claude-sonnet-5` with
  the real key. All 4 tools resolved correctly with sensible resolved parameters; the refusal
  question correctly produced no tool call and a plain-language "I can't answer that" response
  naming the missing capability (forecasting) rather than guessing. Raw model text and the
  resolved tool call for each were captured for the README §8 transcript.

- **Fail-safe against losing the key/credit mid-session** (explicit user request, not in the
  original spec). `has_api_key()` only proves the env var is *set* — it says nothing about whether
  the account behind it still has funds. Without a fix, a key that goes bad mid-session (exhausted
  credit, revoked, network issue) would just repeat an error on every question after that point.
  Reused the already-built keyless direct-tool-call path as an automatic fallback:
  `handle_question()` in `app.py` now tries the live call first when a key is present, and if it
  errors on a *preset* question, falls back to calling that preset's fixed `(tool, params)`
  directly — the exact mechanism used when there's no key at all — with a caption naming the
  failure reason instead of a silent retry. Free text has no fixed tool mapping to fall back to
  (NL parsing genuinely needs the model), so it surfaces a plain error message rather than
  crashing. Verified by simulating a dead key (`ANTHROPIC_API_KEY=sk-ant-invalid...`): the preset
  path correctly caught the `AuthenticationError`, fell back, and returned the same correct real
  data as the direct-tool test.

- **Browser verification.** No project skill or browser-automation tool was pre-installed for this
  repo; installed Playwright's Chromium build on demand (one-time, ~140MB) rather than reporting
  the UI done from code-level checks alone. Screenshotted the Dashboard tab (unchanged from Part
  1), the AI Assistant tab (keyless banner correctly absent since a real key is configured, preset
  buttons + chat input both rendered), and the result of clicking a live preset button (natural-
  language answer + a "Resolved tool call: `get_site_ranking`" expander beneath it, matching the
  direct-SDK test's numbers exactly). Zero browser console errors.

## Part 1: signed off

All Part 1 Definition-of-Done items (build spec §8) are built, verified, and committed:
pipeline (3 sites x 2 metrics x 720h = 4320 rows), anomaly flags/tiers/reasons/fences persisted
as columns, dashboard with tier-distinguished flags and shaded IQR band, README §§1-7/9-11,
verified clean-clone run via a real `git clone`. The one remaining item from spec §0 --
creating/pushing the GitHub remote and granting access to `bongvaldozjr@gmail.com` -- is a
submission-level action, not a Part-1-gate item (the spec's hard sequencing rule only requires
Part 1 to be committed locally before Part 2 starts), and stays deferred per the user's earlier
explicit instruction until they're ready to do it. Part 2 continues in a separate agent/session;
see `part2-handoff.md`.
