# ── ADD THIS BLOCK to the top of do_POST in your existing api/stripe.py ──
# Insert it right after you parse the body JSON, before the existing payment link creation code

# Handle deactivate action
if body.get('action') == 'deactivate':
    payment_link_id = body.get('payment_link_id', '')
    if not payment_link_id:
        self._respond(400, {'error': 'No payment_link_id provided'})
        return
    try:
        import urllib.request, urllib.parse
        stripe_key = os.environ.get('STRIPE_SECRET_KEY', '')
        patch_data = urllib.parse.urlencode({'active': 'false'}).encode()
        req = urllib.request.Request(
            f'https://api.stripe.com/v1/payment_links/{payment_link_id}',
            data=patch_data,
            method='POST'
        )
        req.add_header('Authorization', f'Bearer {stripe_key}')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
        self._respond(200, {'deactivated': True, 'id': payment_link_id, 'active': result.get('active', False)})
    except Exception as e:
        # Non-fatal — log and return success anyway so invoice save isn't blocked
        print(f'Deactivate payment link error: {e}')
        self._respond(200, {'deactivated': False, 'error': str(e)})
    return

# ── END OF PATCH — existing stripe.py code continues below ──
