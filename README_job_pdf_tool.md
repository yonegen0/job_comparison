# 求人PDF整理（JSON版）

求人PDFを外部へ送信せず解析し、求人情報JSON・処理レポートJSONの作成と安全なPDF改名を行います。PDF本文は変更しません。

## GUI

「求人PDF整理」タブでPDFフォルダー、任意の見出し別名JSON、任意のJSON出力フォルダーを選びます。空欄なら`<PDFフォルダー>\output\求人情報.json`と`処理レポート.json`を使います。「事前確認」はファイルやフォルダーを作りません。

既存JSONの置換では追加項目とPDF未検出jobを保持し、処理レポートを再生成します。別名保存では共通番号を付け、既存JSONを読み書きしません。旧`求人情報.csv`の初回移行では標準20列、追加列、PDF未検出行をJSONへ移しますが、CSVのbyte列は変更しません。

## CLI

```text
usage: job_pdf_tool.py --input PATH [--apply]
                       [--output-json PATH] [--report-json PATH]
                       [--import-main-csv PATH] [--aliases PATH]
```

- `--apply`なしはdry-runです。
- `--output-json`省略時、入力フォルダー直下の`求人情報*.json`が0件なら`求人情報.json`、1件ならそのファイル、複数ならエラーです。
- `--report-json`省略時は入力フォルダー直下の`処理レポート.json`です。
- CSV移行は`--import-main-csv`を明示した場合だけ行います。移行先JSONが既にある場合は拒否します。
- 旧`--output-csv`と`--report-csv`は使用できません。

終了コードは`0`が成功、`2`が警告付き成功または引数エラー、`1`が致命的失敗です。会社名・募集ポジションを確定できないPDFは求人情報と改名対象から除外し、元の場所へ残します。

## JSON仕様

`求人情報.json`は`format: job-comparison/job-info`、`format_version: 1`、`field_order`、`jobs`を持ちます。標準値は文字列で、絶対パスや実行日時は記録しません。`処理レポート.json`は`format: job-comparison/processing-report`と`reports`を持ち、ページだけが正の整数またはnullです。両方ともUTF-8（BOMなし）、2スペースindent、LF、末尾LFで決定的に保存します。

重複JSONキー、NaN、Infinity、schema不一致、標準項目欠落、field_order外のjobキーは`INPUT001`です。本実行では入力・出力をfingerprintで再検証し、PDF改名と2 JSONを同一トランザクションで確定します。途中失敗時は既存JSONとPDF名を復元します。
