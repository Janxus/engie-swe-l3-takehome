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
