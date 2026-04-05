# Macquarie Budget Tracker

A lightweight personal finance dashboard for Macquarie bank offset account holders. Drop in your PDF statements, run a Python script, and open a single HTML file — no server, no database, no sign-ups.

![Dashboard](https://img.shields.io/badge/stack-Python%20%2B%20HTML%20%2B%20Chart.js-blue)

## Features

- **PDF parsing** — Handles both old (`DD Mon`) and new (`Mon DD`) Macquarie statement formats
- **Automatic categorisation** — Config-driven keyword matching for 25+ spending categories
- **Mortgage offset tracking** — Progress bar and projections toward your target balance
- **Hub & Spoke model** — Separates fixed auto-debits (Hub) from variable lifestyle spending (Spoke)
- **Spending account planner** — Interactive slider to model weekly spending budgets and offset impact
- **Charts & tables** — Income vs spending, category breakdown, top merchants, daily trends, subscriptions
- **Zero infrastructure** — Works from `file://`, no server needed

## Quick Start

```bash
# 1. Clone
git clone https://github.com/parsoFish/macquarie-budget-tracker.git
cd macquarie-budget-tracker

# 2. Install dependency
pip install pdfplumber

# 3. Configure
cp data/config.example.json data/config.json
# Edit data/config.json — set your mortgage_target, account keywords, merchants, etc.

# 4. Add statements
# Drop your Macquarie PDF statements into the statements/ folder

# 5. Process
python process.py

# 6. View
# Open index.html in your browser
```

## Configuration

Edit `data/config.json` to match your accounts:

| Field | Purpose |
|---|---|
| `mortgage_target` | Your offset balance goal (e.g. `273000`) |
| `categories` | Map category names to keyword arrays that match transaction descriptions |
| `internal_credit_keywords` | Keywords identifying internal account transfers (excluded from income) |
| `hub_categories` | Fixed recurring costs (auto-debited from offset) |
| `spoke_categories` | Variable lifestyle costs (for the spending account planner) |

Keywords are matched case-insensitively against transaction descriptions.

## How It Works

```
statements/*.pdf  →  process.py  →  data/data.js  →  index.html
                         ↑
                  data/config.json
```

1. `process.py` reads all PDFs in `statements/`, parses transactions using text extraction, deduplicates, and categorises using your config
2. Outputs `data/data.js` (loaded by the dashboard) and `data/transactions.json` (reference copy)
3. `index.html` loads `data/data.js` via a `<script>` tag — no fetch, no CORS issues, works from `file://`

## Re-processing

When you download new statements, just drop them in `statements/` and re-run:

```bash
python process.py
```

Duplicates are automatically removed, so overlapping date ranges are fine.

## Privacy

Your financial data stays entirely local. No data is sent anywhere — the dashboard is a static HTML file with no network requests (except Chart.js CDN). Statements, config, and generated data files are all gitignored.
