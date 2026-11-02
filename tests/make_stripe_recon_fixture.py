"""Synthetic UI evidence only; never connects to Stripe or a database."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from lib import stripe_recon as recon

BASE = {'base_price': 3933, 'taxrate': 10.55, 'addons': [{'desc': 'Logistics', 'price': 185, 'type': 'rental'}]}


def row(invnum, client, data):
    return {'invnum': invnum, 'client': client, 'invdate': '2026-10-01', 'created_at': '2026-10-01T00:00:00Z', 'data': data}


def charge(id, dollars, created, metadata_invoice='', name='', refunded=0, livemode=True):
    import datetime as dt
    ts = int(dt.datetime.fromisoformat(created + 'T12:00:00+00:00').timestamp())
    return {'id': id, 'amount': round(dollars * 100), 'amount_refunded': round(refunded * 100), 'created': ts,
            'livemode': livemode, 'metadata': {'invoice': metadata_invoice} if metadata_invoice else {},
            'billing_details': {'name': name}, 'status': 'succeeded', 'payment_intent': 'pi_' + id}


rows = [
    row('TEST-MULLENIX', 'Synthetic Mullenix Example', {**BASE, 'paid': True, 'paid_date': '2026-10-01', 'stripe_amount': 3527.28}),
    row('TEST-BAKER', 'Synthetic Baker Example', {**BASE, 'paid': True, 'paid_date': '2026-10-15', 'stripe_amount': 3978.00}),
    row('TEST-HILL', 'Synthetic Hill Example', {**BASE, 'extensions': [
        {'invnum': 'TEST-HILL-EXT1', 'amount': 1280.54, 'paid': True, 'paid_date': None},
        {'invnum': 'TEST-HILL-EXT2', 'amount': 1280.54, 'paid': True, 'paid_date': '2026-07-21'},
    ]}),
    row('TEST-REFUND', 'Synthetic Refund Example', {**BASE, 'paid': True, 'paid_date': '2026-10-10', 'stripe_amount': 500}),
    row('TEST-NOAMOUNT', 'Synthetic No-Amount Example', {**BASE, 'paid': True, 'paid_date': '2026-10-20'}),
    row('TEST-MISMATCH', 'Synthetic Mismatch Example', {**BASE, 'paid': True, 'paid_date': '2026-10-05', 'stripe_amount': 3500.00}),
]

charges = [
    charge('ch_mullenix', 3527.28, '2026-10-01', metadata_invoice='TEST-MULLENIX', name='Synthetic Mullenix Example'),
    charge('ch_baker', 3978.00, '2026-10-15', metadata_invoice='', name='Synthetic Baker Example'),
    charge('ch_hill_ext2', 1280.54, '2026-07-21', metadata_invoice='TEST-HILL-EXT2', name='Synthetic Hill Example'),
    charge('ch_hill_amb', 1280.54, '2026-10-01', metadata_invoice='', name='Synthetic Hill Example'),
    charge('ch_deji', 1024.50, '2026-10-01', metadata_invoice='', name='Synthetic Deji Example'),
    charge('ch_refund', 500, '2026-10-10', metadata_invoice='TEST-REFUND', name='Synthetic Refund Example', refunded=500),
    charge('ch_mismatch', 3600.00, '2026-10-05', metadata_invoice='TEST-MISMATCH', name='Synthetic Mismatch Example'),
]

report = recon.build_report(rows, charges, '2026-10')
report['data_source'] = 'synthetic_test'
report['coverage'] = {'complete': True, 'verification': 'synthetic fixture — no Stripe or database access',
                       'completed_at': '2026-11-02T17:00:01Z', 'snapshot_note': 'Test fixture only. These are not live business records.'}
report['exports'] = {view: recon.csv_report(report, view) for view in ('all', 'matched', 'unmatched_charges', 'unmatched_items', 'mismatches')}
folder = ROOT / 'tests/fixtures'; folder.mkdir(exist_ok=True)
(folder / 'stripe-recon-report.json').write_text(json.dumps(report, indent=2) + '\n')
print('Wrote synthetic Stripe recon browser fixture: ' + str(report['matched_count']) + ' matched, ' +
      str(report['unmatched_charge_count']) + ' unmatched charges, ' + str(report['unmatched_item_count']) +
      ' unmatched items, ' + str(report['mismatch_count']) + ' mismatches.')
