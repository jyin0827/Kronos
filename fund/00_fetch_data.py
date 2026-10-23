"""포트폴리오 종목의 일봉 OHLCV를 yfinance로 받아 data/ohlcv_daily.csv에 저장한다.

vibe 가상환경(yfinance 설치됨)에서 실행. 한글 사용자 경로 때문에 CURL_CA_BUNDLE이 필요하다:
    set CURL_CA_BUNDLE=C:\\Users\\Public\\vibe-trading\\cacert.pem
    C:\\Users\\정영인\\.venvs\\vibe\\Scripts\\python.exe fund\\00_fetch_data.py
"""

from pathlib import Path

import pandas as pd
import yfinance as yf

TICKERS = ["VLO", "DHT", "RIG", "LEN", "CRS"]
START = "2019-01-01"
OUT = Path(__file__).resolve().parent / "data" / "ohlcv_daily.csv"

raw = yf.download(TICKERS, start=START, auto_adjust=True, progress=False, group_by="ticker")

frames = []
for t in TICKERS:
    df = raw[t].dropna(subset=["Close"]).rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df["amount"] = df["close"] * df["volume"]
    df.insert(0, "symbol", t)
    frames.append(df)
data = pd.concat(frames).rename_axis("date").reset_index()
data["date"] = pd.to_datetime(data["date"]).dt.date

# 미국 장중이면 오늘 봉은 아직 확정되지 않았으므로 뺀다
now_et = pd.Timestamp.now(tz="America/New_York")
if now_et.hour < 16:
    data = data[data["date"] < now_et.date()]

OUT.parent.mkdir(parents=True, exist_ok=True)
data.sort_values(["symbol", "date"]).to_csv(OUT, index=False)

summary = data.groupby("symbol")["date"].agg(["min", "max", "count"])
print(summary.to_string())
print(f"saved: {OUT}")
