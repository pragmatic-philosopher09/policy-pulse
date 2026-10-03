# Policy Pulse

**The 3-minute weekly brief on Indian policy that tells you when you can still act.**

A free, open, no-login public site for 18–30 year-old Indians (students, first-time voters,
early-career professionals) that tracks government activity on the issues that hit their lives —
jobs, exams, taxes, digital rights, gig work, AI regulation, criminal justice — and shows how attention on each has built up
over time.

## What it does

Every Monday, Policy Pulse reads PRS Legislative Research's record of formal government action,
scores which everyday topics are moving, cross-checks each item against independent coverage, and
publishes a brief — opened by a model-drafted **"This week in 60 seconds"** — in three parts:

1. **🗣 You can still respond** — drafts open for public comment from PRS's live *Announcements*
   table (exact deadlines), with the submission email and addressee **read out of the notice
   itself**, the regulator's usual channel, and a copy-paste comment template. Daily reminders at
   7 days and 48 hours.
2. **🔥 Three signals worth watching** — each with a *why it's moving* mini-explanation (dated actions,
   institutions involved, a rule-based interpretation), a hook headline,
   "For you →" lines per persona (student / gig worker / founder / salaried — pick yours once and
   the page filters to you), a confidence label, and ✓ marks where the action was independently reported.
3. **😴 Confirmed quiet** — topics with no formal action, stated explicitly.

**Follow** any topic and the next visit opens with *"Since your last visit: 2 new actions · now
Heating up, was Steady"* — computed in your browser from a local snapshot; nothing is sent anywhere.

Each topic also shows **what people are saying** — conversations from whitelisted newsroom feeds,
Google News, Bluesky and Mastodon, grouped by story and labelled *Confirmed* /
*One newsroom* / *Community* by who is in them. A single post is never shown; each conversation says
whether it tracks a formal PRS action or is running ahead of the record. This layer sits beside the
score, never inside it.

Each topic also shows **what the states are legislating** — Bills from 37 state legislatures tagged
to the same topics (a separate `/states.html` index lists them all). This is where "Centre quiet"
topics like gig work often turn out to be very much alive.

The website (English + Hindi) is the archive: every topic's 17-month evidence trail, policy
**journeys** (the same Bill tracked committee → draft → law → rules, with its current lifecycle
stage), what would make each signal fade, connected topics, and the full method.
The Telegram channel is the product; the site is where the receipts live.

## What makes it different

- **Evidence-weighted momentum, not a black box** — a law passed counts more than a committee report;
  the score compares the last 3 months to the 6 before. Everything is recomputable from `/method.html`.
- **Two-source discipline** — each item is checked against Google News (which indexes PIB, News On AIR
  and the national press). Government or 2+ newspaper hits = "independently confirmed"; confidence
  is lifted only when most recent evidence is confirmed. PRS itself never counts as corroboration.
- **Chatter with a bar** — social and news sources are allow-listed, rate-limited, spam-filtered,
  clustered, and only shown when two or more documents (or three accounts) agree. It never touches the score.
- **Honest about absence** — "Quiet" says *no formal action recorded*, and explains that courts,
  strikes and implementation are outside the source.
- **Act-now first** — deadlines parsed, closed items demoted, response templates included.
- **Persistent, compounding** — SQLite committed to the repo; `docs/radar.json` is free to build on.

## Taxonomy: domain × impact × stage

Every action is classified on three independent axes so labels never fight each other:
**Domain** (the part of government acting — 8 of them along ministry lines; these are scored),
**Impact** (who it reaches — 💼 Jobs · 💰 Money · 🎓 Education · 📱 Digital · ⚖️ Rights · 🏭 Business ·
🩺 Health; a lens for filtering, never a score), **Stage** (draft → committee → Parliament → law →
rules → implementation), plus **Persona** lines. The original seven topic URLs redirect to their domain.

## Where the AI is (and isn't)

Deterministic: scoring, confidence rules, deadline parsing, corroboration counting, reading
submission emails/addressees out of notices (regex over PDF/HTML text).
Model-generated (Claude when `ANTHROPIC_API_KEY` is set; model-written seeds in `data/` until then):
plain-English summaries, hook headlines, persona "For you" lines, the weekly editor's note,
Hindi translations. Everything model-drafted is labelled "AI-drafted · source-linked" on the page.
Curated: topic tag corrections (`data/tag_overrides.json`), policy journeys (`data/chains.json`),
per-consultation facts a human verified (`data/respond_overrides.json`).

