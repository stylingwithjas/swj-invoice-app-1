import json
import os

# Counter file stored in /tmp (persists during Vercel function warm state)
# For true persistence we use a simple file in the repo or env variable
COUNTER_FILE = '/tmp/swj_counter.txt'
START = 202092

def get_next():
    try:
        if os.path.exists(COUNTER_FILE):
            with open(COUNTER_FILE) as f:
                val = int(f.read().strip())
            next_val = max(val + 1, START + 1)
        else:
            next_val = START + 1
        with open(COUNTER_FILE, 'w') as f:
            f.write(str(next_val))
        return next_val
    except:
        return START + 1

def handler(request):
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Content-Type': 'application/json',
    }

    if request.method == 'OPTIONS':
        return Response('', headers=headers)

    if request.method == 'POST':
        # Save a specific counter value
        try:
            data = json.loads(request.body)
            val = int(data.get('value', START + 1))
            with open(COUNTER_FILE, 'w') as f:
                f.write(str(val))
            return Response(json.dumps({'value': val}), headers=headers)
        except Exception as e:
            return Response(json.dumps({'error': str(e)}), status=400, headers=headers)

    # GET — return next invoice number
    next_val = get_next()
    return Response(json.dumps({'value': next_val}), headers=headers)
