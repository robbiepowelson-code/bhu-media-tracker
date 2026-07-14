# BHU Media Tracker

A self-updating media coverage tracker for the **Berkeley Homeless Union**. Every morning it
scans news feeds for stories about homelessness and street vendors in Berkeley and Oakland,
extracts reporter bylines, keeps the press list current, and publishes everything to a
dashboard you can link or embed on the BHU website.

**What it does automatically, every day:**

- Scans Google News, Berkeleyside, The Oaklandside, The Daily Californian, Street Spirit,
  KQED, East Bay Times, SF Standard, SFGATE, and Reddit (r/berkeley, r/oakland, r/bayarea)
- Filters for homelessness / encampment / street-vendor stories in Berkeley & Oakland
- **Adds reporters** to the press list when their byline appears on a tracked story
- **Removes reporters** who go 6+ months without a byline (individual reporters only —
  news desks, advocacy, government and legal contacts are flagged for review, never auto-deleted)
- **Restores** a removed reporter automatically if they publish again
- Emails you a digest of new stories and press-list changes (optional)
- Updates the public dashboard

**What it can't do:** Facebook groups and Nextdoor block automated reading (login walls and
their terms of service), so they aren't scanned. The dashboard's Source Health tab says this
too. Public Reddit communities cover some of the same neighborhood chatter.

---

## Setup (about 15 minutes, all free)

You need a GitHub account — [github.com/signup](https://github.com/signup) if you don't have one.

### 1. Create the repository

1. On GitHub click **+** → **New repository**.
2. Name it (e.g. `bhu-media-tracker`), set it to **Public** (required for free GitHub Pages), click **Create repository**.
3. On the new repo page choose **uploading an existing file**, and drag in **everything inside
   this folder** (including the `.github` folder — if your computer hides it, use
   GitHub Desktop or the command line instead:)

   ```bash
   cd bhu-media-tracker
   git init && git add -A && git commit -m "initial"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/bhu-media-tracker.git
   git push -u origin main
   ```

### 2. Turn on the dashboard (GitHub Pages)

1. In the repo: **Settings → Pages**.
2. Under *Build and deployment*, Source = **Deploy from a branch**, Branch = **main** / **(root)**. Save.
3. After a minute your dashboard is live at
   `https://YOUR-USERNAME.github.io/bhu-media-tracker/`

### 3. Allow the robot to save its updates

**Settings → Actions → General → Workflow permissions** → select **Read and write permissions** → Save.

### 4. (Optional) Email digest

The digest is sent from a Gmail account using an *app password* (not your real password):

1. On the Google account you want to send **from**: turn on 2-Step Verification, then create an
   app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret**, add:
   - `GMAIL_ADDRESS` — the sending Gmail address
   - `GMAIL_APP_PASSWORD` — the 16-character app password
   - `DIGEST_TO` — (optional) where to deliver the digest; defaults to `GMAIL_ADDRESS`.
     Multiple recipients: separate with commas.

No secrets set = no email, everything else still works.

### 5. Run it once now

**Actions** tab → **Daily media scan** → **Run workflow**. Watch it finish (~1–2 min), then
open your dashboard. It runs by itself every morning at 7:15 AM Pacific after this.

### 6. Put it on the BHU website

Link to the dashboard URL, or embed it:

```html
<iframe src="https://YOUR-USERNAME.github.io/bhu-media-tracker/"
        style="width:100%;height:1400px;border:none;" title="BHU Media Tracker"></iframe>
```

---

## Everyday use

- **Coverage feed** — new stories, searchable; "notable" = strong topic match. Use the filters.
- **Press list** — sortable/searchable. Reporters auto-added from bylines show as
  **New — needs contact info**: track down their email and add it (see below).
- **Removed & flagged** — who the 6-month rule removed, and who's flagged as stale/dormant.
- **Source health** — whether each feed worked on the last scan. A failing feed usually means
  the outlet changed its RSS URL; edit `scanner/config.json`.

### Editing the press list by hand

Edit `data/press_list.json` in the GitHub web editor (pencil icon). Each contact is a small
block — fill in `"email"`, fix a beat, or change `"status"`. Commit and the dashboard updates.
The scanner never overwrites emails or beats you enter by hand.

### Tuning what counts as a story

Edit `scanner/config.json`:

- `topic_keywords` / `geo_keywords` — what makes a story relevant
- `strong_keywords` — what earns the "notable" tag
- `feeds` — add or remove sources (any RSS feed works)
- `removal_rule.stale_days` — the 6-month rule (183 days; change it here)

### The 6-month rule, precisely

- Applies only to contacts whose category is `Reporter / journalist`.
- A reporter is removed when the scanner **has seen** a byline from them and the newest one is
  older than 183 days. A warning status appears a month before.
- Contacts imported from your spreadsheet with no byline observed yet are never silently
  deleted — after 6 months of nothing they're marked **dormant** for you to verify manually
  (matching the verification recipe from your spreadsheet's "Cowork run" tab).

## Files

```
index.html                  the dashboard (GitHub Pages serves this)
data/press_list.json        the living press list (+ removed list)
data/coverage.json          every tracked story
data/scan_log.json          last scan's source-health report
scanner/scan.py             the daily scanner (plain Python, no dependencies)
scanner/config.json         keywords, feeds, rules
scanner/seed_from_xlsx.py   one-time importer used to seed the list
.github/workflows/daily-scan.yml   the daily schedule
```