## How it works

```
PRS Monthly Policy Review + PRS Announcements (CC BY 4.0)     public feeds & APIs (rate-limited, 6h cache)
        │  weekly GitHub Actions cron (daily for deadlines), 10s crawl-delay      │
        ▼
  radar/parse.py         → structured items (month, ministry, title, body, links)
  radar/announcements.py → live drafts open for comment, exact deadlines, notice links
  radar/states.py        → state legislature Bills (37 states, this + last year) + PRS state briefs
  radar/notice.py        → reads the notice: submission email, addressee, deadline; "likely closed" inference
  radar/score.py       → action type · topic tags (+ overrides) · momentum · confidence
  radar/crosscheck.py  → independent coverage per item (Google News RSS → PIB / newspapers)
  radar/chatter.py     → public chatter: newsroom/PIB feeds, Google News, GDELT, Bluesky, Mastodon
                          → quality gate → clustering → credibility label → match to PRS actions
  radar/citizens.py    → approved X/Reddit APIs → deduplicated observations + collection gaps
                          → recurring text cues → Citizen's Corner + citizens.json (daily)
  radar/summarize.py   → one-line summary          ┐
  radar/enrich.py      → hook + "For you" lines    ├ Claude, or seed files
  radar/translate.py   → Hindi titles/summaries    ┘
  radar/build_site.py  → static site (EN + HI) + radar.json → GitHub Pages
  radar/notify.py      → Telegram: Monday digest, 7-day and 48-hour deadline pings
```

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ANTHROPIC_API_KEY='' python -m radar collect --daily  # monitored feeds + consultation/citizen samples, build
ANTHROPIC_API_KEY='' python -m radar collect --months 18  # also PRS, states, independent cross-checks
python -m radar run --months 18          # ingest, retag, summarise, translate, enrich, crosscheck, build
python -m radar notify --digest --pings  # prints the Telegram messages (dry run without a bot token)
python -m http.server -d docs 8000       # open http://localhost:8000
pytest
```

The news/Bluesky/Mastodon chatter layer is keyless. `POLICY_PULSE_SKIP_CHATTER=1` skips it;
`CHATTER_MAX_REQUESTS` caps its run (default 160). X/Reddit are collected only by Citizen's Corner,
with approved credentials, not by the older raw-response-caching chatter collector.

## Citizen's Corner

`citizens.html` (also in Hindi) shows a 30-day window of sampled discussions, grouped by the
policy/issue watch phrases in `CHATTER_QUERIES`. It is linked from the homepage, navigation and
topic pages. `citizens.json` exports the same counts, source statuses and recurring cues.
No opinion data is fabricated or backfilled from the existing news clusters.

**Setup:** add `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` and `X_BEARER_TOKEN` as repository
Actions secrets, never in source or chat. Reddit requires approved Data API access and an app
permitted to use client-credentials OAuth; X requires recent-search access and the applicable
billing entitlement. Check current platform terms before enabling collection; access is not
guaranteed to be free. Set repository variable `REDDIT_USER_AGENT` to an identifying app/version
and contact (for example `python:policy-pulse:0.3 (by /u/YOUR_ACCOUNT)`).

```bash
python -m radar citizens  # at most 16 searches + one token request; credentials from environment
python -m radar build     # render the latest stored snapshot without API calls
```

The daily 09:30 IST workflow collects discussions and public feeds, builds, commits
the database/site, and deploys before sending deadline reminders. Weekly collection also runs;
successful domain/platform
samples are not queried again on the same UTC day. Each search returns at most 25 recent
results without pagination (up to 200 per platform). X's recent window is normally seven days,
Reddit's search window is a month. These different sampling frames cannot be compared as population
shares. Stored cooldowns honor 429/Retry-After and remaining/reset headers; failures remain visible
as gaps, never successful zeroes. Missing credentials cause no anonymous fallback requests.

Daily counts mean **newly observed deduplicated posts**, dated when collected, not total mentions
or posting-date volumes. First-day results are a baseline sample, not a sudden spike in opinion.
Exact normalized copies are collapsed, each account contributes at most one new post per policy
per day, and promotions, hashtag walls and low-signal Reddit posts are excluded. Exact phrase
matching deliberately sacrifices recall; the English watchlist and rules miss synonyms, Hindi
and code-switching. Results are views in the sample, not a poll of Indians.

Viewpoint labels use conservative, policy-sentence-level text rules: explicit support/opposition,
questions, privacy, costs, implementation and access. Only cues appearing in at least three distinct
platform accounts are shown, with source links. They are not fact-checks, model-generated summaries,
or a sentiment percentage. Sarcasm, paraphrased coordination and attribution remain limitations.
Social shares no longer count as newsroom verification in the existing chatter credibility rule.

SQLite tables `citizen_runs`, `citizen_posts` and `citizen_cooldowns` preserve collection outcomes,
deduplication and rate-limit state. This collector does **not** persist raw API responses, full
post text, usernames or profiles: only canonical post links, policy-scoped account hashes,
observation dates and cue labels. Live rows are retained for 90 days; git history and published
snapshots can persist longer, so this is not a platform deletion-compliance service. Review storage
and deletion obligations before enabling an approved production integration.

Set `ANTHROPIC_API_KEY` to get model-written summaries, hooks, persona lines and Hindi for new items;
without it the pipeline uses first sentences and the seed files in `data/`.

## Deploy

**GitHub Pages (current, $0):**

1. Merge changes into the intended deployment repository's `main`. The workflow in
   `.github/workflows/radar.yml` runs weekly at 09:00 IST Monday and daily at 09:30 IST
   (GitHub can delay cron jobs). It commits refreshed `data/` + `docs/` and deploys to Pages.
   GitHub-hosted runners must be permitted for that repository; a workflow cannot override
   an organization/enterprise runner restriction.
2. In repo **Settings → Pages**, set source to **GitHub Actions**.
3. Secrets: `ANTHROPIC_API_KEY` (optional), `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (for the channel),
   `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` and `X_BEARER_TOKEN` (optional, for Citizen's Corner).
   Variables: `POLICY_PULSE_CHANNEL_URL` (shown as the site's CTA), `POLICY_PULSE_SITE_URL`.
4. Telegram: create a public channel, create a bot via @BotFather, add the bot as channel admin,
   set `TELEGRAM_CHAT_ID` to `@yourchannel`. The Monday run posts the digest; a daily run posts
   deadline reminders. Re-runs never double-post (see the `posts` table).

**GCP (planned):** the output is a plain static folder, so moving is trivial — sync `docs/` to a
Cloud Storage bucket behind Cloud CDN (or serve via Cloud Run + nginx), and trigger
`python -m radar run` from Cloud Scheduler → Cloud Run Job. The SQLite file moves to the bucket
or Cloud SQL when the dataset outgrows git.

## Data model, and where it goes next

Fetched source data is stored by the pipeline (editorial seed files remain curated). Sources land in
`data/radar.sqlite` (`items`, `announcements`, `state_bills`, `corroborations`, `chatter_docs`,
`chatter_clusters`, `citizen_posts`, `citizen_runs`, …), the HTML in `docs/` is a build artefact rendered from that database, and
`docs/radar.json` is the public read API. Committing the SQLite file to git is deliberate at this
scale — history compounds publicly and anyone can `git clone` the whole dataset. When it outgrows
git (or the site needs live queries), a hosted database and API can replace this storage/build
boundary. PostgreSQL is not a drop-in connection change: SQLite-specific SQL, migrations,
transaction handling and deployment would also need adapting. No hosted backend is required today.

## Phased open-source rollout

**Phase 1: observable collection.** The scheduled path uses `radar collect`, with model access
disabled in Actions. PRS reviews/state Bills/cross-checks run weekly; consultations, public
news/social feeds and configured citizen APIs run daily. Each stage records its own outcome
in `pipeline_health`; one source outage does not prevent other sources from collecting or
the last available dataset and health report from being built.

`health.html` (English/Hindi) and `health.json` distinguish collection attempts from page
generation. Missing historical monitoring is shown as unknown, not backfilled as success.
Daily sources become stale after two days without a complete monitored run; weekly sources
after eight days. Completion can include valid cached responses and is not a guarantee of
exhaustive coverage. Citizen's Corner retains finer per-domain/platform status; unconfigured
X/Reddit is expected and does not trigger requests. Run `collect` for monitored collection;
the legacy individual CLI commands and model-enabled `run` do not update this stage report.

Failed cross-checks retain the previous corroborations and retry eligibility. Cross-check
response caches expire after seven days. State listing refreshes refetch all visited pages,
not only the first. Chatter validates feed/API envelopes, uses a hard request budget without
429 retry loops, and stores Retry-After cooldowns in SQLite across invocations. A partial
sample is explicitly marked partial, even when useful records were collected.

The workflow publishes the health report and available data after collection failures, then
reports failure in a separate job so it remains visible in Actions. Build/test/deployment
errors still stop the affected jobs. Telegram notifications run after deployment and cannot
prevent publishing the site. Source health is also copied to the workflow summary.

**Phase 2: opt-in local Ollama drafts with a publication gate.** The optional local path uses
an already-installed `deepseek-r1:8b` (or `--model` selecting another installed local model).
It does not download models, call a paid API, fall back to the cloud, or run in scheduled
collection. Ollama must already be serving on `127.0.0.1:11434`. Requests disable environment
proxies and redirects; cloud-backed inventory entries are rejected. For defence in depth,
also disable cloud features in your Ollama service configuration. Model weights have their
own licenses; "open-weight" is not a blanket open-source or commercial-use guarantee.
Qwen3 14B is another local candidate: its upstream model card lists an
[Apache 2.0 license](https://huggingface.co/Qwen/Qwen3-14B), and the Ollama quantized download
is approximately 9.3 GB. Downloading it is an explicit operator decision, not part of collection.

```bash
# Synthetic-only acceptance checks; a local report, never the public website/dataset.
python -m radar analyze --evaluate --output .cache/analysis/evaluation.json
# Up to six PRS records in one domain, from the last six months (not a citizen-opinion summary).
python -m radar analyze --topic work --output .cache/analysis/work-draft.json
# Read the entire report, check every summary against its excerpt AND the linked PRS record.
# Only then approve its exact 64-character id from the report:
python -m radar approve-analysis <draft-id>
ANTHROPIC_API_KEY='' python -m radar build
```

Drafts and generation outcomes live in SQLite `analysis_drafts` / `analysis_attempts`.
The evaluation command uses an isolated in-memory database; its synthetic outputs stay only
in the requested local JSON report. Do not commit review/evaluation reports or write them into
`docs/` (the CLI rejects that public output directory).
Successful drafts are cached by source fingerprint, prompt/schema/options, model name and
installed weight digest. Sources are bounded to 3,000 characters each; truncation and the
six-record selection limit mean this is **not** a comprehensive policy review. Each result
contains at most five claims. The model selects source/excerpt IDs; the pipeline attaches the
original text itself rather than asking the model to copy quotes (which can become paraphrases).
The model returns claims and exclusions only; the application derives `insufficient` from
an empty claims list. This removes contradictory generated flags without accepting invalid
claims or suppressing a genuine abstention.
Malformed/truncated JSON, missing/unknown citations, altered excerpts and insufficient
discussion samples are rejected or explicitly abstained from, never replaced with fabricated
success. Model errors are recorded and surfaced. There is no silent retry or remote fallback.
The adapter reads Ollama's `/api/show` thinking controls and requests non-thinking output
only when the model explicitly supports `false`; otherwise it preserves the model default.
For older Ollama versions without that metadata, it also recognizes the Qwen3 family's
documented boolean control when the server advertises its thinking capability.
This keeps reasoning tokens from consuming the bounded structured-answer budget on compatible
models. The chosen settings are saved in each draft and evaluation report and included in
the cache key. Older drafts retain unknown settings (`{}`), not invented historical values.

Only an explicitly approved PRS draft appears in the English/Hindi topic-page **Local AI
policy notes** section and `analysis.json`. Generated prose and excerpts remain labelled
English; the interface is translated. The section exposes model digest, prompt version,
draft ID and generation/approval dates. New unreviewed drafts or model failures do not replace
an approved note; changed/aged-out source bundles withhold old notes until a new draft is
reviewed. Builds read saved results without contacting Ollama. Formal policy scores never
use these notes.

Exact excerpt matching checks provenance, **not semantic entailment**: a model can attach a
real quote to a misleading paraphrase. Human approval is mandatory. The small synthetic
acceptance set covers mixed views, off-topic prompt injection, small-sample abstention and
Hinglish attribution, including a regression check for confusing consent withdrawal with
retrieval. Suite `discussion-v2` retains those four cases and adds negation/conditional support
and Hindi-script statements. Its category and key-meaning checks are bounded regression
assertions, not a general-purpose entailment judge. Compare candidates explicitly:

```bash
python -m radar analyze --evaluate --model deepseek-r1:8b --output .cache/analysis/deepseek-evaluation.json
python -m radar analyze --evaluate --model qwen3:14b --output .cache/analysis/qwen-evaluation.json
python -m radar analyze --topic work --model qwen3:14b --output .cache/analysis/qwen-work-draft.json
```

Passing these bounded checks is not proof of semantic accuracy, population-level
accuracy or production readiness.
Live X/Reddit model analysis remains disabled pending approved access, content-processing
permissions and deletion compliance. PRS records are not used to invent public opinion.
Hosted inference, automatic publication and new model downloads remain separate decisions.

**Initial model assessment (2026-10-03): not ready for unattended publication.** With prompt
`grounded-v3` and installed `deepseek-r1:8b` digest
`28f8fd6cdc677661426adab9338ce3c013d7e69a5bea9e704b364171a5d61a10`,
three of four synthetic cases passed; the Hinglish case missed the explicit support viewpoint.
The three inference cases took roughly 27–31 seconds each locally; the small-sample case
abstained without inference. The real `work` PRS draft passed structural citation checks,
but inspection found several summaries paired with excerpts that did not support the claims.
It remains **unapproved**. This demonstrates why excerpt existence is not a truth or
entailment check. Improve/evaluate the model before expanding to live discussion synthesis;
do not remove the publication gate to make an evaluation appear successful.

**Local comparison (2026-10-03): Qwen3 improves results, but neither model is publication-ready.**
Using suite `discussion-v2`, prompt `grounded-v5`, an 8,192-token context, 1,800 output-token
limit, temperature 0 and seed 42:

| Model | Synthetic cases passed | Local inference per case | Remaining findings |
|---|---:|---:|---|
| `deepseek-r1:8b` | 2 / 6 | 27–41 seconds | Incorrect exclusions/sample handling, Hinglish rendering, missing question attribution |
| `qwen3:14b` (`think: false`) | 5 / 6 | 17–25 seconds | Copied Hinglish rather than producing the required English summary |

The small-sample case abstains without inference and is included in both totals, not in the
timing ranges. Qwen3's installed digest is
`bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8`.
These are small, single-run acceptance results, not general benchmark scores. Earlier prompt
variants produced different results; the model is sensitive to the output contract.

The Qwen3 PRS draft covers all three selected `work` records, but review still found unfinished
sentences and claims extending beyond their selected excerpts. It remains unapproved, and
no default-model promotion, automatic publication or live social inference was enabled.
Use `--model qwen3:14b` explicitly for further local experiments. Ignored reports under
`.cache/analysis/` preserve the full outputs, including
`qwen3-discussion-v2-grounded-v5.json`, `deepseek-discussion-v2-grounded-v5.json`, and
`qwen3-work-grounded-v5.json`. Synthetic reports are not part of the website or committed
dataset; PRS candidates are separately retained in SQLite as unapproved drafts.

**Phase 3 (planned): source-grounded assistant.** Add read-only tools for policy evidence,
discussion samples and coverage. MCP is optional tool transport, not an API-access workaround
or a replacement for the scheduler/database. Hosted storage is a separate deployment decision.

## Roadmap

- [x] Phase 0 — PRS pipeline → momentum score → public site
- [x] Phase 1 — Hindi edition, confidence + caveats, this-week brief, Telegram digest + deadline pings
- [x] Phase 2 — independent cross-check (PIB / newspapers via Google News), policy journeys
- [x] Phase 3a — live consultations feed with exact deadlines; notices read for submission address; persona lens; editor's note
- [x] Phase 3b — follow topics + "since your last visit", "why is this moving?", counter-signals, connected topics, lifecycle stages
- [x] Phase 3c — state legislatures layer (PRS state Bills + briefs), states index page
- [ ] Phase 3d — email digest, exam (GS-paper) tags, Hindi hooks/"For you", Hindi audio, location relevance, PIB RSS direct feed
- [ ] Phase 4 — model-structured notice reading (format, page limits, addressee); PIB RSS as a direct feed

## Design

"The weekly issue": an editorial index rather than a dashboard — hairline rules, a sticky marginalia
column numbering each section, oversized italic Instrument Serif, DM Mono for every number, one
Klein-blue accent; light and dark themes. Visuals are built from the data itself (a 17-month ×
7-topic heatmap, a tile-grid cartogram of India for state Bills, self-drawing sparklines, journey
steppers) plus three photographs from Wikimedia Commons (CC BY-SA 4.0, credited in the footer and
`radar/static/img/manifest.json`). No AI-generated imagery. Motion respects `prefers-reduced-motion`;
sound is opt-in and synthesized.

## Attribution

Source data © [PRS Legislative Research](https://prsindia.org), licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). PRS is non-partisan and does not
endorse this project. Summaries, tagging and scores are our own and may contain errors — the
linked primary document is always authoritative.
