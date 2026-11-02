"""Read-only Section 7 audit using the app's existing server database environment.

No new DB credential, migration, deploy, Stripe request or client communication.
Never pass credentials on the command line or paste them into a chat.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from api.webhook import SUPABASE_URL
from lib.section7 import build_report, csv_report
from lib.section7_http import scan, IncompleteScan


def main(argv=None, scanner=None):
    parser=argparse.ArgumentParser(description='Read-only historical invoice reconciliation. Uses existing SUPABASE_KEY from the server environment.')
    parser.add_argument('--review-period',default='2026-10',help='YYYY-MM; highlights receipt dates without narrowing the historical scan.')
    parser.add_argument('--output-dir',type=Path,default=Path(tempfile.gettempdir())/'swj-section7-output')
    args=parser.parse_args(argv)
    try:
        # Validate before attempting any data read.
        build_report([],args.review_period)
        key=os.environ.get('SUPABASE_KEY','')
        if not key:
            print('Existing server database configuration is unavailable. No database read was performed.',file=sys.stderr)
            return 2
        rows,coverage=(scan if scanner is None else scanner)(SUPABASE_URL,key)
        report=build_report(rows,args.review_period)
        report['coverage']=coverage;report['fingerprint']=coverage['fingerprint']
        if not coverage.get('complete'):
            raise IncompleteScan('Database coverage is incomplete; no export was created.')
        args.output_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
        stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        prefix='SWJ_Section7_'+args.review_period+'_'+stamp+'_'+report['fingerprint'][:8]
        files=[(prefix+'.json',json.dumps(report,indent=2,ensure_ascii=True,allow_nan=False)+'\n'),
               (prefix+'_all.csv',csv_report(report)),
               (prefix+'_paid_period.csv',csv_report(report,'paid_period'))]
        written=[]
        try:
            for filename,content in files:
                target=args.output_dir/filename
                fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w',encoding='utf-8',newline='')as stream:stream.write(content)
                written.append(target)
        except Exception:
            for target in written:
                target.unlink(missing_ok=True)
            raise
        print('Historical scan complete: '+str(report['invoices_checked'])+' invoices checked; '+str(report['discrepancy_count'])+' with arithmetic discrepancies; '+str(report['unverifiable_count'])+' need information.')
        print('Recorded-paid discrepancies in '+args.review_period+': '+str(report['paid_period_discrepancy_count'])+'.')
        print('No source records were changed. Outputs:')
        for target in written:print(str(target.resolve()))
        return 0
    except (IncompleteScan,ValueError)as error:
        print(str(error),file=sys.stderr);return 2
    except Exception:
        print('Historical audit unavailable. No complete export was created. Check the existing server configuration or retry.',file=sys.stderr);return 2


if __name__=='__main__':
    raise SystemExit(main())
