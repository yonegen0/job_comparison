"""媒体別CSVから求人比較AI向けMarkdownとJSONを生成するCLI。"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

from recruit_job_tool.app_paths import AppPaths
from recruit_job_tool.application import RecruitJobApplication
from recruit_job_tool.errors import PromptOutputError, RecruitJobToolError
from recruit_job_tool.models import JobSource
from recruit_job_tool.single_instance import SingleInstanceLock



_PROVIDERS = {
    "careerbank": JobSource.CAREERBANK,
    "jobins": JobSource.JOBINS,
}

_JST = timezone(timedelta(hours=9), name="JST")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m recruit_job_tool.analysis_prompt",
        description="媒体別CSVから求人比較AI向けMarkdownとJSONを生成します。",
        epilog=(
            "既定出力（JST）:\n"
            "  <HTML保存フォルダー>/output/<provider>_analysis_jobs_YYYYMMDD_HHMMSS[_NN].json\n"
            "  <HTML保存フォルダー>/output/<provider>_analysis_guide_YYYYMMDD_HHMMSS[_NN].md\n"
            "求人JSONだけで分析できます。Markdownは任意の詳細ガイドです。"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("provider", choices=tuple(_PROVIDERS))
    parser.add_argument(
        "--input",
        type=Path,
        help="入力CSV（省略時: 媒体別に記憶したoutput内のCSV）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="旧CLI互換の出力Markdown指定（--guide-outputの別名）",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="主成果物の出力JSON（省略時はJST日時・独立連番付き）",
    )
    parser.add_argument(
        "--guide-output",
        type=Path,
        help="任意の詳細ガイドMarkdown",
    )
    parser.add_argument(
        "--no-guide",
        action="store_true",
        help="任意Markdownを生成せず求人JSONだけを生成する",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    provider = _PROVIDERS[args.provider]
    try:
        if args.output is not None and args.guide_output is not None:
            raise PromptOutputError("--outputと--guide-outputは同時指定できません")
        guide_path = args.guide_output or args.output
        if args.no_guide and guide_path is not None:
            raise PromptOutputError("--no-guideとMarkdown出力先は同時指定できません")
        with SingleInstanceLock():
            paths = AppPaths.from_local_app_data()
            paths.initialize()
            result = RecruitJobApplication(paths).create_analysis_files(
                provider=provider,
                input_path=args.input,
                json_path=args.json_output,
                guide_path=guide_path,
                include_guide=not args.no_guide,
            )
    except RecruitJobToolError as error:
        print(f"[ERROR:{error.error_code}] 処理を完了できませんでした。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n操作を中止しました。", file=sys.stderr)
        return 130
    except Exception:
        print("[ERROR:APP001] 予期しないエラーで終了しました。", file=sys.stderr)
        return 1

    print(f"分析用求人JSONを生成しました（{result.job_count}件）。")
    print(f"求人JSON（必須）: {result.json_path.name}")
    if result.guide_path is None:
        print("詳細ガイド（任意）: 作成されていません")
    else:
        print(f"詳細ガイド（任意）: {result.guide_path.name}")
    print("求人JSONだけを添付して分析できます。複数JSONもまとめて比較できます。")
    for warning in result.warnings:
        print(f"警告: {warning}", file=sys.stderr)
    return 0


def _output_paths(
    *,
    provider_name: str,
    markdown_path: Path | None,
    json_path: Path | None,
    generated_at: datetime | None = None,
) -> tuple[Path, Path]:
    try:
        instant = generated_at or _current_time_jst()
        if json_path is None:
            json_directory = markdown_path.parent if markdown_path is not None else Path("output")
            json_path = _next_cli_path(
                json_directory, provider_name, "jobs", ".json", instant
            )
        if markdown_path is None:
            markdown_path = _next_cli_path(
                json_path.parent, provider_name, "guide", ".md", instant
            )
        return markdown_path, json_path
    except (TypeError, ValueError) as exc:
        raise PromptOutputError("出力パスを決定できません") from exc


def _next_cli_path(
    directory: Path,
    provider_name: str,
    kind: str,
    extension: str,
    generated_at: datetime,
) -> Path:
    timestamp = _filename_timestamp(generated_at)
    sequence = 0
    while True:
        suffix = "" if sequence == 0 else f"_{sequence:02d}"
        candidate = directory / (
            f"{provider_name}_analysis_{kind}_{timestamp}{suffix}{extension}"
        )
        if not _path_is_occupied(candidate):
            return candidate
        sequence += 1


def _current_time_jst() -> datetime:
    return datetime.now(_JST).replace(microsecond=0)


def _filename_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PromptOutputError("生成日時が不正です")
    return value.astimezone(_JST).strftime("%Y%m%d_%H%M%S")


def _path_is_occupied(path: Path) -> bool:
    return path.exists() or path.is_symlink()


if __name__ == "__main__":
    raise SystemExit(main())
