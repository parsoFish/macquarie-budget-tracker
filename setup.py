"""
setup.py — Auto-generate data/config.json from your Macquarie statements.

Parses all PDFs in statements/, extracts transaction descriptions, identifies
recurring merchants and transfer patterns, and generates a starter config
with pre-categorised keywords. Review and refine the output before running process.py.

Usage: python setup.py
"""

import glob
import json
import os
import re
import sys
from collections import Counter

import pdfplumber

# Import the PDF parser from process.py
from process import parse_pdf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATEMENTS_DIR = os.path.join(BASE_DIR, "statements")
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
EXAMPLE_PATH = os.path.join(DATA_DIR, "config.example.json")

# ─── Well-known merchant patterns for auto-categorisation ───
# These are matched case-insensitively against descriptions.
KNOWN_MERCHANTS = {
    "Groceries": [
        "WOOLWORTHS", "COLES", "ALDI", "IGA", "COSTCO", "HARRIS FARM",
        "FOODWORKS", "FRESH MARKET",
    ],
    "Dining & Drinks": [
        "KFC", "MCDONALDS", "SUBWAY", "GUZMAN", "GYG ", "NANDOS",
        "GRILL'D", "HUNGRY JACKS", "BETTYS BURGERS", "OPORTO",
        "STARBUCKS", "ZAMBRERO", "WENDYS", "BOOST JUICE",
    ],
    "Food Delivery": [
        "UBER *EATS", "UBER* EATS", "DOORDASH", "MENULOG",
        "DELIVEROO", "DOMINOS",
    ],
    "Rideshare & Transport": [
        "UBER *TRIP", "UBER* TRIP", "DIDI", "OLA", "LIME*",
        "TRANSLINK", "OPAL", "MYKI",
    ],
    "Streaming": [
        "NETFLIX", "STAN.COM", "YOUTUBEPREMIUM", "PRIMEVIDEO",
        "DISNEY PLUS", "SPOTIFY", "APPLE.COM/BILL", "BINGE",
        "PARAMOUNT", "AMZNPRIMEA",
    ],
    "Tech Subscriptions": [
        "OPENAI", "GITHUB", "GOOGLE ONE", "CURSOR.COM", "ANTHROPIC",
        "NOTION", "CLAUDE.AI", "AMAZON WEB", "VERCEL", "DROPBOX",
        "MICROSOFT", "ICLOUD",
    ],
    "Phone/Internet": [
        "OPTUS", "TELSTRA", "VODAFONE", "TPG", "BELONG", "AUSSIE BROADBAND",
    ],
    "Shopping": [
        "AMAZON", "KMART", "BIG W", "JB HI-FI", "OFFICEWORKS",
        "REBEL", "UNIQLO", "TARGET", "MYER", "DAVID JONES",
        "CHEMIST WAREHOUSE", "BUNNINGS",
    ],
    "Medical": [
        "PHARMACY", "DENTAL", "TERRYWHITE", "PRICELINE PHARM",
        "MEDICAL", "PATHOLOGY", "RADIOLOGY",
    ],
    "Health Insurance": [
        "MEDIBANK", "BUPA", "HCF", "NIB HEALTH", "SUNCORP HEALTH",
        "AHMA HEALTH",
    ],
    "Gym & Fitness": [
        "ANYTIME FITNESS", "FITNESS FIRST", "F45 ", "GOODLIFE",
        "FITSTOP", "URBANCLIMB",
    ],
    "Alcohol": [
        "BWS", "DAN MURPHY", "LIQUORLAND", "FIRST CHOICE LIQUOR",
    ],
    "Tobacco": [
        "TOBACCONIST", "TSG ",
    ],
    "Convenience": [
        "7-ELEVEN", "NIGHT OWL",
    ],
    "Entertainment": [
        "CINEMA", "HOYTS", "EVENT CINEMAS", "TICKETMASTER", "TICKETEK",
        "MOSHTIX", "TIXEL", "STEAM PURCHASE", "STEAMGAMES",
    ],
}

# Patterns for identifying account transfers
TRANSFER_RE = re.compile(
    r"(TRANSFER TO (?:LINKED ACCOUNT )?XX\d{4}|TO ACCOUNT XX\d{4}|TO LINKED ACCOUNT XX\d{4}|FROM LINKED ACCOUNT XX\d{4})",
    re.IGNORECASE,
)
PERSONAL_TRANSFER_RE = re.compile(
    r"(?:TRANSFER |FUNDS TRANSFER )?TO ([A-Z][A-Z ]+?)(?:\s*-|\s*$)", re.IGNORECASE
)


