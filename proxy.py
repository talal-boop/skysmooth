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

# IATA airport code → ICAO airport code (for converting FR24 results)
AIRPORT_IATA_TO_ICAO = {
    # North America
    'JFK':'KJFK','LAX':'KLAX','ORD':'KORD','DFW':'KDFW','ATL':'KATL','SFO':'KSFO',
    'MIA':'KMIA','BOS':'KBOS','IAD':'KIAD','EWR':'KEWR','IAH':'KIAH','DEN':'KDEN',
    'SEA':'KSEA','LAS':'KLAS','MCO':'KMCO','CLT':'KCLT','MSP':'KMSP','DTW':'KDTW',
    'PHX':'KPHX','PHL':'KPHL','SAN':'KSAN','TPA':'KTPA','PDX':'KPDX','HNL':'PHNL',
    'YYZ':'CYYZ','YVR':'CYVR','YUL':'CYUL','YYC':'CYYC','YEG':'CYEG',
    # UK & Ireland
    'LHR':'EGLL','LGW':'EGKK','MAN':'EGCC','EDI':'EGPH','BHX':'EGBB',
    'LCY':'EGLC','STN':'EGSS','LTN':'EGGW','BRS':'EGGD','GLA':'EGPF',
    'DUB':'EIDW','SNN':'EINN','ORK':'EICK',
    # France
    'CDG':'LFPG','ORY':'LFPO','NCE':'LFMN','LYS':'LFLL','MRS':'LFML','BOD':'LFBD',
    # Germany
    'FRA':'EDDF','MUC':'EDDM','DUS':'EDDL','HAM':'EDDH','BER':'EDDB','STR':'EDDS','CGN':'EDDK',
    # Netherlands, Belgium, Switzerland
    'AMS':'EHAM','BRU':'EBBR','ZRH':'LSZH','GVA':'LSGG','BSL':'LFSB',
    # Spain & Portugal
    'MAD':'LEMD','BCN':'LEBL','PMI':'LEPA','AGP':'LEMG','VLC':'LEVC','SVQ':'LEZL',
    'LIS':'LPPT','OPO':'LPPR','FAO':'LPFR',
    # Italy
    'FCO':'LIRF','MXP':'LIMC','LIN':'LIML','BGY':'LIME','NAP':'LIRN','VCE':'LIPZ',
    'BLQ':'LIPE','CTA':'LICC','PMO':'LICJ','FLR':'LIRQ',
    # Scandinavia & Finland
    'CPH':'EKCH','ARN':'ESSA','GOT':'ESGG','OSL':'ENGM','BGO':'ENBR','TRD':'ENVA',
    'HEL':'EFHK','RVN':'EFRO',
    # Eastern Europe
    'VIE':'LOWW','PRG':'LKPR','BUD':'LHBP','WAW':'EPWA','KRK':'EPKK',
    'BEG':'LYBE','SOF':'LBSF','OTP':'LROP','SKP':'LWSK','ZAG':'LDZA',
    'LJU':'LJLJ','SJJ':'LQSA',
    # Greece & Cyprus
    'ATH':'LGAV','SKG':'LGTS','HER':'LGIR','RHO':'LGRP','LCA':'LCLK','PFO':'LCPH',
    # Turkey
    'IST':'LTFM','SAW':'LTBS','ADB':'LTBJ','ESB':'LTAC','AYT':'LTAI','ADA':'LTAF',
    # Russia
    'SVO':'UUEE','DME':'UUDD','VKO':'UUWW','LED':'ULLI','SVX':'USSS','OVB':'UNNT',
    # Middle East
    'DXB':'OMDB','DWC':'OMDW','AUH':'OMAA','SHJ':'OMSJ',
    'DOH':'OTHH',
    'RUH':'OERK','JED':'OEJN','DMM':'OEDF','MED':'OEMA','ABH':'OEBA',
    'BAH':'OBBI',
    'KWI':'OKBK',
    'BEY':'OLBA',
    'AMM':'OJAI','AQJ':'OJAQ',
    'MCT':'OOMS','SLL':'OOSA',
    'TLV':'LLBG',
    'CAI':'HECA','HRG':'HEGN','SSH':'HESH','LXR':'HELX',
    'ADD':'HAAB',
    'BGW':'ORBI','BSR':'ORMM','EBL':'ORER',
    # Africa
    'JNB':'FAOR','CPT':'FACT','DUR':'FALE','PLZ':'FAPE',
    'NBO':'HKJK','MBA':'HKMO',
    'DAR':'HTDA','ZNZ':'HTZA',
    'MRU':'FIMP',
    'TNR':'FMMI',
    'CMN':'GMMN','RAK':'GMMX','TNG':'GMTT','AGA':'GMAD',
    'ALG':'DAAG','TUN':'DTTA','SFA':'DTTX',
    'LOS':'DNMM','ABV':'DNAA','KAN':'DNKN',
    'ACC':'DGAA',
    'DKR':'GOBD',
    'ROB':'GLRB',
    'BJL':'GBYD',
    # Asia — China
    'PEK':'ZBAA','PKX':'ZBAD','PVG':'ZSPD','SHA':'ZSSS',
    'CAN':'ZGGG','SZX':'ZGSZ','CTU':'ZUUU','CKG':'ZUCK',
    'HGH':'ZSHC','WUH':'ZHHH','XIY':'ZLXY','CSX':'ZGHA',
    'KMG':'ZPPP','TSN':'ZBYN','DLC':'ZYTL','TNA':'ZSJN',
    'CGO':'ZHCC','NKG':'ZSNJ','HFE':'ZSOF','FOC':'ZSFZ',
    'XMN':'ZSAM','HAK':'ZJHK','SYX':'ZJSY','URC':'ZWWW',
    # Asia — Korea & Japan
    'ICN':'RKSI','GMP':'RKSS','PUS':'RKPK','CJU':'RKPC',
    'NRT':'RJAA','HND':'RJTT','KIX':'RJBB','NGO':'RJGG',
    'CTS':'RJCC','FUK':'RJFF','OKA':'ROAH',
    # Asia — Southeast
    'SIN':'WSSS',
    'KUL':'WMKK','PEN':'WMKP','LGK':'WMKL',
    'BKK':'VTBS','DMK':'VTBD','HKT':'VTSP','CNX':'VTCC','USM':'VTSM',
    'MNL':'RPLL','CEB':'RPVM','DVO':'RPMD',
    'CGK':'WIII','DPS':'WADD','SUB':'WARR','JOG':'WARJ','UPG':'WAAA',
    'SGN':'VVTS','HAN':'VVNB','DAD':'VVDN',
    'RGN':'VYYY',
    'PNH':'VDPP','REP':'VDSR',
    'VTE':'VLVT',
    # Asia — South
    'BOM':'VABB','DEL':'VIDP','MAA':'VOMM','BLR':'VOBL','HYD':'VOHS',
    'CCU':'VECC','COK':'VOCI','GOI':'VOGO','AMD':'VAAH','PNQ':'VAPO',
    'TRV':'VOTV',
    'CMB':'VCBI','MLE':'VRMM',
    'DAC':'VGZR','CGP':'VGEG',
    'KTM':'VNKT',
    'KHI':'OPKC','ISB':'OPIS','LHE':'OPLA','PEW':'OPPS','SKT':'OPST',
    'KBL':'OAKB',
    # Asia — Central
    'TAS':'UTTT','ALA':'UAAA','NQZ':'UACC',
    'TBS':'UGTB','EVN':'UDYZ','GYD':'UBBB',
    # Oceania
    'SYD':'YSSY','MEL':'YMML','BNE':'YBBN','PER':'YPPH','ADL':'YPAD',
    'CBR':'YSCB','OOL':'YBCG','HBA':'YMHB','DRW':'YPDN','CNS':'YBCS',
    'AKL':'NZAA','CHC':'NZCH','WLG':'NZWN','ZQN':'NZQN',
    'NAN':'NFFN','APW':'NSFA',
    # South America
    'GRU':'SBGR','GIG':'SBGL','BSB':'SBBR','FOR':'SBFZ','REC':'SBRF',
    'SSA':'SBSV','POA':'SBPA','CWB':'SBCT','BEL':'SBBE','MAO':'SBEG',
    'EZE':'SAEZ','AEP':'SABE','COR':'SAAC','MDZ':'SAME','TUC':'SANT',
    'SCL':'SCEL','IPC':'SCIP',
    'BOG':'SKBO','MDE':'SKRG','CLO':'SKCL','CTG':'SKCG',
    'LIM':'SPJC',
    'GYE':'SEGU','UIO':'SEQM',
    'CCS':'SVMI',
    'PTY':'MPTO',
    'MGA':'MNMG',
    'SJO':'MROC',
    'GUA':'MGGT',
    'HAV':'MUHA',
    'SDQ':'MDSD','PUJ':'MDPC',
    'MBJ':'MKJS','KIN':'MKJP',
    'CUN':'MMUN','MEX':'MMMX','GDL':'MMGL','MTY':'MMMY','TIJ':'MMTJ',
}

