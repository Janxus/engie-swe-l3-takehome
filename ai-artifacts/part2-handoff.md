# Part 2 Handoff — AI Agent (Conversational Interface)

Written for a fresh agent picking up this repo with no memory of the Part 1 build session.
Read this file first, then `README.md` (what's built and why, Part 1) and
`ai-artifacts/ENGIE_Takehome_Build_Spec.md` (the settled brief you're building against — do not re-litigate
its locked decisions). `ai-artifacts/build-log.md` and `ai-artifacts/prompts.md` are the full
decision/conversation record if you need more depth than this summary gives.

## Where Part 1 left off

Part 1 (data pipeline + dashboard) is complete, verified, and committed. Do not modify Part 1
files except where Part 2 genuinely requires it (e.g. adding a second tab to `app.py`). If you
think a Part 1 decision needs to change, flag it to the user — don't silently alter it.

- `data/openmeteo.duckdb` — the real store. Table `observations`, long format, one row per
  (timestamp, site, metric). Columns: `timestamp, site_id, site_name, metric, value, unit,
  quality_flag, is_anomaly, anomaly_tier, anomaly_reason, fence_lower, fence_upper`.
  `metric` is one of `solar_radiation` / `wind_speed` (see `src/config.py` `METRIC_SOLAR` /
  `METRIC_WIND`). Flags/tiers/reasons/fences are **already computed and persisted** — never
  recompute them; read them directly.
- `data/demo_faulty.duckdb` — synthetic, deliberately corrupted (gitignored, regenerate with
  `python src/inject_faults.py` if it's missing). Not the default data source. Irrelevant to
  Part 2 unless the user asks you to demo the agent against it specifically.
- `src/config.py` — shared constants: `SITES` (3 sites with `site_id`/`site_name`/lat/lon/tz),
  `DB_PATH`, `METRIC_SOLAR`, `METRIC_WIND`. Import from here, don't hardcode site names/ids.
- `app.py` — Streamlit dashboard, currently Part 1 only (one tab). Add Part 2 as a **second
  tab** (`st.tabs`), per the spec — same app, not a separate service or CLI.

Run `python src/pipeline.py` from the repo root's venv (`.venv`, Python 3.14) if you need to
rebuild the store; `streamlit run app.py` to view the dashboard.

## What Part 2 must build (spec §5, non-negotiable, already decided — do not re-open)

**Architecture:** tool/function calling over typed Python functions. The model only does intent
classification and parameter extraction; it never computes, never touches numbers. Model:
**`claude-sonnet-5`** via the `anthropic` Python SDK (already in `requirements.txt`, pinned
`anthropic==1.2.0`). Read `ANTHROPIC_API_KEY` from `.env` (`.env.example` already stubs the var;
`.env` itself is gitignored).

**Known constraint:** as of the Part 1 session, the user has a Claude *subscription*, not a
funded API console key — so **build the keyless fallback as the primary, always-working path**,
and the real tool-calling path as a bonus that activates automatically if a key shows up. Do not
assume a key will be present when you test.

**Exactly 4 tools — do not add a 5th:**
| Function | Answers | Parameters |
|---|---|---|
| `get_site_ranking` | "Which site had the highest average solar radiation last week?" | metric, aggregation, date range, sort direction |
| `get_anomalies` | "Were there anomalous wind readings at Site B in the last 7 days?" | site, metric, date range |
| `compare_sites` | "Compare generation potential across all three sites for the past month." | metric, date range, site list |
| `get_summary_stats` | min/max/mean/count, one site, one metric, one window | site, metric, aggregation, date range |

**`compare_sites` wording trap (spec §5.2a):** "generation potential" ≠ raw irradiance/wind speed
(wind power scales with velocity³; solar output needs a capacity/efficiency assumption neither
metric alone provides). Report raw metric aggregates as a **documented proxy** and say so in both
the tool's response text and the README — one sentence, stated once in both places, e.g.
"reporting relative irradiance/wind-speed as a proxy for generation potential; a true estimate
requires turbine power curves and panel capacity/efficiency data, which are out of scope here."

**Two rules that make this look deliberate (spec §5.3):**
1. Date resolution ("last week", "past 7 days") is a **deterministic Python helper**
   (`src/dates.py`, not yet built), resolved against `MAX(timestamp)` in the store — never
   `datetime.now()`, never left to the LLM.
2. `get_anomalies` reads the persisted `is_anomaly`/`anomaly_tier`/`anomaly_reason` columns
   directly. It does not recompute anything from `src/anomaly.py`.

**Refusal is a feature (spec §5.4):** every tool returns a structured result plus an explicit
empty/no-data signal. When no tool matches or a tool returns nothing, the agent says so plainly.
Demonstrate this in the README transcript with an out-of-scope question (e.g. "what will solar
radiation be tomorrow?").

**Presentation (spec §5.5):** second tab in `app.py`, not a separate app. Preset example buttons
alongside free-text input. Render the resolved tool call (function name + parameters) beneath
each answer — this is the grounding claim made visible, not just asserted. Keyless fallback:
banner explaining the missing key, preset buttons still wired directly to the tools so the app
never looks broken without a key. Log raw model output next to the resolved tool call at least
once in the README transcript.

## Suggested build order

1. `src/dates.py` — deterministic date-range resolver against `MAX(timestamp)`.
2. `src/tools.py` — the 4 tool functions, read-only queries against `data/openmeteo.duckdb`
   via `duckdb`. Each returns structured data + an explicit "no data" signal.
3. Tool schemas + the manual request loop (call → `tool_use`? → run matched function → feed
   `tool_result` back → final NL answer) using the Anthropic SDK. Keep it simple — 4 fixed
   tools, roughly one round-trip per user turn, no need for the beta Tool Runner.
4. Second tab in `app.py`: preset buttons + chat input, keyless-fallback banner, resolved-tool-
   call rendering.
5. Verify: run every preset button with no key (fallback path), then with a key if one becomes
   available, then the out-of-scope refusal question.
6. README §8 (AI agent design + transcript) — **only after the above actually works**, not
   before. Don't describe a plan as if it were a result.

## Process rules to keep following (the user has been explicit about these)

- **Always raise ambiguity, never silently assume.** This whole build has been steered by
  `AskUserQuestion` at every real fork (sites, model, wind height/skew, fault injection, git
  identity). Part 2 has fewer open forks since the spec is more prescriptive here, but if
  something is genuinely ambiguous (e.g. exact tool JSON schema shape, error-message wording),
  ask rather than guess.
- **Maintain `ai-artifacts/build-log.md`**: after each meaningful step, append a short dated
  entry — what you did, and for anything not explicitly pinned down in the spec, the specific
  decision made and why. A few lines, not a narrative. Follow the existing entries' format/tone.
- **Maintain `ai-artifacts/prompts.md`**: append the literal instruction the user gives you each
  time they redirect or add something, verbatim, under a dated `## <date>` heading (continue
  numbering from where Part 1 left off, or start a fresh numbered list under a `## Part 2`
  heading — either is fine, just keep entries verbatim and in order).
- **Commit early and often, real messages, not one paste-in commit.** Commit `ai-artifacts/`
  updates *alongside* the code they relate to, not as a separate cleanup pass afterward. Local
  git identity and the GitHub credential helper are already configured for this repo (see
  `build-log.md`'s "Git/GitHub identity resolution" entry) — just `git add` / `git commit` as
  normal, no further identity setup needed. Do not run `gh auth login` or `gh auth switch`
  again; the repo-local credential helper already handles push auth without touching the
  machine's global `gh` account.
- **Do not write README §8 until Part 2 actually works.** Same rule that applied to §§1–7/9–11
  in Part 1.
- If Part 2 goes sideways and can't be finished cleanly, the spec's own instruction is to delete
  it and ship a complete Part 1 rather than a half-finished bonus — surface that choice to the
  user rather than deciding it unilaterally.
