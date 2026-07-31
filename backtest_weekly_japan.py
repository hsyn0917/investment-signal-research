#!/usr/bin/env python3
"""Weekly Japanese-equity strategy research using public Yahoo chart data.

The implementation deliberately uses only information known before each
rebalance. Signals are observed at the prior close and positions are entered at
the next trading day's adjusted open.
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "results"
START = "2015-01-01"
END = "2026-08-01"
ONE_WAY_COST = 0.0015
MIN_ADV20 = 1_000_000_000  # JPY
N_HOLDINGS = 10
WIKI_URL = "https://en.wikipedia.org/wiki/Nikkei_225"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
UA = "Mozilla/5.0 (compatible; weekly-japan-research/1.0)"


def fetch_bytes(url: str, attempts: int = 5) -> bytes:
    error = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()
        except Exception as exc:  # network errors are retried and then reported
            error = exc
            time.sleep(1.0 + 1.5 * attempt)
    raise RuntimeError(f"Failed to fetch {url}: {error}")


def current_nikkei_codes() -> list[str]:
    DATA.mkdir(exist_ok=True)
    cache = DATA / "nikkei225_current.html"
    if not cache.exists():
        cache.write_bytes(fetch_bytes(WIKI_URL))
    text = cache.read_text(encoding="utf-8")
    codes = sorted(set(re.findall(r"topSearchStr=(\d{4})", text)))
    if len(codes) < 200:
        raise RuntimeError(f"Only found {len(codes)} Nikkei codes")
    return codes


def epoch(date: str) -> int:
    return int(pd.Timestamp(date, tz="UTC").timestamp())


def download_ticker(ticker: str) -> tuple[str, pd.DataFrame | None, str | None]:
    DATA.mkdir(exist_ok=True)
    cache = DATA / f"{ticker.replace('^', 'INDEX_')}.csv"
    if cache.exists() and os.environ.get("REFRESH_DATA") != "1":
        frame = pd.read_csv(cache, parse_dates=["date"]).set_index("date")
        return ticker, frame, None
    url = (
        YAHOO_URL.format(ticker=ticker)
        + f"?period1={epoch(START)}&period2={epoch(END)}"
        + "&interval=1d&events=div%2Csplits"
    )
    try:
        payload = json.loads(fetch_bytes(url))
        result = payload["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        adj = result["indicators"].get("adjclose", [{}])[0].get("adjclose")
        idx = pd.to_datetime(result["timestamp"], unit="s", utc=True).tz_convert(
            "Asia/Tokyo"
        ).tz_localize(None).normalize()
        close = np.asarray(quote["close"], dtype=float)
        adjclose = np.asarray(adj if adj is not None else close, dtype=float)
        factor = np.divide(
            adjclose, close, out=np.ones_like(adjclose), where=np.isfinite(close)
        )
        frame = pd.DataFrame(
            {
                "open": np.asarray(quote["open"], dtype=float) * factor,
                "close": adjclose,
                "raw_close": close,
                "volume": np.asarray(quote["volume"], dtype=float),
            },
            index=idx,
        ).dropna(subset=["open", "close"])
        frame.index.name = "date"
        frame.to_csv(cache)
        return ticker, frame, None
    except Exception as exc:
        return ticker, None, str(exc)


def get_data(codes: list[str]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    tickers = [f"{code}.T" for code in codes] + ["^N225"]
    data: dict[str, pd.DataFrame] = {}
    failures = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(download_ticker, t): t for t in tickers}
        for future in as_completed(futures):
            ticker, frame, error = future.result()
            if frame is not None and len(frame) >= 260:
                data[ticker] = frame
            else:
                failures.append((ticker, error or "insufficient history"))
    pd.DataFrame(failures, columns=["ticker", "error"]).to_csv(
        OUT / "download_failures.csv", index=False
    )
    if "^N225" not in data:
        # 1306 is a liquid TOPIX ETF and serves as a fallback regime proxy.
        ticker, frame, error = download_ticker("1306.T")
        if frame is None:
            raise RuntimeError(f"No Japanese equity benchmark: {error}")
        data["^N225"] = frame
    benchmark = data.pop("^N225")
    return data, benchmark


def wide(data: dict[str, pd.DataFrame], column: str) -> pd.DataFrame:
    return pd.concat(
        {ticker: data[ticker][column] for ticker in sorted(data)}, axis=1
    )


def percentile(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True)


def build_features(data: dict[str, pd.DataFrame], benchmark: pd.DataFrame):
    close = wide(data, "close").sort_index()
    open_ = wide(data, "open").reindex(close.index)
    raw_close = wide(data, "raw_close").reindex(close.index)
    volume = wide(data, "volume").reindex(close.index)
    traded_value = raw_close * volume
    features = {
        "ret5": close.pct_change(5, fill_method=None),
        "ret20": close.pct_change(20, fill_method=None),
        "ret40": close.pct_change(40, fill_method=None),
        "ret60": close.pct_change(60, fill_method=None),
        "ret80": close.pct_change(80, fill_method=None),
        "ret120": close.pct_change(120, fill_method=None),
        "vol20": close.pct_change(fill_method=None).rolling(20).std(),
        "volume_surge": volume.rolling(5).mean() / volume.rolling(20).mean(),
        "adv20": traded_value.rolling(20).mean(),
        "ma20_gap": close / close.rolling(20).mean() - 1,
    }
    benchmark = benchmark.reindex(close.index).ffill()
    return close, open_, features, benchmark


def scores(features: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    ret5 = percentile(features["ret5"])
    ret20 = percentile(features["ret20"])
    ret40 = percentile(features["ret40"])
    ret60 = percentile(features["ret60"])
    ret80 = percentile(features["ret80"])
    ret120 = percentile(features["ret120"])
    vol20 = percentile(features["vol20"])
    vol_surge = percentile(features["volume_surge"].clip(upper=3))
    overheat = percentile(features["ma20_gap"].abs())
    return {
        "MOM20": ret20,
        "MOM40": ret40,
        "MOM60": ret60,
        "MOM80": ret80,
        "MOM120": ret120,
        "MOM_60_120": (ret60 + ret80 + ret120) / 3,
        "REV5": 1 - ret5,
        "BLEND": (
            0.40 * ret20
            + 0.30 * ret60
            + 0.15 * vol_surge
            + 0.15 * (1 - vol20)
            - 0.10 * overheat
        ),
    }


def capped_inverse_vol(vol: pd.Series, cap: float = 0.15) -> pd.Series:
    inv = 1 / vol.replace(0, np.nan)
    weights = inv / inv.sum()
    # Iteratively redistribute weight above the cap.
    for _ in range(10):
        above = weights > cap
        if not above.any():
            break
        excess = (weights[above] - cap).sum()
        weights[above] = cap
        below = ~above
        if weights[below].sum() > 0:
            weights[below] += excess * weights[below] / weights[below].sum()
    return weights / weights.sum()


def run_strategy(
    name: str,
    score: pd.DataFrame,
    open_: pd.DataFrame,
    features: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    regime: bool,
    n_holdings: int = N_HOLDINGS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    # Every first trading session of an ISO week is a rebalance date.
    dates = pd.Series(open_.index, index=open_.index)
    iso = open_.index.isocalendar()
    rebalances = dates.groupby([iso.year, iso.week]).first().tolist()
    rebalances = [d for d in rebalances if d >= pd.Timestamp("2016-01-01")]
    records, holdings_records = [], []
    prev_weights = pd.Series(dtype=float)

    for current, nxt in zip(rebalances[:-1], rebalances[1:]):
        prior_idx = open_.index[open_.index < current]
        if len(prior_idx) == 0:
            continue
        signal_date = prior_idx[-1]
        eligible = (
            (features["adv20"].loc[signal_date] >= MIN_ADV20)
            & score.loc[signal_date].notna()
            & open_.loc[current].notna()
            & open_.loc[nxt].notna()
        )
        ranked = score.loc[signal_date, eligible].sort_values(ascending=False)
        selected = ranked.head(n_holdings).index
        weights = capped_inverse_vol(features["vol20"].loc[signal_date, selected])

        exposure = 1.0
        if regime:
            hist = benchmark.loc[:signal_date, "close"].dropna()
            if len(hist) < 60 or hist.iloc[-1] < hist.iloc[-60:].mean():
                exposure = 0.5
        weights *= exposure

        all_names = prev_weights.index.union(weights.index)
        turnover = (
            prev_weights.reindex(all_names, fill_value=0)
            - weights.reindex(all_names, fill_value=0)
        ).abs().sum()
        stock_returns = open_.loc[nxt, selected] / open_.loc[current, selected] - 1
        gross = float((weights * stock_returns).sum())
        cost = turnover * ONE_WAY_COST
        net = gross - cost
        records.append(
            {
                "date": current,
                "next_date": nxt,
                "strategy": name,
                "gross_return": gross,
                "cost": cost,
                "net_return": net,
                "turnover": turnover,
                "exposure": exposure,
                "n": len(selected),
            }
        )
        for ticker in selected:
            holdings_records.append(
                {
                    "date": current,
                    "strategy": name,
                    "ticker": ticker,
                    "score": score.loc[signal_date, ticker],
                    "weight": weights[ticker],
                }
            )
        prev_weights = weights
    return pd.DataFrame(records), pd.DataFrame(holdings_records)


def metrics(returns: pd.Series) -> dict[str, float]:
    returns = returns.dropna()
    if returns.empty:
        return {}
    equity = (1 + returns).cumprod()
    years = (returns.index.max() - returns.index.min()).days / 365.25
    cagr = equity.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    vol = returns.std(ddof=1) * math.sqrt(52)
    sharpe = returns.mean() / returns.std(ddof=1) * math.sqrt(52)
    drawdown = equity / equity.cummax() - 1
    return {
        "CAGR": cagr,
        "AnnualVol": vol,
        "Sharpe0": sharpe,
        "MaxDrawdown": drawdown.min(),
        "WinRate": (returns > 0).mean(),
        "TotalReturn": equity.iloc[-1] - 1,
        "Weeks": len(returns),
    }


def period_metrics(all_returns: pd.DataFrame) -> pd.DataFrame:
    rows = []
    periods = {
        "全期間 2016-2026": ("2016-01-01", "2026-12-31"),
        "前半 2016-2020": ("2016-01-01", "2020-12-31"),
        "後半 2021-2026": ("2021-01-01", "2026-12-31"),
    }
    for period, (start, end) in periods.items():
        sliced = all_returns.loc[start:end]
        for strategy in sliced.columns:
            row = {"period": period, "strategy": strategy}
            row.update(metrics(sliced[strategy]))
            rows.append(row)
    return pd.DataFrame(rows)


def cost_sensitivity(runs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for strategy in ("MOM80", "MOM_60_120", "MOM_60_120_REGIME"):
        sample = runs[runs["strategy"] == strategy].set_index("date")
        for bps in (0, 10, 15, 25, 50):
            returns = sample["gross_return"] - sample["turnover"] * bps / 10_000
            row = {"strategy": strategy, "one_way_cost_bps": bps}
            row.update(metrics(returns))
            rows.append(row)
    return pd.DataFrame(rows)


def svg_equity(curves: pd.DataFrame, output: Path) -> None:
    width, height, pad = 1000, 540, 60
    curves = curves.dropna(how="all").ffill().fillna(1)
    logv = np.log(curves.clip(lower=0.05))
    ymin, ymax = float(logv.min().min()), float(logv.max().max())
    if ymax == ymin:
        ymax += 1
    colors = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#64748b"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="60" y="30" font-family="sans-serif" font-size="20">Equity curve (log scale)</text>',
    ]
    for i in range(6):
        y = pad + i * (height - 2 * pad) / 5
        val = math.exp(ymax - i * (ymax - ymin) / 5)
        parts.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{width-pad}" y2="{y:.1f}" stroke="#e5e7eb"/>')
        parts.append(f'<text x="8" y="{y+4:.1f}" font-family="sans-serif" font-size="12">{val:.2f}x</text>')
    for j, col in enumerate(curves.columns):
        vals = logv[col].to_numpy()
        xs = np.linspace(pad, width - pad, len(vals))
        ys = pad + (ymax - vals) / (ymax - ymin) * (height - 2 * pad)
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
        color = colors[j % len(colors)]
        parts.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2"/>')
        lx = 650 + (j % 2) * 170
        ly = 25 + (j // 2) * 18
        parts.append(f'<line x1="{lx}" y1="{ly}" x2="{lx+22}" y2="{ly}" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="{lx+28}" y="{ly+4}" font-family="sans-serif" font-size="12">{html.escape(col)}</text>')
    years = pd.date_range(curves.index.min(), curves.index.max(), freq="YS")
    for date in years:
        pos = curves.index.searchsorted(date)
        if pos < len(curves):
            x = pad + pos / max(len(curves) - 1, 1) * (width - 2 * pad)
            parts.append(f'<text x="{x-12:.1f}" y="{height-18}" font-family="sans-serif" font-size="11">{date.year}</text>')
    parts.append("</svg>")
    output.write_text("\n".join(parts), encoding="utf-8")


def make_report(
    summary: pd.DataFrame,
    weekly: pd.DataFrame,
    latest: pd.DataFrame,
    sensitivity: pd.DataFrame,
    runs: pd.DataFrame,
    n_downloaded: int,
) -> None:
    full = summary[summary["period"] == "全期間 2016-2026"].copy()
    best = full.sort_values("Sharpe0", ascending=False).iloc[0]
    recommended = full[full["strategy"] == "MOM_60_120_REGIME"].iloc[0]
    rec_runs = runs[runs["strategy"] == "MOM_60_120_REGIME"]
    def pct(x): return f"{100*x:.1f}%"
    lines = [
        "# 日本株・週次戦略バックテスト",
        "",
        f"実行日: {pd.Timestamp.today().date()}",
        "",
        "## 結論",
        "",
        f"単純比較で全期間のリスク調整後成績が最良だったのは **{best['strategy']}** です。"
        f"全期間CAGRは {pct(best['CAGR'])}、最大ドローダウンは {pct(best['MaxDrawdown'])}、"
        f"シャープレシオは {best['Sharpe0']:.2f} でした。",
        "",
        f"実運用案は、単一期間への最適化を避けるため **MOM_60_120_REGIME** とします。"
        f"CAGR {pct(recommended['CAGR'])}、最大ドローダウン {pct(recommended['MaxDrawdown'])}、"
        f"シャープレシオ {recommended['Sharpe0']:.2f} です。60・80・120日の順位を平均し、"
        "相場悪化時に投資比率を半分へ落とすことで頑健性を優先しています。",
        "",
        "## 検証条件",
        "",
        f"- 現行の日経225採用銘柄のうち取得成功した {n_downloaded} 銘柄",
        "- シグナルは前営業日の終値まで、翌営業日の調整後始値で売買",
        f"- 毎週10銘柄、20日平均売買代金10億円以上、片道コスト {ONE_WAY_COST:.2%}",
        "- 個別銘柄は20日ボラティリティの逆数で配分し、1銘柄15%を上限",
        "- REGIME付き戦略は日経225が60日移動平均を下回ると投資比率50%",
        "",
        "## 成績",
        "",
        "|期間|戦略|CAGR|年率変動率|Sharpe|最大DD|勝率|",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in summary.iterrows():
        lines.append(
            f"|{row['period']}|{row['strategy']}|{pct(row['CAGR'])}|"
            f"{pct(row['AnnualVol'])}|{row['Sharpe0']:.2f}|"
            f"{pct(row['MaxDrawdown'])}|{pct(row['WinRate'])}|"
        )
    lines += [
        "",
        "![資産曲線](equity_curve.svg)",
        "",
        "## コスト耐性",
        "",
        f"推奨戦略の平均週次売買回転率は {rec_runs['turnover'].mean():.1%}、"
        f"平均投資比率は {rec_runs['exposure'].mean():.1%} です。",
        "",
        "|戦略|片道コスト|CAGR|Sharpe|最大DD|",
        "|---|---:|---:|---:|---:|",
    ]
    for _, row in sensitivity.iterrows():
        lines.append(
            f"|{row['strategy']}|{row['one_way_cost_bps']:.0f}bp|"
            f"{pct(row['CAGR'])}|{row['Sharpe0']:.2f}|{pct(row['MaxDrawdown'])}|"
        )
    lines += [
        "",
        "## 戦略定義",
        "",
        "- MOM20～MOM120: 各ルックバック期間の上昇率上位",
        "- MOM_60_120: 60日・80日・120日モメンタム順位の平均",
        "- REV5: 過去5日の下落率上位（短期反転）",
        "- BLEND: 20日・60日モメンタム、出来高増加、低ボラティリティ、過熱ペナルティの合成",
        "- BLEND_REGIME: BLENDに日経225の60日移動平均フィルターを追加",
        "- MOM60_REGIME: MOM60に同じ市場環境フィルターを追加",
        "- MOM_60_120_REGIME: MOM_60_120に同じ市場環境フィルターを追加（推奨案）",
        "",
        "## 重要な限界",
        "",
        "- 現在の構成銘柄を過去へ遡っているため、生存者バイアスがあります。",
        "- Yahoo配信データは研究用途の便宜的データで、正式な取引所データではありません。",
        "- 始値で全量約定できる仮定です。実運用では指値・分割発注が必要です。",
        "- 税金、信用金利、貸株料、注文拒否、ストップ高・安での未約定は含みません。",
        "- 業績修正・決算サプライズは、過去時点データを取得できていないため未使用です。",
        "- 過去の成績は将来の利益を保証しません。",
        "",
        "## 最終完了週の保有銘柄（研究用）",
        "",
        "|日付|コード|スコア|比率|",
        "|---|---|---:|---:|",
    ]
    for _, row in latest.iterrows():
        lines.append(
            f"|{row['date'].date()}|{row['ticker'].replace('.T','')}|"
            f"{row['score']:.3f}|{row['weight']:.1%}|"
        )
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=True)
    codes = current_nikkei_codes()
    data, benchmark = get_data(codes)
    close, open_, features, benchmark = build_features(data, benchmark)
    score_map = scores(features)
    runs, holdings = [], []
    for name, score in score_map.items():
        result, held = run_strategy(name, score, open_, features, benchmark, False)
        runs.append(result)
        holdings.append(held)
    result, held = run_strategy(
        "BLEND_REGIME", score_map["BLEND"], open_, features, benchmark, True
    )
    runs.append(result)
    holdings.append(held)
    result, held = run_strategy(
        "MOM60_REGIME", score_map["MOM60"], open_, features, benchmark, True
    )
    runs.append(result)
    holdings.append(held)
    result, held = run_strategy(
        "MOM_60_120_REGIME",
        score_map["MOM_60_120"],
        open_,
        features,
        benchmark,
        True,
    )
    runs.append(result)
    holdings.append(held)
    for count in (5, 20):
        result, held = run_strategy(
            f"MOM_60_120_H{count}",
            score_map["MOM_60_120"],
            open_,
            features,
            benchmark,
            False,
            n_holdings=count,
        )
        runs.append(result)
        holdings.append(held)
    all_runs = pd.concat(runs, ignore_index=True)
    all_holdings = pd.concat(holdings, ignore_index=True)
    weekly = all_runs.pivot(index="date", columns="strategy", values="net_return")

    # Benchmark is measured over the same weekly open-to-open windows.
    bench_open = benchmark["open"]
    weekly["NIKKEI225"] = [
        (
            bench_open.get(nxt, np.nan) / bench_open.get(date, np.nan) - 1
            if pd.notna(bench_open.get(date, np.nan))
            else np.nan
        )
        for date, nxt in zip(
            weekly.index,
            all_runs[all_runs["strategy"] == "MOM20"].set_index("date").loc[
                weekly.index, "next_date"
            ],
        )
    ]
    summary = period_metrics(weekly)
    sensitivity = cost_sensitivity(all_runs)
    curves = (1 + weekly.fillna(0)).cumprod()
    summary.to_csv(OUT / "summary.csv", index=False)
    sensitivity.to_csv(OUT / "cost_sensitivity.csv", index=False)
    weekly.to_csv(OUT / "weekly_returns.csv")
    all_runs.to_csv(OUT / "trade_summary.csv", index=False)
    all_holdings.to_csv(OUT / "holdings_history.csv", index=False)
    curves.to_csv(OUT / "equity_curves.csv")
    svg_equity(curves, OUT / "equity_curve.svg")
    latest = all_holdings[
        all_holdings["strategy"] == "MOM_60_120_REGIME"
    ].sort_values("date").groupby("strategy").tail(N_HOLDINGS)
    latest.to_csv(OUT / "latest_candidates.csv", index=False)
    make_report(summary, weekly, latest, sensitivity, all_runs, len(data))
    print(summary.to_string(index=False))
    print(f"\nDownloaded equities: {len(data)} / requested {len(codes)}")
    print(f"Outputs: {OUT}")


if __name__ == "__main__":
    main()
