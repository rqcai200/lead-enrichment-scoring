# DIY lead enrichment and lead scoring

At Maven, I built an in-house lead enrichment + scoring pipeline for about **$0.004 per lead** — instead of ~$0.18 on Clay or ~$0.30 on Crustdata pay-as-you-go. I built our original Clay -> CRM sync but the costs quickly accumulated, costing us $700+/month for only 2-3k leads/month. We were growing our outbound and inbound and this simply wasn't scalable for us. So, I used Claude Code to build my own lead enrichment + lead scoring workflow, and I'm sharing this guide so you can do it yourself!

## What's in this:
- Read [`BUILD.md`](BUILD.md) -> point your agent at `BUILD.md` and let it wire up the rest against your CRM.
- Fill in [`scoring/lead_score.template.py`](scoring/lead_score.template.py) with your own scoring thresholds

## CRM enrichment comparisons

I've explored a number of enrichment tools like Clay, Crustdata, Reverse Contact, and Apify, but you're paying a heavy per-lead margin for two things you can do yourself: (1) calling a LinkedIn scraper, and (2) running an if/else scoring formula. Do those two directly and the cost drops ~45–75×.

| Provider | Pricing model | ~Cost to enrich 1 lead | vs. DIY |
|----------|---------------|-----------------------:|--------:|
| Clay | data credits (~4 / lead) | ~$0.18 | ~45× |
| Crustdata | pay-as-you-go | ~$0.30 | ~75× |
| **DIY (this guide)** | Apify usage, billed directly | **~$0.004** | **1×** |

That's profile enrichment. The optional posting-activity pass (see below) adds roughly $0.01, and only on your strongest leads. Refreshing an entire ~25k-contact CRM on a monthly rotation runs on the order of $100–150 of Apify usage — versus thousands on a credit-based tool.

## What you'll need

| Piece | Role | Required? |
|-------|------|-----------|
| **A CRM with a REST API** | Read new leads; write back the enriched fields + score. I used **Attio**; HubSpot, Salesforce, Pipedrive, Airtable, etc. all work the same way (records + an update endpoint). | Required |
| **Apify account + token** | The enrichment engine — runs the LinkedIn scrapers. Pay-as-you-go, billed on actual usage. | Required |
| **An email→profile lookup** | Fallback for leads that arrive as an email with no LinkedIn URL (e.g. Reverse Contact). | Optional |
| **A cheap LLM API** | The "wrong-person" guard — judges whether a scraped profile is actually the lead (e.g. Gemini Flash, or any small model). | Optional |

No Clay, no Zapier/n8n, no per-task automation fees. The whole thing runs itself on free GitHub Actions cron.

## The Apify actors I used

All public Apify Marketplace actors. Costs are approximate and billed as Apify usage.

| Actor | Gives you | ~Cost |
|-------|-----------|------:|
| `harvestapi/linkedin-profile-scraper` | headline, title, current company, city/state/country, follower count, full work history | **$0.004** / profile |
| `harvestapi/linkedin-profile-posts` | recent *own* posts → posting-activity signal + viral detection | ~$0.0015 / post |
| `apidojo/twitter-user-scraper` | Twitter/X follower count (if you score on Twitter audience) | ~$0.0006 / handle |
| `digispruce/substack-scraper` | Substack subscriber count (if you score on newsletter audience) | ~$0.003 / newsletter |

Swap in whatever actors you prefer — the pipeline only cares that profile enrichment returns the fields your scorer reads.

## Architecture

```
   NEW LEAD lands in your CRM
            │
            ▼
   ┌──────────────────────────────────────────────┐
   │ 1 · ENRICH                                     │
   │    Apify LinkedIn profile scraper   (~$0.004)  │
   │    email-only lead?  → reverse-lookup fallback │
   │    wrong-person guard (blocklist + name + LLM) │
   └──────────────────────────────────────────────┘
            │
            ▼
   ┌──────────────────────────────────────────────┐
   │ 2 · SCORE  →  1 / 2 / 3                         │
   │    your model, runs locally, free              │
   │    audience + title + company  →  blend        │
   │    activity gate: pull recent posts            │
   │    (~$0.01, strong leads only)                 │
   └──────────────────────────────────────────────┘
            │
            ▼
   ┌──────────────────────────────────────────────┐
   │ 3 · WRITE BACK to the CRM (REST PATCH)         │
   │    score · title · company · followers ·       │
   │    location · last_enriched_at                 │
   └──────────────────────────────────────────────┘
            │
            ▼
   ┌──────────────────────────────────────────────┐
   │ 4 · REFRESH loop  (optional)                   │
   │    re-run 1–3 across the whole CRM on a        │
   │    staleness rotation → catch job changes,     │
   │    follower jumps, viral posts → alert owner   │
   └──────────────────────────────────────────────┘
            └──────── runs on GitHub Actions cron ──────────
```

## How to use this repo

1. **Read [`BUILD.md`](BUILD.md)** — the full spec, written so a coding agent can implement it end-to-end.
2. **Fill in [`scoring/lead_score.template.py`](scoring/lead_score.template.py)** — this is the one part only you can write. The skeleton shows the structure (audience floor, title/company tiers, weighted blend, activity gate); you decide the weights and thresholds for *your* definition of a good lead.
3. **Copy [`.env.example`](.env.example) to `.env`** and fill in your tokens.
4. **Hand `BUILD.md` to your agent** ("implement this against my CRM") and let it generate the enrich → score → write-back pipeline and the GitHub Actions workflow. A starter prompt is at the bottom of `BUILD.md`.

## The one idea worth stealing: the activity gate

A follower count is a *static* signal. It tells you someone was once worth following — not whether they still create. For most lead definitions (mine was "would make a good course instructor / creator"), what you actually want to know is: **does this person regularly post original content?**

So after the base score, pull the lead's recent posts and gate on real activity:

- **Dormant** (no original post in months) → cap the score, even with a great title and a marquee logo. A "Founder & CEO, ex-FAANG, 5k followers" who hasn't posted since last year is not the lead the logo makes them look like.
- **Active** (posting regularly with real engagement) → lift the score.

Reposts don't count — they're low-effort and carry someone else's reach. You only spend the extra posts-scrape on leads that already scored well, so it costs almost nothing and removes a whole class of false positives. Define your own recency / frequency / engagement thresholds in the template.

## The other guard: don't enrich the wrong human

The pipeline scrapes whatever LinkedIn URL is on the record. A placeholder or a pasted celebrity URL will faithfully enrich the wrong person — a test lead with a junk URL once enriched as a public figure with millions of followers and auto-scored top-tier. Two cheap guards stop this:

1. A **placeholder blocklist** (`/in/your-name`, `/in/test`, `/in/john-doe`) dropped before scraping.
2. A **name match** between the lead and the scraped profile; only a real mismatch gets sent to a cheap LLM arbiter that rejects on a confident "different person." Rejected leads aren't scored.

## License

MIT — see [`LICENSE`](LICENSE). Use it, fork it, build your own.
