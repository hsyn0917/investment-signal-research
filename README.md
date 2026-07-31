# 日本株・週次ランキング戦略

現行の日経225構成銘柄を対象に、週次で複数の価格・出来高戦略を比較する
再現可能なバックテストです。

実行:

```bash
/Users/khosoya/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  /Users/khosoya/20260731_Investment/backtest_weekly_japan.py
```

結果は `results/`、取得済みデータは `data/` に保存されます。再実行時はキャッシュを
利用します。

## 週次シグナル

市場判定と売買指示の自動生成については
[AUTOMATION.md](AUTOMATION.md)を参照してください。
# 日米統合ポートフォリオ・シグナル

公開レポート:
[日米統合投資戦略 — 日本株 + SOXL / TQQQ](https://soxl-tqqq-risk-report.hsyn0917.chatgpt.site/)

このディレクトリには、週次の日本株モメンタム戦略と、日次の
TQQQ / SOXLトレンド・ボラティリティ戦略が含まれます。
`generate_combined_signal.py` は両者の最新シグナルを読み、
一つの目標ポートフォリオと売買差分に変換します。

初期の資本枠は日本株80%、米国LETF20%です。
`portfolio_config.json` の `japan_sleeve` と `us_letf_sleeve` で変更できます。
日本株戦略がリスク抑制中に使わない枠は `JPY_CASH`、
米国戦略が使わない枠は `BIL` として表示します。

## 実行

米国データを更新し、日本株の最新週次シグナルと統合します。

```bash
./run_combined_signal.sh
```

日本株シグナルも更新する週次実行は次の順序です。

```bash
python3 generate_weekly_signal.py
python3 signal_system/signal_engine.py
python3 generate_combined_signal.py
```

統合結果は `combined_signals/` にJSON、Markdown、CSV、HTMLで保存されます。
これはシグナル生成専用で、証券会社への自動発注は行いません。
