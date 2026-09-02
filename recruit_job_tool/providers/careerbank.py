"""Validated import of a saved CareerBank search-results HTML page."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from recruit_job_tool.models import JobSource, RawJob
from recruit_job_tool.constants import MAX_CURRENT_JOBS

from .errors import (
    ImportValidationError,
    UnsupportedFormatError,
)

MAX_IMPORT_BYTES = 20 * 1024 * 1024
EXTRACTOR_VERSION = "careerbank-search-html-v2"

_IGNORED_TEXT_TAGS = frozenset({"script", "style", "noscript"})
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_PAGE_TITLE_PATTERN = re.compile(
    r"^.+の検索結果 – キャリアバンクジョブサーチ$"
)
_DETAIL_PATH_PATTERN = re.compile(r"^/jobs/(?P<source_job_id>[0-9]+)$")
_SAVED_URL_PATTERN = re.compile(r"https?://[^\s)]+")
_HORIZONTAL_SPACE = re.compile(r"[^\S\r\n]+")
_EMPTY_CONDITION_LINES = frozenset({"-", "－", "―"})
_REQUIRED_CONDITION_LABEL = (
    r"(?:必須(?:条件|要件|スキル(?:・経験・資格)?)|必要要件[／/]MUST)"
)
_PREFERRED_CONDITION_LABEL = r"歓迎(?:条件|要件|スキル(?:・経験・資格)?)"
_REQUIRED_CONDITION_MARKER = re.compile(
    rf"^(?:"
    rf"＜\s*{_REQUIRED_CONDITION_LABEL}\s*＞\s*"
    rf"(?:[:：]\s*)?(?P<angle>.*)"
    rf"|【\s*{_REQUIRED_CONDITION_LABEL}\s*】\s*"
    rf"(?:[:：]\s*)?(?P<bracket>.*)"
    rf"|■\s*{_REQUIRED_CONDITION_LABEL}"
    rf"(?:\s*[:：]\s*(?P<square>.*))?"
    rf")$",
    re.IGNORECASE,
)
_PREFERRED_CONDITION_MARKER = re.compile(
    rf"^(?:"
    rf"＜\s*{_PREFERRED_CONDITION_LABEL}\s*＞\s*"
    rf"(?:[:：]\s*)?(?P<angle>.*)"
    rf"|【\s*{_PREFERRED_CONDITION_LABEL}\s*】\s*"
    rf"(?:[:：]\s*)?(?P<bracket>.*)"
    rf"|■\s*{_PREFERRED_CONDITION_LABEL}"
    rf"(?:\s*[:：]\s*(?P<square>.*))?"
    rf")$",
    re.IGNORECASE,
)
_CARD_CLASSES = frozenset({"panel", "panel-default"})
_PANEL_BODY_CLASSES = frozenset({"panel-body"})
_TITLE_CLASSES = frozenset({"job_detail_h3"})
_TABLE_CLASSES = frozenset({"table", "table-bordered"})
_REQUIRED_LABELS = (
    "企業名",
    "業界",
    "応募資格",
    "年収",
    "最終学歴",
    "年齢",
    "想定勤務地詳細",
    "募集人数",
    "成功報酬条件",
)


@dataclass(slots=True)
class _HtmlNode:
    tag: str
    attributes: dict[str, str]
    parent: _HtmlNode | None = None
    content: list[str | _HtmlNode] = field(default_factory=list)


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HtmlNode("document", {})
        self._stack = [self.root]
        self.saved_urls: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        node = _HtmlNode(
            tag=tag.lower(),
            attributes={name.lower(): value or "" for name, value in attrs},
            parent=self._stack[-1],
        )
        self._stack[-1].content.append(node)
        if node.tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        node = _HtmlNode(
            tag=tag.lower(),
            attributes={name.lower(): value or "" for name, value in attrs},
            parent=self._stack[-1],
        )
        self._stack[-1].content.append(node)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == normalized:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if any(node.tag in _IGNORED_TEXT_TAGS for node in self._stack):
            return
        self._stack[-1].content.append(data)

    def handle_comment(self, data: str) -> None:
        if "saved from url=" not in data.lower():
            return
        match = _SAVED_URL_PATTERN.search(data)
        if match is not None:
            self.saved_urls.append(match.group(0))


class CareerBankSearchHtmlImporter:
    """Validate one saved search-results page and convert all visible cards."""

    def __init__(self, *, max_bytes: int = MAX_IMPORT_BYTES) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise TypeError("max_bytes must be an integer.")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive.")
        self._max_bytes = max_bytes

    def load(self, path: Path) -> list[RawJob]:
        source_path = Path(path)
        if source_path.suffix.lower() not in {".html", ".htm"}:
            raise UnsupportedFormatError(
                "CareerBank import accepts only saved HTML files.",
                code="CAREERBANK_INPUT_FORMAT_INVALID",
            )
        try:
            stat_result = source_path.stat()
        except OSError as error:
            raise ImportValidationError(
                "CareerBank input file could not be inspected.",
                code="CAREERBANK_INPUT_UNAVAILABLE",
            ) from error
        if not stat.S_ISREG(stat_result.st_mode):
            raise ImportValidationError(
                "CareerBank input path must be a regular file.",
                code="CAREERBANK_INPUT_NOT_FILE",
            )
        if stat_result.st_size > self._max_bytes:
            raise ImportValidationError(
                "CareerBank input file exceeds the size limit.",
                code="CAREERBANK_INPUT_TOO_LARGE",
            )
        try:
            payload = source_path.read_bytes()
        except OSError as error:
            raise ImportValidationError(
                "CareerBank input file could not be read completely.",
                code="CAREERBANK_INPUT_UNAVAILABLE",
            ) from error
        if b"\x00" in payload:
            raise ImportValidationError(
                "CareerBank input file contains invalid data.",
                code="CAREERBANK_ENCODING_INVALID",
            )
        try:
            document = payload.decode("utf-8-sig", errors="strict")
        except UnicodeError as error:
            raise ImportValidationError(
                "CareerBank input file must be UTF-8 HTML.",
                code="CAREERBANK_ENCODING_INVALID",
            ) from error

        parser = _TreeParser()
        try:
            parser.feed(document)
            parser.close()
        except (ValueError, RecursionError) as error:
            raise ImportValidationError(
                "CareerBank input does not contain a usable HTML structure.",
                code="CAREERBANK_PAGE_INVALID",
            ) from error

        article = _validated_results_article(parser)
        cards = [
            child
            for child in _child_nodes(article)
            if child.tag == "div" and _CARD_CLASSES.issubset(_class_tokens(child))
        ]
        if not cards:
            raise ImportValidationError(
                "CareerBank search-results page does not contain job cards.",
                code="CAREERBANK_PAGE_INVALID",
            )

        timestamp = datetime.now(timezone.utc)
        batch_id = uuid4().hex
        jobs: list[RawJob] = []
        source_job_ids: set[str] = set()
        for card in cards:
            raw_job = _extract_card(card, timestamp=timestamp, batch_id=batch_id)
            source_job_id = raw_job.source_job_id
            if source_job_id is None or source_job_id in source_job_ids:
                raise ImportValidationError(
                    "CareerBank input contains a duplicate source job ID.",
                    code="CAREERBANK_SOURCE_JOB_ID_DUPLICATE",
                )
            source_job_ids.add(source_job_id)
            jobs.append(raw_job)
        return jobs


class CareerBankProvider:
    """Saved search-results HTML provider."""

    def __init__(
        self,
        *,
        importer: CareerBankSearchHtmlImporter,
    ) -> None:
        self._importer = importer

    def load(self, path: Path, *, limit: int = MAX_CURRENT_JOBS) -> list[RawJob]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError(
                f"limit must be an integer from 1 to {MAX_CURRENT_JOBS}."
            )
        if not 1 <= limit <= MAX_CURRENT_JOBS:
            raise ValueError(f"limit must be from 1 to {MAX_CURRENT_JOBS}.")
        return self._importer.load(path)[:limit]


def _validated_results_article(parser: _TreeParser) -> _HtmlNode:
    titles = [
        _single_line_text(node)
        for node in _walk(parser.root)
        if node.tag == "title"
    ]
    headings = [
        _single_line_text(node)
        for node in _walk(parser.root)
        if node.tag == "h1"
    ]
    bodies = [node for node in _walk(parser.root) if node.tag == "body"]
    articles = [node for node in _walk(parser.root) if node.tag == "article"]
    valid_page = (
        len(titles) == 1
        and _PAGE_TITLE_PATTERN.fullmatch(titles[0]) is not None
        and headings == ["求人一覧"]
        and len(bodies) == 1
        and {"search", "search-results"}.issubset(_class_tokens(bodies[0]))
        and len(articles) == 1
        and sum(
            child.tag == "div" and "c-article-count" in _class_tokens(child)
            for child in _child_nodes(articles[0])
        )
        == 1
    )
    if not valid_page:
        raise ImportValidationError(
            "CareerBank input is not a recognized search-results page.",
            code="CAREERBANK_PAGE_INVALID",
        )
    for saved_url in parser.saved_urls:
        if not _is_valid_saved_origin(saved_url):
            raise ImportValidationError(
                "CareerBank input has an unexpected saved-page origin.",
                code="CAREERBANK_PAGE_INVALID",
            )
    return articles[0]


def _is_valid_saved_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").lower() == "careerbank-jobsearch.com"
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and parsed.path == "/"
    )


def _extract_card(
    card: _HtmlNode,
    *,
    timestamp: datetime,
    batch_id: str,
) -> RawJob:
    panel_bodies = [
        node
        for node in _walk(card)
        if node is not card
        and node.tag == "div"
        and _PANEL_BODY_CLASSES.issubset(_class_tokens(node))
    ]
    if len(panel_bodies) != 1:
        raise _card_error()
    panel_body = panel_bodies[0]
    headings = [
        node
        for node in _walk(panel_body)
        if node.tag == "h3" and _TITLE_CLASSES.issubset(_class_tokens(node))
    ]
    if len(headings) != 1:
        raise _card_error()
    title_links = [node for node in _walk(headings[0]) if node.tag == "a"]
    if len(title_links) != 1:
        raise _card_error()
    job_title = _clean_display_value(_node_text(headings[0]))
    if not job_title:
        raise _required_value_error()

    tables = [
        node
        for node in _walk(panel_body)
        if node.tag == "table" and _TABLE_CLASSES.issubset(_class_tokens(node))
    ]
    if len(tables) != 1:
        raise _card_error()
    rows = [node for node in _walk(tables[0]) if node.tag == "tr"]
    values: dict[str, str] = {}
    for row in rows:
        headers = [child for child in _child_nodes(row) if child.tag == "th"]
        cells = [child for child in _child_nodes(row) if child.tag == "td"]
        if len(headers) != 1 or len(cells) != 1:
            raise _card_error()
        label = _single_line_text(headers[0])
        if label in values:
            raise _card_error()
        values[label] = _clean_display_value(_node_text(cells[0]))
    if len(rows) != len(_REQUIRED_LABELS) or set(values) != set(_REQUIRED_LABELS):
        raise _card_error()
    if any(not values[label] for label in _REQUIRED_LABELS):
        raise _required_value_error()

    required_conditions, preferred_conditions = _extract_conditions(
        values["応募資格"]
    )
    source_url, source_job_id = _validated_detail(card, title_links[0])
    return RawJob(
        source=JobSource.CAREERBANK,
        source_job_id=source_job_id,
        source_url=source_url,
        fields={
            "job_title": job_title,
            "company_name": values["企業名"],
            "industry": values["業界"],
            "occupation": "",
            "employment_type": "",
            "required_conditions": required_conditions,
            "preferred_conditions": preferred_conditions,
            "salary_text": values["年収"],
            "education": values["最終学歴"],
            "age_requirement": values["年齢"],
            "location": values["想定勤務地詳細"],
            "number_of_positions": values["募集人数"],
            "commission": "",
            "success_fee_conditions": values["成功報酬条件"],
            "overtime": "",
        },
        acquired_at=timestamp,
        last_verified_at=timestamp,
        batch_id=batch_id,
        extractor_version=EXTRACTOR_VERSION,
    )


def _validated_detail(
    card: _HtmlNode,
    title_link: _HtmlNode,
) -> tuple[str, str]:
    candidates: list[_HtmlNode] = []
    seen_nodes: set[int] = set()
    for node in _walk(card):
        if node.tag != "a":
            continue
        href = node.attributes.get("href", "")
        classes = _class_tokens(node)
        if node is title_link or "/jobs/" in href or "btn-default" in classes:
            marker = id(node)
            if marker not in seen_nodes:
                candidates.append(node)
                seen_nodes.add(marker)
    if not candidates:
        raise _detail_error()

    normalized: dict[str, str] = {}
    for node in candidates:
        href = node.attributes.get("href", "")
        try:
            parsed = urlsplit(href)
            port = parsed.port
        except ValueError as error:
            raise _detail_error() from error
        match = _DETAIL_PATH_PATTERN.fullmatch(parsed.path)
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").lower() != "careerbank-jobsearch.com"
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or match is None
        ):
            raise _detail_error()
        source_job_id = match.group("source_job_id")
        url = urlunsplit(
            ("https", "careerbank-jobsearch.com", parsed.path, "", "")
        )
        normalized[url] = source_job_id
    if len(normalized) != 1:
        raise _detail_error()
    return next(iter(normalized.items()))


def _walk(node: _HtmlNode):
    yield node
    for child in _child_nodes(node):
        yield from _walk(child)


def _child_nodes(node: _HtmlNode) -> list[_HtmlNode]:
    return [item for item in node.content if isinstance(item, _HtmlNode)]


def _class_tokens(node: _HtmlNode) -> frozenset[str]:
    return frozenset(node.attributes.get("class", "").split())


def _node_text(node: _HtmlNode) -> str:
    parts: list[str] = []
    for item in node.content:
        if isinstance(item, str):
            parts.append(item)
        elif item.tag == "br":
            parts.append("\n")
        else:
            parts.append(_node_text(item))
    value = "".join(parts).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in value.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _single_line_text(node: _HtmlNode) -> str:
    return " ".join(_node_text(node).split())


def _clean_display_value(value: str) -> str:
    normalized = value.strip()
    return "" if " ".join(normalized.split()) in {"-", "－", "―"} else normalized


def _condition_lines(value: str) -> list[str]:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [
        _HORIZONTAL_SPACE.sub(" ", line).strip()
        for line in normalized.split("\n")
    ]
    return [
        line
        for line in lines
        if line and line not in _EMPTY_CONDITION_LINES
    ]


def _parse_condition_marker(line: str) -> tuple[str | None, str]:
    for section, pattern in (
        ("required", _REQUIRED_CONDITION_MARKER),
        ("preferred", _PREFERRED_CONDITION_MARKER),
    ):
        match = pattern.fullmatch(line)
        if match is None:
            continue
        remainder = next(
            (value for value in match.groupdict().values() if value is not None),
            "",
        )
        remainder = _HORIZONTAL_SPACE.sub(" ", remainder).strip()
        if remainder in _EMPTY_CONDITION_LINES:
            remainder = ""
        return section, remainder
    return None, line


def _extract_conditions(value: str) -> tuple[str, str]:
    current_section = "required"
    required_lines: list[str] = []
    preferred_lines: list[str] = []
    destinations = {
        "required": required_lines,
        "preferred": preferred_lines,
    }

    for line in _condition_lines(value):
        section, remainder = _parse_condition_marker(line)
        if section is not None:
            current_section = section
            if remainder:
                destinations[current_section].append(remainder)
            continue
        destinations[current_section].append(remainder)

    if not required_lines and not preferred_lines:
        raise _required_value_error()
    return " / ".join(required_lines), " / ".join(preferred_lines)


def _card_error() -> ImportValidationError:
    return ImportValidationError(
        "CareerBank job card has an invalid or ambiguous structure.",
        code="CAREERBANK_CARD_INVALID",
    )


def _required_value_error() -> ImportValidationError:
    return ImportValidationError(
        "CareerBank job card contains an empty required value.",
        code="CAREERBANK_REQUIRED_VALUE_MISSING",
    )


def _detail_error() -> ImportValidationError:
    return ImportValidationError(
        "CareerBank job card contains an invalid detail link.",
        code="CAREERBANK_DETAIL_URL_INVALID",
    )


__all__ = [
    "CareerBankProvider",
    "CareerBankSearchHtmlImporter",
    "EXTRACTOR_VERSION",
    "MAX_IMPORT_BYTES",
]