# Try importing FlightRadar24 (installed via requirements.txt on Render)
try:
    from FlightRadar24 import FlightRadar24API as _FR24API
    _fr24 = _FR24API()
    _FR24_AVAILABLE = True
    print('[flight] FlightRadar24 API loaded', file=sys.stderr, flush=True)
except Exception as _e:
    _FR24_AVAILABLE = False
    print(f'[flight] FlightRadar24 not available: {_e}', file=sys.stderr, flush=True)


def _lookup_flight_fr24(flight_num):
    """
    Look up a flight route via FlightRadar24 live data.
    Returns (dep_icao, dest_icao) ICAO codes, or raises ValueError.
    Only works for flights that are currently airborne or recently flew.
    """
    if not _FR24_AVAILABLE:
        raise ValueError('FR24 not available')

    result = _fr24.search(flight_num)
    live = result.get('live', [])

    for item in live:
        d = item.get('detail', {})
        dep_iata  = d.get('schd_from') or d.get('orig_iata')
        dest_iata = d.get('schd_to')   or d.get('dest_iata')
        if dep_iata and dest_iata:
            dep_icao  = AIRPORT_IATA_TO_ICAO.get(dep_iata.upper())
            dest_icao = AIRPORT_IATA_TO_ICAO.get(dest_iata.upper())
            if dep_icao and dest_icao:
                print(f'[flight] FR24 live: {dep_iata}->{dest_iata} ({dep_icao}->{dest_icao})', file=sys.stderr, flush=True)
                return dep_icao, dest_icao

    raise ValueError('not found in FR24 live data')


