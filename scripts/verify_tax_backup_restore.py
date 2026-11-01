"""
Restore test for the monthly tax-data backup export (fix spec addendum, Nov 1 2026: "An
untested export is not a backup"). Proves the CSV export format alone — without the
original Supabase rows, without any app object in memory — can rebuild a month's Gross
Receipts figure.

Usage: python3 scripts/verify_tax_backup_restore.py
Exits non-zero and prints which assertion failed if anything doesn't round-trip.

This file is NOT under api/ on purpose — it is a standalone verification tool, never a
Vercel function, and must stay off that path so it doesn't count toward the Hobby plan's
12-function cap.
"""
import os
import csv
import io
import importlib.util

os.environ.setdefault('SUPABASE_KEY', 'test')
os.environ.setdefault('SUPABASE_URL', 'https://example.test')

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('webhook', os.path.join(_HERE, '..', 'api', 'webhook.py'))
webhook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(webhook)

# Mock October 2026 fixture — the same figures used throughout the tax-report fix (Baker
# signed $3,978, Mullenix unsigned $3,527.28, Cruson unsigned $4,111.60 = $11,616.88), plus
# an August invoice and system/review rows to prove those don't leak or get dropped.
FIXTURE = [
    {'invnum': '2020128', 'client': 'Baker', 'address': '123 Main St, Seattle, WA 98101', 'invdate': '2026-10-01', 'created_at': '2026-10-01T00:00:00Z',
     'data': {'signed': True, 'price_led_tax': True, 'city': 'Seattle', 'zip': '98101', 'taxrate': 10.25, 'base_price': 3600, 'addons': [],
              'payments': [{'status': 'paid', 'amount': 3978.00, 'paid_date': '2026-10-15', 'manual_method': 'stripe'}]}},
    {'invnum': '2020123', 'client': 'Mullenix', 'address': '456 Oak Ave, Tacoma, WA 98402', 'invdate': '2026-10-01', 'created_at': '2026-10-02T00:00:00Z',
     'data': {'signed': False, 'price_led_tax': True, 'city': 'Tacoma', 'zip': '98402', 'taxrate': 10.3, 'base_price': 3200, 'addons': [],
              'payments': [{'status': 'paid', 'amount': 3527.28, 'paid_date': '2026-10-01'}]}},
    {'invnum': '2020129', 'client': 'Cruson', 'address': '789 Pine Rd, Bellevue, WA 98004', 'invdate': '2026-10-01', 'created_at': '2026-10-03T00:00:00Z',
     'data': {'signed': False, 'price_led_tax': True, 'city': 'Bellevue', 'zip': '98004', 'taxrate': 10.3, 'base_price': 3700, 'addons': [],
              'payments': [{'status': 'paid', 'amount': 4111.60, 'paid_date': '2026-10-31'}]}},
    {'invnum': '4010001', 'client': 'Williamson', 'address': '333 Cedar Ct, Everett, WA 98201', 'invdate': '2026-08-01', 'created_at': '2026-08-01T00:00:00Z',
     'data': {'signed': True, 'price_led_tax': True, 'city': 'Everett', 'zip': '98201', 'taxrate': 9.9, 'base_price': 2900, 'addons': [],
              'payments': [{'status': 'paid', 'amount': 3123.60, 'paid_date': '2026-08-10'}]}},
    {'invnum': 'FINANCE_CONFIG', 'client': 'FINANCE_CONFIG', 'address': '', 'invdate': '2026-01-01', 'created_at': '2026-01-01T00:00:00Z',
     'data': {'profit_target_pct': 30, 'tithe_pct': 10}},
    {'invnum': 'TAX_REVIEW_2026-10', 'client': 'TAX_REVIEW_2026-10', 'address': '', 'invdate': '2026-11-01', 'created_at': '2026-11-01T00:00:00Z',
     'data': {'reviewed': True, 'reviewed_at': '2026-11-01T10:00:00', 'reconciled_actual': 11616.88, 'reconciled_diff': 0.0,
              'reconciled_clean': True, 'reconciled_explain': ''}},
]


def main():
    webhook._tax_list_all_rows = lambda: FIXTURE
    csvs, stats = webhook.build_tax_backup_export()
    print('Export stats:', stats)

    def parse_csv(name):
        return list(csv.DictReader(io.StringIO(csvs[name].decode('utf-8'))))

    restored_invoices = parse_csv('invoices.csv')
    restored_payments = parse_csv('payments.csv')
    restored_config = parse_csv('config.csv')
    restored_reviews = parse_csv('tax_reviews.csv')

    assert all(not r['invnum'].startswith(('FINANCE_', 'PRICING_', 'TIER_', 'TAX_REVIEW_')) for r in restored_invoices), \
        'FAIL: a system/review row leaked into invoices.csv'
    assert len(restored_config) == 1 and restored_config[0]['config_key'] == 'FINANCE_CONFIG', 'FAIL: config.csv wrong'
    assert len(restored_reviews) == 1 and restored_reviews[0]['period_key'] == '2026-10', 'FAIL: tax_reviews.csv wrong'

    october_gross = sum(
        float(p['amount']) for p in restored_payments if '2026-10-01' <= p['paid_date'] <= '2026-10-31'
    )
    print(f'Rebuilt October 2026 Gross Receipts from the export alone: ${october_gross:,.2f}')
    assert abs(october_gross - 11616.88) < 0.01, f'FAIL: expected $11,616.88, got ${october_gross:,.2f}'

    baker_payment = next(p for p in restored_payments if p['invnum'] == '2020128')
    assert baker_payment['manual_method'] == 'stripe', 'FAIL: manual_method correction did not round-trip'

    print('RESTORE TEST PASSED — October 2026 rebuilds to $11,616.88 from the export alone, '
          'system/review rows correctly isolated, manual_method correction round-trips.')
    print('NOTE: verified against the mock fixture only — re-run this same script against a '
          'real production export (point FIXTURE/_tax_list_all_rows at the live export instead) '
          'before trusting a real backup.')


if __name__ == '__main__':
    main()
