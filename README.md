# APAC Cyber Policy Tracker

Tracks new cyber laws, regulations, consultations and enforcement across Asia Pacific, including the financial sector. It updates twice a day (about 7:15 AM and 5:15 PM Malaysia time), and every Monday at about 8 AM it emails you a weekly digest.

## What's on the page

1. **Consultations closing soon:** consultations found in the news with a closing date, plus any you add to `data/consultations.yml`.
2. **Financial Sector:** BNM, MAS, OJK, Bank of Thailand, BSP, SBV, APRA, ASIC, RBI, SEBI, HKMA. Covers technology risk, fraud and scam rules, and operational resilience.
3. **One section per country:** Malaysia, Singapore, Indonesia, Thailand, Philippines, Vietnam, Australia, India.
4. **Regional & Global Bodies:** ASEAN, APEC, GSMA, ITU, the Counter Ransomware Initiative.

Each item is tagged automatically: **Consultation, New law, Finance, Telecom, AI, Data protection, Critical infrastructure, Scams & fraud, Enforcement**. Tap any tag or country at the top to filter.

## How items are chosen

Most regulators don't publish RSS feeds, so each section runs a Google News search for that country's regulators and laws (see `sources.yml`). A headline is kept only if it contains a **policy word** (bill, act, guideline, consultation, fine...) **and** a **cyber word** (cyber, data, digital, AI, telecom, scam...). This drops general news about the same agencies. Stories covered by several outlets appear once.

These are news reports, not official notices. Check the regulator's own site before acting.

## Weekly digest by email

Every Monday the workflow opens a GitHub **issue** titled "Weekly APAC cyber policy digest" that mentions you, and GitHub emails it to you. Last week's digest issue is closed automatically. You can also comment on the issue to keep notes.

To send one now: **Actions → Policy tracker → Run workflow**, tick **Also send the weekly digest now**, then **Run workflow**.

If the email doesn't arrive, check https://github.com/settings/notifications and make sure **Email** is ticked under "Participating, @mentions and custom".

## Changing it

| To | Edit |
|---|---|
| Add a regulator, law or country | the `google:` search in `sources.yml`, or add a section |
| Track a consultation with a known closing date | `data/consultations.yml` |
| Change what counts as relevant | `require_policy` / `require_cyber` in `sources.yml` |
| Add or adjust a tag | `tags:` in `sources.yml` |
| Change how far back it looks | `days_back` in `sources.yml` |

## Files

| File | What it does |
|---|---|
| `tracker.py` | Fetches, filters, tags and writes the page, the Markdown copy and the digest |
| `sources.yml` | Searches, filters, tags and countries |
| `data/consultations.yml` | Consultations you track by hand |
| `.github/workflows/tracker.yml` | Schedule and the weekly digest |
| `docs/` | The website (GitHub Pages) |
| `briefs/` | Markdown copy of each day's edition |