def _lookup_flight(raw_num):
    """
    Look up a flight route. Tries FR24 live data first, then OpenSky.
    Returns (dep_icao, dest_icao) or raises ValueError.
    """
    s = raw_num.upper().replace(' ', '').replace('-', '')
    m = _re.match(r'^([A-Z]{2,3})0*(\d{1,4})$', s)
    if not m:
        raise ValueError('invalid flight number format')

    # ── Step 1: FlightRadar24 live lookup ───────────────────────────────
    try:
        return _lookup_flight_fr24(s)
    except Exception as e:
        print(f'[flight] FR24 miss ({e}), trying OpenSky', file=sys.stderr, flush=True)

    # ── Step 2: OpenSky route database ─────────────────────────────────
    iata, num = m.group(1), int(m.group(2))
    icao = AIRLINE_ICAO.get(iata, iata)

    candidates = [
        f'{icao}{num}',
        f'{icao}{str(num).zfill(4)}',
        f'{iata}{num}',
        f'{iata}{str(num).zfill(4)}',
    ]
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
            print(f'[flight] OpenSky callsign={cs} status={resp.status}', file=sys.stderr, flush=True)
            if resp.status == 200:
                data = _json.loads(body)
                route = data.get('route', [])
                if len(route) >= 2:
                    return route[0], route[-1]
        except Exception as e:
            print(f'[flight] OpenSky {cs} error={e}', file=sys.stderr, flush=True)
            continue

    raise ValueError('flight not found')


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
