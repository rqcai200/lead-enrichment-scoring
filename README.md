# DIY lead enrichment and lead scoring

At Maven, I built an in-house lead enrichment + scoring pipeline for about **$0.004 per lead** — instead of ~$0.18 on Clay or ~$0.30 on Crustdata pay-as-you-go. I built our original Clay -> CRM sync but the costs quickly accumulated, costing us $700+/month for only 2-3k leads/month. We were growing our outbound and inbound and this simply wasn't scalable for us. So, I used Claude Code to build my own lead enrichment + lead scoring workflow, and I'm sharing this guide so you can do it yourself!

- ## How to use this repo

1. **Read [`BUILD.md`](BUILD.md)** - this is what you will give to your coding agent to build the workflow
2. **Fill in [`scoring/lead_score.template.py`](scoring/lead_score.template.py)** — this is the one part only you need to write. The skeleton shows the structure (audience floor, title/company tiers, weighted blend, activity gate); you decide the weights and thresholds for *your* definition of a good lead.
3. **Copy [`.env.example`](.env.example) to `.env`** and fill in your tokens.
4. **Hand `BUILD.md` to your agent** ("implement this against my CRM") and let it generate the enrich → score → write-back pipeline and the GitHub Actions workflow. A starter prompt is at the bottom of `BUILD.md`.

## CRM enrichment comparisons

I've explored a number of enrichment tools like Clay, Crustdata, Reverse Contact, and Apify, but you're paying a heavy per-lead margin for two things you can do yourself: (1) calling a LinkedIn scraper, and (2) running an if/else scoring formula. Do those two directly and the cost drops ~45–75×.

| Provider | Pricing model | ~Cost to enrich 1 lead | vs. DIY |
|----------|---------------|-----------------------:|--------:|
| Clay | data credits (~4 / lead) | ~$0.18 | ~45× |
| Crustdata | pay-as-you-go | ~$0.30 | ~75× |
| **DIY (this guide)** | Apify usage, billed directly | **~$0.004** | **1×** |

That's profile enrichment. The optional posting-activity pass (see below) adds roughly $0.01, and only on your strongest leads. Refreshing an entire ~25k-contact CRM on a monthly rotation runs on the order of $100–150 of Apify usage — versus thousands on a credit-based tool.

## What you'll need

| Piece | Role | Pricing | Required? |
|-------|------|---------|-----------|
| **A CRM with a REST API** | Read new leads; write back the enriched fields + score. I used **Attio**; HubSpot, Salesforce, Pipedrive, Airtable, etc. all work the same way (records + an update endpoint). | Whatever you already pay — the API is included | Required |
| **Apify account + token** | The enrichment engine — runs the LinkedIn scrapers. Usage-based, so you pay for compute, not seats. | Free tier = $5 credits/mo (~1,200 profiles); paid plans from **$29/mo**. You burn only ~$0.004 of compute per lead. | Required |
| **A backup email→profile lookup (optional)** (e.g. Reverse Contact, Clay) | Fallback for leads that arrive as an email with no LinkedIn URL. The agent will default to Apify because it's cheapest but then go through the more expensive options if the profile cannot be found. Reverse Contact is $99/mo for 2,000 credits (~$0.05 / matched contact); free on a no-match 
| **An LLM API to act as a judge** (e.g. Gemini Flash) | You will use this to verify if the scraped profile actually matches the lead
| **Github Actions** | The whole thing runs itself on free GitHub Actions cron.

## The Apify actors I used

These are the Apify actors I got the best results with: 

| Actor | Gives you | ~Cost |
|-------|-----------|------:|
| `harvestapi/linkedin-profile-scraper` | headline, title, current company, city/state/country, follower count, full work history | **$0.004** / profile |
| `harvestapi/linkedin-profile-posts` | recent *own* posts → posting-activity signal + viral detection | ~$0.0015 / post |
| `apidojo/twitter-user-scraper` | Twitter/X follower count (if you score on Twitter audience) | ~$0.0006 / handle |
| `digispruce/substack-scraper` | Substack subscriber count (if you score on newsletter audience) | ~$0.003 / newsletter |

Swap in whatever actors you prefer based on the enrichment fields you need!

## Architecture

```
   NEW LEAD lands in your CRM
            │
            ▼
   ┌──────────────────────────────────────────────┐
   │ 1 · ENRICH                                     │
   │    Apify LinkedIn profile scraper   (~$0.004)  │
   │    email-only lead?  → reverse-lookup fallback │
   └──────────────────────────────────────────────┘
            │
            ▼
   ┌──────────────────────────────────────────────┐
   │ 2 · LEAD SCORE  →                              │
   │    Based on the formula/weights you set,       │
   │    such as company size/followers/job title    │
   │    (~$0.01, strong leads only)                 │
   └──────────────────────────────────────────────┘
            │
            ▼
   ┌──────────────────────────────────────────────┐
   │ 3 · WRITE BACK to the CRM (REST PATCH)         │
   │    score · enriched data · last_enriched_at    │
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



## For creator sourcing, check engagement rates

At Maven we are identifying high potential expert creators who could sell a top-selling product on our platform. A follower count is a *static* signal and does not reflect the engagement of their audience. We mainly want to check for **does this person regularly post original high quality content with high engagement?**

So after the base score, pull the lead's recent posts and gate on real activity:

- **Dormant** (no original post in months) → even with a great title and a marquee logo like a "Founder & CEO, ex-FAANG, 5k followers", if they haven't posted since last year, it is not necessarily a great when identifying high potential creators.
- **Active** (posting regularly with real engagement) may lift the score.

Reposts don't count in the model. Generally, this scrape costs almost nothing and removes a whole class of false positives. Make sure to define your own recency / frequency / engagement thresholds in the template.

## The other guard: don't enrich the wrong person!

The pipeline scrapes whatever LinkedIn URL is on the record. Some leads put in a fake LinkedIn URL (like Barack Obama) and you don't want to enrich the wrong person. Two guards stop this:

1. A **placeholder blocklist** (`/in/your-name`, `/in/test`, `/in/john-doe`) dropped before scraping.
2. A **name match** between the lead and the scraped profile; only a real mismatch gets sent to a cheap LLM arbiter that rejects on a confident "different person." Rejected leads aren't scored.

## License

MIT — see [`LICENSE`](LICENSE). Use it, fork it, build your own.
