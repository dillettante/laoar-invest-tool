"""
VR 엔진 코어 — 라오어式 TQQQ VR(밸류 리밸런싱) V5.0.1 시트 수식의 충실한 Python 포팅.
시트(VR 적립식)의 셀 수식을 verbatim 추출해 1:1로 옮긴 것.
검증: 시트의 (date, close, 실거래 qty/price/fee)만 입력으로 주고 파생 컬럼을 재계산해
      시트 저장값과 행별 대조(validate.py).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
import math

SPLIT_DATE = date(2022, 1, 13)  # TQQQ 2:1 액면분할 보정 기준

@dataclass
class Params:
    seed_pool: float = 5910.0    # E5 투입원금(=시드 POOL)
    deposit: float = 200.0       # F5 적립금액(짝수주차 진입시)
    fee_rate: float = 0.0007     # G5 계좌수수료
    growth_base: float = 10.0    # H5 상승률 기준
    band: float = 0.15           # I5 V 밴드 ±
    start_date: date = date(2021, 11, 17)
    acct_type: str = "적립식"     # 적립식/거치식/인출식
    invested0: float = 0.0       # 수익률 기준 투입원금(0이면 seed_pool 사용). 재앵커 시 실제 투입자본.
    def pool_floor_mult(self) -> float:
        # I열 POOLCAP 배수 / Order I6 = H*(1-floor) ⇒ 적립식 floor 25%, 거치 50%, 인출 75%
        return {"적립식": 0.25, "거치식": 0.50, "인출식": 0.75}[self.acct_type]

@dataclass
class Row:
    date: date
    close: float
    week: int = 0
    V: float = 0.0
    Vmin: float = 0.0
    Vmax: float = 0.0
    deposit: float = 0.0
    need: float = 0.0      # 매매 필요 금액 (>0 매수 / <0 매도 / 0 홀드)
    qty: float = 0.0       # 실거래 수량(체결 입력)
    price: float = 0.0     # 실거래 단가
    fee: float = 0.0       # 실거래 수수료
    tradeamt: float = 0.0
    shares: float = 0.0
    avg: float = 0.0
    equity: float = 0.0
    pool: float = 0.0
    total: float = 0.0


@dataclass
class Anchor:
    """재앵커(A) — 장부를 잃었을 때 오늘의 실제 포지션을 새 최초원금으로 삼는 시드.
    2021 리플레이 대신 이 상태에서 앞으로만 정상 운영한다(라오어 VR 표준 복구법)."""
    date: date
    close: float
    shares: float
    avg: float
    pool: float
    V: float                     # 목표(=보통 평가금 shares*close). 지정 안 하면 vr.py가 평가금으로 채움.
    band: float = 0.15


def seed_from_anchor(a: Anchor) -> Row:
    r = Row(date=a.date, close=a.close)
    r.week = 0
    r.V = a.V
    r.Vmin = a.V * (1 - a.band)
    r.Vmax = a.V * (1 + a.band)
    r.deposit = 0.0
    r.need = 0.0                  # 앵커 시점엔 강제 매매 없음
    r.shares = a.shares
    r.avg = a.avg
    r.equity = a.shares * a.close
    r.pool = a.pool
    r.total = r.equity + r.pool
    return r


def _denom(week: int, p: Params) -> float:
    # IF(MOD(week,4)=0, (week/4)*0.1+H5, ((week-2)/4)*0.1+H5)
    base = (week // 4) if week % 4 == 0 else ((week - 2) // 4)
    return base * 0.1 + p.growth_base


def step(prev: Row | None, date_: date, close: float,
         qty: float, price: float, fee: float, p: Params,
         window: list[Row], q11: float | None = None) -> Row:
    """한 거래일(행) 계산. window = 직전 최대 10개 행(POOLCAP 룩백용, 과거→현재 순).
    q11 = 시드행 POOL 실제값($Q$11), week<2 POOLCAP 참조용."""
    r = Row(date=date_, close=close, qty=qty or 0.0, price=price or 0.0, fee=fee or 0.0)

    if prev is None:  # ── 시드 행(11행) ──
        r.week = 0
        r.V = p.seed_pool if p.acct_type == "적립식" else (
              p.seed_pool * 0.90 if p.acct_type == "거치식" else p.seed_pool * 0.80)
        r.Vmin = r.V * (1 - p.band)
        r.Vmax = r.V * (1 + p.band)
        r.deposit = 0.0                # H11 = 0 (POOL 시드는 E5 파라미터에서 직접)
        r.need = r.V                   # I11 = E11
        r.tradeamt = r.qty * r.price + r.fee
        r.shares = r.qty * 2 if date_ < SPLIT_DATE else r.qty
        r.avg = r.price / 2 if date_ < SPLIT_DATE else r.price
        r.equity = r.shares * close
        r.pool = p.seed_pool - r.tradeamt
        r.total = r.equity + r.pool
        return r

    # ── D 진행 주차 ──
    days = (date_ - p.start_date).days
    r.week = prev.week if days < 7 * (prev.week + 1) else prev.week + 1
    even_entry = (r.week % 2 == 0) and (prev.week != r.week)

    # ── H 적립 금액 ──
    r.deposit = p.deposit if even_entry else 0.0

    # ── E V 목표 ──
    if even_entry:
        dn = _denom(r.week, p)
        r.V = round(prev.V + prev.pool / dn
                    + (prev.equity - prev.V) / (2 * math.sqrt(dn)) + p.deposit, 2)
    else:
        r.V = prev.V
    r.Vmin = r.V * (1 - p.band)
    r.Vmax = r.V * (1 + p.band)

    # ── I 매매 필요 금액 ──
    r.need = _need(prev, r, p, window, q11)

    # ── M 거래금액 ──
    r.tradeamt = r.qty * r.price + r.fee
    # ── N 보유수량 ──
    r.shares = prev.shares + (r.qty * 2 if date_ < SPLIT_DATE else r.qty)
    # ── O 평단가 ──
    if r.qty == "" or r.qty <= 0:
        r.avg = prev.avg
    else:
        r.avg = (prev.shares * prev.avg + r.qty * r.price) / r.shares if r.shares else prev.avg
    # ── P 평가금액 ──
    r.equity = r.shares * close
    # ── Q POOL ──
    r.pool = prev.pool + r.deposit - r.tradeamt
    # ── R 합산 ──
    r.total = r.equity + r.pool
    return r


def _poolcap(prev: Row, cur: Row, p: Params, window: list[Row], q11: float | None) -> float:
    """POOLCAP = (POOL_at_prevOddWeek + deposit) * floor_mult.
    prevOddWeek = week-1 (week 짝수) else week-2. 직전 10행 윈도우에서 MATCH(...,1) 룩백."""
    if cur.week < 2:
        return q11 if q11 is not None else p.seed_pool  # IFS(D<2, $Q$11)
    target = cur.week - 1 if cur.week % 2 == 0 else cur.week - 2
    win = window[-10:]
    pool_at = None
    for row in win:                      # MATCH(target, weeks_asc, 1): target 이하 최댓값의 마지막 행
        if row.week <= target:
            pool_at = row.pool
    if pool_at is None:
        pool_at = prev.pool
    return (pool_at + p.deposit) * p.pool_floor_mult()


def _need(prev: Row, cur: Row, p: Params, window: list[Row], q11: float | None = None) -> float:
    F, G, Pp = cur.Vmin, cur.Vmax, prev.equity
    if F <= Pp <= G:
        return 0.0
    if Pp < F:  # 매수
        avail = prev.pool + cur.deposit
        if avail <= 0:
            return 0.0
        cap = _poolcap(prev, cur, p, window, q11)
        if avail - (F - Pp) <= cap:           # 풀 바닥(floor) 침범 → 제한 매수
            if prev.pool - cap < 0:
                return 0.0
            return prev.pool + cur.deposit - cap
        return F - Pp                          # 하단까지 정상 매수
    # Pp > G  매도
    return -(Pp - G)


def vr_plan(V: float, qty: float, avg: float, pool: float, close: float,
            band: float = 0.15, acct_type: str = "적립식") -> dict:
    """입력 기반 VR 신호·밴드·LOC 사다리(계좌 유형별 POOL 바닥 반영). 거치/적립 플래너 공용.
    V=목표, qty=보유, avg=평단, pool=현금, close=현재가. 원장 replay 없이 순수 계산."""
    p = Params(band=band, acct_type=acct_type)
    Vmin, Vmax = round(V * (1 - band), 2), round(V * (1 + band), 2)
    equity = qty * close
    if Vmin <= equity <= Vmax:
        sig, tone, act = "홀드", "hold", "신규 주문 없음 (밴드 안)"
    elif equity < Vmin:
        n = math.floor((Vmin - equity) / close) if close > 0 else 0
        sig, tone, act = "매수", "buy", f"≈ {n}주 매수 (${Vmin-equity:,.0f})"
    else:
        n = math.floor((equity - Vmax) / close) if close > 0 else 0
        sig, tone, act = "매도", "sell", f"≈ {n}주 매도 (${equity-Vmax:,.0f})"
    cur = Row(date=date(2000, 1, 1), close=close, V=V, Vmin=Vmin, Vmax=Vmax)
    lad = loc_ladder(cur, p, base_shares=qty, base_pool=pool)
    cost = qty * avg
    return {
        "acct": acct_type, "band": band, "close": close,
        "V": round(V, 2), "Vmin": Vmin, "Vmax": Vmax, "equity": round(equity, 2),
        "shares": qty, "avg": round(avg, 4), "pool": round(pool, 2),
        "total": round(equity + pool, 2), "cost": round(cost, 2),
        "pos_return": (equity / cost - 1) if cost else 0.0,
        "signal": sig, "tone": tone, "action": act, "ladder": lad,
    }


def loc_ladder(cur: Row, p: Params, base_shares: float, base_pool: float,
               unit: int = 1, max_rungs: int = 12) -> dict:
    """VR Order 탭 LOC 사다리 포팅.
    base_shares = 직전 홀수주차말 보유수량(G6), base_pool = 그 POOL + 적립(H6).
    매수: 가격 하락 시 1주씩(단가 = Vmin/보유수량), POOL 바닥(floor=base_pool*floor_mult) 까지.
    매도: 가격 상승 시 1주씩(단가 = Vmax/(보유수량-0.5)), 현재가 대비 +100% 미만이고 보유>1 동안."""
    floor = base_pool * p.pool_floor_mult()      # I6 = H6*(1-deploy) → 적립식 H6*0.25
    if base_shares <= 0:                          # 보유 0 → 증분 사다리 정의 불가(0나눗셈 방지)
        return {"floor": round(floor, 2), "buys": [], "sells": []}
    buys = []
    sh, pool = base_shares, base_pool
    for _ in range(max_rungs):
        price = cur.Vmin / sh                     # C: 1주 추가 시 단가
        if pool - price < floor:                  # 바닥 침범하면 중단
            break
        sh += unit
        pool -= price * unit
        buys.append({"price": round(price, 2), "shares_after": sh, "pool_after": round(pool, 2)})
    sells = []
    sh = base_shares
    for _ in range(max_rungs):
        if not (sh > 1):
            break
        price = cur.Vmax / (sh - unit / 2)
        if price / cur.close - 1 >= 1.0:          # 현재가 대비 +100% 이상이면 중단
            break
        sh -= unit
        sells.append({"price": round(price, 2), "shares_after": sh})
    return {"floor": round(floor, 2), "buys": buys, "sells": sells}
