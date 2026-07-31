#!/usr/bin/env python3
"""Generate a self-contained HTML report from the backtest CSV outputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


def rows(name: str) -> list[dict[str, str]]:
    with (RESULTS / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def pct(value: str | float, digits: int = 1) -> str:
    return f"{float(value) * 100:.{digits}f}%"


def num(value: str | float, digits: int = 2) -> str:
    return f"{float(value):.{digits}f}"


summary = rows("summary.csv")
costs = rows("cost_sensitivity.csv")
holdings = rows("latest_candidates.csv")
curves_raw = rows("equity_curves.csv")

company_names = {
    "3436": "SUMCO",
    "5301": "東海カーボン",
    "5333": "日本ガイシ",
    "6098": "リクルートホールディングス",
    "6752": "パナソニック ホールディングス",
    "6976": "太陽誘電",
    "6981": "村田製作所",
    "7735": "SCREENホールディングス",
    "8035": "東京エレクトロン",
    "9147": "NIPPON EXPRESSホールディングス",
}

full = {r["strategy"]: r for r in summary if r["period"] == "全期間 2016-2026"}
recommended = full["MOM_60_120_REGIME"]
benchmark = full["NIKKEI225"]
best = full["MOM80"]

chart_names = ["MOM_60_120_REGIME", "MOM80", "NIKKEI225"]
chart_data = {
    "dates": [r["date"] for r in curves_raw],
    "series": {name: [float(r[name]) for r in curves_raw] for name in chart_names},
}
cost_data = [
    {
        "strategy": r["strategy"],
        "bps": int(float(r["one_way_cost_bps"])),
        "cagr": float(r["CAGR"]),
    }
    for r in costs
]

strategies = [
    "MOM_60_120_REGIME",
    "MOM80",
    "MOM_60_120",
    "MOM60_REGIME",
    "MOM60",
    "MOM120",
    "MOM40",
    "MOM20",
    "NIKKEI225",
    "BLEND",
    "REV5",
]
comparison_rows = "\n".join(
    f"""<tr class="{'recommended-row' if name == 'MOM_60_120_REGIME' else ''}">
      <td><span class="strategy-name">{name}</span>{'<span class="tag">推奨</span>' if name == 'MOM_60_120_REGIME' else ''}</td>
      <td>{pct(full[name]['CAGR'])}</td>
      <td>{pct(full[name]['AnnualVol'])}</td>
      <td>{num(full[name]['Sharpe0'])}</td>
      <td class="negative">{pct(full[name]['MaxDrawdown'])}</td>
      <td>{pct(full[name]['WinRate'])}</td>
    </tr>"""
    for name in strategies
)

period_rows = []
for period in ("前半 2016-2020", "後半 2021-2026"):
    p = {r["strategy"]: r for r in summary if r["period"] == period}
    period_rows.append(
        f"""<tr>
          <td>{period}</td>
          <td>{pct(p['MOM_60_120_REGIME']['CAGR'])}</td>
          <td>{num(p['MOM_60_120_REGIME']['Sharpe0'])}</td>
          <td>{pct(p['MOM_60_120_REGIME']['MaxDrawdown'])}</td>
          <td>{pct(p['NIKKEI225']['CAGR'])}</td>
          <td>{num(p['NIKKEI225']['Sharpe0'])}</td>
        </tr>"""
    )

cost_rows = "\n".join(
    f"""<tr>
      <td>{r['strategy']}</td>
      <td>{int(float(r['one_way_cost_bps']))}bp</td>
      <td>{pct(r['CAGR'])}</td>
      <td>{num(r['Sharpe0'])}</td>
      <td class="negative">{pct(r['MaxDrawdown'])}</td>
    </tr>"""
    for r in costs
)

holding_rows = "\n".join(
    f"""<tr>
      <td>{r['ticker'].replace('.T', '')}</td>
      <td class="company-name">{company_names.get(r['ticker'].replace('.T', ''), '—')}</td>
      <td>{num(r['score'], 3)}</td>
      <td><div class="weight-cell"><span style="width:{float(r['weight']) * 1000:.1f}px"></span><b>{pct(r['weight'])}</b></div></td>
    </tr>"""
    for r in sorted(holdings, key=lambda x: float(x["weight"]), reverse=True)
)
holding_date = holdings[0]["date"] if holdings else "—"

html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="日本株週次モメンタム戦略のバックテストレポート">
  <title>日本株・週次戦略バックテスト</title>
  <style>
    :root {{
      --ink:#172033; --muted:#657084; --line:#dfe5ec; --paper:#ffffff;
      --canvas:#f3f6f8; --navy:#102a43; --blue:#2563eb; --blue2:#dbeafe;
      --green:#087f5b; --green2:#d3f9d8; --red:#c92a2a; --amber:#f59f00;
      --shadow:0 14px 40px rgba(16,42,67,.08); --radius:18px;
    }}
    * {{ box-sizing:border-box }}
    html {{ scroll-behavior:smooth }}
    body {{
      margin:0; color:var(--ink); background:var(--canvas);
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans JP",sans-serif;
      font-feature-settings:"tnum"; line-height:1.65;
    }}
    a {{ color:inherit }}
    .shell {{ max-width:1180px; margin:auto; padding:0 24px 64px }}
    .hero {{
      margin:24px 0; padding:42px 46px; color:#fff; overflow:hidden;
      border-radius:24px; background:
        radial-gradient(circle at 88% 5%,rgba(70,170,255,.34),transparent 32%),
        linear-gradient(135deg,#0b1f33 0%,#123d62 72%,#176b87 100%);
      box-shadow:var(--shadow); position:relative;
    }}
    .eyebrow {{ margin:0 0 8px; color:#9fd6ff; letter-spacing:.13em; font-size:12px; font-weight:800 }}
    h1 {{ margin:0; font-size:clamp(29px,4vw,48px); line-height:1.16; letter-spacing:-.03em }}
    .hero p {{ max-width:720px; color:#d9e8f5; margin:18px 0 0; font-size:16px }}
    .hero-meta {{ display:flex; flex-wrap:wrap; gap:20px; margin-top:30px; color:#bcd0df; font-size:13px }}
    .hero-meta b {{ color:#fff }}
    nav {{
      position:sticky; top:10px; z-index:5; display:flex; gap:5px; overflow:auto;
      margin:0 0 24px; padding:7px; background:rgba(255,255,255,.94);
      border:1px solid var(--line); border-radius:13px; box-shadow:0 8px 24px rgba(16,42,67,.06);
      backdrop-filter:blur(12px);
    }}
    nav a {{ text-decoration:none; white-space:nowrap; padding:8px 12px; border-radius:8px; color:var(--muted); font-size:13px; font-weight:700 }}
    nav a:hover {{ background:#edf2f7; color:var(--navy) }}
    .grid {{ display:grid; grid-template-columns:repeat(12,1fr); gap:18px }}
    .card {{
      background:var(--paper); border:1px solid var(--line); border-radius:var(--radius);
      padding:24px; box-shadow:var(--shadow);
    }}
    .span-12 {{ grid-column:span 12 }} .span-8 {{ grid-column:span 8 }}
    .span-7 {{ grid-column:span 7 }} .span-6 {{ grid-column:span 6 }}
    .span-5 {{ grid-column:span 5 }} .span-4 {{ grid-column:span 4 }}
    .section-title {{ display:flex; justify-content:space-between; align-items:flex-end; gap:14px; margin-bottom:18px }}
    h2 {{ margin:0; color:var(--navy); font-size:23px; letter-spacing:-.02em }}
    h3 {{ margin:0 0 8px; color:var(--navy); font-size:16px }}
    .sub {{ margin:4px 0 0; color:var(--muted); font-size:13px }}
    .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px }}
    .kpi {{ padding:19px; border:1px solid var(--line); border-radius:14px; background:#fbfcfd }}
    .kpi .label {{ color:var(--muted); font-size:12px; font-weight:700 }}
    .kpi strong {{ display:block; margin:7px 0 1px; font-size:29px; line-height:1.15; color:var(--navy) }}
    .kpi small {{ color:var(--muted); font-size:11px }}
    .positive {{ color:var(--green)!important }} .negative {{ color:var(--red) }}
    .decision {{
      border-left:5px solid var(--blue); background:linear-gradient(90deg,#eff6ff,#fff);
      padding:20px 22px; border-radius:12px; margin-top:18px;
    }}
    .decision strong {{ color:var(--blue) }}
    .chart-wrap {{ position:relative; min-height:390px }}
    svg.chart {{ width:100%; height:390px; display:block; overflow:visible }}
    .legend {{ display:flex; flex-wrap:wrap; gap:14px; margin:2px 0 12px }}
    .legend label {{ display:flex; align-items:center; gap:7px; font-size:12px; font-weight:700; cursor:pointer }}
    .legend i {{ width:11px; height:11px; border-radius:50%; display:block }}
    .tooltip {{
      display:none; position:absolute; pointer-events:none; z-index:3; padding:8px 10px;
      background:#102a43; color:#fff; border-radius:8px; font-size:11px; box-shadow:var(--shadow);
    }}
    table {{ width:100%; border-collapse:collapse; font-size:13px }}
    th {{ color:var(--muted); font-size:11px; text-transform:none; letter-spacing:.02em; background:#f7f9fb }}
    th,td {{ padding:11px 12px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap }}
    th:first-child,td:first-child {{ text-align:left }}
    tbody tr:hover {{ background:#f8fafc }}
    .table-scroll {{ overflow:auto; border:1px solid var(--line); border-radius:12px }}
    .table-scroll table tr:last-child td {{ border-bottom:0 }}
    .recommended-row {{ background:#eff6ff }}
    .tag {{ display:inline-block; margin-left:8px; padding:2px 7px; border-radius:99px; color:#1d4ed8; background:#dbeafe; font-size:10px; font-weight:800 }}
    .strategy-name {{ font-weight:700 }}
    .company-name {{ font-weight:700; white-space:normal; min-width:180px }}
    .rule-list {{ list-style:none; padding:0; margin:0 }}
    .rule-list li {{ display:grid; grid-template-columns:38px 1fr; gap:12px; align-items:start; padding:13px 0; border-bottom:1px solid var(--line) }}
    .rule-list li:last-child {{ border:0 }}
    .rule-list b {{ display:grid; place-items:center; width:34px; height:34px; border-radius:10px; background:var(--blue2); color:var(--blue); font-size:12px }}
    .rule-list span {{ color:var(--muted); font-size:13px }}
    .rule-list strong {{ display:block; color:var(--ink); font-size:14px }}
    .weight-cell {{ min-width:150px; display:flex; justify-content:flex-end; align-items:center; gap:10px }}
    .weight-cell span {{ display:block; height:7px; max-width:75px; background:linear-gradient(90deg,#60a5fa,#2563eb); border-radius:8px }}
    .weight-cell b {{ min-width:40px }}
    .warning {{ border-top:4px solid var(--amber) }}
    .warning ul {{ margin:12px 0 0; padding-left:19px; color:var(--muted); font-size:13px }}
    .warning li {{ margin:7px 0 }}
    .mini-chart {{ height:230px }}
    footer {{ margin:28px 0 0; padding:18px 4px; color:var(--muted); font-size:12px; text-align:center }}
    .print-btn {{ border:1px solid rgba(255,255,255,.35); color:#fff; background:rgba(255,255,255,.08); border-radius:9px; padding:8px 12px; cursor:pointer; font:inherit }}
    @media (max-width:900px) {{
      .span-8,.span-7,.span-6,.span-5,.span-4 {{ grid-column:span 12 }}
      .kpis {{ grid-template-columns:repeat(2,1fr) }}
    }}
    @media (max-width:560px) {{
      .shell {{ padding:0 12px 40px }} .hero {{ margin-top:12px; padding:28px 22px }}
      .card {{ padding:18px }} .kpis {{ grid-template-columns:1fr 1fr; gap:8px }}
      .kpi {{ padding:14px }} .kpi strong {{ font-size:23px }}
      th,td {{ padding:9px 10px }} nav {{ top:4px }}
    }}
    @media print {{
      body {{ background:#fff }} .shell {{ max-width:none; padding:0 }} nav,.print-btn {{ display:none }}
      .hero,.card {{ box-shadow:none; break-inside:avoid }} .hero {{ margin-top:0 }}
      .grid {{ display:block }} .card {{ margin:14px 0 }} .chart-wrap {{ min-height:330px }}
    }}
  </style>
</head>
<body>
<main class="shell">
  <header class="hero">
    <p class="eyebrow">QUANTITATIVE RESEARCH REPORT</p>
    <h1>日本株・週次戦略<br>バックテスト</h1>
    <p>日経225採用銘柄を対象に、モメンタム・短期反転・複合スコアを同条件で比較。収益性だけでなく、ドローダウン、期間安定性、売買コストへの耐性から実運用候補を選定しました。</p>
    <div class="hero-meta">
      <span>検証期間 <b>2016–2026</b></span>
      <span>対象 <b>223銘柄</b></span>
      <span>週数 <b>{int(float(recommended['Weeks']))}</b></span>
      <span>基準コスト <b>片道15bp</b></span>
      <button class="print-btn" onclick="window.print()">PDF / 印刷</button>
    </div>
  </header>

  <nav aria-label="レポート内ナビゲーション">
    <a href="#overview">結論</a><a href="#curve">資産曲線</a><a href="#comparison">戦略比較</a>
    <a href="#robustness">頑健性</a><a href="#rules">運用ルール</a><a href="#holdings">保有銘柄</a><a href="#limits">限界</a>
  </nav>

  <div class="grid">
    <section id="overview" class="card span-12">
      <div class="section-title"><div><h2>推奨戦略の結論</h2><p class="sub">MOM_60_120_REGIME — 複数期間のモメンタム順位＋市場環境フィルター</p></div></div>
      <div class="kpis">
        <div class="kpi"><span class="label">年率リターン</span><strong class="positive">{pct(recommended['CAGR'])}</strong><small>日経225 {pct(benchmark['CAGR'])}</small></div>
        <div class="kpi"><span class="label">シャープレシオ</span><strong>{num(recommended['Sharpe0'])}</strong><small>日経225 {num(benchmark['Sharpe0'])}</small></div>
        <div class="kpi"><span class="label">最大ドローダウン</span><strong class="negative">{pct(recommended['MaxDrawdown'])}</strong><small>日経225 {pct(benchmark['MaxDrawdown'])}</small></div>
        <div class="kpi"><span class="label">週次勝率</span><strong>{pct(recommended['WinRate'])}</strong><small>年率変動率 {pct(recommended['AnnualVol'])}</small></div>
      </div>
      <div class="decision"><strong>判断：</strong> 単純比較の最高成績はMOM80（CAGR {pct(best['CAGR'])}）ですが、単一パラメータへの依存と最大DD {pct(best['MaxDrawdown'])}を考慮し、60・80・120日の順位平均と市場フィルターを採用します。</div>
    </section>

    <section id="curve" class="card span-8">
      <div class="section-title"><div><h2>資産曲線</h2><p class="sub">初期資産を1とした累積推移・対数スケール</p></div></div>
      <div id="legend" class="legend"></div>
      <div class="chart-wrap"><svg id="equityChart" class="chart" role="img" aria-label="資産曲線"></svg><div id="tip" class="tooltip"></div></div>
    </section>

    <section id="rules" class="card span-4">
      <div class="section-title"><div><h2>運用ルール</h2><p class="sub">週次で機械的に更新</p></div></div>
      <ol class="rule-list">
        <li><b>01</b><span><strong>ユニバース</strong>日経225、20日平均売買代金10億円以上</span></li>
        <li><b>02</b><span><strong>ランキング</strong>60・80・120日上昇率の銘柄内順位を平均</span></li>
        <li><b>03</b><span><strong>ポートフォリオ</strong>上位10銘柄、逆ボラ配分、1銘柄15%上限</span></li>
        <li><b>04</b><span><strong>市場フィルター</strong>日経225が60日線未満なら投資比率50%</span></li>
        <li><b>05</b><span><strong>執行</strong>前営業日終値までで判定、翌営業日始値で売買</span></li>
      </ol>
    </section>

    <section id="comparison" class="card span-7">
      <div class="section-title"><div><h2>全期間の戦略比較</h2><p class="sub">片道15bp控除後・2016–2026</p></div></div>
      <div class="table-scroll"><table>
        <thead><tr><th>戦略</th><th>CAGR</th><th>年率変動率</th><th>Sharpe</th><th>最大DD</th><th>勝率</th></tr></thead>
        <tbody>{comparison_rows}</tbody>
      </table></div>
    </section>

    <section id="robustness" class="card span-5">
      <div class="section-title"><div><h2>期間分割テスト</h2><p class="sub">前半と後半の両方でプラスかを確認</p></div></div>
      <div class="table-scroll"><table>
        <thead><tr><th rowspan="2">期間</th><th colspan="3">推奨戦略</th><th colspan="2">日経225</th></tr><tr><th>CAGR</th><th>Sharpe</th><th>最大DD</th><th>CAGR</th><th>Sharpe</th></tr></thead>
        <tbody>{''.join(period_rows)}</tbody>
      </table></div>
      <p class="sub" style="margin-top:16px">後半では推奨戦略のリターンは指数を上回りましたが、変動率も高く、シャープレシオでは指数を下回ります。</p>
    </section>

    <section class="card span-6">
      <div class="section-title"><div><h2>コスト感応度</h2><p class="sub">片道コスト上昇に伴うCAGRの変化</p></div></div>
      <svg id="costChart" class="chart mini-chart" role="img" aria-label="コスト感応度"></svg>
      <div class="table-scroll"><table>
        <thead><tr><th>戦略</th><th>片道コスト</th><th>CAGR</th><th>Sharpe</th><th>最大DD</th></tr></thead>
        <tbody>{cost_rows}</tbody>
      </table></div>
    </section>

    <section id="holdings" class="card span-6">
      <div class="section-title"><div><h2>最終完了週の保有銘柄</h2><p class="sub">{holding_date}・研究用（現在の推奨ではありません）</p></div></div>
      <div class="table-scroll"><table>
        <thead><tr><th>コード</th><th>会社名</th><th>スコア</th><th>比率</th></tr></thead>
        <tbody>{holding_rows}</tbody>
      </table></div>
      <p class="sub" style="margin-top:14px">合計比率が50%なのは、当該週に市場環境フィルターが作動していたためです。</p>
    </section>

    <section id="limits" class="card span-12 warning">
      <div class="section-title"><div><h2>重要な限界と次の検証</h2><p class="sub">数値を実運用成績とみなさないための留意事項</p></div></div>
      <ul>
        <li>現行の日経225構成銘柄を過去へ遡っているため、生存者バイアスがあり、成績は上振れしている可能性があります。</li>
        <li>価格は研究用の公開配信データです。税金、信用金利、注文拒否、値幅制限による未約定は含みません。</li>
        <li>始値で全量約定する仮定です。平均週次売買回転率は約52.6%であり、実運用では指値・分割発注が必要です。</li>
        <li>決算や業績修正は、当時利用可能だった情報を完全に復元できないため今回のモデルには含めていません。</li>
        <li>次段階は、過去時点の構成銘柄、正式な株価、TDnet開示を導入した再検証と8–12週間のペーパートレードです。</li>
      </ul>
    </section>
  </div>
  <footer>作成日 2026-07-31 ｜ 本資料は投資助言ではなく、過去データを用いた定量研究です。過去の成績は将来の収益を保証しません。</footer>
</main>
<script>
const equity = {json.dumps(chart_data, ensure_ascii=False, separators=(",", ":"))};
const costData = {json.dumps(cost_data, ensure_ascii=False, separators=(",", ":"))};
const colors = {{"MOM_60_120_REGIME":"#2563eb","MOM80":"#f59f00","NIKKEI225":"#64748b","MOM_60_120":"#087f5b"}};
const labels = {{"MOM_60_120_REGIME":"推奨戦略","MOM80":"MOM80","NIKKEI225":"日経225","MOM_60_120":"MOM 60–120"}};
const ns = "http://www.w3.org/2000/svg";
function node(tag, attrs={{}}, text="") {{
  const el=document.createElementNS(ns,tag); Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v)); if(text) el.textContent=text; return el;
}}
function drawEquity() {{
  const svg=document.getElementById("equityChart"), legend=document.getElementById("legend");
  const active={{}}; Object.keys(equity.series).forEach(k=>active[k]=true);
  legend.innerHTML="";
  Object.keys(active).forEach(k=>{{
    const lab=document.createElement("label"), cb=document.createElement("input"), dot=document.createElement("i");
    cb.type="checkbox"; cb.checked=true; dot.style.background=colors[k]; lab.append(cb,dot,document.createTextNode(labels[k]));
    cb.onchange=()=>{{active[k]=cb.checked; render();}}; legend.append(lab);
  }});
  function render() {{
    const W=800,H=390,p={{l:56,r:18,t:16,b:36}}; svg.setAttribute("viewBox",`0 0 ${{W}} ${{H}}`); svg.innerHTML="";
    const names=Object.keys(active).filter(k=>active[k]); if(!names.length)return;
    const vals=names.flatMap(k=>equity.series[k].map(Math.log)); const min=Math.min(...vals),max=Math.max(...vals);
    for(let i=0;i<5;i++){{const y=p.t+i*(H-p.t-p.b)/4, v=Math.exp(max-i*(max-min)/4);
      svg.append(node("line",{{x1:p.l,y1:y,x2:W-p.r,y2:y,stroke:"#e5e7eb"}}));
      svg.append(node("text",{{x:5,y:y+4,fill:"#657084","font-size":11}},v.toFixed(1)+"×"));
    }}
    names.forEach(k=>{{const pts=equity.series[k].map((v,i)=>{{
      const x=p.l+i*(W-p.l-p.r)/(equity.dates.length-1), y=p.t+(max-Math.log(v))*(H-p.t-p.b)/(max-min); return `${{x.toFixed(1)}},${{y.toFixed(1)}}`;
    }}).join(" "); svg.append(node("polyline",{{points:pts,fill:"none",stroke:colors[k],"stroke-width":k==="MOM_60_120_REGIME"?3:2,"stroke-linejoin":"round"}}));}});
    for(let year=2016;year<=2026;year+=2){{const idx=equity.dates.findIndex(d=>d.startsWith(String(year))); if(idx>=0){{const x=p.l+idx*(W-p.l-p.r)/(equity.dates.length-1); svg.append(node("text",{{x:x-13,y:H-10,fill:"#657084","font-size":11}},String(year)));}}}}
    const overlay=node("rect",{{x:p.l,y:p.t,width:W-p.l-p.r,height:H-p.t-p.b,fill:"transparent"}});
    const tip=document.getElementById("tip"); overlay.addEventListener("mousemove",e=>{{
      const box=svg.getBoundingClientRect(), x=(e.clientX-box.left)/box.width*W, idx=Math.max(0,Math.min(equity.dates.length-1,Math.round((x-p.l)/(W-p.l-p.r)*(equity.dates.length-1))));
      tip.innerHTML=`<b>${{equity.dates[idx]}}</b><br>`+names.map(k=>`${{labels[k]}}: ${{equity.series[k][idx].toFixed(2)}}×`).join("<br>");
      tip.style.display="block"; tip.style.left=Math.min(e.offsetX+12,box.width-150)+"px"; tip.style.top=(e.offsetY+8)+"px";
    }}); overlay.addEventListener("mouseleave",()=>tip.style.display="none"); svg.append(overlay);
  }} render();
}}
function drawCost() {{
  const svg=document.getElementById("costChart"), W=700,H=230,p={{l:48,r:14,t:12,b:34}}; svg.setAttribute("viewBox",`0 0 ${{W}} ${{H}}`);
  const names=[...new Set(costData.map(d=>d.strategy))], max=.32;
  for(let i=0;i<4;i++){{const y=p.t+i*(H-p.t-p.b)/3,v=max-i*max/3; svg.append(node("line",{{x1:p.l,y1:y,x2:W-p.r,y2:y,stroke:"#e5e7eb"}})); svg.append(node("text",{{x:3,y:y+4,fill:"#657084","font-size":11}},Math.round(v*100)+"%"));}}
  names.forEach(k=>{{const a=costData.filter(d=>d.strategy===k); const pts=a.map(d=>{{const x=p.l+d.bps/50*(W-p.l-p.r),y=p.t+(max-d.cagr)/max*(H-p.t-p.b);return `${{x}},${{y}}`;}}).join(" "); svg.append(node("polyline",{{points:pts,fill:"none",stroke:colors[k],"stroke-width":k==="MOM_60_120_REGIME"?3:2}}));}});
  [0,10,25,50].forEach(v=>{{const x=p.l+v/50*(W-p.l-p.r); svg.append(node("text",{{x:x-8,y:H-8,fill:"#657084","font-size":11}},v+"bp"));}});
}}
drawEquity(); drawCost();
</script>
</body>
</html>
"""

(RESULTS / "report.html").write_text(html, encoding="utf-8")
print(RESULTS / "report.html")
