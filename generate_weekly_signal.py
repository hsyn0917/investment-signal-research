#!/usr/bin/env python3
"""Generate the latest weekly Japanese-equity target portfolio and trade list."""

from __future__ import annotations

import csv
import html
import json
import os
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

from backtest_weekly_japan import (
    MIN_ADV20,
    N_HOLDINGS,
    build_features,
    capped_inverse_vol,
    current_nikkei_codes,
    get_data,
    scores,
)


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "signals"
STATE = OUT / "latest_signal.json"
REBALANCE_THRESHOLD = 0.005

JAPANESE_NAMES = {
    "3086": "J.フロント リテイリング",
    "3436": "SUMCO",
    "3697": "SHIFT",
    "5301": "東海カーボン",
    "5333": "日本ガイシ",
    "6098": "リクルートホールディングス",
    "6532": "ベイカレント",
    "6724": "セイコーエプソン",
    "6752": "パナソニック ホールディングス",
    "6971": "京セラ",
    "6976": "太陽誘電",
    "6981": "村田製作所",
    "7735": "SCREENホールディングス",
    "8035": "東京エレクトロン",
    "8750": "第一生命ホールディングス",
    "9147": "NIPPON EXPRESSホールディングス",
}


class ComponentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.text: list[str] = []
        self.codes: list[str] = []
        self.rows: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            if self.depth == 0:
                self.text, self.codes = [], []
            self.depth += 1
        if self.depth:
            for key, value in attrs:
                if key == "href" and value:
                    match = re.search(r"topSearchStr=(\d{4})", value)
                    if match:
                        self.codes.append(match.group(1))

    def handle_endtag(self, tag):
        if tag == "li" and self.depth:
            self.depth -= 1
            if self.depth == 0 and self.codes:
                code = self.codes[-1]
                text = " ".join(" ".join(self.text).split())
                text = re.sub(r"\s*\(\s*TYO\s*:\s*\d{4}\s*\).*$", "", text)
                self.rows[code] = text.strip()

    def handle_data(self, data):
        if self.depth:
            self.text.append(data)


def company_names() -> dict[str, str]:
    component_cache = ROOT / "data" / "nikkei225_current.html"
    if not component_cache.exists():
        current_nikkei_codes()
    parser = ComponentParser()
    parser.feed(component_cache.read_text(encoding="utf-8"))
    parser.rows.update(JAPANESE_NAMES)
    return parser.rows


def previous_targets() -> dict[str, float]:
    if os.environ.get("IGNORE_PREVIOUS_SIGNAL") == "1":
        return {}
    if not STATE.exists():
        return {}
    payload = json.loads(STATE.read_text(encoding="utf-8"))
    return {row["ticker"]: float(row["target_weight"]) for row in payload["targets"]}


def classify(old: float, new: float) -> str:
    if old == 0 and new > 0:
        return "新規買い"
    if old > 0 and new == 0:
        return "全売却"
    change = new - old
    if change >= REBALANCE_THRESHOLD:
        return "買い増し"
    if change <= -REBALANCE_THRESHOLD:
        return "一部売却"
    return "維持"


def write_csv(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)


