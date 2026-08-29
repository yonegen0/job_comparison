# 求人比較資料PDF生成ツール

求人比較Markdownを、設定で固定した10件の元求人PDFと照合し、完全ローカルでA4・12ページの比較資料へ変換します。入力Markdownと元求人PDFは読み取り専用です。

## GUIでの利用

配布版の`求人比較ツールを起動.bat`をダブルクリックし、「比較資料PDF生成」タブを開きます。比較Markdown、既存の元PDF対応JSON、出力PDFを選び、「事前確認」で10求人と判定内容を確認してから「PDFを生成」を押します。

GUIは元PDF対応JSONを自動作成・修正せず、類似PDFへの切替やSHA-256更新も行いません。検証済みMarkdownと検証結果CSVは既定で出力PDFと同じフォルダーへ生成されます。以下のCLIも引き続き利用できます。

## 必要環境

- Windows
- `py`ランチャーで起動できるPython
- `pypdf`（既存の`requirements.txt`に記載）
- Microsoft EdgeまたはGoogle Chrome

外部API、クラウドAI、Webフォント、CDN、OCRは使用しません。実行時のネットワーク接続は不要です。

## 事前確認

```powershell
py job_comparison_report.py `
  --input "C:\Users\user\Downloads\# 求人比較資料_オフィスワーカー.md" `
  --source-manifest ".\reports\オフィスワーカー_sources.json" `
  --output "C:\Users\user\Downloads\求人比較資料_オフィスワーカー.pdf"
```

`--apply`を付けない限り、PDF・Markdown・CSVは変更されません。

## 本実行

```powershell
py job_comparison_report.py `
  --input "C:\Users\user\Downloads\# 求人比較資料_オフィスワーカー.md" `
  --source-manifest ".\reports\オフィスワーカー_sources.json" `
  --output "C:\Users\user\Downloads\求人比較資料_オフィスワーカー.pdf" `
  --apply
```

通常は次を生成します。

- `C:\Users\user\Downloads\求人比較資料_オフィスワーカー.pdf`
- `reports\求人比較資料_オフィスワーカー_検証済み.md`
- `reports\求人比較資料_オフィスワーカー_検証結果.csv`

中間HTMLも確認したい場合は`--keep-html`を追加します。表紙日付を変更する場合は`--date YYYY-MM-DD`を指定します。

## 終了コード

- `0`: 警告なしで完了
- `2`: 未確認条件、複数記載、記載なし、修正項目を含むが生成完了
- `1`: 入力不正、指定PDF欠落、元PDFのSHA-256変更、レイアウト超過、PDF生成失敗、出力失敗

## 元PDF対応設定

`reports\オフィスワーカー_sources.json`で、順位、会社名、募集ポジション、元PDFの絶対パス、SHA-256を固定しています。類似ファイルへの自動切替は行いません。

PDFが正式に差し替えられた場合は、内容を確認してから設定内のSHA-256と必要な修正・併記ルールを更新します。partnersは未経験版、レバレジーズは名古屋CA版を明示的に指定しています。

## 失敗時と復旧

成果物は一時ファイルとして完成・検証してから正式名へ切り替えます。確定途中で失敗した場合は既存成果物を復元し、不完全PDFを正式名へ残しません。

`.job_comparison_report.lock`が残っていて、同じ処理が実行中でないことを確認できた場合だけ削除してください。入力Markdownと元求人PDFは処理対象ではないため、復旧操作は不要です。

PDFが12ページでない、A4でない、求人名が欠落している、または`LAYOUT_ERROR`を含む場合は成果物を確定しません。

## テスト

```powershell
py -m pytest tests\test_comparison_report.py -q
```
