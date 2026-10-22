"""skfolio로 종목별 비중을 계산해 weights.csv로, 백테스트용 가격을 prices.csv로 저장한다.

Kronos 가상환경(skfolio 설치됨)에서 실행:
    C:\\Users\\정영인\\.venvs\\kronos\\Scripts\\python.exe portfolio_backtest\\make_weights.py
"""

from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split
from skfolio.datasets import load_sp500_dataset
from skfolio.optimization import MeanRisk
from skfolio.preprocessing import prices_to_returns

OUT_DIR = Path(__file__).resolve().parent

prices = load_sp500_dataset()
X = prices_to_returns(prices)
X_train, X_test = train_test_split(X, test_size=0.33, shuffle=False)

# 학습 구간으로 최소분산 비중 계산
model = MeanRisk().fit(X_train)
weights_df = (
    pd.DataFrame({"symbol": X.columns, "weight": model.weights_.round(6)})
    .query("weight > 1e-4")  # 0.01% 미만은 사실상 0이라 제외
    .reset_index(drop=True)
)
weights_df["weight"] = (weights_df["weight"] / weights_df["weight"].sum()).round(6)
weights_df.to_csv(OUT_DIR / "weights.csv", index=False)

# 백테스트는 학습에 쓰지 않은 테스트 구간 가격으로 한다 (종가만 있는 데이터)
test_prices = prices.loc[X_test.index[0]:, weights_df["symbol"]]
long = (
    test_prices.rename_axis("date")
    .reset_index()
    .melt(id_vars="date", var_name="symbol", value_name="close")
    .sort_values(["date", "symbol"])
)
long.to_csv(OUT_DIR / "prices.csv", index=False)

print(weights_df.to_string(index=False))
print(f"비중 합계: {weights_df['weight'].sum():.4f}")
print(f"가격: {test_prices.index[0].date()} ~ {test_prices.index[-1].date()}, {len(test_prices)}일 x {test_prices.shape[1]}종목")
