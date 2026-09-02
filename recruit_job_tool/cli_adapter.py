"""Non-interactive developer CLI backed by the application facade."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .app_paths import AppPaths
from .application import RecruitJobApplication
from .errors import RecruitJobToolError
from .models import JobSource
from .providers.errors import ProviderError
from .single_instance import SingleInstanceLock
from .workflow import ImportMode


def parser_for(provider: JobSource) -> argparse.ArgumentParser:
    label = "JoBins" if provider is JobSource.JOBINS else "CareerBank"
    parser = argparse.ArgumentParser(
        prog=f"python -m recruit_job_tool.{provider.value.lower()}",
        description=f"{label}の開発者向け内部CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    importer = subparsers.add_parser("import")
    importer.add_argument("input_path", type=Path)
    importer.add_argument(
        "--mode",
        choices=tuple(mode.value for mode in ImportMode),
        default=ImportMode.REPLACE.value,
    )
    subparsers.add_parser("export")
    subparsers.add_parser("restore")
    return parser


def run_provider_cli(
    provider: JobSource,
    argv: list[str] | None = None,
    *,
    paths_factory=AppPaths.from_local_app_data,
) -> int:
    args = parser_for(provider).parse_args(argv)
    try:
        with SingleInstanceLock():
            paths = paths_factory()
            paths.initialize()
            app = RecruitJobApplication(paths)
            if args.command == "import":
                result = app.import_saved_html(
                    provider=provider,
                    input_path=args.input_path,
                    mode=ImportMode(args.mode),
                )
                print(f"{provider.value}: {result.current_count}件")
            elif args.command == "export":
                result = app.export_csv(provider=provider)
                print(f"{provider.value}: {result.count}件")
            else:
                result = app.restore_previous(provider=provider)
                print(f"{provider.value}: {result.count}件")
        return 0
    except (RecruitJobToolError, ProviderError) as error:
        code = error.error_code if isinstance(error, RecruitJobToolError) else error.code
        print(f"[ERROR:{code}] 処理を完了できませんでした。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n操作を中止しました。", file=sys.stderr)
        return 130
    except Exception:
        print("[ERROR:APP001] 予期しないエラーで終了しました。", file=sys.stderr)
        return 1


__all__ = ["parser_for", "run_provider_cli"]
