import json
import os

def handler(request):
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Content-Type': 'application/json',
    }
    # Serve the Stripe key from environment variable — never hardcoded
    return Response(
        json.dumps({
            'stripe_key': os.environ.get('STRIPE_SECRET_KEY', '')
        }),
        headers=headers
    )
