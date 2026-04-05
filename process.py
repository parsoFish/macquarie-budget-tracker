"""
process.py — Macquarie Budget Tracker PDF Pipeline

Scans statements/*.pdf, parses transactions from both old (DD Mon) and new (Mon DD)
Macquarie statement formats using text-based parsing, deduplicates, categorises
via data/config.json, and writes data/data.js for the dashboard to load.

Usage: python process.py
"""

import glob
import json
import os
import re
import sys

import pdfplumber

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATEMENTS_DIR = os.path.join(BASE_DIR, "statements")
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
OUTPUT_JS = os.path.join(DATA_DIR, "data.js")
OUTPUT_JSON = os.path.join(DATA_DIR, "transactions.json")

MONTH_MAP = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}
MONTHS_PAT = "(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"

# Regex patterns
MONTH_YEAR_RE = re.compile(rf"^({MONTHS_PAT})\s+(\d{{4}})")
DATE_NEW_RE = re.compile(rf"^({MONTHS_PAT})\s+(\d{{1,2}})\s+(.*)")
DATE_OLD_RE = re.compile(rf"^(\d{{1,2}})\s+({MONTHS_PAT})\s+(.*)")
BALANCE_CR_RE = re.compile(r"([\d,]+\.\d{2})\s*CR\s*$", re.IGNORECASE)
AMOUNT_END_RE = re.compile(r"([\d,]+\.\d{2})\s*$")

TX_TYPE_PREFIXES = [
    "Recurring", "Funds transfer", "Funds Transfer",
    "Direct debit", "Direct Debit", "Debit card", "Debit Card",
]

SKIP_MARKERS = [
    "account name", "offset account", "transaction listing",
    "transaction history", "your transactions", "please check",
    "overview of", "closing balance", "end of statement", "end of report",
    "macquarie.com", "enquiries", "abn 46", "afsl", "contact-us",
    "date transaction description", "now available", "disputing",
    "help.macquarie", "your messages", "we're excited", "temporary lock",
    "find it again", "opening interest rate",
]


def load_config():
    if not os.path.isfile(CONFIG_PATH):
        print(f"Error: {CONFIG_PATH} not found.")
        print(f"Copy data/config.example.json to data/config.json and customise it.")
        sys.exit(1)
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def categorize(desc, categories):
    up = desc.upper()
    for cat, keywords in categories.items():
        for kw in keywords:
            if kw in up:
                return cat
    return "Other"


def classify_credit(desc, internal_keywords):
    up = desc.upper()
    for kw in internal_keywords:
        if kw in up:
            return "Internal Transfer"
    return "Income"


def is_skip_line(line):
    low = line.lower().strip()
    if not low or len(low) < 3:
        return True
    for marker in SKIP_MARKERS:
        if marker in low:
            return True
    return False


def pn(s):
    """Parse numeric string like '70,199.23' to float."""
    return float(s.replace(",", ""))


def parse_pdf(filepath):
    """Parse a single Macquarie statement PDF and return list of transactions."""
    transactions = []
    current_year = None
    prev_balance = None
    format_type = None
    pending_desc = ""

    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue

            seen_section = False

            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue

                # Month/year header (e.g. "Jan 2026", "Jul 2025 - continued")
                my_match = MONTH_YEAR_RE.match(line)
                if my_match:
                    yr = int(my_match.group(2))
                    if yr >= 2020:
                        current_year = str(yr)
                    seen_section = True
                    pending_desc = ""
                    continue

                # Column header → marks start of transaction section
                if "debits" in line.lower() and "credits" in line.lower():
                    seen_section = True
                    pending_desc = ""
                    continue

                if not seen_section and not current_year:
                    continue

                # Before section marker on this page, skip headers
                if not seen_section:
                    if is_skip_line(line):
                        continue
                    d_new = DATE_NEW_RE.match(line)
                    d_old = DATE_OLD_RE.match(line)
                    if (d_new and int(d_new.group(2)) <= 31) or (
                        d_old and int(d_old.group(1)) <= 31
                    ):
                        seen_section = True
                    else:
                        continue

                # Opening balance without date prefix (new format)
                if "opening balance" in line.lower() and not DATE_OLD_RE.match(line):
                    bal_m = BALANCE_CR_RE.search(line)
                    if bal_m:
                        prev_balance = pn(bal_m.group(1))
                    continue

                # Try date line
                date_str = None
                rest = None

                d_new = DATE_NEW_RE.match(line)
                d_old = DATE_OLD_RE.match(line)

                if d_new and int(d_new.group(2)) <= 31:
                    month_abbr, day, rest = d_new.group(1), d_new.group(2).zfill(2), d_new.group(3)
                    if format_type is None:
                        format_type = "new"
                elif d_old and int(d_old.group(1)) <= 31:
                    day, month_abbr, rest = d_old.group(1).zfill(2), d_old.group(2), d_old.group(3)
                    if format_type is None:
                        format_type = "old"

                if rest is not None and current_year:
                    month_num = MONTH_MAP.get(month_abbr, "00")
                    date_str = f"{current_year}-{month_num}-{day}"

                    # Extract balance (last number + CR at end)
                    bal_m = BALANCE_CR_RE.search(rest)
                    if not bal_m:
                        pending_desc += " " + rest
                        continue

                    balance = pn(bal_m.group(1))
                    before_bal = rest[: bal_m.start()].strip()

                    # Opening balance with date prefix (old format)
                    if "opening balance" in before_bal.lower():
                        prev_balance = balance
                        pending_desc = ""
                        continue

                    # Skip interest rate lines
                    if "interest rate" in before_bal.lower():
                        continue

                    # Extract amount (last decimal number before balance)
                    amt_m = AMOUNT_END_RE.search(before_bal)
                    if not amt_m:
                        continue

                    amount = pn(amt_m.group(1))
                    desc_part = before_bal[: amt_m.start()].strip()

                    # Combine with pending description
                    if format_type == "old" and pending_desc.strip():
                        full_desc = (pending_desc.strip() + " " + desc_part).strip()
                    else:
                        full_desc = desc_part
                    pending_desc = ""

                    # Determine debit vs credit via balance comparison
                    debit = 0.0
                    credit = 0.0
                    if prev_balance is not None:
                        if abs(round(prev_balance - amount, 2) - balance) < 0.05:
                            debit = amount
                        elif abs(round(prev_balance + amount, 2) - balance) < 0.05:
                            credit = amount
                        elif balance < prev_balance:
                            debit = amount
                        else:
                            credit = amount
                    else:
                        debit = amount

                    prev_balance = balance

                    # Extract transaction type prefix
                    tx_type = ""
                    for prefix in TX_TYPE_PREFIXES:
                        if full_desc.lower().startswith(prefix.lower()):
                            tx_type = prefix
                            full_desc = full_desc[len(prefix) :].strip()
                            break

                    transactions.append({
                        "date": date_str,
                        "type": tx_type,
                        "description": full_desc,
                        "debit": round(debit, 2),
                        "credit": round(credit, 2),
                        "balance": round(balance, 2),
                    })
                else:
                    # Non-date line
                    if is_skip_line(line):
                        continue

                    if format_type == "new" and transactions:
                        transactions[-1]["description"] += " " + line
                    else:
                        pending_desc += " " + line

    return transactions


