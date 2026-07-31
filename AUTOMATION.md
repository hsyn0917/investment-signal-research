# 日米統合売買シグナル自動化

## 推奨構成

日本株は週次、米国LETFは日次で判定し、最後に資本枠80%対20%で統合します。
各エンジンの時間軸は混ぜず、統合層だけを共通化しています。

1. ローカルで4〜8週間、毎週の出力と実際の約定可能性を確認
2. 非公開GitHubリポジトリへ移し、GitHub Actionsで定期実行
3. 売買指示を確認して手動発注
4. 十分な検証後に限り、証券会社APIをローカル環境から接続

証券口座の認証情報をGitHubへ置く自動発注は、現在の構成では行いません。

## 出力

- `signals/signal_report.html`: 市場判定と売買指示
- `signals/trade_instructions.csv`: Excel等で開ける指示一覧
- `signals/latest_signal.json`: 次回比較用の前回目標
- `signal_system/output/`: TQQQ / SOXLの日次シグナル
- `combined_signals/latest_portfolio.html`: 日米統合レポート
- `combined_signals/trade_instructions.csv`: 統合後の売買差分

売買指示は実口座の残高ではなく、前回シグナルとの差分です。

## 手動実行

キャッシュを使った試験実行:

```bash
/Users/khosoya/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  /Users/khosoya/20260731_Investment/generate_weekly_signal.py
```

市場データを更新して実行:

```bash
cd /Users/khosoya/20260731_Investment
REFRESH_DATA=1 ./run_weekly_signal.sh
```

米国シグナルだけを更新し、直近の日本株週次シグナルと統合:

```bash
cd /Users/khosoya/20260731_Investment
./run_combined_signal.sh
```

## macOSでの定期実行

テンプレートは `local_schedule/` にあります。日本株更新を含む週次版は土曜日
8:30、米国更新と統合だけを行う日次版は火〜金曜日7:17（いずれも日本時間）です。
Macが停止・スリープ中の場合は実行が遅れる可能性があります。

インストールはまだ行っていません。

## GitHub Actions

`.github/workflows/weekly-signal.yml` は土曜日8:17に日本株・米国・統合シグナルを
更新します。`.github/workflows/daily-us-combined-signal.yml` は火〜金曜日7:17に
米国と統合シグナルを更新します（いずれも日本時間）。

- リポジトリは非公開を推奨
- 手動実行にも対応
- 結果はActionsのSummaryと`signals/`へ保存
- 更新されたシグナルはActions botがリポジトリへコミット
- 証券口座の認証情報は使用しない

GitHubのスケジュール実行は混雑時に遅延する可能性があるため、寄り付き直前ではなく
十分前に設定しています。

## 次に必要な改善

- Yahoo配信データからJ-Quants等の正式なデータへ切り替える
- 実口座の現在保有比率をCSVで取り込み、実際の注文株数を計算する
- 決算発表予定日の直前銘柄を除外する
- メール、LINE、Slack等への通知を追加する
- 二重実行防止、データ鮮度、異常値の監視を追加する
