"""Internal CareerBank command adapter."""

from recruit_job_tool.cli_adapter import run_provider_cli
from recruit_job_tool.models import JobSource


def main(argv: list[str] | None = None) -> int:
    return run_provider_cli(JobSource.CAREERBANK, argv)


if __name__ == "__main__":
    raise SystemExit(main())
