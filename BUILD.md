# BUILD.md — the spec

This is the build spec for a low-cost lead enrichment + scoring pipeline. It's written to be handed to a coding agent (Claude Code, Codex, Cursor) and implemented end-to-end against your CRM. Read it top to bottom, make the decisions in the checklist, fill in the scoring template, then ask your agent to build it.

The reference implementation it's abstracted from is in Python with `requests` and a handful of small scripts (the six in the Components table below). You can use any language — the shape is what matters.

---

## Goal

When a new inbound lead lands in your CRM, automatically:

1. **Enrich** it from a LinkedIn URL (or an email, via a fallback lookup).
2. **Score** it 1 (best) / 2 / 3 (or blank if there isn't enough signal) using *your* model.
3. **Write** the enriched fields + the score back onto the CRM record.

And, optionally, keep the whole CRM current by re-running that across every contact on a staleness rotation.

Cost target: **~$0.004 per lead** for profile enrichment (vs. ~$0.18 Clay / ~$0.30 Crustdata).

---

## Prerequisites

- **A CRM with a REST API** that lets you (a) list/query records, (b) read a record's fields, and (c) PATCH fields on a record by ID. Attio, HubSpot, Salesforce, Pipedrive, and Airtable all qualify.
- **An Apify account + API token.** Pay-as-you-go billing, no seat fees.
- *(Optional)* an **email→profile** provider for email-only leads.
- *(Optional)* a **cheap LLM** API key for the wrong-person guard.

See [`.env.example`](.env.example) for the variables to set.

---

## Components to build

| File (suggested) | Role |
|------------------|------|
| `enrich_sync.py` | The inbound pipeline: pull new leads → enrich → score → write back. The cron entrypoint. |
| `lead_score.py` | Your 1/2/3 scoring model. **Start from [`scoring/lead_score.template.py`](scoring/lead_score.template.py).** Local, no API calls, free. |
| `enrich_validate.py` | Wrong-person guard: placeholder blocklist + name match + LLM arbiter. |
| `crm.py` | Thin CRM client: query records, read a record, PATCH a record. The only file that knows your CRM's API shape. |
| `apify.py` | Thin Apify client: run an actor, poll, read the dataset. |
| `refresh.py` | *(optional)* whole-CRM staleness rotation + change detection. |

Keeping the CRM-specific code in one small adapter (`crm.py`) is the trick that makes this portable — everything else works in terms of a normalized lead dict.

---

## Data flow (the inbound pipeline)

1. **Pull** new leads from the CRM created in a rolling window (e.g. last N days). Query by your "created at" field and your inbound source/segment filter.
2. **Skip already-scored leads.** If the record already has a `lead_score`, don't re-spend. This makes the whole run **idempotent** — a missed or re-fired cron run never double-charges or drops a lead. Use a wide window + this skip instead of a tight window.
3. **Enrich:**
   - **Has a LinkedIn URL →** run the Apify profile scraper (`harvestapi/linkedin-profile-scraper`). Get headline, title, current company, city/state/country, follower count, work history.
   - **Email only →** call your reverse-lookup provider to recover a LinkedIn URL + profile, then proceed. Skip if no provider key is set.
4. **Wrong-person guard** (see below). Drop or flag bad matches before scoring.
5. **Score** with your model → 1 / 2 / 3 / blank.
6. **Activity gate** (see below) — only for provisional 1s and 2s, a second Apify call for recent posts. Adjust the score on real posting activity.
7. **Write back** to the CRM with a PATCH. Write **only non-empty fields**, and **never overwrite an existing value** — fill blanks only. Stamp `last_enriched_at`.

Print the run's spend and the score distribution every run.

### Fields to write back

| Field | Source |
|-------|--------|
| `lead_score` | your 1/2/3 score |
| `job_title` | formatted work history (e.g. `Role at Co (start–end)`) |
| `description` | headline + about/bio |
| `linkedin` | recovered profile URL (if it came from the email path) |
| `linkedin_follower_count` | follower count |
| `primary_location` | locality / region / country |
| `company` | linked/created company record (see note) |
| `last_enriched_at` | timestamp of this run |

**Company linking is dupe-prone** — if you link or create company records, match an existing one first (by LinkedIn URL → domain → exact name), only link on a single unambiguous hit, and stamp the created record with its LinkedIn URL so the next run matches it instead of creating a second. Skip names that already have multiple company records. If this is more than you need, just write the company **name** as text.

### Mapping the scraper output to the normalized lead

Your `crm.py`/`apify.py` code turns the raw actor response into the normalized dict the scorer expects. This is the shape `harvestapi/linkedin-profile-scraper` returns (illustrative — confirm against the actor's live output, which evolves):

```jsonc
{
  "headline": "Head of AI at Example Co",
  "about": "...bio...",
  "currentPosition": [{ "position": "Head of AI", "companyName": "Example Co" }],
  "experience": [
    { "position": "Head of AI", "companyName": "Example Co",
      "startDate": { "year": 2023, "month": "Jan" }, "endDate": { "text": "Present" } }
  ],
  "location": { "parsed": { "city": "Austin", "state": "Texas", "country": "United States" } },
  "followerCount": 12000,
  "connectionsCount": 500
}
```

Map it to the scorer's keys:

| Scraper field | → | Normalized key |
|---------------|---|----------------|
| `followerCount` | → | `linkedin_followers` |
| `headline` | → | `headline` |
| `experience[].position` (recent first) | → | `titles` |
| `currentPosition[0].companyName` | → | `company` |
| `location.parsed.country` | → | `country` |

Twitter/Substack follower counts (from their own actors) fill `twitter_followers` / `substack_subs`. Recent posts from `harvestapi/linkedin-profile-posts` fill `recent_posts` for the activity gate.

---

## The scoring model — this is yours to define

Everything above is plumbing. **The score is the product.** Don't copy someone else's weights; they encode someone else's definition of a good lead.

[`scoring/lead_score.template.py`](scoring/lead_score.template.py) gives you the structure to fill in:

- **Un-scorable → blank.** No audience anywhere, no title, no company = not enough signal. Don't guess.
- **Auto-qualify → 1.** Define the conditions that make a lead obviously great regardless of the blend (e.g. a senior/leadership title at a top-tier company; or a "mega" audience).
- **Below the floor → 3.** Define a minimum audience floor; below it on every metric is a 3.
- **Otherwise, a weighted blend** of the factors you care about, each scored 1/2/3 and combined. The reference used audience + a secondary social channel + title tier + company tier. **You choose the factors and the weights.**

Things worth stealing from the reference model (concepts, not numbers):

- **Region-aware audience bars.** 10k followers means something different in a huge market vs. a small one. The reference used lower bars for one set of countries, higher for the rest. Optional.
- **Title tiers with an anti-signal.** Senior/leadership/specialist titles score high. But **"Founder / CEO / Owner" is an anti-signal**, not a positive — anyone can be CEO of a one-person shop. Count it as top-tier *only* if the audience backs it up; otherwise treat it as weak.
- **Company tiers from a list you control.** Maintain your own list of tier-1/tier-2 companies (or pull one from a public source). Don't hardcode someone else's.
- **Recency window on past jobs.** Only count a past employer toward title/company tier if the role ended recently (the reference used 8 years); current/undated roles always count.

### The activity gate (the part that earns its keep)

A follower count is static. After the base score, re-check any provisional **1 or 2** against the lead's recent *own* posts (reposts excluded):

- **Active** — posted recently, regularly, with real engagement → can **lift** a 2 to a 1.
- **Occasional** — posted at some point but not recently/often → knock a 1 down to a 2.
- **Dormant** — no original post in a long time → **cap** at 3 (or one tier up if they have a huge audience).

Only check 1s and 2s — never pay to confirm a weak lead. A provisional 3 stays a 3. Define "recently," "regularly," and "real engagement" yourself in the template.

---

## Wrong-person guard

The scraper enriches whatever URL is on the record, so a junk or celebrity URL enriches the wrong human and can auto-score them top-tier. Two cheap defenses:

1. **Placeholder blocklist** — drop URLs like `/in/your-name`, `/in/test`, `/in/john-doe`, `/in/linkedin` *before* scraping. Fall back to the email path if present.
2. **Name match + LLM arbiter** — compare the lead's name to the scraped profile's name. A clean match passes free. A zero-overlap mismatch (which also covers legit nicknames / romanizations) goes to a cheap LLM that only rejects on a confident "different person." Rejected leads are **not scored** and are listed in the run output. Without an LLM key, flag mismatches for manual review instead of auto-rejecting.

---

## The refresh loop (optional but high-value)

The inbound pipeline enriches a lead **once**. People drift — they gain followers, change jobs, start or stop posting — and a lead scored a 3 a year ago can be a great target today. `refresh.py` walks the whole CRM on a **staleness rotation** and re-runs enrich → score → write-back.

- Give each record a refresh cadence based on how valuable it is (e.g. your strongest leads every ~2 weeks with a posts pass; everyone else monthly, profile-only). A record becomes *due* once that many days have passed since its `last_enriched_at`.
- Each run processes the **oldest-due records first** and stops at a **hard cost cap** (`--max-cost`). The budget is the throttle, not a fixed batch size.
- Stamp `last_enriched_at` on **every** processed record — including misses and wrong-person rejects — so a dead record is never re-scraped in a loop.

### Change detection → alerts

For your high-value tier, diff the fresh scrape against what was stored and raise an alert (a CRM task to the record's owner, or a note) on:

- **Follower jump** — e.g. +25% *and* +1,000 absolute over the cycle.
- **Job change** — current company differs from the stored one.
- **Viral post** — a single post far above the person's normal engagement.
- **Score change** — any movement in `lead_score`.

Pick your own thresholds.

---

## Automation (GitHub Actions)

Two scheduled workflows, both free on Actions:

| Workflow | Schedule | Runs |
|----------|----------|------|
| Inbound enrichment | every 6h | `enrich_sync.py --since-days 4` |
| CRM refresh (optional) | daily | `refresh.py --max-cost 5` |

Store tokens as **repo Secrets**, not in the repo. Add a `workflow_dispatch` trigger with `dry_run` / `limit` inputs for manual runs. Read your Apify month-to-date usage at the start of each run and **cap the run to the headroom left** so you can never blow the monthly budget regardless of flags.

---

## Decisions you must make (checklist)

- [ ] Which CRM, and what's the inbound query (created-at field + source/segment filter)?
- [ ] Which CRM fields do the enriched values map to? What's your "already scored" field for the idempotency skip?
- [ ] What is a *good lead* for you — the auto-qualify, floor, factors, and weights in `lead_score.py`?
- [ ] Your company tier list (your own, or a public source).
- [ ] Activity-gate thresholds: recency, frequency, engagement rate.
- [ ] Are you scoring on secondary audiences (Twitter, Substack) or LinkedIn only?
- [ ] Refresh cadences per tier, and the change-detection thresholds + alert target.
- [ ] Your monthly Apify budget and per-run cost cap.

---

## Starter prompt for your agent

> I want to build a lead enrichment + scoring pipeline as specified in `BUILD.md`. My CRM is **\<CRM\>** and its API docs are at **\<link\>**. Start by building the `crm.py` adapter (query inbound records, read a record, PATCH a record) and a `--dry-run` mode that resolves and prints the batch with no API spend and no writes. Then wire up the Apify profile enrichment and the write-back. I'll fill in `lead_score.py` myself from `scoring/lead_score.template.py`; call it as a black box that takes an enriched lead dict and returns 1/2/3/None. Add the wrong-person guard and the activity gate last. Keep every Apify call behind a `--max-cost` cap and print spend + score distribution each run.
