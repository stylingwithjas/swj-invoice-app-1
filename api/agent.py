import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler

ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'

# SWJ Staging economics
FIXED_COST_PER_JOB = 600   # truck + mover
TARGET_MARGIN = 0.60        # 60% profit margin target
# Max allowed costs = (1 - TARGET_MARGIN) = 40% of revenue
# Shopping budget = (40% × revenue) − FIXED_COST_PER_JOB

# Staging sweet-spot prices by category (used as fallback if no job data)
CATEGORY_CEILINGS = {
    'accent chair': 150,
    'chair': 120,
    'sofa': 300,
    'couch': 300,
    'lamp': 60,
    'floor lamp': 75,
    'mirror': 100,
    'artwork': 40,
    'art': 40,
    'pillow': 25,
    'throw': 35,
    'blanket': 35,
    'rug': 150,
    'side table': 120,
    'coffee table': 150,
    'dining chair': 60,
    'nightstand': 80,
    'dresser': 200,
    'bookshelf': 100,
    'plant': 30,
    'vase': 25,
    'decor': 35,
}

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
        except Exception as e:
            self._respond(400, {'error': f'Invalid request: {e}'})
            return

        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            self._respond(500, {'error': 'ANTHROPIC_API_KEY not configured'})
            return

        prompt = body.get('prompt', '')
        use_web_search = body.get('web_search', False)
        jobs_data = body.get('jobs', [])  # optional job financial data
        system_override = body.get('system')  # optional: replace staging system prompt
        try:
            max_tokens = int(body.get('max_tokens', 2000))
        except (TypeError, ValueError):
            max_tokens = 2000
        max_tokens = max(256, min(max_tokens, 4096))

        if not prompt:
            self._respond(400, {'error': 'No prompt provided'})
            return

        # Build staging economics context if jobs provided
        economics_context = ''
        if jobs_data:
            job_budgets = []
            for job in jobs_data:
                revenue = float(job.get('grand_total', 0))
                item_count = int(job.get('item_count', 1))
                if revenue > 0:
                    allowed_costs = revenue * (1 - TARGET_MARGIN)
                    shopping_budget = max(0, allowed_costs - FIXED_COST_PER_JOB)
                    per_item = round(shopping_budget / max(item_count, 1))
                    job_budgets.append(
                        f"- {job.get('client','Job')}: ${revenue:,.0f} revenue → "
                        f"${shopping_budget:,.0f} total shopping budget → "
                        f"~${per_item} per item ceiling"
                    )
            if job_budgets:
                economics_context = (
                    f"\n\nSWJ STAGING ECONOMICS (60% margin target, ${FIXED_COST_PER_JOB} fixed costs):\n"
                    + '\n'.join(job_budgets)
                    + "\n\nItems over per-item ceiling go in 'Worth Considering' section. "
                    + "Items within budget go in 'Best Deals' section."
                )

        # Inject staging criteria and economics into every prompt
        staging_system = (
            "You are a professional home staging purchasing assistant for Styling With Jas (SWJ), "
            "a premium staging company in the Seattle/Tacoma area.\n\n"
            "STAGING SHOPPING PHILOSOPHY:\n"
            "- Goal: maximum visual impact at minimum cost. Items must LOOK expensive, not BE expensive.\n"
            "- Target stores: HomeGoods, TJ Maxx, World Market, IKEA, Amazon, Wayfair, Target.\n"
            "- Prefer neutral tones (greige, white, cream, warm gray, natural wood) — photographs well in any home.\n"
            "- Accept any color IF Claude judges it staging-appropriate (muted, earthy, timeless).\n"
            "- Avoid: bold patterns, bright/neon colors, trendy statement pieces, actual luxury brands.\n"
            "- For used items (Craigslist): 'good' or 'like new' condition only.\n"
            "- Clean modern lines > ornate. Simple > complex. Timeless > trendy.\n"
            "- Two sections in results:\n"
            "  1. BEST DEALS — within per-job shopping budget, staging-appropriate\n"
            "  2. WORTH CONSIDERING — slightly over budget but exceptional visual value\n"
            f"{economics_context}"
        )

        # A custom system context (e.g. the free-form sourcing agent) overrides the
        # staging-decor framing, which would otherwise bias utility-item searches.
        if system_override:
            full_prompt = str(system_override) + "\n\n---\n\n" + prompt
        else:
            full_prompt = staging_system + "\n\n---\n\n" + prompt

        try:
            payload_dict = {
                'model': 'claude-sonnet-4-20250514',
                'max_tokens': max_tokens,
                'messages': [{'role': 'user', 'content': full_prompt}]
            }

            # Enable web search tool if requested
            if use_web_search:
                payload_dict['tools'] = [
                    {'type': 'web_search_20250305', 'name': 'web_search'}
                ]

            payload = json.dumps(payload_dict).encode()

            req = urllib.request.Request(
                ANTHROPIC_API_URL,
                data=payload,
                method='POST'
            )
            req.add_header('Content-Type', 'application/json')
            req.add_header('x-api-key', api_key)
            req.add_header('anthropic-version', '2023-06-01')

            with urllib.request.urlopen(req, timeout=45) as r:
                result = json.loads(r.read())

            # Extract all text blocks (web search may produce multiple)
            text = ''
            for block in result.get('content', []):
                if block.get('type') == 'text':
                    text += block.get('text', '')

            self._respond(200, {'text': text})

        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            self._respond(500, {'error': f'Anthropic API error {e.code}: {error_body}'})
        except Exception as e:
            self._respond(500, {'error': f'Agent failed: {str(e)}'})

    def _respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, format, *args):
        pass
