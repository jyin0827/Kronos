"""Kronos로 종목별 다음 5거래일 종가를 예측해 forecast.csv로 저장한다.

종목마다 같은 입력으로 표본을 SAMPLES개 뽑아 평균과 10~90% 구간, 상승 확률을 함께 기록한다.

과거 구간(LOOKBACK)은 120일을 쓴다. 250일 이상이면 최근 크게 오른 종목(VLO, DHT)을
구간 평균 쪽으로 끌어내려 5일 -25~-40% 같은 비현실적인 예측이 나왔다 (2026-10-23 점검).

Kronos 가상환경에서 저장소 루트를 작업 폴더로 실행:
    C:\\Users\\정영인\\.venvs\\kronos\\Scripts\\python.exe fund\\01_forecast_kronos.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402

FUND = Path(__file__).resolve().parent
LOOKBACK, PRED_LEN, SAMPLES, SEED = 120, 5, 30, 42
COLS = ["open", "high", "low", "close", "volume", "amount"]

torch.manual_seed(SEED)
np.random.seed(SEED)

tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
predictor = KronosPredictor(model, tokenizer, max_context=512)

data = pd.read_csv(FUND / "data" / "ohlcv_daily.csv", parse_dates=["date"])
last_date = data["date"].max()
# 다음 5거래일: 주말만 건너뛴다 (이 기간 10/23~10/29에는 미국 증시 휴장일이 없다)
future = pd.Series(pd.bdate_range(last_date + pd.Timedelta(days=1), periods=PRED_LEN)).reset_index(drop=True)

symbols, x_dfs, x_ts, y_ts = [], [], [], []
for symbol, df in data.groupby("symbol", sort=False):
    df = df.sort_values("date").tail(LOOKBACK).reset_index(drop=True)
    assert len(df) == LOOKBACK and df["date"].iloc[-1] == last_date, symbol
    for _ in range(SAMPLES):
        symbols.append(symbol)
        x_dfs.append(df[COLS])
        x_ts.append(df["date"])
        y_ts.append(future)

preds = predictor.predict_batch(
    x_dfs, x_ts, y_ts, pred_len=PRED_LEN, T=1.0, top_p=0.9, sample_count=1, verbose=False
)

rows = []
for symbol in dict.fromkeys(symbols):
    last_close = data.loc[(data["symbol"] == symbol) & (data["date"] == last_date), "close"].item()
    closes = np.stack([p["close"].to_numpy() for s, p in zip(symbols, preds) if s == symbol])  # (SAMPLES, PRED_LEN)
    for i, day in enumerate(future):
        rows.append(
            {
                "symbol": symbol,
                "date": day.date(),
                "step": i + 1,
                "last_date": last_date.date(),
                "last_close": round(last_close, 4),
                "pred_close": round(closes[:, i].mean(), 4),
                "pred_close_p10": round(np.percentile(closes[:, i], 10), 4),
                "pred_close_p90": round(np.percentile(closes[:, i], 90), 4),
                "pred_return": round(closes[:, i].mean() / last_close - 1, 6),
                "prob_up": round((closes[:, i] > last_close).mean(), 3),
            }
        )

forecast = pd.DataFrame(rows)
forecast.to_csv(FUND / "forecast.csv", index=False)

day5 = forecast[forecast["step"] == PRED_LEN].set_index("symbol")
print(f"last close date: {last_date.date()} | forecast dates: {future.iloc[0].date()} ~ {future.iloc[-1].date()} | samples: {SAMPLES}")
print(day5[["last_close", "pred_close", "pred_close_p10", "pred_close_p90", "pred_return", "prob_up"]].to_string())
print(f"saved: {FUND / 'forecast.csv'}")
