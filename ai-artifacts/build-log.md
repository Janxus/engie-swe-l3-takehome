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
