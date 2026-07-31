#!/usr/bin/env python3
"""Combine the weekly Japan-stock and daily US LETF signals.

The program only produces target allocations. It does not connect to a broker.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
TOKYO = ZoneInfo("Asia/Tokyo")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_config(config: dict) -> None:
    required = {
        "japan_sleeve",
        "us_letf_sleeve",
        "rebalance_threshold_percentage_points",
        "max_japan_signal_age_calendar_days",
        "max_us_signal_age_calendar_days",
        "japan_cash_asset",
    }
    missing = sorted(required - set(config))
    if missing:
        raise ValueError(f"統合設定が不足しています: {', '.join(missing)}")
    total = float(config["japan_sleeve"]) + float(config["us_letf_sleeve"])
    if not math.isclose(total, 1.0, abs_tol=1e-9):
        raise ValueError("日本株枠と米国LETF枠の合計は1.0である必要があります")
    if min(float(config["japan_sleeve"]), float(config["us_letf_sleeve"])) < 0:
        raise ValueError("資産枠は0以上で指定してください")


def classify(previous: float, target: float, threshold_pp: float) -> str:
    threshold = threshold_pp / 100.0
    change = target - previous
    if previous <= 1e-12 and target > 1e-12:
        return "新規買い"
    if previous > 1e-12 and target <= 1e-12:
        return "全売却"
    if change >= threshold:
        return "買い増し"
    if change <= -threshold:
        return "一部売却"
    return "維持"


def _row(
    *,
    market: str,
    asset: str,
    name: str,
    currency: str,
    previous: float,
    target: float,
    threshold_pp: float,
) -> dict:
    return {
        "market": market,
        "asset": asset,
        "name": name,
        "currency": currency,
        "action": classify(previous, target, threshold_pp),
        "previous_weight": round(previous, 10),
        "target_weight": round(target, 10),
        "change_percentage_points": round((target - previous) * 100, 6),
    }


def build_combined(
    japan: dict, us: dict, config: dict, *, today: date | None = None
) -> dict:
    validate_config(config)
    today = today or datetime.now(TOKYO).date()
    jp_date = date.fromisoformat(japan["signal_date"])
    us_date = date.fromisoformat(us["data_date"])
    jp_age = (today - jp_date).days
    us_age = (today - us_date).days
    if jp_age < 0 or us_age < 0:
        raise ValueError("将来日付のシグナルは統合できません")

    warnings = list(us.get("warnings", []))
    status = "OK"
    if jp_age > int(config["max_japan_signal_age_calendar_days"]):
        warnings.append(f"日本株シグナルが{jp_age}日古いため更新が必要です")
        status = "STALE"
    if us_age > int(config["max_us_signal_age_calendar_days"]):
        warnings.append(f"米国シグナルが{us_age}日古いため更新が必要です")
        status = "STALE"
    if us.get("status") != "OK":
        warnings.append(f"米国シグナル状態: {us.get('status', 'UNKNOWN')}")
        if status == "OK":
            status = "WARNING"
    if warnings and status == "OK":
        status = "WARNING"

    jp_scale = float(config["japan_sleeve"])
    us_scale = float(config["us_letf_sleeve"])
    threshold = float(config["rebalance_threshold_percentage_points"])
    rows: list[dict] = []

    jp_target_details = {row["ticker"]: row for row in japan["targets"]}
    for instruction in japan["instructions"]:
        ticker = instruction["ticker"]
        detail = jp_target_details.get(ticker, {})
        rows.append(
            _row(
                market="日本",
                asset=ticker,
                name=instruction.get("company_name", detail.get("company_name", ticker)),
                currency="JPY",
                previous=jp_scale * float(instruction["current_weight"]),
                target=jp_scale * float(instruction["target_weight"]),
                threshold_pp=threshold,
            )
        )

    jp_previous_risk = sum(
        jp_scale * float(row["current_weight"]) for row in japan["instructions"]
    )
    jp_target_risk = sum(
        jp_scale * float(row["target_weight"]) for row in japan["instructions"]
    )
    rows.append(
        _row(
            market="日本",
            asset=str(config["japan_cash_asset"]),
            name="円待機資金",
            currency="JPY",
            previous=max(0.0, jp_scale - jp_previous_risk),
            target=max(0.0, jp_scale - jp_target_risk),
            threshold_pp=threshold,
        )
    )

    us_sleeves = {row["etf"]: row for row in us["sleeves"]}
    cash_assets = [asset for asset in us["target_weights"] if asset not in us_sleeves]
    if len(cash_assets) != 1:
        raise ValueError("米国シグナルの待機資産を一意に特定できません")
    for asset, target_within_us in us["target_weights"].items():
        if asset in us_sleeves:
            sleeve = us_sleeves[asset]
            previous_within_us = float(sleeve["previous_target_weight"])
            name = f"{asset}（レバレッジETF）"
        else:
            previous_risk = sum(
                float(row["previous_target_weight"]) for row in us["sleeves"]
            )
            previous_within_us = max(0.0, 1.0 - previous_risk)
            name = f"{asset}（米ドル待機資金）"
        rows.append(
            _row(
                market="米国",
                asset=asset,
                name=name,
                currency="USD",
                previous=us_scale * previous_within_us,
                target=us_scale * float(target_within_us),
                threshold_pp=threshold,
            )
        )

    previous_total = sum(row["previous_weight"] for row in rows)
    target_total = sum(row["target_weight"] for row in rows)
    if not math.isclose(previous_total, 1.0, abs_tol=1e-7):
        raise RuntimeError(f"前回比率の合計が100%ではありません: {previous_total:.8f}")
    if not math.isclose(target_total, 1.0, abs_tol=1e-7):
        raise RuntimeError(f"目標比率の合計が100%ではありません: {target_total:.8f}")

    risky_assets = [
        row
        for row in rows
        if row["asset"] not in {str(config["japan_cash_asset"]), cash_assets[0]}
    ]
    return {
        "schema_version": 1,
        "generated_at_jst": datetime.now(TOKYO).isoformat(timespec="seconds"),
        "status": status,
        "source_dates": {
            "japan": japan["signal_date"],
            "us": us["data_date"],
        },
        "source_signal_ages_calendar_days": {"japan": jp_age, "us": us_age},
        "capital_sleeves": {"japan": jp_scale, "us_letf": us_scale},
        "regimes": {
            "japan": japan["market"]["label"],
            "japan_exposure_within_sleeve": japan["market"]["exposure"],
            "us": us["overall_regime"],
        },
        "summary": {
            "risk_asset_weight": round(
                sum(row["target_weight"] for row in risky_assets), 10
            ),
            "defensive_asset_weight": round(
                1.0 - sum(row["target_weight"] for row in risky_assets), 10
            ),
            "target_weight_total": round(target_total, 10),
        },
        "instructions": sorted(
            rows,
            key=lambda row: (
                {"全売却": 0, "一部売却": 1, "新規買い": 2, "買い増し": 3, "維持": 4}[
                    row["action"]
                ],
                -abs(row["change_percentage_points"]),
            ),
        ),
        "warnings": warnings,
        "execution_policy": "SIGNAL_ONLY_NO_BROKER_ORDERS",
        "method": {
            "japan": japan["strategy"],
            "us": "200日トレンド + 20日実現ボラティリティ調整",
            "combination": "固定資本枠の中で各サブ戦略の目標比率を比例配分",
        },
    }


def render_markdown(payload: dict) -> str:
    lines = [
        "# 日本株 + TQQQ / SOXL 統合シグナル",
        "",
        f"- 状態: **{payload['status']}**",
        f"- 日本株基準日: **{payload['source_dates']['japan']}**",
        f"- 米国基準日: **{payload['source_dates']['us']}**",
        f"- 資本枠: 日本株 {payload['capital_sleeves']['japan']:.0%} / "
        f"米国LETF {payload['capital_sleeves']['us_letf']:.0%}",
        f"- リスク資産: **{payload['summary']['risk_asset_weight']:.1%}** / "
        f"待機資産: **{payload['summary']['defensive_asset_weight']:.1%}**",
        "",
        "|市場|資産|銘柄名|指示|前回目標|今回目標|変更|",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for row in payload["instructions"]:
        lines.append(
            f"|{row['market']}|{row['asset']}|{row['name']}|**{row['action']}**|"
            f"{row['previous_weight']:.2%}|{row['target_weight']:.2%}|"
            f"{row['change_percentage_points']:+.2f}pt|"
        )
    if payload["warnings"]:
        lines += ["", "## 警告", ""]
        lines += [f"- {warning}" for warning in payload["warnings"]]
    lines += [
        "",
        "---",
        "",
        "この出力は研究用の目標配分であり、発注命令・投資助言・収益保証ではありません。"
        "円・ドル間の為替変動、税金、売買コスト、実口座残高は別途確認してください。",
    ]
    return "\n".join(lines) + "\n"


def render_html(payload: dict) -> str:
    rows = []
    for row in payload["instructions"]:
        action_class = (
            "buy"
            if row["action"] in {"新規買い", "買い増し"}
            else "sell"
            if row["action"] in {"全売却", "一部売却"}
            else "hold"
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(row['market'])}</td>"
            f"<td><b>{html.escape(row['asset'])}</b></td>"
            f"<td>{html.escape(row['name'])}</td>"
            f"<td><span class='tag {action_class}'>{row['action']}</span></td>"
            f"<td>{row['previous_weight']:.2%}</td>"
            f"<td><b>{row['target_weight']:.2%}</b></td>"
            f"<td>{row['change_percentage_points']:+.2f}pt</td>"
            "</tr>"
        )
    warning_html = "".join(f"<li>{html.escape(x)}</li>" for x in payload["warnings"])
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>日米統合ポートフォリオ・シグナル</title>
<style>
:root{{--ink:#26343c;--muted:#6b7780;--line:#dfe4e7;--green:#516c61;--red:#7c5a58}}
*{{box-sizing:border-box}}body{{margin:0;background:#fff;color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans JP",sans-serif}}
main{{max-width:1080px;margin:auto;padding:34px 20px 60px}}header{{padding:24px 0;border-bottom:1px solid var(--line);color:var(--ink);background:#fff}}
h1{{margin:0 0 8px;font-size:26px;font-weight:600}}header p{{margin:0;color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0}}
.card{{background:white;border:1px solid var(--line);border-radius:4px;padding:18px;box-shadow:none}}.wide{{grid-column:span 4}}
.metric small{{display:block;color:var(--muted);margin-bottom:5px}}.metric strong{{font-size:24px}}.scroll{{overflow:auto}}
table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:11px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2),th:nth-child(3),td:nth-child(3),th:nth-child(4),td:nth-child(4){{text-align:left}}
th{{color:var(--muted);background:#f8fafc;font-size:12px}}.tag{{padding:3px 8px;border-radius:7px;font-weight:800;font-size:12px}}
.buy{{color:var(--green);background:#d3f9d8}}.sell{{color:var(--red);background:#ffe3e3}}.hold{{color:#475569;background:#e9eef3}}
.note{{font-size:13px;color:var(--muted);line-height:1.7}}ul{{margin-bottom:0}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr 1fr}}.wide{{grid-column:span 2}}h1{{font-size:25px}}}}
</style></head><body><main>
<header><h1>日米統合ポートフォリオ・シグナル</h1><p>日本株 {payload['source_dates']['japan']} ｜ 米国 {payload['source_dates']['us']} ｜ 状態 {payload['status']}</p></header>
<section class="grid">
<div class="card metric"><small>日本株資本枠</small><strong>{payload['capital_sleeves']['japan']:.0%}</strong></div>
<div class="card metric"><small>米国LETF資本枠</small><strong>{payload['capital_sleeves']['us_letf']:.0%}</strong></div>
<div class="card metric"><small>リスク資産</small><strong>{payload['summary']['risk_asset_weight']:.1%}</strong></div>
<div class="card metric"><small>待機資産</small><strong>{payload['summary']['defensive_asset_weight']:.1%}</strong></div>
<div class="card wide"><h2>統合売買指示</h2><div class="scroll"><table><thead><tr><th>市場</th><th>資産</th><th>銘柄名</th><th>指示</th><th>前回</th><th>目標</th><th>変更</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></div>
<div class="card wide note"><b>市場判定</b>　日本: {html.escape(str(payload['regimes']['japan']))} / 米国: {html.escape(str(payload['regimes']['us']))}
{f"<ul>{warning_html}</ul>" if warning_html else ""}
<p>研究用の目標配分です。自動発注は行いません。為替、税金、売買コスト、実口座残高を確認してから人間が最終判断してください。</p></div>
</section></main></body></html>"""


def write_outputs(payload: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "latest_portfolio.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "latest_portfolio.md").write_text(
        render_markdown(payload), encoding="utf-8"
    )
    (output_dir / "latest_portfolio.html").write_text(
        render_html(payload), encoding="utf-8"
    )
    with (output_dir / "trade_instructions.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        fieldnames = list(payload["instructions"][0])
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(payload["instructions"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="日本株と米国LETFのシグナルを統合")
    parser.add_argument("--japan", type=Path, default=ROOT / "signals/latest_signal.json")
    parser.add_argument(
        "--us", type=Path, default=ROOT / "signal_system/output/latest_signal.json"
    )
    parser.add_argument("--config", type=Path, default=ROOT / "portfolio_config.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "combined_signals")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = build_combined(
            load_json(args.japan), load_json(args.us), load_json(args.config)
        )
        write_outputs(payload, args.output_dir)
        print(render_markdown(payload))
        return 0
    except Exception as exc:
        print(f"COMBINED SIGNAL GENERATION FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
