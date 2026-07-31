#!/usr/bin/env python3
"""Reproducible SOXL/TQQQ trend and volatility-managed backtest.

Data source: Yahoo Finance chart endpoint (adjusted close).
Signals are observed at the close and applied to the next trading day's return.
Results are before tax and include a configurable one-way transaction cost.
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs" / "soxl_tqqq_backtest_20260731"
DATA_DIR = OUTPUT_DIR / "data"
RESULTS_DIR = OUTPUT_DIR / "results"

TICKERS = ["TQQQ", "SOXL", "QQQ", "SOXX", "BIL"]
ETF_TO_SIGNAL = {"TQQQ": "QQQ", "SOXL": "SOXX"}
ONE_WAY_COST = 0.0005
TARGET_VOL = 0.25
VOL_WINDOW = 20
SMA_FAST = 50
SMA_SLOW = 200
TRADING_DAYS = 252


def fetch_yahoo_prices(ticker: str) -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache = DATA_DIR / f"{ticker}.json"
    if not cache.exists():
        params = urllib.parse.urlencode(
            {
                "period1": 946684800,  # 2000-01-01 UTC
                "period2": int(time.time()) + 86400,
                "interval": "1d",
                "events": "div,splits",
                "includeAdjustedClose": "true",
            }
        )
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            cache.write_bytes(response.read())

    payload = json.loads(cache.read_text())
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    adjclose = result["indicators"]["adjclose"][0]["adjclose"]
    close = result["indicators"]["quote"][0]["close"]
    idx = pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(None).normalize()
    frame = pd.DataFrame(
        {"adjusted_close": adjclose, "close": close},
        index=idx,
        dtype="float64",
    ).dropna()
    return frame[~frame.index.duplicated(keep="last")]


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1.0).min())


def metrics(returns: pd.Series, position: pd.Series, cash_returns: pd.Series) -> dict:
    r = returns.dropna()
    pos = position.reindex(r.index).fillna(0.0)
    cash = cash_returns.reindex(r.index).fillna(0.0)
    years = len(r) / TRADING_DAYS
    equity = (1.0 + r).cumprod()
    total_return = float(equity.iloc[-1] - 1.0)
    cagr = float(equity.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else np.nan
    ann_vol = float(r.std(ddof=1) * math.sqrt(TRADING_DAYS))
    excess = r - cash
    sharpe = float(excess.mean() / r.std(ddof=1) * math.sqrt(TRADING_DAYS)) if r.std() else np.nan
    mdd = max_drawdown(equity)
    calmar = float(cagr / abs(mdd)) if mdd else np.nan
    turnover = float(pos.diff().abs().fillna(pos.abs()).sum() / years) if years else np.nan
    entries = int(((pos > 0) & (pos.shift(1).fillna(0) <= 0)).sum())
    return {
        "start": r.index.min().strftime("%Y-%m-%d"),
        "end": r.index.max().strftime("%Y-%m-%d"),
        "days": int(len(r)),
        "total_return": total_return,
        "cagr": cagr,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "calmar": calmar,
        "worst_day": float(r.min()),
        "time_in_market": float(pos.mean()),
        "annual_turnover": turnover,
        "entries": entries,
        "final_10000": float(10000.0 * equity.iloc[-1]),
    }


def build_sleeve(
    etf_price: pd.Series,
    signal_price: pd.Series,
    cash_return: pd.Series,
    sma_slow: int = SMA_SLOW,
    target_vol: float = TARGET_VOL,
) -> dict[str, pd.DataFrame]:
    frame = pd.concat(
        [
            etf_price.rename("etf_price"),
            signal_price.rename("signal_price"),
            cash_return.rename("cash_return"),
        ],
        axis=1,
    ).sort_index()
    frame["etf_return"] = frame["etf_price"].pct_change()
    frame["sma_fast"] = frame["signal_price"].rolling(SMA_FAST, min_periods=SMA_FAST).mean()
    frame["sma_slow"] = frame["signal_price"].rolling(sma_slow, min_periods=sma_slow).mean()
    frame["etf_vol"] = (
        frame["etf_return"].rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std(ddof=1)
        * math.sqrt(TRADING_DAYS)
    )

    signal = {
        "Buy & Hold": pd.Series(1.0, index=frame.index),
        f"SMA{sma_slow}": (frame["signal_price"] > frame["sma_slow"]).astype(float),
        f"SMA{SMA_FAST}/{sma_slow}": (
            (frame["signal_price"] > frame["sma_slow"]) & (frame["sma_fast"] > frame["sma_slow"])
        ).astype(float),
        f"Trend + {target_vol:.0%} Vol": (
            (frame["signal_price"] > frame["sma_slow"]).astype(float)
            * (target_vol / frame["etf_vol"]).clip(lower=0.0, upper=1.0)
        ),
    }

    outputs = {}
    first_etf_date = etf_price.first_valid_index()
    for name, close_position in signal.items():
        # Signal at close t becomes the position earning return t+1.
        held_position = close_position.shift(1).fillna(0.0)
        gross = held_position * frame["etf_return"] + (1.0 - held_position) * frame["cash_return"]
        cost = held_position.diff().abs().fillna(held_position.abs()) * ONE_WAY_COST
        net = gross - cost
        out = frame.copy()
        out["close_signal_position"] = close_position
        out["held_position"] = held_position
        out["turnover"] = held_position.diff().abs().fillna(held_position.abs())
        out["strategy_return"] = net
        outputs[name] = out.loc[first_etf_date:].dropna(subset=["etf_return", "strategy_return"])
    return outputs


def slice_period(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    if period == "Full":
        return frame
    if period == "In-sample 2010-2018":
        return frame.loc[: "2018-12-31"]
    if period == "OOS 2019+":
        return frame.loc["2019-01-01":]
    if period == "COVID bull 2019-2021":
        return frame.loc["2019-01-01":"2021-12-31"]
    if period == "2022 bear":
        return frame.loc["2022-01-01":"2022-12-31"]
    if period == "Recent 2023+":
        return frame.loc["2023-01-01":]
    raise ValueError(period)


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    downloaded = {t: fetch_yahoo_prices(t) for t in TICKERS}
    prices = pd.concat(
        {t: downloaded[t]["adjusted_close"] for t in TICKERS}, axis=1
    )
    prices.columns = prices.columns.get_level_values(0)
    signal_prices = pd.concat({t: downloaded[t]["close"] for t in TICKERS}, axis=1)
    signal_prices.columns = signal_prices.columns.get_level_values(0)
    prices.to_csv(RESULTS_DIR / "adjusted_close.csv", index_label="Date")
    signal_prices.to_csv(RESULTS_DIR / "unadjusted_close.csv", index_label="Date")

    cash_return = prices["BIL"].pct_change().fillna(0.0).clip(lower=-0.01, upper=0.01)
    sleeves = {
        etf: build_sleeve(prices[etf], signal_prices[signal_ticker], cash_return)
        for etf, signal_ticker in ETF_TO_SIGNAL.items()
    }

    periods = [
        "Full",
        "In-sample 2010-2018",
        "OOS 2019+",
        "COVID bull 2019-2021",
        "2022 bear",
        "Recent 2023+",
    ]
    records = []
    equity_export = []
    daily_export = []
    for etf, strategies in sleeves.items():
        for strategy, frame in strategies.items():
            full_equity = (1 + frame["strategy_return"]).cumprod()
            equity_export.append(full_equity.rename(f"{etf} | {strategy}"))
            daily = frame[
                [
                    "etf_price",
                    "signal_price",
                    "sma_fast",
                    "sma_slow",
                    "etf_vol",
                    "close_signal_position",
                    "held_position",
                    "turnover",
                    "strategy_return",
                ]
            ].copy()
            daily.insert(0, "Strategy", strategy)
            daily.insert(0, "ETF", etf)
            daily_export.append(daily.reset_index(names="Date"))
            for period in periods:
                sample = slice_period(frame, period)
                if len(sample) < 2:
                    continue
                row = metrics(
                    sample["strategy_return"],
                    sample["held_position"],
                    sample["cash_return"],
                )
                row.update({"etf": etf, "strategy": strategy, "period": period})
                records.append(row)

    # Equal-weight portfolio of both sleeves, rebalanced daily.
    common_start = max(prices["TQQQ"].first_valid_index(), prices["SOXL"].first_valid_index())
    for strategy in sleeves["TQQQ"]:
        left = sleeves["TQQQ"][strategy].loc[common_start:]
        right = sleeves["SOXL"][strategy].loc[common_start:]
        joined = pd.concat(
            {
                "t_return": left["strategy_return"],
                "s_return": right["strategy_return"],
                "t_pos": left["held_position"],
                "s_pos": right["held_position"],
                "cash": cash_return,
            },
            axis=1,
        ).dropna()
        joined["strategy_return"] = 0.5 * joined["t_return"] + 0.5 * joined["s_return"]
        joined["held_position"] = 0.5 * joined["t_pos"] + 0.5 * joined["s_pos"]
        equity_export.append(
            (1 + joined["strategy_return"]).cumprod().rename(f"50/50 | {strategy}")
        )
        for period in periods:
            sample = slice_period(joined, period)
            if len(sample) < 2:
                continue
            row = metrics(sample["strategy_return"], sample["held_position"], sample["cash"])
            row.update({"etf": "50/50 Portfolio", "strategy": strategy, "period": period})
            records.append(row)

    results = pd.DataFrame(records)
    results = results[
        [
            "etf",
            "strategy",
            "period",
            "start",
            "end",
            "days",
            "total_return",
            "cagr",
            "ann_vol",
            "sharpe",
            "max_drawdown",
            "calmar",
            "worst_day",
            "time_in_market",
            "annual_turnover",
            "entries",
            "final_10000",
        ]
    ]
    results.to_csv(RESULTS_DIR / "summary_metrics.csv", index=False)
    pd.concat(equity_export, axis=1).to_csv(RESULTS_DIR / "equity_curves.csv", index_label="Date")
    pd.concat(daily_export, ignore_index=True).to_csv(RESULTS_DIR / "daily_detail.csv", index=False)

    robustness = []
    for etf, signal_ticker in ETF_TO_SIGNAL.items():
        for slow in [150, 175, 200, 225, 250]:
            tested = build_sleeve(
                prices[etf], signal_prices[signal_ticker], cash_return, sma_slow=slow
            )[f"SMA{slow}"]
            sample = tested.loc["2019-01-01":]
            row = metrics(sample["strategy_return"], sample["held_position"], sample["cash_return"])
            robustness.append({"etf": etf, "test": "SMA window", "parameter": slow, **row})
        for target in [0.15, 0.20, 0.25, 0.30, 0.35]:
            tested = build_sleeve(
                prices[etf], signal_prices[signal_ticker], cash_return, target_vol=target
            )[f"Trend + {target:.0%} Vol"]
            sample = tested.loc["2019-01-01":]
            row = metrics(sample["strategy_return"], sample["held_position"], sample["cash_return"])
            robustness.append(
                {"etf": etf, "test": "Target volatility", "parameter": target, **row}
            )
    pd.DataFrame(robustness).to_csv(RESULTS_DIR / "robustness_oos.csv", index=False)

    metadata = {
        "generated_at": pd.Timestamp.now(tz="Asia/Tokyo").isoformat(),
        "last_price_date": {
            t: prices[t].dropna().index.max().strftime("%Y-%m-%d") for t in TICKERS
        },
        "assumptions": {
            "one_way_transaction_cost": ONE_WAY_COST,
            "cash_proxy": "BIL adjusted close total return",
            "target_volatility": TARGET_VOL,
            "volatility_window_days": VOL_WINDOW,
            "fast_sma_days": SMA_FAST,
            "slow_sma_days": SMA_SLOW,
            "annualization_days": TRADING_DAYS,
            "tax": "not included",
            "slippage_beyond_transaction_cost": "not included",
        },
        "data_source": "Yahoo Finance chart API, adjusted close",
    }
    (RESULTS_DIR / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2)
    )

    full = results[results["period"] == "Full"].sort_values(
        ["etf", "calmar"], ascending=[True, False]
    )
    print(full.to_string(index=False))
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
