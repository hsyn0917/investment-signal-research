#!/usr/bin/env python3
"""Daily TQQQ/SOXL signal generator.

This tool produces target allocations and human-readable instructions only.
It never connects to a broker and never submits orders.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


ETF_SIGNAL_MAP = {"TQQQ": "QQQ", "SOXL": "SOXX"}
ETF_SLEEVE_KEYS = {"TQQQ": "tqqq_sleeve", "SOXL": "soxl_sleeve"}
REQUIRED_TICKERS = ("TQQQ", "SOXL", "QQQ", "SOXX", "BIL")
TRADING_DAYS = 252
TOKYO = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class PricePoint:
    day: date
    close: float
    adjusted_close: float


@dataclass(frozen=True)
class SleeveSignal:
    etf: str
    underlying: str
    data_date: str
    underlying_close: float
    sma: float
    trend_on: bool
    annualized_volatility: float
    sleeve_budget: float
    within_sleeve_weight: float
    target_portfolio_weight: float
    previous_target_weight: float
    change_percentage_points: float
    instruction: str


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "target_volatility",
        "volatility_window",
        "trend_window",
        "tqqq_sleeve",
        "soxl_sleeve",
        "transaction_cost_bps",
        "rebalance_threshold_percentage_points",
        "cash_proxy",
        "max_data_age_calendar_days",
    }
    missing = sorted(required - set(config))
    if missing:
        raise ValueError(f"設定が不足しています: {', '.join(missing)}")
    sleeve_total = float(config["tqqq_sleeve"]) + float(config["soxl_sleeve"])
    if not math.isclose(sleeve_total, 1.0, abs_tol=1e-9):
        raise ValueError("TQQQ袖とSOXL袖の合計は1.0である必要があります")
    if not 0 < float(config["target_volatility"]) <= 1.0:
        raise ValueError("target_volatilityは0超1以下で指定してください")
    if int(config["trend_window"]) < 50 or int(config["volatility_window"]) < 5:
        raise ValueError("移動平均またはボラティリティ期間が短すぎます")
    return config


def _parse_yahoo_payload(payload: dict, ticker: str) -> list[PricePoint]:
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"{ticker}: Yahoo error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"{ticker}: 価格データが空です")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = result.get("indicators", {}).get("quote", [{}])[0]
    adjusted = result.get("indicators", {}).get("adjclose", [{}])[0]
    closes = quote.get("close") or []
    adjusted_closes = adjusted.get("adjclose") or []
    points: list[PricePoint] = []
    for timestamp, close, adjusted_close in zip(timestamps, closes, adjusted_closes):
        if close is None or adjusted_close is None:
            continue
        day = datetime.fromtimestamp(timestamp, timezone.utc).date()
        points.append(
            PricePoint(
                day=day,
                close=float(close),
                adjusted_close=float(adjusted_close),
            )
        )
    deduplicated = {point.day: point for point in points}
    output = [deduplicated[day] for day in sorted(deduplicated)]
    if not output:
        raise RuntimeError(f"{ticker}: 有効な価格データがありません")
    return output


def fetch_prices(ticker: str, lookback_days: int = 520) -> list[PricePoint]:
    now = datetime.now(timezone.utc)
    period1 = int((now - timedelta(days=lookback_days)).timestamp())
    period2 = int((now + timedelta(days=1)).timestamp())
    params = urllib.parse.urlencode(
        {
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "true",
        }
    )
    errors: list[str] = []
    for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{ticker}?{params}"
        for attempt in range(3):
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 LETF-Signal/1.0",
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return _parse_yahoo_payload(
                        json.loads(response.read().decode("utf-8")), ticker
                    )
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                errors.append(f"{host} attempt {attempt + 1}: {exc}")
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{ticker}: 価格取得に失敗しました: {'; '.join(errors)}")


def simple_moving_average(values: list[float], end_index: int, window: int) -> float:
    start = end_index - window + 1
    if start < 0:
        raise ValueError("移動平均の計算に必要な履歴が不足しています")
    return statistics.fmean(values[start : end_index + 1])


def daily_returns(adjusted_closes: list[float]) -> list[float]:
    returns: list[float] = []
    for previous, current in zip(adjusted_closes, adjusted_closes[1:]):
        if previous <= 0:
            raise ValueError("価格は正である必要があります")
        value = current / previous - 1.0
        if abs(value) > 0.65:
            raise ValueError(f"異常な日次リターンを検出しました: {value:.1%}")
        returns.append(value)
    return returns


def annualized_volatility(
    adjusted_closes: list[float], end_index: int, window: int
) -> float:
    start_price_index = end_index - window
    if start_price_index < 0:
        raise ValueError("ボラティリティ計算に必要な履歴が不足しています")
    returns = daily_returns(adjusted_closes[start_price_index : end_index + 1])
    if len(returns) != window:
        raise ValueError("ボラティリティ計算期間が一致しません")
    volatility = statistics.stdev(returns) * math.sqrt(TRADING_DAYS)
    if not 0.01 <= volatility <= 3.0:
        raise ValueError(f"実現ボラティリティが想定範囲外です: {volatility:.1%}")
    return volatility


def target_weight(
    trend_on: bool, volatility: float, target_vol: float, sleeve_budget: float
) -> tuple[float, float]:
    if not trend_on:
        return 0.0, 0.0
    within_sleeve = min(1.0, target_vol / volatility)
    return within_sleeve, sleeve_budget * within_sleeve


def instruction_for_change(
    current: float, previous: float, threshold_percentage_points: float
) -> str:
    threshold = threshold_percentage_points / 100.0
    change = current - previous
    if current == 0 and previous > 0:
        return "EXIT_TO_CASH"
    if current > 0 and previous == 0:
        return "BUY"
    if change >= threshold:
        return "INCREASE"
    if change <= -threshold:
        return "REDUCE"
    return "HOLD"


def align_to_common_dates(
    series: dict[str, list[PricePoint]]
) -> tuple[list[date], dict[str, dict[date, PricePoint]]]:
    by_ticker = {
        ticker: {point.day: point for point in points}
        for ticker, points in series.items()
    }
    common_dates = sorted(
        set.intersection(*(set(points) for points in by_ticker.values()))
    )
    if len(common_dates) < 205:
        raise RuntimeError("共通営業日の履歴が不足しています")
    return common_dates, by_ticker


def compute_sleeve_signal(
    etf: str,
    common_dates: list[date],
    by_ticker: dict[str, dict[date, PricePoint]],
    config: dict,
    offset: int,
) -> tuple[bool, float, float, float, float, float]:
    underlying = ETF_SIGNAL_MAP[etf]
    end = len(common_dates) - 1 - offset
    trend_window = int(config["trend_window"])
    vol_window = int(config["volatility_window"])
    underlying_closes = [
        by_ticker[underlying][day].close for day in common_dates
    ]
    etf_adjusted = [
        by_ticker[etf][day].adjusted_close for day in common_dates
    ]
    close = underlying_closes[end]
    sma = simple_moving_average(underlying_closes, end, trend_window)
    trend_on = close > sma
    volatility = annualized_volatility(etf_adjusted, end, vol_window)
    sleeve_budget = float(config[ETF_SLEEVE_KEYS[etf]])
    within_sleeve, portfolio_weight = target_weight(
        trend_on,
        volatility,
        float(config["target_volatility"]),
        sleeve_budget,
    )
    return trend_on, close, sma, volatility, within_sleeve, portfolio_weight


def build_signal(config: dict, now: datetime | None = None) -> dict:
    generated_at = now or datetime.now(TOKYO)
    downloaded = {ticker: fetch_prices(ticker) for ticker in REQUIRED_TICKERS}
    common_dates, by_ticker = align_to_common_dates(downloaded)
    latest_day = common_dates[-1]
    age_days = (generated_at.date() - latest_day).days
    warnings: list[str] = []
    if age_days < 0:
        raise RuntimeError("価格日が生成日より未来です")
    if age_days > int(config["max_data_age_calendar_days"]):
        raise RuntimeError(
            f"価格データが古すぎます: 最終日 {latest_day.isoformat()}、{age_days}日前"
        )
    if age_days >= 2:
        warnings.append(
            f"最終価格は{age_days}日前です。週末・米国休場日か確認してください。"
        )

    sleeves: list[SleeveSignal] = []
    for etf, underlying in ETF_SIGNAL_MAP.items():
        current = compute_sleeve_signal(
            etf, common_dates, by_ticker, config, offset=0
        )
        previous = compute_sleeve_signal(
            etf, common_dates, by_ticker, config, offset=1
        )
        current_target = current[5]
        previous_target = previous[5]
        sleeves.append(
            SleeveSignal(
                etf=etf,
                underlying=underlying,
                data_date=latest_day.isoformat(),
                underlying_close=current[1],
                sma=current[2],
                trend_on=current[0],
                annualized_volatility=current[3],
                sleeve_budget=float(config[ETF_SLEEVE_KEYS[etf]]),
                within_sleeve_weight=current[4],
                target_portfolio_weight=current_target,
                previous_target_weight=previous_target,
                change_percentage_points=(current_target - previous_target) * 100,
                instruction=instruction_for_change(
                    current_target,
                    previous_target,
                    float(config["rebalance_threshold_percentage_points"]),
                ),
            )
        )

    risky_weight = sum(sleeve.target_portfolio_weight for sleeve in sleeves)
    cash_weight = max(0.0, 1.0 - risky_weight)
    if risky_weight > 1.0000001:
        raise RuntimeError("目標リスク資産比率が100%を超えました")
    overall = (
        "RISK_OFF"
        if risky_weight == 0
        else "RISK_ON"
        if all(sleeve.trend_on for sleeve in sleeves)
        else "MIXED"
    )
    return {
        "schema_version": 1,
        "generated_at_jst": generated_at.isoformat(),
        "data_date": latest_day.isoformat(),
        "data_age_calendar_days": age_days,
        "status": "OK" if not warnings else "WARNING",
        "overall_regime": overall,
        "target_weights": {
            **{
                sleeve.etf: round(sleeve.target_portfolio_weight, 8)
                for sleeve in sleeves
            },
            str(config["cash_proxy"]): round(cash_weight, 8),
        },
        "sleeves": [asdict(sleeve) for sleeve in sleeves],
        "warnings": warnings,
        "assumptions": {
            "target_volatility": config["target_volatility"],
            "volatility_window": config["volatility_window"],
            "trend_window": config["trend_window"],
            "rebalance_threshold_percentage_points": config[
                "rebalance_threshold_percentage_points"
            ],
            "transaction_cost_bps_backtest_only": config["transaction_cost_bps"],
        },
        "execution_policy": "SIGNAL_ONLY_NO_BROKER_ORDERS",
        "data_source": "Yahoo Finance chart API",
    }


INSTRUCTION_JA = {
    "BUY": "新規買い",
    "INCREASE": "比率を増やす",
    "REDUCE": "比率を減らす",
    "EXIT_TO_CASH": "売却して現金へ",
    "HOLD": "維持",
}


def render_markdown(signal: dict) -> str:
    lines = [
        "# TQQQ / SOXL 日次シグナル",
        "",
        f"- 判定状態: **{signal['status']}**",
        f"- 市場レジーム: **{signal['overall_regime']}**",
        f"- 価格基準日: **{signal['data_date']}**",
        f"- 生成日時: {signal['generated_at_jst']}",
        "",
        "## 売買指示（目標配分）",
        "",
        "| 資産 | 指示 | 目標比率 | 前日目標 | 変化 | トレンド | 20日年率ボラ |",
        "|---|---|---:|---:|---:|---|---:|",
    ]
    for sleeve in signal["sleeves"]:
        lines.append(
            "| {etf} | **{instruction}** | {target:.1%} | {previous:.1%} | "
            "{change:+.1f}pt | {trend} | {vol:.1%} |".format(
                etf=sleeve["etf"],
                instruction=INSTRUCTION_JA[sleeve["instruction"]],
                target=sleeve["target_portfolio_weight"],
                previous=sleeve["previous_target_weight"],
                change=sleeve["change_percentage_points"],
                trend="ON" if sleeve["trend_on"] else "OFF",
                vol=sleeve["annualized_volatility"],
            )
        )
    cash_proxy = next(
        key
        for key in signal["target_weights"]
        if key not in ETF_SIGNAL_MAP
    )
    lines.append(
        f"| {cash_proxy} | 待機資金 | {signal['target_weights'][cash_proxy]:.1%} | — | — | — | — |"
    )
    lines.extend(
        [
            "",
            "## 判定根拠",
            "",
        ]
    )
    for sleeve in signal["sleeves"]:
        relation = "上" if sleeve["trend_on"] else "下"
        lines.append(
            f"- {sleeve['etf']}: {sleeve['underlying']}終値 "
            f"{sleeve['underlying_close']:.2f} は200日線 "
            f"{sleeve['sma']:.2f} の{relation}。"
        )
    if signal["warnings"]:
        lines.extend(["", "## 警告", ""])
        lines.extend(f"- {warning}" for warning in signal["warnings"])
    lines.extend(
        [
            "",
            "---",
            "",
            "**重要:** これはバックテスト規則に基づく機械的シグナルであり、"
            "投資助言・収益保証・発注命令ではありません。実際の保有残高、税金、"
            "為替、スプレッド、寄付ギャップを確認してから人間が最終判断してください。",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(signal: dict, output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "latest_signal.json"
    markdown_path = output_dir / "latest_signal.md"
    csv_path = output_dir / "latest_signal.csv"
    json_path.write_text(
        json.dumps(signal, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(signal), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "data_date",
                "asset",
                "instruction",
                "target_weight",
                "previous_target_weight",
                "change_percentage_points",
                "trend_on",
                "annualized_volatility",
            ]
        )
        for sleeve in signal["sleeves"]:
            writer.writerow(
                [
                    signal["data_date"],
                    sleeve["etf"],
                    sleeve["instruction"],
                    sleeve["target_portfolio_weight"],
                    sleeve["previous_target_weight"],
                    sleeve["change_percentage_points"],
                    sleeve["trend_on"],
                    sleeve["annualized_volatility"],
                ]
            )
    return json_path, markdown_path, csv_path


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="TQQQ/SOXLの目標配分シグナルを生成します（発注はしません）"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=base / "config.json",
        help="設定JSON",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=base / "output",
        help="出力ディレクトリ",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        signal = build_signal(config)
        _, markdown_path, _ = write_outputs(signal, args.output_dir)
        print(markdown_path.read_text(encoding="utf-8"))
        return 0
    except Exception as exc:
        print(f"SIGNAL GENERATION FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