def extract_keyword(desc):
    """Extract a meaningful keyword from a transaction description."""
    # Strip common prefixes
    for prefix in ["Purchase at ", "Purchase from ", "Online purchase from ",
                    "Debit card ", "Recurring ", "Funds transfer ", "Funds Transfer ",
                    "Direct debit ", "Direct Debit "]:
        if desc.startswith(prefix):
            desc = desc[len(prefix):]
            break

    # Take the merchant/payee portion (before double-space, location suffix, or reference numbers)
    desc = re.split(r"\s{2,}|\d{6,}|REF:|Ref:", desc)[0].strip()
    # Remove trailing location fragments like "BRISBANE AU" or "NSW AUS"
    desc = re.sub(r"\s+[A-Z]{2,3}\s+(AU|AUS|AUST?)$", "", desc)
    desc = re.sub(r"\s+(AU|AUS)$", "", desc)

    return desc.strip().upper()


def auto_categorize_keyword(keyword):
    """Try to match a keyword against known merchant patterns."""
    for cat, patterns in KNOWN_MERCHANTS.items():
        for pattern in patterns:
            if pattern in keyword or keyword.startswith(pattern.rstrip()):
                return cat
    return None


def main():
    if os.path.isfile(CONFIG_PATH):
        print(f"Warning: {CONFIG_PATH} already exists.")
        resp = input("Overwrite? (y/N): ").strip().lower()
        if resp != "y":
            print("Aborted.")
            return

    pdf_files = sorted(glob.glob(os.path.join(STATEMENTS_DIR, "*.pdf")))
    if not pdf_files:
        print("No PDF files found in statements/")
        print("Drop your Macquarie statement PDFs there first.")
        sys.exit(1)

    print(f"Scanning {len(pdf_files)} PDF(s)...\n")

    all_txns = []
    for pdf_path in pdf_files:
        filename = os.path.basename(pdf_path)
        print(f"  {filename}...", end=" ")
        try:
            txns = parse_pdf(pdf_path)
            print(f"{len(txns)} transactions")
            all_txns.extend(txns)
        except Exception as e:
            print(f"ERROR: {e}")

    if not all_txns:
        print("\nNo transactions found. Check your PDFs are Macquarie statements.")
        sys.exit(1)

    print(f"\n  Total: {len(all_txns)} transactions\n")

    # ─── Analyse descriptions ───
    desc_counter = Counter()
    keyword_map = {}  # keyword → [descriptions]

    for t in all_txns:
        kw = extract_keyword(t["description"])
        if len(kw) < 3:
            continue
        desc_counter[kw] += 1
        keyword_map.setdefault(kw, set()).add(t["description"][:60])

    # ─── Identify account transfers ───
    transfer_keywords = {}  # "TO ACCOUNT XX1234" → count
    personal_transfers = set()
    internal_credits = set()

    for t in all_txns:
        up = t["description"].upper()
        m = TRANSFER_RE.search(up)
        if m:
            transfer_keywords[m.group(1)] = transfer_keywords.get(m.group(1), 0) + 1
            if "FROM LINKED ACCOUNT" in m.group(1):
                internal_credits.add(m.group(1))
            continue
        # Check for personal transfers ("TO FIRSTNAME LASTNAME")
        m = PERSONAL_TRANSFER_RE.search(up)
        if m:
            name = m.group(1).strip()
            # Filter out common non-name patterns
            if len(name.split()) <= 3 and not any(
                x in name for x in ["ACCOUNT", "LINKED", "BALANCE"]
            ):
                personal_transfers.add(f"TO {name}")

    # ─── Build categories ───
    categories = {}

    # Account transfers — prompt user to label them
    print("=" * 60)
    print("ACCOUNT TRANSFERS DETECTED")
    print("=" * 60)
    transfer_cats = {
        "mortgage": "Mortgage",
        "m": "Mortgage",
        "rent": "Rent",
        "r": "Rent",
        "savings": "Savings Transfer",
        "s": "Savings Transfer",
        "partner": "Partner Spending",
        "p": "Partner Spending",
        "charity": "Charity",
        "c": "Charity",
    }
    print("\nFor each transfer, type a label or press Enter to skip:")
    print("  Shortcuts: m=Mortgage, r=Rent, s=Savings, p=Partner, c=Charity\n")

    for kw, count in sorted(transfer_keywords.items(), key=lambda x: -x[1]):
        direction = "incoming" if "FROM" in kw else "outgoing"
        label = input(f"  {kw} ({count}x, {direction}) -> ").strip()
        if not label:
            continue
        cat = transfer_cats.get(label.lower(), label)
        categories.setdefault(cat, []).append(kw)

    # Personal transfers
    if personal_transfers:
        categories["Personal Transfers"] = sorted(personal_transfers)

    # Internal credits
    if internal_credits:
        internal_credit_keywords = sorted(internal_credits)
    else:
        internal_credit_keywords = []

    # ─── Auto-categorise known merchants ───
    auto_matched = {}   # cat → [keywords]
    unmatched = []       # (keyword, count)

    for kw, count in desc_counter.most_common():
        # Skip transfer patterns already handled
        if TRANSFER_RE.search(kw) or PERSONAL_TRANSFER_RE.search(kw):
            continue
        if count < 2 and len(desc_counter) > 50:
            continue  # Skip one-offs when there's plenty of data

        cat = auto_categorize_keyword(kw)
        if cat:
            auto_matched.setdefault(cat, []).append(kw)
        else:
            unmatched.append((kw, count))

    # Add auto-matched keywords
    for cat, keywords in auto_matched.items():
        existing = categories.get(cat, [])
        categories[cat] = existing + keywords

    # Show unmatched for awareness
    if unmatched:
        print(f"\n{'=' * 60}")
        print(f"UNCATEGORISED MERCHANTS ({len(unmatched)} unique)")
        print("=" * 60)
        print("These will be tagged 'Other'. Add keywords to config.json later.\n")
        for kw, count in unmatched[:30]:
            examples = list(keyword_map.get(kw, set()))[:2]
            print(f"  {kw:<40s} ({count:>3d}x)")
        if len(unmatched) > 30:
            print(f"  ... and {len(unmatched) - 30} more")

    # ─── Detect mortgage target (largest linked account balance) ───
    balances = sorted(t["balance"] for t in all_txns)
    max_balance = balances[-1] if balances else 0
    # Suggest a round target above the max balance
    suggested_target = ((max_balance // 50000) + 1) * 50000
    print(f"\nHighest observed balance: ${max_balance:,.2f}")
    target_input = input(f"Mortgage offset target (Enter for ${suggested_target:,.0f}): ").strip()
    if target_input:
        mortgage_target = int(target_input.replace(",", "").replace("$", ""))
    else:
        mortgage_target = int(suggested_target)

    # ─── Assemble hub/spoke from categories ───
    hub_defaults = {
        "Rent", "Mortgage", "Charity", "Health Insurance", "Phone/Internet",
        "Streaming", "Tech Subscriptions", "Other Subscriptions",
        "Gym & Fitness", "Cleaning",
    }
    spoke_defaults = {
        "Dining & Drinks", "Food Delivery", "Groceries", "Convenience",
        "Vending", "Rideshare & Transport", "Shopping", "Medical",
        "Tobacco", "Alcohol", "Entertainment", "Personal Transfers", "Other",
    }
    hub_categories = [c for c in categories if c in hub_defaults]
    spoke_categories = [c for c in categories if c in spoke_defaults]
    # Add any defaults that have keywords
    for c in hub_defaults:
        if c in categories and c not in hub_categories:
            hub_categories.append(c)
    for c in spoke_defaults:
        if c not in spoke_categories:
            spoke_categories.append(c)

    # ─── Sort keyword lists for readability ───
    for cat in categories:
        categories[cat] = sorted(set(categories[cat]))

    config = {
        "mortgage_target": mortgage_target,
        "categories": dict(sorted(categories.items())),
        "internal_credit_keywords": internal_credit_keywords,
        "hub_categories": sorted(hub_categories),
        "spoke_categories": sorted(spoke_categories),
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

    cat_count = len(categories)
    kw_count = sum(len(v) for v in categories.values())
    print(f"\n{'=' * 60}")
    print(f"Generated {CONFIG_PATH}")
    print(f"  {cat_count} categories, {kw_count} keywords")
    print(f"  Mortgage target: ${mortgage_target:,}")
    print(f"\nNext steps:")
    print(f"  1. Review data/config.json — move merchants to better categories")
    print(f"  2. Run: python process.py")
    print(f"  3. Open index.html")


if __name__ == "__main__":
    main()
