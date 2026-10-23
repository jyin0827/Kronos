"""NautilusTrader 주간 리밸런싱 백테스트 예제.

weights.csv(종목별 목표 비중)를 읽어 매주 첫 거래일(보통 월요일) 장 시작에
목표 비중대로 리밸런싱한다. 수수료는 체결 금액의 0.1%.

입력 (make_weights.py가 만듦)
    weights.csv : symbol,weight
    prices.csv  : date,symbol,close  (open 열이 있으면 그 시가를 쓰고, 없으면 전일 종가를 시가로 씀)

출력 (results/ 폴더)
    fills.csv              : 체결 내역 (가격, 수량, 수수료)
    equity_curve.csv       : 일별 평가금액
    cumulative_return.png  : 누적 수익률 그래프

nautilus 가상환경에서 실행:
    C:\\Users\\정영인\\.venvs\\nautilus\\Scripts\\python.exe portfolio_backtest\\backtest_weekly_rebalance.py

다른 입력/출력을 쓰려면 --weights, --prices, --out, --start 인자를 준다.
비중 합이 1보다 작으면 나머지는 현금으로 둔다.
"""

import argparse
from decimal import Decimal
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AccountType, AggressorSide, OmsType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy

HERE = Path(__file__).resolve().parent

VENUE = "SIM"
STARTING_CASH = 1_000_000
FEE_RATE = Decimal("0.001")  # 0.1%
MARKET_TZ = "America/New_York"
OPEN_TIME, CLOSE_TIME = "09:30", "16:00"


class WeeklyRebalanceConfig(StrategyConfig, frozen=True):
    weights: dict[str, float]
    venue: str = VENUE
    # 수수료와 정수 주식 반올림 때문에 현금이 모자라지 않도록 남겨두는 비율
    cash_buffer: float = 0.002


class WeeklyRebalance(Strategy):
    def __init__(self, config: WeeklyRebalanceConfig) -> None:
        super().__init__(config)
        self.venue = Venue(config.venue)
        self.targets = {
            InstrumentId.from_str(f"{symbol}.{config.venue}"): weight
            for symbol, weight in config.weights.items()
        }
        self._last_week = None
        self._last_close_ts = None
        self.equity_curve: list[dict] = []
        self.rebalance_dates: list[pd.Timestamp] = []

    def on_start(self) -> None:
        for instrument_id in self.targets:
            self.subscribe_trade_ticks(instrument_id)

    def on_trade_tick(self, tick: TradeTick) -> None:
        local = pd.Timestamp(tick.ts_event, unit="ns", tz="UTC").tz_convert(MARKET_TZ)
        if local.strftime("%H:%M") == OPEN_TIME:
            # 새 주의 첫 시가 → 모든 종목의 시가가 들어온 직후(1µs 뒤) 리밸런싱
            week = local.isocalendar()[:2]
            if week != self._last_week:
                self._last_week = week
                self.clock.set_time_alert(
                    f"rebalance-{tick.ts_event}",
                    pd.Timestamp(tick.ts_event + 1_000, unit="ns", tz="UTC"),
                    self._rebalance,
                )
        elif tick.ts_event != self._last_close_ts:
            # 장 마감 후 평가금액 기록
            self._last_close_ts = tick.ts_event
            self.clock.set_time_alert(
                f"mark-{tick.ts_event}",
                pd.Timestamp(tick.ts_event + 1_000, unit="ns", tz="UTC"),
                self._record_equity,
            )

    def _prices(self) -> dict[InstrumentId, float]:
        return {iid: self.cache.trade_tick(iid).price.as_double() for iid in self.targets}

    def _cash(self) -> float:
        return self.portfolio.account(self.venue).balance_total(USD).as_double()

    def _equity(self, prices: dict[InstrumentId, float]) -> float:
        holdings = sum(float(self.portfolio.net_position(iid)) * px for iid, px in prices.items())
        return self._cash() + holdings

    def _rebalance(self, event) -> None:
        prices = self._prices()
        investable = self._equity(prices) * (1 - self.config.cash_buffer)

        deltas = []
        for iid, weight in self.targets.items():
            target_qty = int(investable * weight / prices[iid])
            delta = target_qty - int(self.portfolio.net_position(iid))
            if delta != 0:
                deltas.append((iid, delta))

        # 매도부터 체결시켜 현금을 확보한 뒤 매수
        for iid, delta in sorted(deltas, key=lambda d: d[1] > 0):
            order = self.order_factory.market(
                instrument_id=iid,
                order_side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
                quantity=Quantity.from_int(abs(delta)),
            )
            self.submit_order(order)

        self.rebalance_dates.append(pd.Timestamp(event.ts_event, unit="ns", tz="UTC").tz_convert(MARKET_TZ))

    def _record_equity(self, event) -> None:
        local = pd.Timestamp(event.ts_event, unit="ns", tz="UTC").tz_convert(MARKET_TZ)
        if self.equity_curve and self.equity_curve[-1]["date"] == local.date():
            return
        self.equity_curve.append(
            {"date": local.date(), "cash": self._cash(), "equity": self._equity(self._prices())}
        )

    def on_stop(self) -> None:
        # 마지막 날 종가 직후 알림은 데이터가 끝나 실행되지 않으므로 여기서 기록한다.
        # 포지션은 청산하지 않고 평가금액으로만 남긴다.
        if self._last_close_ts is not None:
            self._record_equity(type("LastClose", (), {"ts_event": self._last_close_ts}))


def make_instrument(symbol: str) -> Equity:
    return Equity(
        instrument_id=InstrumentId(Symbol(symbol), Venue(VENUE)),
        raw_symbol=Symbol(symbol),
        currency=USD,
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        lot_size=Quantity.from_int(1),
        ts_event=0,
        ts_init=0,
        maker_fee=FEE_RATE,
        taker_fee=FEE_RATE,
    )