def deduplicate(transactions):
    transactions.sort(key=lambda t: (t["date"], t["balance"]))
    seen = set()
    result = []
    for t in transactions:
        key = f"{t['date']}|{t['description'][:40]}|{t['debit']}|{t['credit']}|{t['balance']}"
        if key not in seen:
            seen.add(key)
            result.append(t)
    return result


def main():
    if not os.path.isdir(STATEMENTS_DIR):
        print(f"Error: statements/ folder not found at {STATEMENTS_DIR}")
        sys.exit(1)

    config = load_config()
    categories = config["categories"]
    internal_keywords = config.get("internal_credit_keywords", [])

    pdf_files = sorted(glob.glob(os.path.join(STATEMENTS_DIR, "*.pdf")))
    if not pdf_files:
        print("No PDF files found in statements/")
        sys.exit(1)

    print(f"Found {len(pdf_files)} PDF file(s) in statements/\n")

    all_transactions = []
    for pdf_path in pdf_files:
        filename = os.path.basename(pdf_path)
        print(f"  Parsing {filename}...", end=" ")
        try:
            txns = parse_pdf(pdf_path)
            print(f"{len(txns)} transactions")
            for t in txns:
                t["_source"] = filename
            all_transactions.extend(txns)
        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()

    before = len(all_transactions)
    all_transactions = deduplicate(all_transactions)
    dupes = before - len(all_transactions)
    print(f"\n  Total: {before} raw -> {len(all_transactions)} after dedup ({dupes} duplicates removed)")

    exclude_keywords = [kw.upper() for kw in config.get("exclude_from_averages", [])]

    for t in all_transactions:
        if t["debit"] > 0:
            t["category"] = categorize(t["description"], categories)
        elif t["credit"] > 0:
            t["category"] = classify_credit(t["description"], internal_keywords)
        else:
            t["category"] = "Other"

        # Mark one-offs to exclude from averages
        up = t["description"].upper()
        if any(kw in up for kw in exclude_keywords):
            t["excluded"] = True

    dates = sorted(t["date"] for t in all_transactions)
    if dates:
        print(f"  Date range: {dates[0]} to {dates[-1]}")

    total_debits = sum(t["debit"] for t in all_transactions if t["debit"] > 0)
    total_credits = sum(t["credit"] for t in all_transactions if t["credit"] > 0)
    last_balance = all_transactions[-1]["balance"] if all_transactions else 0
    print(f"  Total debits: ${total_debits:,.2f}")
    print(f"  Total credits: ${total_credits:,.2f}")
    print(f"  Latest balance: ${last_balance:,.2f}")

    cat_counts = {}
    for t in all_transactions:
        cat_counts[t["category"]] = cat_counts.get(t["category"], 0) + 1
    print("\n  Category breakdown:")
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        total = sum(t["debit"] for t in all_transactions if t["category"] == cat and t["debit"] > 0)
        total += sum(t["credit"] for t in all_transactions if t["category"] == cat and t["credit"] > 0)
        print(f"    {cat:<25s} {count:>4d} txns  ${total:>10,.2f}")

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_JSON, "w") as f:
        json.dump(all_transactions, f, indent=2)
    print(f"\n  Written: {OUTPUT_JSON} ({os.path.getsize(OUTPUT_JSON) / 1024:.0f} KB)")

    with open(OUTPUT_JS, "w") as f:
        f.write("// Auto-generated by process.py — do not edit\n")
        f.write(f"const TRANSACTIONS = {json.dumps(all_transactions, separators=(',', ':'))};\n")
        f.write(f"const CONFIG = {json.dumps(config, separators=(',', ':'))};\n")
    print(f"  Written: {OUTPUT_JS} ({os.path.getsize(OUTPUT_JS) / 1024:.0f} KB)")

    print("\nDone. Open index.html to view the dashboard.")


if __name__ == "__main__":
    main()
