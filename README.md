# 求人比較ツール 0.3.1

JoBins／CareerBankの保存済み検索結果HTML取込、求人PDFの整理、全文Markdown統合、10求人の比較資料PDF生成を、1つのWindows GUIで行う完全ローカルツールです。求人媒体や外部サービスへ自動接続せず、ファイルを送信しません。

## GUIの4タブ

1. 「求人取得」: 保存済みJoBins／CareerBank検索結果HTMLを取り込み、媒体別CSV、直前状態、AI比較用JSONと任意ガイドを管理します。
2. 「求人PDF整理」: PDFを解析し、改名内容、`求人情報.json`、`処理レポート.json`を事前確認してから一括確定します。
3. 「求人PDF全文統合テキスト化」: 選択フォルダー直下のPDF本文を、境界マーカー付きの`output\PDF全文_統合テキスト化.md`へ統合します。OCR、要約、補正は行いません。
4. 「比較資料PDF生成」: 比較Markdownと元PDF対応JSONを照合し、A4縦12ページのPDF、検証済みMarkdown、検証結果CSVを生成します。

各工程の「事前確認」はファイルや`output`フォルダーを作成しません。本実行時も必ず最新状態をprepareし、確認後のPDF・JSON・旧CSV・Markdown変更は`CHANGED001`として停止します。

## 求人取得と既存データ

初回は「求人取得」タブの案内から初期設定を開始します。独立版で作成済みの`%LOCALAPPDATA%\RecruitJobTool`をそのまま再利用し、旧版フォルダーから移行する場合も移行元を変更・削除しません。独立版が起動中の場合は求人取得だけを停止し、PDF関連の3タブは引き続き利用できます。

求人取得のAI用JSONは`job-comparison-jobs-v4`です。PDF整理の`job-comparison/job-info`や比較資料用manifestとは別形式であり、自動変換・自動受け渡しは行いません。

## JSON出力と旧CSV移行

求人PDF整理の出力フォルダーを省略すると`<PDFフォルダー>\output`へ保存します。

- `求人情報.json`: `format`が`job-comparison/job-info`、`format_version`が`1`の求人情報配列です。標準20項目と既存追加項目を保持します。
- `処理レポート.json`: `format`が`job-comparison/processing-report`、`format_version`が`1`の処理記録です。

既存JSONには置換・別名保存・キャンセルを解析前に選びます。別名保存は2ファイルに同じ`(n)`を付けます。基準JSONがなく旧`求人情報.csv`が見つかった場合は、初回だけJSONへ取り込めます。CSVは変更・削除されません。

## 全文統合テキスト化とAI添付

「整理タブと同じフォルダーを使用」は、その時点の値を1回コピーします。その後の2タブは独立しています。空ページ、NUL置換、大容量は警告付き成功です。読取不能PDFが1件でもある場合は生成しません。

`求人情報.json`と`PDF全文_統合テキスト化.md`を別AIへ添付する場合は、Markdownの`source_file`とJSONの`新ファイル名`を照合してください。JSONの求人配列は`jobs`です。値がない場合は推測せず`記載なし`とし、既存値を不用意に上書きしないでください。

## CLI

```powershell
py job_pdf_tool.py --input "C:\求人PDF" --output-json "C:\出力\求人情報.json" --report-json "C:\出力\処理レポート.json"
py job_pdf_tool.py --input "C:\求人PDF" --apply --import-main-csv "C:\旧\求人情報.csv"
```

既定はdry-runです。旧`--output-csv`と`--report-csv`は廃止されました。比較資料CLIの検証結果CSVは従来どおりです。詳細は`README_job_pdf_tool.md`と`README_job_comparison_report.md`を参照してください。

## 配布

- EXE: `JobComparisonTool.exe`
- ZIP: `release\JobComparisonTool-0.3.1-win-x64.zip`
- checksum: `release\SHA256SUMS-0.3.1.txt`

比較資料PDF生成にはEdgeまたはChromeが必要です。配布物には実求人PDF、JSON、CSV、Markdown、manifest、単体Exporter EXEを含めません。
