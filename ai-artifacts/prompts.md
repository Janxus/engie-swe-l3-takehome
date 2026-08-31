# Prompts / Redirection Log

Verbatim record of instructions the user gave during this session, in order. Exact wall-clock
timestamps aren't available from the tool transcript, so entries are dated (session date) and
numbered in conversation order. This is the raw material; `build-log.md` is the distilled
decision record and the README is the polished write-up.

## 2026-08-31

### 1. Initial task
> Start with build spec attached. Formulate a well-thought plan. After writing a plan, I want to
> see the step by step TODOs in bulleted list of the things we are going to do deliver the
> requirement. Just a heads up, the instruction file stated "We're looking at how you approach
> real data engineering problems, how you make decisions under ambiguity, and how you communicate
> those decisions. The code matters, but so does the reasoning behind it." so expect ambiguity on
> the build spec and on the lower-detail task as we work on this but, raise them to me, let's
> think it through, why we decide to do option X vs option Y. Got it? Be exhaustive of the details
> especially on the requirements. Never assume, always raise and ask. /ponytail:ponytail

*(Attached: `ENGIE_Takehome_Build_Spec.md`. `/ponytail:ponytail` activated ponytail mode — lazy
means efficient, ladder-based simplicity, full intensity.)*

### 2. Clarifying answers (AskUserQuestion round 1 — sites, LLM, wind, fault injection)
> "Which three sites?" -> "Global contrast (Recommended)"
> "Part 2 LLM / do you have a key?" -> "I am okay using sonnet-5, problem is I only have access to
> my own subscription, how to get the API key I need?"
> "Wind height + skew adjustment?" -> "100m + 3x IQR (Recommended)"
> "Fault injection handling?" -> "Separate demo DB (Recommended)"

### 3. Local command: model switch
> `/model sonnet` — switched session model to claude-sonnet-5.

### 4. Git setup — deferred, first pass
> ooof we don't do the git stuff right now. because what is currently configured is not my
> personal github. tell me if I need to setup my personal git first before we proceed. or we can
> defer to git actions for now then proceed on writing codes first.

### 5. Git identity clarification (AskUserQuestion round 2)
> "Which git identity should commits use?" -> "Local override, personal (Recommended)"
> "Which GitHub account should own this repo?" -> "aldjanm-droid is not my personal. my personal
> is not yet configured on this machine"

### 6. Explicit redirect: start coding, defer git entirely
> let's write some codes after the plan. I'll worry about the git on the latter part of this
> project when it's about to finish.

### 7. Mid-turn instruction: artifact logging + commit cadence
> As you work, maintain two running files in /ai-artifacts/:
>
> 1. build-log.md — after each meaningful step, append a short dated entry: what you did, and for
>    anything not explicitly pinned down in the spec (exact Open-Meteo variable names, library
>    choices, exact fence values, function signatures, etc.), the specific decision you made and
>    why. A few lines per entry, not a narrative.
>
> 2. prompts.md — append the literal instruction I give you each time I redirect or add something
>    during this session, verbatim, with a timestamp.
>
> Commit early and often, with real specific commit messages — not one commit at the end. Commit
> /ai-artifacts/ updates alongside the code changes they relate to, not as a separate cleanup pass
> afterward.
>
> Do not write the final README sections until the corresponding work is actually done. The
> README documents what was built and decided, not what's planned.

**Note on #7 vs #6:** #7's "commit early and often" instruction supersedes #6's "worry about git
later" for *local* commits specifically — flagged back to the user in-session since local commits
still need a real author identity, which #5 raised but never got a concrete answer for (personal
email still not provided as of this entry). Proceeding with local git init once an identity is
given; GitHub remote/push still deferred per #6 until the personal account is set up.

### 8. Part 1 gate reached — commit-boundary question raised, user opts to set up personal GitHub live
Agent flagged that the spec's own hard gate wants a *committed* Part 1 before Part 2 starts, and
asked (AskUserQuestion) how to proceed given git was still deferred. User's reply:
> before I answer that, can I gh-login then open to the chrome window where my personal git is
> logged in and configure from that?

Agent ran `gh auth login --web` (device code flow); user authorized in browser; account
authenticated as **`Janxus`**. Agent then asked (AskUserQuestion) to confirm Janxus + get a
commit name/email; user confirmed Janxus, then separately provided:
> name: Aldjan W Mararac
> email: aldjanw.mararac@gmail.com

### 9. Isolation requirement for the gh/GitHub config
> make sure that this gh config is only for this project and will not be used on other projects.

Resolved by restoring `technology-dev` as the global `gh` active account and giving *this repo
only* a `git config --local credential.https://github.com.helper` that resolves to
`gh auth token --user Janxus` regardless of the global active account. Full reasoning in
`build-log.md`'s "Git/GitHub identity resolution" entry.

### 10. Part 2 handoff requirement, before git commands proceed
> before we proceed on part 2, make sure build-log is updated. and generate a new md file that a
> new agent will pickup on to work on part 2. Part 2 will be handled by another agent for token
> maximizing. In that part 2 md file you will write tell all the relevant commands it needs to
> know such as but not limited to: recording of prompt and logging to build, the decision branches
> we took. proceed on git commands you're intended to do

Produced `ai-artifacts/part2-handoff.md`, then proceeded with `git init` + the local identity/
credential-helper config + 6 feature-scoped commits covering the whole Part 1 build.

### 11. Move build spec into ai-artifacts/
> move the ENGIE_Takehome_Build_Spec.md from root to ai-artifacts/ as it's only fitting for that
> file to be in there then commit it too.

