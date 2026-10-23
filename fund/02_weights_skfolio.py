"""Kronos 5일 예측 수익률을 기대수익으로 넣어 skfolio MeanRisk로 비중을 계산하고 weights.csv로 저장한다.

- 기대수익(mu): forecast.csv의 5일 뒤 예측 수익률을 하루 단위로 환산 ((1+r5)^(1/5)-1)
- 공분산: 최근 252거래일 일간 수익률, Ledoit-Wolf 축소 추정
- 목적함수: 효용 최대화 (mu'w - risk_aversion/2 * w'Σw)
- 제약: 종목당 최대 40%, 공매도 없음, 비중 합 0~100% (나머지는 현금)
  → 예측 수익률이 모두 음수면 전부 현금이 된다.

Kronos 가상환경(skfolio 설치됨)에서 실행:
    C:\\Users\\정영인\\.venvs\\kronos\\Scripts\\python.exe fund\\02_weights_skfolio.py
"""

from pathlib import Path

import numpy as np
import pandas as pd
from skfolio.moments import BaseMu, LedoitWolf
from skfolio.optimization import MeanRisk, ObjectiveFunction
from skfolio.prior import EmpiricalPrior

FUND = Path(__file__).resolve().parent
COV_WINDOW = 252
RISK_AVERSION = 3.0
MAX_WEIGHT = 0.40


class ForecastMu(BaseMu):
    """외부 예측값을 그대로 기대수익으로 쓰는 mu 추정기."""

    def __init__(self, mu: np.ndarray | None = None):
        super().__init__()
        self.mu = mu

    def fit(self, X, y=None):
        self.mu_ = np.asarray(self.mu, dtype=float)
        return self


forecast = pd.read_csv(FUND / "forecast.csv")
horizon = forecast["step"].max()
r5 = forecast[forecast["step"] == horizon].set_index("symbol")["pred_return"]
mu_daily = (1 + r5) ** (1 / horizon) - 1

prices = pd.read_csv(FUND / "data" / "ohlcv_daily.csv", parse_dates=["date"])
closes = prices.pivot(index="date", columns="symbol", values="close")[r5.index]
X = closes.pct_change().dropna().tail(COV_WINDOW)

model = MeanRisk(
    objective_function=ObjectiveFunction.MAXIMIZE_UTILITY,
    risk_aversion=RISK_AVERSION,
    prior_estimator=EmpiricalPrior(
        mu_estimator=ForecastMu(mu_daily.to_numpy()),
        covariance_estimator=LedoitWolf(),
    ),
    min_weights=0.0,
    max_weights=MAX_WEIGHT,
    budget=None,
    min_budget=0.0,
    max_budget=1.0,
)
model.fit(X)

weights = pd.DataFrame(
    {
        "symbol": X.columns,
        "weight": np.round(model.weights_, 4),
        "pred_return_5d": r5.to_numpy().round(4),
        "mu_daily": mu_daily.to_numpy().round(6),
        "vol_annual": (X.std() * np.sqrt(252)).to_numpy().round(4),
    }
)
weights.loc[weights["weight"].abs() < 1e-4, "weight"] = 0.0
weights.to_csv(FUND / "weights.csv", index=False)

print(f"covariance window: {X.index[0].date()} ~ {X.index[-1].date()} ({len(X)} days) | risk aversion {RISK_AVERSION} | max weight {MAX_WEIGHT:.0%}")
print(weights.to_string(index=False))
print(f"invested {weights['weight'].sum():.1%} | cash {1 - weights['weight'].sum():.1%}")
print(f"saved: {FUND / 'weights.csv'}")
