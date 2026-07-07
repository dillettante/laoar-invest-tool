#!/usr/bin/env python3
"""라오어 투자 도구 — 무상태(stateless) 서버 · 배포용 · 무의존(Python stdlib only).

사용자 금융 데이터는 서버에 저장하지 않습니다. 계좌·원장은 브라우저 localStorage에만 있습니다.
서버는 (1) 시세/환율 조회, (2) 전략 계산만 합니다.

실행:  python3 server.py [port] [host]      (기본: 8770 0.0.0.0)
        예) python3 server.py               → http://localhost:8770 (LAN 노출)
            python3 server.py 8000 127.0.0.1 → 로컬 전용

엔드포인트:
  GET  /                         index.html
  GET  /api/price?ticker=TQQQ    최신 종가 (Yahoo)
  GET  /api/fx                   USD/KRW 환율 (Yahoo)
  POST /api/vr_plan  {V,qty,avg,pool,close,band,acct_type}
  POST /api/im       {seed,divisions,qty,avg,cum_buy,close,take_profit,big_a,big_b}
"""
import sys, os, json, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vr_core import vr_plan
from im_core import IMParams, plan as im_plan

HERE = os.path.dirname(os.path.abspath(__file__))

def _send(h, obj, code=200):
    b = json.dumps(obj, ensure_ascii=False).encode()
    h.send_response(code)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(b)))
    h.end_headers(); h.wfile.write(b)

_CACHE = {}          # sym → (ts, result). 레이트리밋 방지용 3분 캐시.
_CACHE_TTL = 180

def yahoo_last(sym):
    """티커 최신 종가 (date_iso, close). 무인증 Yahoo 차트 API. 3분 캐시."""
    now = time.time(); hit = _CACHE.get(sym)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    j = json.load(urllib.request.urlopen(req, timeout=20))
    res = j["chart"]["result"][0]
    ts, cl = res["timestamp"], res["indicators"]["quote"][0]["close"]
    out = [(t, c) for t, c in zip(ts, cl) if c is not None]
    result = None
    if out:
        t, c = out[-1]
        result = (datetime.fromtimestamp(t, timezone.utc).date().isoformat(), round(float(c), 2))
    _CACHE[sym] = (now, result)
    return result

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    b = f.read()
            except OSError:
                self.send_error(500, "index.html 없음"); return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b); return
        if self.path.startswith("/api/price"):
            q = parse_qs(urlparse(self.path).query)
            sym = (q.get("ticker", ["TQQQ"])[0] or "TQQQ").upper().strip()
            try:
                r = yahoo_last(sym)
            except Exception as e:
                _send(self, {"error": f"시세 조회 실패: {e}"}, 502); return
            if not r:
                _send(self, {"error": f"{sym} 시세 없음(티커 확인)"}, 404); return
            _send(self, {"ticker": sym, "date": r[0], "close": r[1]}); return
        if self.path == "/api/fx":
            try:
                r = yahoo_last("KRW=X")
            except Exception as e:
                _send(self, {"error": f"환율 조회 실패: {e}"}, 502); return
            _send(self, {"usdkrw": r[1] if r else None}); return
        self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if self.path == "/api/vr_plan":
                close = float(body.get("close", 0))
                if close <= 0:
                    raise ValueError("현재가(close)는 0보다 커야 합니다")
                qty = float(body.get("qty", 0)); avg = float(body.get("avg", 0))
                V = float(body["V"]) if body.get("V") not in (None, "") else qty * close
                _send(self, vr_plan(V, qty, avg, float(body.get("pool", 0)), close,
                                    band=float(body.get("band", 0.15)),
                                    acct_type=str(body.get("acct_type", "적립식")))); return
            if self.path == "/api/im":
                close = float(body.get("close", 0))
                if close <= 0:
                    raise ValueError("현재가(close)는 0보다 커야 합니다")
                p = IMParams(seed=float(body.get("seed", 4000)), divisions=int(body.get("divisions", 40)),
                             take_profit=float(body.get("take_profit", 0.10)),
                             big_a=float(body.get("big_a", 15)), big_b=float(body.get("big_b", 1.5)))
                _send(self, im_plan(float(body.get("qty", 0)), float(body.get("avg", 0)),
                                    float(body.get("cum_buy", 0)), close, p)); return
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            _send(self, {"error": str(e)}, 400); return
        self.send_error(404)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    host = sys.argv[2] if len(sys.argv) > 2 else "0.0.0.0"
    print(f"라오어 투자 도구 → http://{host}:{port}  (Ctrl-C 종료)")
    ThreadingHTTPServer((host, port), H).serve_forever()