def make_ticks(instrument: Equity, df: pd.DataFrame) -> list[TradeTick]:
    """하루를 시가 틱(09:30)과 종가 틱(16:00) 두 개로 표현한다."""
    size = Quantity.from_int(1_000_000_000)  # 체결량 제약이 없도록 충분히 크게
    ticks = []
    for row in df.itertuples():
        for label, clock_time, px in (("O", OPEN_TIME, row.open), ("C", CLOSE_TIME, row.close)):
            ts = pd.Timestamp(f"{row.date} {clock_time}", tz=MARKET_TZ).value
            ticks.append(
                TradeTick(
                    instrument_id=instrument.id,
                    price=Price(round(px, 2), 2),
                    size=size,
                    aggressor_side=AggressorSide.NO_AGGRESSOR,
                    trade_id=TradeId(f"{instrument.id.symbol}-{row.date}-{label}"),
                    ts_event=ts,
                    ts_init=ts,
                )
            )
    return ticks


def load_inputs(weights_path: Path, prices_path: Path, start: str | None) -> tuple[dict[str, float], pd.DataFrame]:
    weights = pd.read_csv(weights_path).query("weight > 0")
    weights = dict(zip(weights["symbol"], weights["weight"]))

    prices = pd.read_csv(prices_path)
    prices = prices[prices["symbol"].isin(weights)].sort_values(["symbol", "date"])
    if start:
        prices = prices[prices["date"] >= start]
    if "open" not in prices.columns:
        # 종가만 있는 데이터: 시가 = 전일 종가 (첫날은 당일 종가)
        prices["open"] = prices.groupby("symbol")["close"].shift(1).fillna(prices["close"])
    return weights, prices


def save_results(engine: BacktestEngine, strategy: WeeklyRebalance, out_dir: Path, title: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    report = engine.trader.generate_order_fills_report()
    fills = pd.DataFrame(
        {
            "time_et": pd.to_datetime(report["ts_last"]).dt.tz_convert(MARKET_TZ).dt.strftime("%Y-%m-%d %H:%M"),
            "symbol": report["instrument_id"].str.replace(f".{VENUE}", "", regex=False),
            "side": report["side"],
            "quantity": report["filled_qty"].astype(float).astype(int),
            "price": report["avg_px"].astype(float),
        }
    )
    fills["notional"] = (fills["quantity"] * fills["price"]).round(2)
    fills["commission"] = report["commissions"].astype(str).str.extract(r"([\d.]+)")[0].astype(float)
    fills = fills.sort_values(["time_et", "side", "symbol"], ascending=[True, False, True])
    fills.to_csv(out_dir / "fills.csv", index=False)

    curve = pd.DataFrame(strategy.equity_curve).set_index("date")
    curve["cumulative_return"] = curve["equity"] / STARTING_CASH - 1
    curve.to_csv(out_dir / "equity_curve.csv")

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(pd.to_datetime(curve.index), curve["cumulative_return"], color="#1f6feb", linewidth=1.4)
    ax.axhline(0, color="#888", linewidth=0.8)
    ax.yaxis.set_major_formatter(mtick.PercentFormatter(1.0))
    ax.set_title(title)
    ax.set_ylabel("Cumulative return")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "cumulative_return.png", dpi=150)

    # 요약
    equity = curve["equity"]
    years = (pd.to_datetime(curve.index[-1]) - pd.to_datetime(curve.index[0])).days / 365.25
    drawdown = equity / equity.cummax() - 1
    print(f"기간            : {curve.index[0]} ~ {curve.index[-1]}")
    print(f"최종 평가금액   : ${equity.iloc[-1]:,.0f} (시작 ${STARTING_CASH:,.0f})")
    print(f"누적 수익률     : {curve['cumulative_return'].iloc[-1]:.2%}")
    print(f"연환산 수익률   : {(equity.iloc[-1] / STARTING_CASH) ** (1 / years) - 1:.2%}")
    print(f"최대 낙폭       : {drawdown.min():.2%}")
    print(f"리밸런싱 횟수   : {len(strategy.rebalance_dates)}")
    print(f"체결 건수       : {len(fills)}")
    print(f"총 거래대금     : ${fills['notional'].sum():,.0f}")
    print(f"총 수수료       : ${fills['commission'].sum():,.0f}")
    print(f"결과 저장       : {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weights", type=Path, default=HERE / "weights.csv")
    parser.add_argument("--prices", type=Path, default=HERE / "prices.csv")
    parser.add_argument("--out", type=Path, default=HERE / "results")
    parser.add_argument("--start", help="이 날짜(YYYY-MM-DD)부터 백테스트")
    parser.add_argument("--title", default="Weekly rebalance (skfolio MeanRisk weights, fee 0.1%) - cumulative return")
    args = parser.parse_args()

    weights, prices = load_inputs(args.weights, args.prices, args.start)

    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="WARNING")),
    )
    engine.add_venue(
        venue=Venue(VENUE),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=USD,
        starting_balances=[Money(STARTING_CASH, USD)],
        # 주문을 제출 즉시 처리해서, 매도 체결로 생긴 현금으로 바로 매수할 수 있게 한다
        use_message_queue=False,
    )

    for symbol, df in prices.groupby("symbol"):
        instrument = make_instrument(symbol)
        engine.add_instrument(instrument)
        engine.add_data(make_ticks(instrument, df))

    strategy = WeeklyRebalance(WeeklyRebalanceConfig(weights=weights))
    engine.add_strategy(strategy)
    engine.run()

    save_results(engine, strategy, args.out, args.title)
    engine.dispose()


if __name__ == "__main__":
    main()
