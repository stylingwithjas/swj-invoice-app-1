"""Rule-based auto-categorization for Plaid business-Amex transactions (Phase 1, Nov 2026).

Owner spec: "auto-categorize by payee/vendor rules where the mapping is unambiguous, and flag
everything ambiguous as uncategorized for Jasmine/owner review... Do not reclassify historical
transactions silently." This module only ever assigns a category to a transaction that doesn't
already have one (manual or rule-assigned) -- callers must never re-run it over existing rows.
"""
import re

CATEGORIES = ['Supplies', 'Furniture/Inventory', 'Truck & Gas', 'Storage', 'Insurance',
              'Software/Subscriptions', 'Marketing', 'Meals', 'Office', 'Other']

# Checked in order; the first regex that matches merchant_name (falling back to name) wins.
# Deliberately narrow and literal -- a vendor name, not a guess from the purchase amount or
# Plaid's own (often noisy) category. Anything not matched here stays uncategorized.
VENDOR_RULES = [
    (re.compile(r'u-?haul|penske|budget\s*truck|ryder\b', re.I), 'Truck & Gas'),
    (re.compile(r'\bshell\b|\bchevron\b|\bexxon\b|\bmobil\b|\b76\b|\barco\b|\bconoco\b|costco\s*gas|circle\s*k', re.I), 'Truck & Gas'),
    (re.compile(r'home\s*depot|lowe.?s\b|\bikea\b|west\s*elm|crate\s*(&|and)\s*barrel|wayfair|pottery\s*barn|restoration\s*hardware', re.I), 'Furniture/Inventory'),
    (re.compile(r'public\s*storage|extra\s*space|cubesmart|life\s*storage', re.I), 'Storage'),
    (re.compile(r'state\s*farm|\bgeico\b|progressive\b|allstate|farmers\s*insurance|liberty\s*mutual|\bhiscox\b|\bnext\s*insurance\b', re.I), 'Insurance'),
    (re.compile(r'\badobe\b|\bcanva\b|quickbooks|google\s*workspace|microsoft\s*365|\bdropbox\b|\bzoom\.us\b|\bnotion\b|\bplaid\b', re.I), 'Software/Subscriptions'),
    (re.compile(r'facebook\s*ads|instagram|google\s*ads|mailchimp|constant\s*contact|\bklaviyo\b', re.I), 'Marketing'),
    (re.compile(r'\bstaples\b|office\s*depot|\busps\b|ups\s*store|fedex\s*office', re.I), 'Office'),
]


def categorize(merchant_name, name):
    """Returns a category string or None (uncategorized -- never guessed). Vendor text only;
    never considers amount, date, or Plaid's own category (that stays a read-only reference)."""
    text = (merchant_name or name or '').strip()
    if not text:
        return None
    for pattern, category in VENDOR_RULES:
        if pattern.search(text):
            return category
    return None


def categorize_new_rows(rows):
    """Applies categorize() to a batch of freshly-synced transaction dicts (the shape
    plaid_sync_transactions() is about to write for the first time) -- sets category and
    category_source='rule' in place for any row with no category of its own. A row that
    already carries a category (synced before, or a human edit mid-sync) is left untouched;
    this function is only ever meant to run once, at the moment a transaction is first seen."""
    for row in rows:
        if row.get('category') is None:
            matched = categorize(row.get('merchant_name'), row.get('name'))
            if matched:
                row['category'] = matched
                row['category_source'] = 'rule'
    return rows
