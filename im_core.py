"""무한매수법(Infinite Buying) 엔진 코어 — 라오어式 v2.2 기준.

⚠️ 돈 로직 · 소스 상충 주의(§2-3 할루시네이션 금지):
  라오어 공식 원본(네이버 카페·책)은 로그인/유료라 직접 인용 불가.
  아래는 2차 소스 교차검증 결과이며, 주류값을 기본으로 두고 전부 파라미터화했다.
  실제 운용 전 카페 원문으로 아래 3가지를 반드시 확정할 것:
    (1) 별지점(큰수) 산식: (15-1.5T)% [주류] vs (10-0.5T)% [일부]  ← BIG_A/BIG_B
    (2) v2.2 쿼터매도 지정가가 별지점 (15-1.5T)% 인지 고정 +5% 인지
    (3) 40회 소진(T~40) 후 절차 (영혼매도/1회매수액 유지 등)

교차검증 완료(신뢰 높음): 40분할, T=누적매수액/1회매수액, 전/후반전 경계 T=20,
  매수 전반전 2건(평단LOC+별지점LOC)·후반전 1건, 쿼터매도(1/4)+전량익절(평단+10%),
  전량매도→사이클 리셋, RSI=종목선정용(매매로직 밖).
자문 도구: 주문·체결은 사용자가 직접. 이 엔진은 '오늘 걸 LOC 주문'을 계산해 보여줄 뿐.
"""
from __future__ import annotations
from dataclasses import dataclass
import math


@dataclass
class IMParams:
    seed: float = 4000.0        # 총투자원금(USD)
    divisions: int = 40         # 분할수(40 방어형 / 20 공격형)
    half_T: float = 20.0        # T>=half_T → 후반전(보수화)
    take_profit: float = 0.10   # 전량익절 = 평단 ×(1+이 값)  (TQQQ +10%; v3.0 +15%; SOXL +20%)
    big_a: float = 15.0         # 별지점(큰수) 산식 계수 A
    big_b: float = 1.5          # 별지점 산식 계수 B  → big_pct(T)=A-B*T  (주류 15,1.5 / 일부 10,0.5)
    quarter_ratio: float = 0.25 # 쿼터매도 비중(1/4)

    def unit_buy(self) -> float:
        return self.seed / self.divisions


def t_value(cum_buy: float, unit_buy: float) -> float:
    """T = 누적매수액 / 1회매수액, 소수점 둘째자리 올림. (진행 회차 지표)"""
    if unit_buy <= 0:
        return 0.0
    return math.ceil(cum_buy / unit_buy * 100) / 100


def big_pct(T: float, p: IMParams) -> float:
    """별지점(큰수) 지정가 = 평단 ×(1+big_pct/100). T 커질수록 작아져 후반전 보수화."""
    return p.big_a - p.big_b * T


def plan(qty: float, avg: float, cum_buy: float, close: float, p: IMParams) -> dict:
    """오늘 걸 무한매수 주문(LOC) 계산. 자문용 — 실제 주문/체결은 직접.
    qty=보유수량, avg=평단, cum_buy=누적매수액(원금 소진분), close=현재가(지정가 참고)."""
    unit = p.unit_buy()
    T = t_value(cum_buy, unit)
    phase = "후반전" if T >= p.half_T else "전반전"
    star_pct = big_pct(T, p)
    exhausted = T >= p.divisions - 0.9   # ~39.1 이상 = 원금 소진 임박/완료

    buys, sells = [], []
    if qty <= 0:
        # 사이클 1일차: 평단 없음 → 종가 LOC로 1회매수액 진입
        n = math.floor(unit / close) if close > 0 else 0
        if n > 0:
            buys.append({"kind": "LOC", "price": round(close, 2), "shares": n, "note": "1일차 진입(종가 LOC)"})
    elif not exhausted:
        star = avg * (1 + star_pct / 100)                 # 별지점(큰수) 가격
        if T < p.half_T:                                  # 전반전: 2건
            n1 = math.floor((unit / 2) / avg) if avg > 0 else 0
            n2 = math.floor((unit / 2) / star) if star > 0 else 0
            if n1 > 0: buys.append({"kind": "LOC", "price": round(avg, 2), "shares": n1, "note": "작은수(평단)"})
            if n2 > 0: buys.append({"kind": "LOC", "price": round(star, 2), "shares": n2, "note": "큰수(별지점)"})
        else:                                             # 후반전: 1건(별지점 전액)
            n = math.floor(unit / star) if star > 0 else 0
            if n > 0: buys.append({"kind": "LOC", "price": round(star, 2), "shares": n, "note": "별지점 전액(보수)"})

    if qty > 0:
        star = avg * (1 + star_pct / 100)
        q_qtr = math.floor(qty * p.quarter_ratio)
        q_rest = qty - q_qtr
        if q_qtr > 0:
            # 자전거래 방지: 쿼터매도(별지점)가 별지점 매수와 동가가 되면 브로커가 거부 → 매도가 +0.01
            sells.append({"kind": "LOC", "price": round(star + 0.01, 2), "shares": q_qtr,
                          "note": f"쿼터매도(1/4, 별지점 {star_pct:.1f}% +0.01 자전방지)"})
        if q_rest > 0:
            sells.append({"kind": "지정가", "price": round(avg * (1 + p.take_profit), 2), "shares": q_rest,
                          "note": f"전량익절(평단 +{p.take_profit*100:.0f}%)"})

    cost = qty * avg
    equity = qty * close
    # 쿼터손절: 40회 소진(원금 소진)인데 미익절이면 보유 1/4을 MOC/시장가 손절해 평단 낮출 여력 확보.
    # (소스: 카페 v2.2/v3 하락장 가이드. "총시드 25%" vs "보유 1/4" 표현 혼재 → 보유 1/4로 구현, 조정 가능)
    quarter_cut = None
    if exhausted and qty > 0 and close < avg * (1 + p.take_profit):
        quarter_cut = {"shares": math.floor(qty * 0.25), "kind": "MOC/시장가",
                       "note": "원금 소진·미익절 → 보유 1/4 손절(평단 조정)"}
    return {
        "T": T, "phase": phase, "progress": round(T / p.divisions, 4),
        "quarter_cut": quarter_cut,
        "unit_buy": round(unit, 2), "star_pct": round(star_pct, 2),
        "qty": qty, "avg": round(avg, 4), "cum_buy": round(cum_buy, 2),
        "cost": round(cost, 2), "equity": round(equity, 2),
        "unrealized": round(equity - cost, 2),
        "unrealized_pct": round(close / avg - 1, 4) if avg > 0 else 0.0,
        "target_price": round(avg * (1 + p.take_profit), 2) if avg > 0 else None,
        "exhausted": exhausted, "cycle_done": qty <= 0,
        "buys": buys, "sells": sells,
        "close": close, "divisions": p.divisions,
    }


