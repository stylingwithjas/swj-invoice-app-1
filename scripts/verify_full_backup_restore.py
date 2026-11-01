"""
Restore test for the monthly full-website backup export (fix spec addendum, Nov 1 2026:
"An untested export is not a backup" — broadened per Gabe's later request for a backup of
"our entire website ... all the invoices and data and all the work we had done"). Proves
the CSV export format alone — without the original Supabase rows, without any app object
in memory — can rebuild a month's Gross Receipts figure, and that every other section
(drafts, GS Photography, tasks, time entries, employees, contract links) round-trips with
the right shape and that no credential ever appears anywhere in the export.

Usage: python3 scripts/verify_full_backup_restore.py
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
# an August invoice, system/review rows, a draft, and a GS Photography invoice to prove
# none of those leak into the wrong file or get dropped.
INVOICE_FIXTURE = [
    {'invnum': '2020128', 'client': 'Baker', 'address': '123 Main St, Seattle, WA 98101', 'invdate': '2026-10-01', 'created_at': '2026-10-01T00:00:00Z',
     'data': {'signed': True, 'signed_date': '2026-10-05', 'signed_by': 'Jane Baker', 'price_led_tax': True, 'city': 'Seattle', 'zip': '98101',
              'taxrate': 10.25, 'base_price': 3600, 'addons': [],
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
    {'invnum': 'DRAFT-99', 'client': 'Someone Maybe', 'address': '', 'invdate': '2026-10-20', 'created_at': '2026-10-20T00:00:00Z',
     'data': {'client': 'Someone Maybe'}},
    {'invnum': 'GS20261', 'client': 'A GS Client', 'address': '555 Photo Ln', 'invdate': '2026-10-10', 'created_at': '2026-10-10T00:00:00Z',
     'data': {'client': 'A GS Client', 'client_email': 'client@example.com', 'session_type': 'portrait'}},
]
TASKS_FIXTURE = [
    {'id': 1, 'date': '2026-10-05', 'type': 'task', 'status': 'pending', 'title': 'Call Baker', 'notes': '', 'invnum': '2020128',
     'scheduled_time': '', 'price': '', 'room': '', 'sort_order': 0, 'created_at': '2026-10-01T00:00:00Z'},
]
TIME_ENTRIES_FIXTURE = [
    {'id': 1, 'employee_name': 'Jasmine', 'work_type': 'staging', 'rate': 35, 'invnum': '2020128', 'job_label': 'Baker',
     'source': 'manual', 'clock_in': '2026-10-05T09:00:00Z', 'clock_out': '2026-10-05T12:00:00Z', 'hours': 3, 'amount': 105,
     'approved': True, 'approved_at': '2026-10-06T00:00:00Z', 'paid': True},
]
EMPLOYEES_FIXTURE = [
    {'id': 1, 'name': 'Jasmine', 'active': True, 'password_hash': 'super-secret-hash-must-never-export'},
]


def main():
    webhook._tax_list_all_rows = lambda: INVOICE_FIXTURE
    webhook._fetch_table_rows = lambda table, select, limit=5000: {
        'tasks': TASKS_FIXTURE, 'time_entries': TIME_ENTRIES_FIXTURE, 'employees': EMPLOYEES_FIXTURE,
    }[table]
    webhook._tax_sign_contract_url = lambda invnum, expires_in=None: f'https://example.test/signed-contracts/{invnum}-signed.pdf?token=fake'

    csvs, stats = webhook.build_full_backup_export()
    print('Export stats:', stats)

    def parse_csv(name):
        return list(csv.DictReader(io.StringIO(csvs[name].decode('utf-8'))))

    restored_invoices = parse_csv('invoices.csv')
    restored_payments = parse_csv('payments.csv')
    restored_config = parse_csv('config.csv')
    restored_reviews = parse_csv('tax_reviews.csv')
    restored_drafts = parse_csv('drafts.csv')
    restored_gs = parse_csv('gs_invoices.csv')
    restored_contracts = parse_csv('contract_links.csv')
    restored_tasks = parse_csv('tasks.csv')
    restored_time = parse_csv('time_entries.csv')
    restored_employees = parse_csv('employees.csv')

    # --- Core tax figures, unchanged behavior ---
    assert all(not r['invnum'].startswith(('FINANCE_', 'PRICING_', 'TIER_', 'TAX_REVIEW_', 'DRAFT-', 'GS')) for r in restored_invoices), \
        'FAIL: a system/draft/GS row leaked into invoices.csv'
    assert len(restored_config) == 1 and restored_config[0]['config_key'] == 'FINANCE_CONFIG', 'FAIL: config.csv wrong'
    assert len(restored_reviews) == 1 and restored_reviews[0]['period_key'] == '2026-10', 'FAIL: tax_reviews.csv wrong'
    october_gross = sum(float(p['amount']) for p in restored_payments if '2026-10-01' <= p['paid_date'] <= '2026-10-31')
    print(f'Rebuilt October 2026 Gross Receipts from the export alone: ${october_gross:,.2f}')
    assert abs(october_gross - 11616.88) < 0.01, f'FAIL: expected $11,616.88, got ${october_gross:,.2f}'
    baker_payment = next(p for p in restored_payments if p['invnum'] == '2020128')
    assert baker_payment['manual_method'] == 'stripe', 'FAIL: manual_method correction did not round-trip'

    # --- New full-website sections ---
    assert len(restored_drafts) == 1 and restored_drafts[0]['invnum'] == 'DRAFT-99', 'FAIL: drafts.csv wrong'
    assert len(restored_gs) == 1 and restored_gs[0]['invnum'] == 'GS20261', 'FAIL: gs_invoices.csv wrong'
    assert 'client@example.com' in restored_gs[0]['data_json'], 'FAIL: GS invoice data did not round-trip into data_json'
    assert len(restored_contracts) == 1 and restored_contracts[0]['invnum'] == '2020128', 'FAIL: contract_links.csv wrong (expected only Baker, who has signed_date)'
    assert 'signed-contracts/2020128-signed.pdf' in restored_contracts[0]['contract_link'], 'FAIL: contract link missing/wrong'
    assert len(restored_tasks) == 1 and restored_tasks[0]['title'] == 'Call Baker', 'FAIL: tasks.csv wrong'
    assert len(restored_time) == 1 and restored_time[0]['employee_name'] == 'Jasmine', 'FAIL: time_entries.csv wrong'
    assert len(restored_employees) == 1 and restored_employees[0]['name'] == 'Jasmine', 'FAIL: employees.csv wrong'

    # --- The one rule that must never break: no credential anywhere in the export ---
    assert 'password_hash' not in restored_employees[0], 'FAIL: password_hash column present in employees.csv'
    for name, content in csvs.items():
        assert b'super-secret-hash-must-never-export' not in content, f'FAIL: the employee password hash leaked into {name}'

    print('RESTORE TEST PASSED — October 2026 rebuilds to $11,616.88 from the export alone; '
          'drafts, GS Photography, tasks, time entries, employees, and signed-contract links '
          'all round-trip with the right shape; password_hash never appears anywhere.')
    print('NOTE: verified against the mock fixture only — re-run this same script against a '
          'real production export (point the FIXTUREs/_tax_list_all_rows/_fetch_table_rows at '
          'the live data instead) before trusting a real backup.')


if __name__ == '__main__':
    main()
