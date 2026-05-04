# Digital Regulatory Monitor — Setup Guide

This guide sets up the new site from scratch. Follow each step in order.
If anything is unclear, ask — do not skip steps.

---

## What you're setting up

A GitHub Pages website that automatically scrapes regulatory news every morning
and publishes it. No manual work once it's running.

**Website structure:**
- `index.html` — This week's news (last 7 days), opens at your GitHub Pages URL
- `archive.html` — Older news, fully searchable
- `data/articles.json` — The data file (updated automatically every day)
- `scripts/scraper.py` — The scraper that fetches news
- `.github/workflows/daily-scrape.yml` — The daily automation

---

## Step 1: Create the new GitHub repository

1. Go to **https://github.com/new**
2. Sign in as **maleevskaina**
3. Fill in:
   - **Repository name:** `digital-regulatory-monitor`
   - **Description:** `Prosus Digital Regulatory Team — automated intelligence monitor`
   - **Visibility:** ✅ Public (required for free GitHub Pages)
   - Leave everything else as default
4. Click **Create repository**

---

## Step 2: Upload all the files

You have these files to upload (they were saved in your Cowork outputs folder):

```
digital-regulatory-monitor/
├── index.html
├── archive.html
├── data/
│   └── articles.json
├── scripts/
│   ├── scraper.py
│   └── requirements.txt
├── .github/
│   └── workflows/
│       └── daily-scrape.yml
└── SETUP.md
```

**Upload method — GitHub Desktop:**

1. Open **GitHub Desktop**
2. Click **File → Clone Repository**
3. Find `maleevskaina/digital-regulatory-monitor` and clone it to your computer
4. Open Finder and navigate to the cloned folder
5. Copy all the files above into the folder, maintaining the same folder structure
   - Make sure `.github/workflows/` folder exists (note the dot at the start)
6. Go back to GitHub Desktop
7. You'll see all the files listed as "changes"
8. Write a commit message: `Initial setup — Digital Regulatory Monitor`
9. Click **Commit to main**
10. Click **Push origin**

---

## Step 3: Enable GitHub Pages

1. Go to your new repo on GitHub: `https://github.com/maleevskaina/digital-regulatory-monitor`
2. Click **Settings** (top tab)
3. Scroll down to **Pages** in the left menu
4. Under **Source**, select **Deploy from a branch**
5. Under **Branch**, select `main` and folder `/ (root)`
6. Click **Save**
7. Wait 2–3 minutes
8. Your site will be live at: `https://maleevskaina.github.io/digital-regulatory-monitor/`

---

## Step 4: Verify the site works

1. Go to `https://maleevskaina.github.io/digital-regulatory-monitor/`
2. You should see the Digital Regulatory Monitor homepage with sample articles
3. Click **Archive** — you should see older articles
4. Try the topic and region filters — they should filter the cards

If you see a blank page or error, wait another 2 minutes and refresh.

---

## Step 5: Enable the daily automation

The scraper runs automatically every day at 06:00 UTC (07:00 London / 08:00 Brussels time in winter).

To confirm Actions are enabled:
1. Click the **Actions** tab in your repo
2. If you see a yellow banner asking to enable workflows, click **Enable**
3. You'll now see the "Daily Regulatory Scrape" workflow listed

**To run it manually right now (first scrape):**
1. Click **Actions** tab
2. Click **Daily Regulatory Scrape** on the left
3. Click **Run workflow** → **Run workflow**
4. Watch it run — it takes about 3–5 minutes
5. Once it completes (green tick), refresh your website — it will have real articles

---

## Step 6: Check it worked

After the first scrape runs:
1. Go to your repo and open `data/articles.json`
2. You should see real articles from sources like the CMA, ICO, FTC, EDPB, etc.
3. Visit your website — articles will be live

---

## Optional: Connect MLEX Gmail alerts (do this later)

When you're ready to connect MLEX emails from your work Gmail:

1. You'll need to set up Gmail API credentials (one-time, about 20 minutes)
2. The steps involve:
   - Creating a Google Cloud project
   - Enabling the Gmail API
   - Downloading credentials
   - Running a one-time authorisation to get a token
   - Adding both as GitHub Secrets
3. Ask for help when you're ready — this part requires guidance

---

## Optional: Add Claude API for smarter topic classification

If you have an Anthropic API key:
1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `ANTHROPIC_API_KEY`
4. Value: your API key from console.anthropic.com
5. Click **Add secret**

The scraper will automatically use Claude Haiku to improve topic tagging.

---

## Understanding the scraper sources

The scraper pulls from these real sources (no AI-generated content):

| Source | Country | Topics |
|--------|---------|--------|
| CMA (gov.uk Atom feed) | 🇬🇧 UK | Competition |
| ICO | 🇬🇧 UK | Privacy |
| Ofcom | 🇬🇧 UK | DMA/DSA |
| European Commission | 🇪🇺 EU | Competition, DMA/DSA |
| EDPB | 🇪🇺 EU | Privacy |
| EUR-Lex | 🇪🇺 EU | All |
| FTC | 🇺🇸 US | Competition, Privacy |
| DOJ Antitrust | 🇺🇸 US | Competition |
| CADE | 🇧🇷 Brazil | Competition |
| ANPD | 🇧🇷 Brazil | Privacy |
| CCI | 🇮🇳 India | Competition |
| Competition Commission SA | 🇿🇦 South Africa | Competition |

---

## Troubleshooting

**Site shows "Could not load article data"**
→ You're opening `index.html` directly from your computer. It must be hosted on GitHub Pages. Visit your `https://maleevskaina.github.io/...` URL instead.

**Workflow fails with red X**
→ Click the failed run → read the error message → most common cause is a missing folder (`.github/workflows/`) or a Python syntax issue.

**No new articles after scrape**
→ Some sources may be temporarily unavailable. Check the workflow run log for which sources returned 0 articles.

**Articles from wrong jurisdiction**
→ Classification uses keyword matching. If a source is consistently mis-tagged, it can be fixed in the `TOPIC_KEYWORDS` / `JURISDICTION_KEYWORDS` dictionaries in `scraper.py`.

---

*Built by Claude for the Prosus Digital Regulatory Team · Last updated: May 2026*