def position_from_trades(trades: list[tuple], p: IMParams) -> dict:
    """체결 리스트[(qty, price)]로 현재 보유·평단·누적매수액 산출(무한매수 원장 replay).
    qty>0 매수(누적매수액 증가), qty<0 매도(전량매도 시 사이클 리셋)."""
    shares, avg, cum_buy = 0.0, 0.0, 0.0
    for q, price in trades:
        if q > 0:
            cost = shares * avg + q * price
            shares += q
            avg = cost / shares if shares else 0.0
            cum_buy += q * price
        elif q < 0:
            shares += q                     # q<0
            if shares <= 1e-9:              # 전량매도 → 사이클 종료·리셋
                shares, avg, cum_buy = 0.0, 0.0, 0.0
    return {"qty": shares, "avg": avg, "cum_buy": cum_buy}


if __name__ == "__main__":
    p = IMParams(seed=4000, divisions=40)
    # 1) 1회매수액 = 원금/분할
    assert abs(p.unit_buy() - 100) < 1e-9
    # 2) T 산식(둘째자리 올림): 누적 350 / 100 = 3.5
    assert t_value(350, 100) == 3.5 and t_value(351, 100) == 3.51
    # 3) 별지점: T=0→15%, T=10→0%, T=20→-15%(평단 아래=보수)
    assert big_pct(0, p) == 15 and big_pct(10, p) == 0 and big_pct(20, p) == -15
    # 4) 전반전(T=2) 매수 2건(평단+별지점), 후반전(T=25) 1건. (avg=20: 1회매수액 $100≥2주 조건 충족)
    d1 = plan(qty=10, avg=20, cum_buy=200, close=20, p=p)
    assert d1["phase"] == "전반전" and len(d1["buys"]) == 2
    d2 = plan(qty=10, avg=20, cum_buy=2500, close=20, p=p)
    assert d2["phase"] == "후반전" and len(d2["buys"]) == 1
    # 5) 매도: 쿼터(1/4 LOC) + 나머지(전량익절 평단+10%=22.0)
    assert len(d1["sells"]) == 2 and d1["sells"][1]["price"] == 22.0
    # 6) 1일차(보유0): 종가 LOC 1건, 사이클 시작
    d0 = plan(qty=0, avg=0, cum_buy=0, close=50, p=p)
    assert len(d0["buys"]) == 1 and d0["cycle_done"] and d0["buys"][0]["shares"] == 2  # 100/50
    # 7) 원장 replay: 매수 누적 후 전량매도 → 리셋
    pos = position_from_trades([(2,50.0),(2,45.0),(-4,60.0)], p)
    assert pos["qty"] == 0 and pos["cum_buy"] == 0
    pos2 = position_from_trades([(2,50.0),(2,40.0)], p)
    assert pos2["qty"] == 4 and abs(pos2["avg"]-45) < 1e-9 and abs(pos2["cum_buy"]-180) < 1e-9
    print("OK — im_core v2.2 자체검증 통과 (주류 파라미터 15/1.5, +10%익절)")
