"""DB증권 해외주식 잔고를 주기적으로 조회해 포트폴리오를 추적한다 (조회 전용).

- 잔고 조회 API(ov_stock_inquire_balance_margin) 하나만 호출한다. 응답에 종목별 현재가가 들어 있다.
  주문 API는 READ_ONLY_CALLS에 없으므로 이 프로그램에서는 부를 수 없다.
- 목표 비중(fund/weights.csv)과 실제 비중의 차이, 손익, 고점 대비 낙폭을 화면에 보여 준다.
- 매 조회 결과를 history/equity.csv, history/positions.csv에 쌓는다 (계좌 정보라 git에는 올리지 않는다).

broker 가상환경에서 실행:
    C:\\Users\\정영인\\.venvs\\broker\\Scripts\\python.exe tracker\\portfolio_tracker.py
    ... --once      한 번만 조회하고 끝내기
    ... --mock      키 없이 가짜 잔고로 화면 확인
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import random
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HISTORY = HERE / "history"
ET, KST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")

# 이 프로그램이 호출할 수 있는 API는 이 목록뿐이다 (그룹, 메서드).
READ_ONLY_CALLS = frozenset({("ov_stock_order", "ov_stock_inquire_balance_margin")})


@dataclass
class Holding:
    symbol: str
    name: str
    qty: float
    avg_price: float
    price: float
    value: float
    pnl: float
    pnl_pct: float
    day_change_pct: float


@dataclass
class Snapshot:
    time: datetime
    holdings: list[Holding]
    cash: float  # USD 예수금

    @property
    def equity(self) -> float:
        return self.cash + sum(h.value for h in self.holdings)


def num(value) -> float:
    """API 숫자 필드는 문자열로 올 수 있다 ("1,234.5", "", None)."""
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


class DBSecSource:
    """DB증권 Open API에서 잔고를 읽는다. READ_ONLY_CALLS 밖의 호출은 거부한다."""

    label = "DB증권"

    def __init__(self, config_path: Path):
        if not config_path.exists():
            raise SystemExit(
                f"설정 파일이 없습니다: {config_path}\n"
                f"{config_path.name}.example 을 복사해 {config_path.name} 로 만들고 앱키·시크릿을 넣어 주세요."
            )
        from dbsec_sdk import DBSecClient

        self._client = DBSecClient(str(config_path))
        self.label = f"DB증권 ({'실전' if self._client.config.mode == 'production' else '모의투자'})"

    async def _call(self, group: str, method: str, **kwargs):
        if (group, method) not in READ_ONLY_CALLS:
            raise PermissionError(f"조회 전용 추적기: {group}.{method} 호출은 허용되지 않습니다")
        return await getattr(getattr(self._client.apis, group), method)(**kwargs)

    async def snapshot(self) -> Snapshot:
        resp = await self._call(
            "ov_stock_order",
            "ov_stock_inquire_balance_margin",
            TrxTpCode="2",  # 주식잔고상세
            CmsnTpCode="2",  # 매수·매도 제비용 포함
            WonFcurrTpCode="2",  # 외화(USD) 기준
            DpntBalTpCode="0",  # 일반+소수점 전체
            fetch_all=True,
        )
        if resp.rsp_cd != "00000":
            raise RuntimeError(f"잔고 조회 실패 [{resp.rsp_cd}] {resp.rsp_msg}")

        holdings = []
        for row in resp.get("Out2") or []:
            qty = num(row.get("AstkExecBaseQty"))
            if qty == 0:
                continue
            holdings.append(
                Holding(
                    symbol=str(row.get("SymCode") or row.get("AstkIsuNo") or "").strip(),
                    name=str(row.get("AstkHanglIsuNm") or "").strip(),
                    qty=qty,
                    avg_price=num(row.get("AstkAvrPchsPrc")),
                    price=num(row.get("AstkNowPrc")),
                    value=num(row.get("AstkEvalAmt")),
                    pnl=num(row.get("AstkEvalPnlAmt")),
                    pnl_pct=num(row.get("EvalPnlRat")),
                    day_change_pct=num(row.get("AstkUpdnRat")),
                )
            )
        usd = next((r for r in resp.get("Out1") or [] if str(r.get("CrcyCode")).strip() == "USD"), {})
        return Snapshot(time=datetime.now(KST), holdings=holdings, cash=num(usd.get("FcurrDps")))

    async def close(self) -> None:
        await self._client.aclose()


class MockSource:
    """키 없이 화면을 확인하는 가짜 잔고: 목표 비중대로 10만 달러를 담고 가격을 조금씩 흔든다."""

    label = "MOCK (가짜 잔고)"

    def __init__(self, targets: dict[str, float]):
        prices = pd.read_csv(ROOT / "fund" / "data" / "ohlcv_daily.csv")
        last = prices.sort_values("date").groupby("symbol").last()
        self._rng = random.Random(7)
        self._prev = {s: float(last.loc[s, "close"]) for s in last.index}
        self._price = dict(self._prev)
        budget, self._qty, self._avg = 100_000.0, {}, {}
        for s, w in targets.items():
            if w > 0 and s in self._price:
                self._qty[s] = int(budget * w / self._price[s])
                self._avg[s] = self._price[s] * self._rng.uniform(0.9, 1.1)
        self._cash = budget - sum(self._qty[s] * self._price[s] for s in self._qty)

    async def snapshot(self) -> Snapshot:
        holdings = []
        for s, q in self._qty.items():
            self._price[s] *= 1 + self._rng.gauss(0, 0.004)
            p, avg = self._price[s], self._avg[s]
            holdings.append(
                Holding(s, s, q, avg, p, q * p, q * (p - avg), (p / avg - 1) * 100, (p / self._prev[s] - 1) * 100)
            )
        return Snapshot(time=datetime.now(KST), holdings=holdings, cash=self._cash)

    async def close(self) -> None:
        pass


def load_targets(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    w = pd.read_csv(path)
    return {str(s): float(x) for s, x in zip(w["symbol"], w["weight"])}


def append_history(snap: Snapshot, targets: dict[str, float], history: Path) -> float:
    """조회 결과를 쌓고, 지금까지 기록된 최고 평가금액을 돌려준다."""
    history.mkdir(parents=True, exist_ok=True)
    stamp = snap.time.isoformat(timespec="seconds")
    equity = snap.equity

    eq_path = history / "equity.csv"
    new = not eq_path.exists()
    with eq_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time_kst", "equity_usd", "cash_usd", "invested_pct"])
        w.writerow([stamp, round(equity, 2), round(snap.cash, 2), round(1 - snap.cash / equity, 4) if equity else 0])

    pos_path = history / "positions.csv"
    new = not pos_path.exists()
    with pos_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time_kst", "symbol", "qty", "price", "value_usd", "weight", "target_weight"])
        for h in snap.holdings:
            w.writerow([stamp, h.symbol, h.qty, h.price, round(h.value, 2),
                        round(h.value / equity, 4) if equity else 0, targets.get(h.symbol, 0.0)])

    return float(pd.read_csv(eq_path)["equity_usd"].max())


def market_status(now_kst: datetime) -> str:
    et = now_kst.astimezone(ET)
    open_ = et.weekday() < 5 and (9, 30) <= (et.hour, et.minute) < (16, 0)
    return f"미국 장중 (ET {et:%H:%M})" if open_ else f"미국 장 마감 (ET {et:%a %H:%M}) — 가격은 마지막 체결가"


COLUMNS = [("종목", 10), ("수량", 8), ("평균단가", 11), ("현재가", 11), ("등락률", 9), ("평가금액", 13),
           ("평가손익", 12), ("손익률", 9), ("비중", 8), ("목표", 8), ("차이", 8)]


def _pad(text: str, width: int, left: bool = False) -> str:
    """한글처럼 두 칸을 차지하는 글자를 고려해 화면 폭 기준으로 맞춘다."""
    shown = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    space = " " * max(0, width - shown)
    return text + space if left else space + text


def _row(cells: list[str]) -> str:
    return "".join(_pad(c, w, left=(i == 0)) for i, (c, (_, w)) in enumerate(zip(cells, COLUMNS)))


def render(snap: Snapshot, targets: dict[str, float], peak: float, drift_alert: float,
           source: str, interval: int | None, error: str | None = None, history: Path = HISTORY) -> str:
    equity = snap.equity
    symbols = sorted({h.symbol for h in snap.holdings} | {s for s, w in targets.items() if w > 0})
    by_sym = {h.symbol: h for h in snap.holdings}
    rule = "─" * sum(w for _, w in COLUMNS)
    lines = [
        f"포트폴리오 추적 · {source} · {snap.time:%Y-%m-%d %H:%M:%S} KST · {market_status(snap.time)}",
        rule,
        _row([name for name, _ in COLUMNS]),
    ]
    alerts = []
    for s in symbols:
        h = by_sym.get(s)
        weight = h.value / equity if h and equity else 0.0
        target = targets.get(s, 0.0)
        drift = weight - target
        flag = abs(drift) >= drift_alert
        if flag:
            alerts.append(f"{s} 비중 {weight:.1%} (목표 {target:.1%}, 차이 {drift:+.1%}p)")
        tail = [f"{weight:.1%}", f"{target:.1%}", f"{drift:+.1%}" + ("!" if flag else "")]
        if h:
            lines.append(_row([s, f"{h.qty:,.0f}", f"{h.avg_price:,.2f}", f"{h.price:,.2f}", f"{h.day_change_pct:.2f}%",
                               f"{h.value:,.0f}", f"{h.pnl:,.0f}", f"{h.pnl_pct:.2f}%", *tail]))
        else:
            lines.append(_row([s, "미보유", "", "", "", "", "", "", *tail]))

    cash_w = snap.cash / equity if equity else 0.0
    cash_target = max(0.0, 1 - sum(w for w in targets.values() if w > 0))
    cash_drift = cash_w - cash_target
    if targets and abs(cash_drift) >= drift_alert:
        alerts.append(f"현금 비중 {cash_w:.1%} (목표 {cash_target:.1%}, 차이 {cash_drift:+.1%}p)")
    drawdown = equity / peak - 1 if peak else 0.0
    lines += [
        _row(["현금(USD)", "", "", "", "", f"{snap.cash:,.0f}", "", "",
              f"{cash_w:.1%}", f"{cash_target:.1%}", f"{cash_drift:+.1%}"]),
        rule,
        f"총자산 ${equity:,.0f}  ·  평가손익 ${sum(h.pnl for h in snap.holdings):,.0f}  ·  "
        f"기록상 최고 ${peak:,.0f} 대비 {drawdown:+.2%}",
    ]
    if alerts:
        lines.append(f"⚠ 목표 비중과 {drift_alert * 100:g}%p 이상 차이: " + " / ".join(alerts))
    if error:
        lines.append(f"⚠ 마지막 조회 실패: {error} (직전 결과를 표시 중)")
    if interval:
        lines.append(f"{interval}초마다 갱신 · 종료: Ctrl+C · 기록: {history}")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> None:
    targets = load_targets(args.targets)
    source = MockSource(targets) if args.mock else DBSecSource(args.config)
    history = HISTORY / "mock" if args.mock else HISTORY
    last, error, peak = None, None, 0.0
    try:
        while True:
            try:
                last = await source.snapshot()
                peak = append_history(last, targets, history)
                error = None
            except Exception as exc:  # 네트워크·토큰 오류에도 멈추지 않고 다음 주기에 다시 시도
                if args.once or last is None:
                    raise
                error = str(exc)
            screen = render(last, targets, peak, args.drift, source.label,
                            None if args.once else args.interval, error, history)
            if args.once:
                print(screen)
                return
            print("\033[2J\033[H" + screen, flush=True)
            await asyncio.sleep(args.interval)
    finally:
        await source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="DB증권 해외주식 포트폴리오 추적 (조회 전용)")
    parser.add_argument("--config", type=Path, default=HERE / "dbsec_config.yaml", help="DB증권 SDK 설정 파일")
    parser.add_argument("--targets", type=Path, default=ROOT / "fund" / "weights.csv", help="목표 비중 파일")
    parser.add_argument("--interval", type=int, default=30, help="갱신 간격(초). 잔고 API 한도는 초당 3회")
    parser.add_argument("--drift", type=float, default=0.05, help="목표 비중과 이만큼 벌어지면 경고 (0.05 = 5%p)")
    parser.add_argument("--once", action="store_true", help="한 번만 조회하고 끝내기")
    parser.add_argument("--mock", action="store_true", help="키 없이 가짜 잔고로 실행")
    args = parser.parse_args()
    if args.interval < 5:
        parser.error("--interval 은 5초 이상으로 해 주세요")
    if os.name == "nt":
        os.system("")  # Windows 콘솔에서 화면 지우기(ANSI) 활성화
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n추적을 멈췄습니다.")


if __name__ == "__main__":
    sys.exit(main())
