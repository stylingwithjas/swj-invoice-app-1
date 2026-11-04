"""Cross-runtime Option A contract: every UI and the server cash ledger agree."""
import copy
import json
from pathlib import Path
import subprocess
import unittest
from lib.cash_ledger import receipt_events
from lib import payment_events

ROOT=Path(__file__).resolve().parents[1]

class PaymentDriverTests(unittest.TestCase):
    def test_browser_and_server_classify_same_cash_receipts(self):
        base={'base_price':1000,'taxrate':0,'addons':[],'total_override':1000,
              'paid':True,'paid_date':'2026-11-02','amount_paid':1000,'signed':False}
        scenarios=[{}, {'amount_paid':'','stripe_amount':900},
                   {'paid':False,'payments':[{'id':'PAY1','status':'paid','paid_date':'2026-11-03','amount':300},
                                             {'id':'PAY2','status':'pending','amount':700}]},
                   {'paid':True,'payments':[{'id':'PAY1','status':'pending','amount':1000}]},
                   {'is_test':True,'payments':[{'id':'PAY1','status':'paid','paid_date':'2026-11-03','amount':300}]},
                   {'payments':[{'id':'PAY1','status':'paid','paid_date':'2026-11-03','amount':300,'is_test':True}]},
                   {'paid_date':'2026-02-30'}, {'paid_date':'2026-11-02T10:00:00Z'},
                   {'paid':False,'extensions':[{'invnum':'2020101-EXT1','paid':True,'paid_date':'2026-11-03','amount':75}]},
                   {'paid':False,'extensions':[{'invnum':'2020101-EXT1','paid':True,'paid_date':'2026-11-03','amount':75,'is_test':True}]},
                   {'amount_paid':None,'stripe_amount':None}, {'amount_paid':0}]
        rows=[{'invnum':'2020101','client':'Synthetic','data':{**copy.deepcopy(base),**patch}} for patch in scenarios]
        rows += [{'invnum':key,'data':base} for key in ['DRAFT-2020101','GS123','FINANCE_CONFIG']]
        script="""require('./assets/payment-events.js');
let input='';process.stdin.on('data',c=>input+=c);process.stdin.on('end',()=>{
 console.log(JSON.stringify(JSON.parse(input).map(row=>SWJPayments.all(row,d=>d.total_override).map(e=>({date:e.date,amount_cents:Math.round(e.amount*100)})))));
});"""
        result=subprocess.run(['node','-e',script],cwd=ROOT,input=json.dumps(rows),text=True,capture_output=True,check=True)
        browser=json.loads(result.stdout)
        for row,events in zip(rows,browser):
            server,_=receipt_events([row])
            self.assertEqual(events,[{'date':e['date'],'amount_cents':e['amount_cents']} for e in server],row)
            if not row['invnum'].startswith(('DRAFT-','GS')) and row['invnum']!='FINANCE_CONFIG':
                reporting=payment_events.base(row['data'],lambda d:d['total_override'])+payment_events.extensions(row['data'])
                self.assertEqual(events,[{'date':e['date'],'amount_cents':round(e['amount']*100)} for e in reporting],row)

if __name__=='__main__':unittest.main()
