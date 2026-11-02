import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.plaid_categorize import categorize, categorize_new_rows, CATEGORIES, VENDOR_RULES


class CategorizeTests(unittest.TestCase):
    def test_known_vendors_match_expected_category(self):
        cases = [
            ('U-HAUL #123456', 'Truck & Gas'),
            ('PENSKE TRUCK RENTAL', 'Truck & Gas'),
            ('SHELL OIL 57443910902', 'Truck & Gas'),
            ('COSTCO GAS #123', 'Truck & Gas'),
            ('THE HOME DEPOT #1234', 'Furniture/Inventory'),
            ('IKEA RENTON', 'Furniture/Inventory'),
            ('PUBLIC STORAGE #456', 'Storage'),
            ('STATE FARM INSURANCE', 'Insurance'),
            ('GEICO AUTO', 'Insurance'),
            ('ADOBE  CREATIVE CLOUD', 'Software/Subscriptions'),
            ('GOOGLE WORKSPACE', 'Software/Subscriptions'),
            ('FACEBOOK ADS', 'Marketing'),
            ('STAPLES #789', 'Office'),
            ('USPS PO 12345', 'Office'),
        ]
        for merchant, expected in cases:
            with self.subTest(merchant=merchant):
                self.assertEqual(categorize(merchant, None), expected)

    def test_unknown_vendor_is_uncategorized_not_guessed(self):
        self.assertIsNone(categorize('Some Random Cafe LLC', None))
        self.assertIsNone(categorize('', ''))
        self.assertIsNone(categorize(None, None))

    def test_falls_back_to_name_when_merchant_name_missing(self):
        self.assertEqual(categorize(None, 'U-HAUL MOVING'), 'Truck & Gas')
        self.assertEqual(categorize('', 'ADOBE INC'), 'Software/Subscriptions')

    def test_first_matching_rule_wins_deterministically(self):
        # Every category appears exactly once per pass -- confirms there's no accidental
        # overlap between rules that would make the result order-dependent.
        for pattern, category in VENDOR_RULES:
            self.assertIn(category, CATEGORIES)

    def test_case_insensitive(self):
        self.assertEqual(categorize('u-haul #1', None), 'Truck & Gas')
        self.assertEqual(categorize('U-HAUL #1', None), 'Truck & Gas')

    def test_categorize_new_rows_only_touches_rows_with_no_category(self):
        # Mirrors api/webhook.py's actual row construction: a brand-new row has no 'category'
        # key at all (not even category: null) until categorize_new_rows() adds one on a match.
        rows = [
            {'transaction_id': 't1', 'merchant_name': 'U-HAUL #1'},
            {'transaction_id': 't2', 'merchant_name': 'Unknown Vendor'},
            {'transaction_id': 't3', 'merchant_name': 'U-HAUL #2', 'category': 'Marketing', 'category_source': 'manual'},
        ]
        categorize_new_rows(rows)
        self.assertEqual(rows[0]['category'], 'Truck & Gas')
        self.assertEqual(rows[0]['category_source'], 'rule')
        # Already-categorized row (manual, in this case) is never touched, even though its own
        # vendor text would match a different rule -- "never reclassify silently" in code form.
        self.assertEqual(rows[2]['category'], 'Marketing')
        self.assertEqual(rows[2]['category_source'], 'manual')
        self.assertNotIn('category', rows[1])

    def test_categorize_new_rows_leaves_unmatched_rows_without_a_category_key(self):
        # No 'category: null' in the payload for an unmatched row -- see api/webhook.py's
        # comment on why that matters for a merge-duplicates upsert: a key present with value
        # null would still overwrite an existing value on conflict; an absent key never would.
        rows = [{'transaction_id': 't1', 'merchant_name': 'Totally Unknown Co'}]
        categorize_new_rows(rows)
        self.assertNotIn('category', rows[0])
        self.assertNotIn('category_source', rows[0])

    def test_storage_and_insurance_are_real_categories(self):
        # Added for the spec's explicit overhead examples (Section 6): storage, insurance.
        self.assertIn('Storage', CATEGORIES)
        self.assertIn('Insurance', CATEGORIES)


if __name__ == '__main__':
    unittest.main()
