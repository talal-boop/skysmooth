#!/usr/bin/env python3
"""
SkySmooth cloud proxy — runs on Render.com (or any host).
Proxies requests to aviationweather.gov to avoid CORS issues.
"""
import http.server
import http.client
import socketserver
import urllib.parse
import ssl
import os
import sys

PORT = int(os.environ.get('PORT', 8090))

ALLOWED_ORIGIN = os.environ.get('ALLOWED_ORIGIN', '*')

def make_ssl_ctx():
    ctx = ssl.create_default_context()
    for path in [
        '/etc/ssl/cert.pem',
        '/usr/local/etc/openssl/cert.pem',
        '/opt/homebrew/etc/openssl@3/cert.pem',
    ]:
        if os.path.exists(path):
            return ssl.create_default_context(cafile=path)
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

SSL_CTX = make_ssl_ctx()

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/605.1.15 (KHTML, like Gecko) '
        'Version/17.0 Safari/605.1.15'
    ),
    'Accept': 'application/json, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Connection': 'close',
}


# IATA → ICAO airline callsign prefix mapping (for flight number lookup)
AIRLINE_ICAO = {
    'EK':'UAE','QR':'QTR','BA':'BAW','AA':'AAL','UA':'UAL','DL':'DAL',
    'LH':'DLH','AF':'AFR','KL':'KLM','TK':'THY','EY':'ETD','SQ':'SIA',
    'CX':'CPA','NH':'ANA','JL':'JAL','KE':'KAL','IB':'IBE','LX':'SWR',
    'OS':'AUA','SK':'SAS','AY':'FIN','TP':'TAP','EI':'EIN','VS':'VIR',
    'AC':'ACA','QF':'QFA','NZ':'ANZ','MH':'MAS','TG':'THA','CA':'CCA',
    'MU':'CES','CZ':'CSN','AI':'AIC','ET':'ETH','MS':'MSR','WY':'OMA',
    'GF':'GFA','FZ':'FDB','WS':'WJA','PR':'PAL','GA':'GIA','CI':'CAL',
    'BR':'EVA','HU':'CHH','SA':'SAA','KQ':'KQA','RJ':'RJA','ME':'MEA',
    'TL':'ANO','VN':'HVN','OZ':'AAR','FM':'CSH','ZH':'CSZ',
}

import re as _re
import json as _json

def _lookup_flight(raw_num):
    """
    Query OpenSky for a flight number like 'EK001' or 'QR1'.
    Returns (dep_icao, dest_icao) or raises ValueError if not found.
    Uses an 18-second timeout — longer than the 5s client-side timeout.
    """
    s = raw_num.upper().replace(' ', '').replace('-', '')
    m = _re.match(r'^([A-Z]{2,3})0*(\d{1,4})$', s)
    if not m:
        raise ValueError('invalid flight number format')

    iata, num = m.group(1), int(m.group(2))
    icao = AIRLINE_ICAO.get(iata, iata)

    candidates = [
        f'{icao}{num}',
        f'{icao}{str(num).zfill(4)}',
        f'{iata}{num}',
        f'{iata}{str(num).zfill(4)}',
    ]
    # Deduplicate while preserving order
    seen = set()
    candidates = [c for c in candidates if not (c in seen or seen.add(c))]

    for cs in candidates:
        try:
            url = f'/api/routes?callsign={cs}'
            conn = http.client.HTTPSConnection('opensky-network.org', timeout=18, context=SSL_CTX)
            conn.request('GET', url, headers={**HEADERS, 'Host': 'opensky-network.org'})
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
            print(f'[flight] callsign={cs} status={resp.status}', file=sys.stderr, flush=True)
            if resp.status == 200:
                data = _json.loads(body)
                route = data.get('route', [])
                if len(route) >= 2:
                    return route[0], route[-1]
        except Exception as e:
            print(f'[flight] callsign={cs} error={e}', file=sys.stderr, flush=True)
            continue

    raise ValueError('flight not found in OpenSky')


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f'[proxy] {fmt % args}', file=sys.stderr, flush=True)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', ALLOWED_ORIGIN)
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/proxy':
            self._proxy(parsed.query)
        elif parsed.path == '/flight':
            self._flight(parsed.query)
        elif parsed.path in ('/', '/health'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self._cors()
            self.end_headers()
            self.wfile.write(b'SkySmooth proxy OK')
        else:
            self.send_error(404)

    def _flight(self, raw_query):
        params = urllib.parse.parse_qs(raw_query)
        num = params.get('num', [None])[0]
        if not num:
            self.send_error(400, 'Missing num parameter')
            return
        try:
            dep, dest = _lookup_flight(num)
            payload = _json.dumps({'dep': dep, 'dest': dest}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self._cors()
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(payload)
        except ValueError as e:
            self.send_error(404, str(e))
        except Exception as e:
            print(f'[flight] ERROR -> {e}', file=sys.stderr, flush=True)
            self.send_error(502, str(e))

    def _proxy(self, raw_query):
        url = ''
        for part in raw_query.split('&'):
            if part.lower().startswith('url='):
                url = urllib.parse.unquote_plus(part[4:])
                break

        print(f'[proxy] target -> {url}', file=sys.stderr, flush=True)

        ALLOWED = ('https://aviationweather.gov/', 'https://opensky-network.org/')
        if not any(url.startswith(h) for h in ALLOWED):
            self.send_error(403, 'URL not allowed')
            return

        try:
            target = urllib.parse.urlparse(url)
            req_path = target.path
            if target.query:
                req_path = req_path + '?' + target.query

            conn = http.client.HTTPSConnection(
                target.netloc, timeout=22, context=SSL_CTX
            )
            conn.request('GET', req_path, headers={**HEADERS, 'Host': target.netloc})
            resp = conn.getresponse()
            body = resp.read()
            conn.close()

            print(f'[proxy] NOAA status -> {resp.status}', file=sys.stderr, flush=True)

            self.send_response(resp.status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self._cors()
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(body)

        except Exception as e:
            print(f'[proxy] ERROR -> {e}', file=sys.stderr, flush=True)
            self.send_error(502, str(e))


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == '__main__':
    server = ThreadedServer(('0.0.0.0', PORT), Handler)
    print(f'SkySmooth proxy running on port {PORT}', flush=True)
    server.serve_forever()
