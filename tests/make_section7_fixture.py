"""Synthetic UI evidence only; never connects to a database."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from lib.section7 import build_report,csv_report
from lib.section7_http import fingerprint
spec=importlib.util.spec_from_file_location('fixtures',ROOT/'tests/test_section7.py');fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
CLEAN=fixtures.CLEAN;invoice=fixtures.invoice
rows=[invoice(invnum='TEST-CLEAN'),
 invoice({'base_price':3933,'taxrate':10.55,'addons':[{'price':185,'type':'rental'}], 'price_led_tax':True,'rental_share_base':.5,'extrate':1517.48,'paid':True,'paid_date':'2026-10-15'},invnum='TEST-LEGACY',client='Synthetic legacy example'),
 invoice({**CLEAN,'total_override':4354.98,'payments':[{'status':'paid','paid_date':'2026-10-20','amount':100}]},invnum='TEST-OVERRIDE',client='Synthetic manual-total example'),
 invoice({**CLEAN,'base_price':None,'extrate':None,'paid':True},invnum='TEST-MISSING',client='Synthetic incomplete record'),
 invoice({**CLEAN,'tax_override':227.98,'total_override':4345.98},invnum='TEST-TAX',client='Synthetic tax-only example'),
 invoice({**CLEAN,'total_override':10,'payments':[{'status':'paid','paid_date':'2026-10-15','is_test':True}]},invnum='TEST-TEST',client='Synthetic test payment'),
 invoice({},invnum='FINANCE_CONFIG')]
report=build_report(copy.deepcopy(rows),'2026-10');report['data_source']='synthetic_test';report['fingerprint']=fingerprint(rows)
report['coverage']={'complete':True,'verification':'synthetic fixture — no database access','started_at':'2026-11-02T17:00:00Z','completed_at':'2026-11-02T17:00:01Z','database_rows':len(rows),'fingerprint':report['fingerprint'],'snapshot_note':'Test fixture only. These are not live business records.'}
report['exports']={view:csv_report(report,view)for view in ('all','paid','paid_period','unverifiable')}
folder=ROOT/'tests/fixtures';folder.mkdir(exist_ok=True)
(folder/'section7-report.json').write_text(json.dumps(report,indent=2)+'\n')
print('Wrote synthetic Section 7 browser fixture: '+str(report['flagged_count'])+' flagged invoices.')
