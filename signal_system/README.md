# TQQQ / SOXL 日次シグナル

バックテストと同じ固定ルールを毎日再計算し、TQQQ・SOXL・BILの目標配分を出力します。

このシステムは**通知専用**です。証券口座には接続せず、注文を送信しません。

## 判定ルール

1. TQQQはQQQ、SOXLはSOXXの未調整終値が200日移動平均を上回る場合のみリスクオン。
2. 各レバレッジETFの20日実現ボラティリティから、年率25%を目標にETF保有比率を0〜100%で調整。
3. TQQQ袖とSOXL袖を50/50に設定。
4. 非投資部分はBILとして表示。
5. 前営業日の計算上の目標比率との差が0.5ポイント以上なら「増やす／減らす」と表示。

## 手動実行

Python 3.11以降で実行します。外部パッケージは不要です。

```bash
cd /Users/khosoya/20260731_Investment/signal_system
python3 -m unittest discover -s tests -v
python3 signal_engine.py
```

結果は次の形式で `output/` に保存されます。

- `latest_signal.md`: 人間が読む売買指示
- `latest_signal.json`: 他システム連携用
- `latest_signal.csv`: 表計算用

## Macでの定時実行

`local/com.openai.letf-signal.plist.example` を
`~/Library/LaunchAgents/com.openai.letf-signal.plist` にコピーして読み込むと、
日本時間の火曜〜土曜07:17に実行できます。

Macが停止中、スリープ中、オフラインの場合は実行時刻がずれる可能性があります。
まず手動実行が成功することを確認してから有効化してください。

## GitHub Actions

このフォルダをGitHubリポジトリとしてpushすると、
`.github/workflows/daily-signal.yml` が日本時間の火曜〜土曜07:17に実行されます。
結果はActions画面のSummaryと、30日保存されるArtifactで確認できます。

定時ワークフローはGitHub側の混雑で遅延または省略される可能性があります。
重要な判断では `workflow_dispatch` による手動再実行と価格基準日の確認が必要です。

通知用のAPIキーを追加する場合は、設定ファイルやコードに直接書かず、
GitHub Actions Secretsを利用してください。

## 指示の意味

- `BUY`: 前営業日はゼロ、当日は正の目標比率
- `INCREASE`: 目標比率を0.5ポイント以上増加
- `REDUCE`: 目標比率を0.5ポイント以上減少
- `EXIT_TO_CASH`: 目標比率をゼロにする
- `HOLD`: 変化が0.5ポイント未満

目標比率は総運用資金に対する割合です。実際の売買数量は現在残高、税金、
為替、最低注文単位、スプレッドを反映して別途決定する必要があります。

## 安全上の制約

- データが4暦日を超えて古い場合は処理を失敗させます。
- 異常な日次リターンや不自然なボラティリティを検出すると停止します。
- Yahoo Financeの非公式APIに依存しているため、データ取得停止に備えた監視が必要です。
- 本出力は投資助言、収益保証、証券会社への発注指示ではありません。
