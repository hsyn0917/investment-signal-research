#!/usr/bin/env python3
"""Generate compact market and portfolio-risk history for the public dashboard."""

from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from signal_system.signal_engine import (
    PricePoint,
    annualized_volatility,
    fetch_prices,
    simple_moving_average,
)


ROOT = Path(__file__).resolve().parent
TOKYO = ZoneInfo("Asia/Tokyo")
US_TICKERS = ("TQQQ", "SOXL", "QQQ", "SOXX")
ALL_TICKERS = US_TICKERS + ("SPXL", "^N225", "^GSPC", "JPY=X")


def _as_lookup(points: list[PricePoint]) -> tuple[list[date], dict[date, PricePoint]]:
    return [point.day for point in points], {point.day: point for point in points}


def _latest_on_or_before(
    days: list[date], lookup: dict[date, PricePoint], target: date
) -> tuple[date, PricePoint] | None:
    index = bisect.bisect_right(days, target) - 1
    if index < 0:
        return None
    day = days[index]
    return day, lookup[day]


def build_market_history(
    series: dict[str, list[PricePoint]],
    config: dict,
    *,
    observations: int = 180,
    generated_at: datetime | None = None,
) -> dict:
    missing = sorted(set(ALL_TICKERS) - set(series))
    if missing:
        raise ValueError(f"履歴データが不足しています: {', '.join(missing)}")

    target_vol = float(config["target_volatility"])
    vol_window = int(config["volatility_window"])
    trend_window = int(config["trend_window"])
    japan_sleeve = float(config["japan_sleeve"])
    us_sleeve = float(config["us_letf_sleeve"])
    if not math.isclose(japan_sleeve + us_sleeve, 1.0, abs_tol=1e-9):
        raise ValueError("資本枠の合計は1.0である必要があります")

    dates_by_ticker: dict[str, list[date]] = {}
    lookup_by_ticker: dict[str, dict[date, PricePoint]] = {}
    index_by_ticker: dict[str, dict[date, int]] = {}
    adjusted_by_ticker: dict[str, list[float]] = {}
    close_by_ticker: dict[str, list[float]] = {}
    for ticker, points in series.items():
        days, lookup = _as_lookup(points)
        dates_by_ticker[ticker] = days
        lookup_by_ticker[ticker] = lookup
        index_by_ticker[ticker] = {day: index for index, day in enumerate(days)}
        adjusted_by_ticker[ticker] = [point.adjusted_close for point in points]
        close_by_ticker[ticker] = [point.close for point in points]

    common_us_dates = sorted(
        set.intersection(*(set(dates_by_ticker[ticker]) for ticker in US_TICKERS))
    )
    raw_rows: list[dict] = []
    for day in common_us_dates:
        us_targets: dict[str, float] = {}
        valid = True
        for etf, underlying in (("TQQQ", "QQQ"), ("SOXL", "SOXX")):
            underlying_index = index_by_ticker[underlying][day]
            etf_index = index_by_ticker[etf][day]
            if underlying_index < trend_window - 1 or etf_index < vol_window:
                valid = False
                break
            sma = simple_moving_average(
                close_by_ticker[underlying], underlying_index, trend_window
            )
            trend_on = lookup_by_ticker[underlying][day].close >= sma
            volatility = annualized_volatility(
                adjusted_by_ticker[etf], etf_index, vol_window
            )
            us_targets[etf] = (
                0.5 * min(1.0, target_vol / volatility) if trend_on else 0.0
            )
        if not valid:
            continue

        n225_latest = _latest_on_or_before(
            dates_by_ticker["^N225"], lookup_by_ticker["^N225"], day
        )
        fx_latest = _latest_on_or_before(
            dates_by_ticker["JPY=X"], lookup_by_ticker["JPY=X"], day
        )
        sp500_latest = _latest_on_or_before(
            dates_by_ticker["^GSPC"], lookup_by_ticker["^GSPC"], day
        )
        spxl_latest = _latest_on_or_before(
            dates_by_ticker["SPXL"], lookup_by_ticker["SPXL"], day
        )
        if (
            n225_latest is None
            or fx_latest is None
            or sp500_latest is None
            or spxl_latest is None
        ):
            continue
        n225_day, n225_point = n225_latest
        n225_index = index_by_ticker["^N225"][n225_day]
        if n225_index < 59:
            continue
        n225_ma60 = simple_moving_average(
            close_by_ticker["^N225"], n225_index, 60
        )
        japan_exposure = 1.0 if n225_point.close >= n225_ma60 else 0.5
        japan_risk = japan_sleeve * japan_exposure
        us_risk = us_sleeve * sum(us_targets.values())
        raw_rows.append(
            {
                "date": day.isoformat(),
                "risk_asset_weight": japan_risk + us_risk,
                "japan_risk_contribution": japan_risk,
                "us_risk_contribution": us_risk,
                "n225_close": n225_point.close,
                "sp500_close": sp500_latest[1].close,
                "tqqq_close": lookup_by_ticker["TQQQ"][day].close,
                "spxl_close": spxl_latest[1].close,
                "usd_jpy": fx_latest[1].close,
            }
        )

    rows = raw_rows[-observations:]
    if len(rows) < min(30, observations):
        raise RuntimeError("グラフ作成に必要な履歴が不足しています")
    output_rows = []
    for row in rows:
        output_rows.append(
            {
                "date": row["date"],
                "risk_asset_weight": round(row["risk_asset_weight"], 8),
                "japan_risk_contribution": round(
                    row["japan_risk_contribution"], 8
                ),
                "us_risk_contribution": round(row["us_risk_contribution"], 8),
                "n225_close": round(row["n225_close"], 4),
                "sp500_close": round(row["sp500_close"], 4),
                "tqqq_close": round(row["tqqq_close"], 4),
                "spxl_close": round(row["spxl_close"], 4),
                "usd_jpy": round(row["usd_jpy"], 4),
            }
        )
    generated_at = generated_at or datetime.now(TOKYO)
    return {
        "schema_version": 1,
        "generated_at_jst": generated_at.isoformat(timespec="seconds"),
        "observation_count": len(output_rows),
        "method": {
            "risk_allocation": "日本株60日レジーム + 米国200日トレンド・20日ボラ調整",
            "market_prices": "各資産の終値（絶対値）",
            "fx": "USD/JPY終値",
        },
        "points": output_rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="公開グラフ用の市場履歴を生成")
    parser.add_argument(
        "--portfolio-config", type=Path, default=ROOT / "portfolio_config.json"
    )
    parser.add_argument(
        "--us-config", type=Path, default=ROOT / "signal_system/config.json"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "combined_signals/market_history.json",
    )
    parser.add_argument("--observations", type=int, default=180)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = json.loads(args.portfolio_config.read_text(encoding="utf-8"))
        config.update(json.loads(args.us_config.read_text(encoding="utf-8")))
        series = {ticker: fetch_prices(ticker, 760) for ticker in ALL_TICKERS}
        payload = build_market_history(
            series, config, observations=args.observations
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"Market history: {payload['observation_count']} observations -> "
            f"{args.output}"
        )
        return 0
    except Exception as exc:
        print(f"MARKET HISTORY GENERATION FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
