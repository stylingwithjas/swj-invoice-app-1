from lib.owner_guard import require_owner
import json
import urllib.error
from http.server import BaseHTTPRequestHandler
from lib.owner_guard import require_owner
from lib.admin_http import response
from lib.invoice_numbers import reserve


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        response(self,405,{'error':'Use the signed-in website.'})

    def do_GET(self):
        if not require_owner(self,write=False):return
        response(self,405,{'error':'Use POST to reserve an invoice number.'})

    def do_POST(self):
        if not require_owner(self):return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=2048:raise ValueError('Invalid reservation request.')
            body=json.loads(self.rfile.read(length))
            if body.get('action')!='reserve':
                response(self,409,{'error':'Reload the invoice editor. Legacy counter writes are disabled.'});return
            result=reserve(body.get('request_id'),body.get('requested_invnum'))
            response(self,200,result)
        except urllib.error.HTTPError as error:
            if error.code==400:
                response(self,400,{'error':'Use a numeric invoice number with an optional vN revision.'})
            elif error.code==409:
                response(self,409,{'error':'Invoice number is already reserved or issued. Clear it to reserve the next number.'})
            else:
                response(self,503,{'error':'Number reservation is unavailable. Verify setup or retry; no fallback number was assigned.'})
        except (ValueError,TypeError,AttributeError):
            response(self,400,{'error':'Invalid invoice number reservation request.'})
        except Exception:
            response(self,503,{'error':'Number reservation is unavailable. Retry before creating this invoice.'})

    def log_message(self,*args):pass