def render_html(payload: dict) -> str:
    regime_class = "risk-on" if payload["market"]["exposure"] == 1 else "risk-off"
    rows = []
    for row in payload["instructions"]:
        action_class = {
            "新規買い": "buy",
            "買い増し": "buy",
            "全売却": "sell",
            "一部売却": "sell",
            "維持": "hold",
        }[row["action"]]
        rows.append(
            f"""<tr>
              <td><span class="action {action_class}">{row['action']}</span></td>
              <td><b>{row['code']}</b></td>
              <td>{html.escape(row['company_name'])}</td>
              <td>{row['current_weight']:.1%}</td>
              <td><b>{row['target_weight']:.1%}</b></td>
              <td class="{'plus' if row['change'] > 0 else 'minus' if row['change'] < 0 else ''}">{row['change']:+.1%}</td>
            </tr>"""
        )
    market = payload["market"]
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>日本株・週次売買シグナル</title>
<style>
:root{{--ink:#172033;--muted:#64748b;--line:#dce3ea;--blue:#2563eb;--green:#087f5b;--red:#c92a2a}}
*{{box-sizing:border-box}} body{{margin:0;background:#f3f6f8;color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans JP",sans-serif}}
main{{max-width:1050px;margin:auto;padding:28px 20px 60px}} header{{padding:32px;border-radius:20px;color:white;background:linear-gradient(135deg,#102a43,#176b87)}}
h1{{margin:0;font-size:34px}} header p{{margin:8px 0 0;color:#d7e8f5}} .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}}
.card{{background:white;border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:0 10px 30px rgba(16,42,67,.06)}}
.metric small{{display:block;color:var(--muted)}} .metric strong{{font-size:25px}} .wide{{grid-column:span 4}}
.status{{display:inline-block;padding:5px 10px;border-radius:99px;font-weight:800}} .risk-on{{background:#d3f9d8;color:#087f5b}} .risk-off{{background:#fff3bf;color:#a15c00}}
table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{padding:12px;border-bottom:1px solid var(--line);text-align:right}} th:first-child,td:first-child,th:nth-child(3),td:nth-child(3){{text-align:left}}
th{{color:var(--muted);font-size:12px;background:#f8fafc}} .action{{display:inline-block;padding:3px 8px;border-radius:7px;font-size:12px;font-weight:800}}
.buy{{color:var(--green);background:#d3f9d8}} .sell{{color:var(--red);background:#ffe3e3}} .hold{{color:#475569;background:#e9eef3}}
.plus{{color:var(--green)}} .minus{{color:var(--red)}} .note{{color:var(--muted);font-size:13px;line-height:1.7}} .scroll{{overflow:auto}}
@media(max-width:700px){{.grid{{grid-template-columns:1fr 1fr}}.wide{{grid-column:span 2}}h1{{font-size:27px}}}}
</style></head><body><main>
<header><h1>日本株・週次売買シグナル</h1><p>判定日 {payload['signal_date']} ｜ MOM_60_120_REGIME</p></header>
<section class="grid">
 <div class="card metric"><small>市場判定</small><strong><span class="status {regime_class}">{market['label']}</span></strong></div>
 <div class="card metric"><small>投資比率</small><strong>{market['exposure']:.0%}</strong></div>
 <div class="card metric"><small>日経225</small><strong>{market['benchmark_close']:,.0f}</strong></div>
 <div class="card metric"><small>60日移動平均</small><strong>{market['benchmark_ma60']:,.0f}</strong></div>
 <div class="card wide"><h2>売買指示</h2><div class="scroll"><table><thead><tr><th>指示</th><th>コード</th><th>会社名</th><th>前回目標</th><th>今回目標</th><th>変更</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></div>
 <div class="card wide note"><b>運用上の注意</b><br>
 この指示は前営業日までの価格・出来高から機械的に算出した研究用シグナルです。成行注文を前提とせず、実際の価格、決算予定、値幅制限、保有資金を確認してください。
 売買指示は実口座の残高ではなく前回シグナルとの差分です。初回実行では前回目標を0%として表示します。</div>
</section></main></body></html>"""


def main() -> None:
    OUT.mkdir(exist_ok=True)
    names = company_names()
    old = previous_targets()
    codes = current_nikkei_codes()
    data, benchmark = get_data(codes)
    close, open_, features, benchmark = build_features(data, benchmark)
    score = scores(features)["MOM_60_120"]

    availability = close.notna().mean(axis=1)
    valid_dates = availability[availability >= 0.80].index
    signal_date = valid_dates[-1]
    eligible = (
        (features["adv20"].loc[signal_date] >= MIN_ADV20)
        & score.loc[signal_date].notna()
        & close.loc[signal_date].notna()
    )
    selected = score.loc[signal_date, eligible].sort_values(ascending=False).head(
        N_HOLDINGS
    )
    weights = capped_inverse_vol(features["vol20"].loc[signal_date, selected.index])

    bench_hist = benchmark.loc[:signal_date, "close"].dropna()
    bench_close = float(bench_hist.iloc[-1])
    bench_ma60 = float(bench_hist.iloc[-60:].mean())
    exposure = 1.0 if bench_close >= bench_ma60 else 0.5
    weights *= exposure
    new = weights.to_dict()

    instructions = []
    for ticker in sorted(set(old) | set(new)):
        current, target = old.get(ticker, 0.0), new.get(ticker, 0.0)
        code = ticker.replace(".T", "")
        instructions.append(
            {
                "action": classify(current, target),
                "code": code,
                "ticker": ticker,
                "company_name": names.get(code, code),
                "current_weight": current,
                "target_weight": target,
                "change": target - current,
            }
        )
    order = {"全売却": 0, "一部売却": 1, "新規買い": 2, "買い増し": 3, "維持": 4}
    instructions.sort(key=lambda row: (order[row["action"]], -abs(row["change"])))

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "signal_date": str(signal_date.date()),
        "strategy": "MOM_60_120_REGIME",
        "market": {
            "label": "リスクオン" if exposure == 1 else "リスク抑制",
            "exposure": exposure,
            "benchmark_close": bench_close,
            "benchmark_ma60": bench_ma60,
        },
        "targets": [
            {
                "ticker": ticker,
                "code": ticker.replace(".T", ""),
                "company_name": names.get(ticker.replace(".T", ""), ticker),
                "score": float(selected[ticker]),
                "target_weight": float(new[ticker]),
            }
            for ticker in selected.index
        ],
        "instructions": instructions,
    }
    STATE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(OUT / "trade_instructions.csv", instructions)
    (OUT / "signal_report.html").write_text(render_html(payload), encoding="utf-8")

    print(f"Signal date: {payload['signal_date']}")
    print(f"Market: {payload['market']['label']} / exposure {exposure:.0%}")
    for row in instructions:
        if row["action"] != "維持":
            print(
                f"{row['action']:4} {row['code']} {row['company_name']} "
                f"{row['current_weight']:.1%} -> {row['target_weight']:.1%}"
            )
    print(f"Report: {OUT / 'signal_report.html'}")


if __name__ == "__main__":
    main()