### 12. Housekeeping request: local run command + spec-compliance diligence pass
> housekeeping time. I want to make sure we are 100% compliance on part 1 that we have delivered
> it. I need you to add a command where I can test it locally ie. like "npm run dev" on a node
> project do a similar command here so I can see the output of part 1. do a diligence check if we
> have followed everything part 1, refer to the build spec once more (the file we just moved to
> ai-artifacts folder.

Produced the `Makefile` (`make install/pipeline/inject-faults/dev/test`) and
`tests/test_invariants.py`; the diligence pass itself surfaced and fixed two real gaps
(Streamlit's first-run email prompt blocking non-interactively, and two local-only artifacts —
`.claude/settings.local.json` and Streamlit's auto-installed skill symlinks — that weren't yet
gitignored). See `build-log.md` for the full findings.

### 13. Cross-check against the original source PDF
User asked the dashboard's fault-injection banner be checked against the *original* ENGIE PDF
brief (`swe-l3-takehome_rev1.pdf`), not just the derived build spec, then supplied the PDF after
an initial attachment that didn't come through:
> ...so that fault-injection was mistakenly added? here's the attached file that was referred
> earlier

Agent compared the two documents directly: the original PDF's Part 1 anomaly requirement is a
single line ("An anomaly flag on the data (z-score or IQR — pick one and briefly justify your
choice in the README)") with no mention of tiers or fault injection anywhere. The two-tier system
and the fault-injection script are elaborations the user made in their own prior planning session
that produced the build spec (the build spec's own header names "Aldjan W. Mararac (candidate)"
as the author of those decisions) — not something ENGIE asked for, but also not a hallucinated or
mistaken addition; both are traceable to a real problem the user's own more sophisticated design
created (Open-Meteo being clean model data would leave Tier 1 empty in a live demo).

### 14. Strategic question: does this elaboration help in the interview?
> How will adding that feature help me in my case when I present this project to them in line with
> the project requirement that they sent?

Agent's recommendation: yes — it maps directly to the brief's own stated grading criteria
("how you make decisions under ambiguity, and how you communicate those decisions"), giving a
concrete demonstration of judgment rather than just a checkbox anomaly flag. Tradeoff: extra
surface area to explain in a 15-minute walkthrough; needs a ready one-line answer for "why does
this exist" rather than relying on the reviewer finding it in the README unprompted.

### 15. Decision: keep the feature, document it, sign off Part 1
> I actually like how it was clearly indicated that those are just Synthetic Data. I think there's
> no harm of putting it in there. Document this. Then, we can now sign off Part 1?

Decision: fault-injection script and its clearly-labeled synthetic-data banner are KEPT as-is.
Rationale recorded in `build-log.md`. Part 1 signed off as of this entry, pending only the
already-deferred GitHub hosting/access-grant step (§0 of the spec), which is a submission-level
item, not a Part-1-gate item.

## Part 2

Fresh agent session, picking up from `part2-handoff.md` per its own instructions. Fresh numbering.

### 1. Initial task
> let's kick off part 2. I need to see the step-by-step to do after we've formulated a plan on
> delivering this. if you ever stumbled upon ambiguity, do not assume, raise and let us discuss.
> /ponytail:ponytail

### 2. Clarifying answers (AskUserQuestion round 1 — API key path, anomaly-row exclusion, date
range shape)
User picked "Exclude Tier 1, keep Tier 2" and "Enum only" directly, but answered the API-key
question with two follow-up questions instead of picking an option:
> 2 question regarding this. 1. How much do I have to top up for this demo?
> 2. Say that I will use an existing openai api key, will I see in my open AI dashboard that that
> openai key was used for this project?

Agent answered inline (estimated cost ~$0.01/question, ~$0.50 total; explained an OpenAI key
can't authenticate against `api.anthropic.com` regardless of dashboard visibility — different
provider, would require rewriting the whole tool-calling layer against a different SDK, contradicting
the spec's locked `claude-sonnet-5` decision) and re-asked the same fork with the pricing context
attached.

### 3. API key decision (AskUserQuestion round 2)
> I remember having $29 balance I can use in my anthropic. I just need guidance on where to
> generate the anthropic key

Decision: top up/use the existing Anthropic credit and run the real live tool-calling path,
rather than shipping keyless-only. Agent gave step-by-step console.anthropic.com key-generation
instructions and proceeded to plan + build against that assumption.

### 4. Mid-build check-in: platform credit balance
> got back from platform.claude.com I see here I have $5 credit. is this enough?

Agent confirmed $5 is well above the ~$0.50 estimated total spend and continued building.

### 5. Key installed
> alright created an .env with anthropic api key saved in it

Triggered the plan's step 5→6 transition: live verification of all 4 preset questions plus the
out-of-scope refusal question against the real API, which surfaced and fixed a real parallel-tool-use
bug in `src/agent.py` (see `build-log.md`).

### 6. Fail-safe requirement: survive lost credit/key mid-session
> make sure what we are building here has a fail-safe feature where in if some way or another we
> lost the anthropic api key credits this part 2 can still function. got it?

`has_api_key()` only checks that the env var is *set*, not that the account still has funds — so a
key that goes bad mid-session (credits exhausted, revoked, etc.) would otherwise just repeat an
error on every question. Fixed `app.py`'s `handle_question()` so a failed *live* call on a preset
question automatically falls back to calling the tool directly (the same code path already built
for the fully-keyless case), instead of just showing a repeated error. Verified by simulating an
invalid key end-to-end: the preset button still returned the correct real data via the fallback
path, with a caption naming the failure reason. Free text still can't auto-recover (no model means
no NL parsing), so it shows a plain error message instead of crashing.
