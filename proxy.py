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
        elif parsed.path in ('/', '/health'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self._cors()
            self.end_headers()
            self.wfile.write(b'SkySmooth proxy OK')
        else:
            self.send_error(404)

    def _proxy(self, raw_query):
        url = ''
        for part in raw_query.split('&'):
            if part.lower().startswith('url='):
                url = urllib.parse.unquote_plus(part[4:])
                break

        print(f'[proxy] target -> {url}', file=sys.stderr, flush=True)

        ALLOWED = ('https://aviationweather.gov/',)
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

            print(f'[proxy] status -> {resp.status}', file=sys.stderr, flush=True)

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
