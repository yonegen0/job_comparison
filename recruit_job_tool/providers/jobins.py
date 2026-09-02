"""Validated import of a saved JoBins search-results HTML page."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from recruit_job_tool.models import JobSource, RawJob
from recruit_job_tool.constants import MAX_CURRENT_JOBS

from .errors import (
    ImportValidationError,
    UnsupportedFormatError,
)

MAX_IMPORT_BYTES = 20 * 1024 * 1024
MAX_BATCH_LIMIT = MAX_CURRENT_JOBS
EXTRACTOR_VERSION = "jobins-search-html-v5"

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
_CARD_CLASSES = frozenset({"jb-relative", "jb-rounded-[8px]"})
_TITLE_CLASSES = frozenset(
    {"jb-text-agent-secondary", "jb-font-medium", "jb-text-[17px]"}
)
_EMPLOYMENT_PATTERN = re.compile(
    r"(?:正社員|契約社員|派遣社員|業務委託|パート|アルバイト)"
)
_DETAIL_PATH_PATTERN = re.compile(r"^/agent/jobs/[^/]+$")
_SAVED_URL_PATTERN = re.compile(r"https?://[^\s)]+")
_HORIZONTAL_SPACE = re.compile(r"[^\S\r\n]+")
_APPLICATION_CONDITIONS_LABEL = "応募条件"
_EMPTY_CONDITION_LINES = frozenset({"-", "－", "―"})
_REQUIRED_CONDITION_MARKER = re.compile(
    r"^(?:"
    r"■\s*必須(?:条件|要件)(?:\s*[:：]\s*(?P<square>.*))?"
    r"|【\s*必須(?:条件|要件)\s*】\s*(?:[:：]\s*)?(?P<bracket>.*)"
    r")$"
)
_PREFERRED_CONDITION_MARKER = re.compile(
    r"^(?:"
    r"■\s*歓迎(?:条件|要件)(?:\s*[:：]\s*(?P<square>.*))?"
    r"|【\s*歓迎(?:条件|要件)\s*】\s*(?:[:：]\s*)?(?P<bracket>.*)"
    r")$"
)


@dataclass(slots=True)
class _HtmlNode:
    tag: str
    attributes: dict[str, str]
    parent: _HtmlNode | None = None
    children: list[_HtmlNode] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    content: list[str | _HtmlNode] = field(default_factory=list)


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HtmlNode("document", {})
        self._stack = [self.root]
        self.saved_urls: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        node = _HtmlNode(
            tag=tag.lower(),
            attributes={name.lower(): value or "" for name, value in attrs},
            parent=self._stack[-1],
        )
        self._stack[-1].children.append(node)
        self._stack[-1].content.append(node)
        if node.tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        node = _HtmlNode(
            tag=tag.lower(),
            attributes={name.lower(): value or "" for name, value in attrs},
            parent=self._stack[-1],
        )
        self._stack[-1].children.append(node)
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
        self._stack[-1].text_parts.append(data)
        self._stack[-1].content.append(data)

    def handle_comment(self, data: str) -> None:
        if "saved from url=" not in data.lower():
            return
        match = _SAVED_URL_PATTERN.search(data)
        if match is not None:
            self.saved_urls.append(match.group(0))


class JobinsSearchHtmlImporter:
    """Validate one saved search-results page and convert its visible cards."""

    def __init__(self, *, max_bytes: int = MAX_IMPORT_BYTES) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise TypeError("max_bytes must be an integer.")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive.")
        self._max_bytes = max_bytes

    def load(self, path: Path, *, limit: int) -> list[RawJob]:
        _validate_limit(limit)
        source_path = Path(path)
        if source_path.suffix.lower() not in {".html", ".htm"}:
            raise UnsupportedFormatError(
                "JoBins import accepts only saved HTML files.",
                code="JOBINS_INPUT_FORMAT_INVALID",
            )
        try:
            stat_result = source_path.stat()
        except OSError as error:
            raise ImportValidationError(
                "JoBins input file could not be inspected.",
                code="JOBINS_INPUT_UNAVAILABLE",
            ) from error
        if not source_path.is_file():
            raise ImportValidationError(
                "JoBins input path must be a regular file.",
                code="JOBINS_INPUT_NOT_FILE",
            )
        if stat_result.st_size > self._max_bytes:
            raise ImportValidationError(
                "JoBins input file exceeds the size limit.",
                code="JOBINS_INPUT_TOO_LARGE",
            )
        try:
            payload = source_path.read_bytes()
        except OSError as error:
            raise ImportValidationError(
                "JoBins input file could not be read completely.",
                code="JOBINS_INPUT_UNAVAILABLE",
            ) from error
        if b"\x00" in payload:
            raise ImportValidationError(
                "JoBins input file contains invalid data.",
                code="JOBINS_ENCODING_INVALID",
            )
        try:
            document = payload.decode("utf-8-sig", errors="strict")
        except UnicodeError as error:
            raise ImportValidationError(
                "JoBins input file must be UTF-8 HTML.",
                code="JOBINS_ENCODING_INVALID",
            ) from error

        parser = _TreeParser()
        try:
            parser.feed(document)
            parser.close()
        except (ValueError, RecursionError) as error:
            raise ImportValidationError(
                "JoBins input does not contain a usable HTML structure.",
                code="JOBINS_PAGE_INVALID",
            ) from error
        self._validate_page(parser)
        cards = [node for node in _walk(parser.root) if _is_job_card(node)]
        if not cards:
            if _has_explicit_zero_result(parser.root):
                return []
            raise ImportValidationError(
                "JoBins search-results page does not contain job cards.",
                code="JOBINS_PAGE_INVALID",
            )

        timestamp = datetime.now(timezone.utc)
        batch_id = uuid4().hex
        jobs: list[RawJob] = []
        source_job_ids: set[str] = set()
        for card in cards[:limit]:
            raw_job = _extract_card(card, timestamp=timestamp, batch_id=batch_id)
            source_job_id = raw_job.source_job_id
            if source_job_id is None or source_job_id in source_job_ids:
                raise ImportValidationError(
                    "JoBins input contains a duplicate source job ID.",
                    code="JOBINS_SOURCE_JOB_ID_DUPLICATE",
                )
            source_job_ids.add(source_job_id)
            jobs.append(raw_job)
        return jobs

    @staticmethod
    def _validate_page(parser: _TreeParser) -> None:
        titles = [
            _node_text(node)
            for node in _walk(parser.root)
            if node.tag == "title" and _node_text(node)
        ]
        headings = [
            _node_text(node)
            for node in _walk(parser.root)
            if node.tag == "h2" and _node_text(node) == "求人検索"
        ]
        if titles != ["求人検索 | JoBins"] or len(headings) != 1:
            raise ImportValidationError(
                "JoBins input is not a recognized search-results page.",
                code="JOBINS_PAGE_INVALID",
            )
        for saved_url in parser.saved_urls:
            parsed = urlsplit(saved_url)
            if (
                parsed.scheme.lower() != "https"
                or (parsed.hostname or "").lower() != "jobins.jp"
                or parsed.path != "/agent/job/search_1"
            ):
                raise ImportValidationError(
                    "JoBins input has an unexpected saved-page origin.",
                    code="JOBINS_PAGE_INVALID",
                )


class JobinsProvider:
    """Saved search-results HTML provider."""

    def __init__(
        self,
        *,
        importer: JobinsSearchHtmlImporter,
    ) -> None:
        self._importer = importer

    def load(self, path: Path, *, limit: int) -> list[RawJob]:
        _validate_limit(limit)
        return self._importer.load(path, limit=limit)


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError(
            f"limit must be an integer from 1 to {MAX_BATCH_LIMIT}."
        )
    if not 1 <= limit <= MAX_BATCH_LIMIT:
        raise ValueError(f"limit must be from 1 to {MAX_BATCH_LIMIT}.")


def _walk(node: _HtmlNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _class_tokens(node: _HtmlNode) -> frozenset[str]:
    return frozenset(node.attributes.get("class", "").split())


def _direct_text(node: _HtmlNode) -> str:
    return " ".join(" ".join(node.text_parts).split())


def _node_text(node: _HtmlNode) -> str:
    parts = [*node.text_parts]
    for child in node.children:
        parts.append(_node_text(child))
    return " ".join(" ".join(parts).split())


def _is_job_card(node: _HtmlNode) -> bool:
    if node.tag != "div":
        return False
    classes = _class_tokens(node)
    if not _CARD_CLASSES.issubset(classes) or not any(
        value.startswith("jb-shadow-") for value in classes
    ):
        return False
    return True


def _detail_links(card: _HtmlNode) -> list[str]:
    return [
        node.attributes["href"]
        for node in _walk(card)
        if node.tag == "a" and "/agent/jobs/" in node.attributes.get("href", "")
    ]


def _has_explicit_zero_result(root: _HtmlNode) -> bool:
    return any(
        node.tag in {"h2", "p", "span"} and _node_text(node) == "0"
        for node in _walk(root)
    )


def _extract_card(
    card: _HtmlNode,
    *,
    timestamp: datetime,
    batch_id: str,
) -> RawJob:
    source_job_id = _required_label_value(card, "求人ID")
    occupation = _required_label_value(card, "職種分類")
    company_name = _required_label_value(card, "採用企業")
    salary_text = _annual_salary_text(_required_label_value(card, "年収"))
    location = _required_label_value(card, "勤務地")
    referral_reward_your_company = _extract_referral_reward(card, "貴社")
    referral_reward_total = _extract_referral_reward(card, "全額")
    required_conditions, preferred_conditions = _extract_conditions(card)
    job_title = _required_unique_text(
        card,
        lambda node: node.tag == "div"
        and _TITLE_CLASSES.issubset(_class_tokens(node)),
    )
    employment_type = _required_unique_text(
        card,
        lambda node: node.tag == "span"
        and "jb-bg-blue-50" in _class_tokens(node)
        and _EMPLOYMENT_PATTERN.search(_node_text(node)) is not None,
    )
    source_url = _validated_detail_url(card)
    badge_values = [
        _direct_text(node)
        for node in _walk(card)
        if node.tag == "span"
        and "jb-inline-flex" in _class_tokens(node)
        and _direct_text(node)
    ]
    overtime = " / ".join(
        dict.fromkeys(value for value in badge_values if value.startswith("残業"))
    )
    incentives = list(
        dict.fromkeys(value for value in badge_values if "インセンティブ" in value)
    )
    commission = " / ".join(incentives)

    return RawJob(
        source=JobSource.JOBINS,
        source_job_id=source_job_id,
        source_url=source_url,
        fields={
            "job_title": job_title,
            "company_name": company_name,
            "occupation": occupation,
            "employment_type": employment_type,
            "salary_text": salary_text,
            "location": location,
            "required_conditions": required_conditions,
            "preferred_conditions": preferred_conditions,
            "commission": commission,
            "referral_reward_your_company": referral_reward_your_company,
            "referral_reward_total": referral_reward_total,
            "overtime": overtime,
        },
        acquired_at=timestamp,
        last_verified_at=timestamp,
        batch_id=batch_id,
        extractor_version=EXTRACTOR_VERSION,
    )


def _annual_salary_text(value: str) -> str:
    if "年収" in value:
        return value
    return f"年収 {value}"


def _extract_referral_reward(card: _HtmlNode, label: str) -> str:
    blocks = [
        node
        for node in _walk(card)
        if node.attributes.get("id") == "job_search_your_company"
    ]
    if len(blocks) != 1:
        raise _reward_card_error()
    block = blocks[0]
    label_nodes = [
        node
        for node in _walk(block)
        if node.tag == "label" and _direct_text(node) == label
    ]
    if len(label_nodes) != 1:
        raise _reward_card_error()

    label_node = label_nodes[0]
    row = label_node.parent
    if row is None:
        raise _reward_card_error()
    try:
        label_index = row.children.index(label_node)
    except ValueError as exc:
        raise _reward_card_error() from exc
    if label_index + 1 >= len(row.children):
        raise _reward_card_error()
    value_container = row.children[label_index + 1]
    if value_container.tag != "div" or not value_container.children:
        raise _reward_card_error()
    if any(
        child is not label_node and child is not value_container
        for child in row.children
    ):
        raise _reward_card_error()

    amount_display = value_container.children[0]
    if amount_display.tag != "div":
        raise _reward_card_error()
    if any(_node_text(child) for child in value_container.children[1:]):
        raise _reward_card_error()
    value = _clean_display_value(_amount_display_text(amount_display))
    if not value:
        raise ImportValidationError(
            "JoBins job card contains an empty required value.",
            code="JOBINS_REQUIRED_VALUE_MISSING",
        )
    return value


def _reward_card_error() -> ImportValidationError:
    return ImportValidationError(
        "JoBins job card has an ambiguous or missing field structure.",
        code="JOBINS_CARD_INVALID",
    )


def _amount_display_text(node: _HtmlNode) -> str:
    parts: list[str] = []
    for item in node.content:
        if isinstance(item, str):
            normalized = " ".join(item.split())
            if normalized:
                parts.append(normalized)
        elif item.tag not in _IGNORED_TEXT_TAGS:
            nested = _amount_display_text(item)
            if nested:
                parts.append(nested)
    return " ".join(parts)


def _required_label_value(card: _HtmlNode, label: str) -> str:
    label_nodes = [node for node in _walk(card) if _direct_text(node) == label]
    if len(label_nodes) != 1:
        raise ImportValidationError(
            "JoBins job card has an ambiguous or missing field structure.",
            code="JOBINS_CARD_INVALID",
        )
    label_node = label_nodes[0]
    ancestor = label_node.parent
    while ancestor is not None and ancestor is not card:
        branch = _direct_child_containing(ancestor, label_node)
        if branch is not None:
            values = [
                _field_value_text(child)
                for child in ancestor.children
                if child is not branch and _field_value_text(child)
            ]
            if values:
                value = _clean_display_value(" / ".join(values))
                if value:
                    return value
                break
        ancestor = ancestor.parent
    raise ImportValidationError(
        "JoBins job card contains an empty required value.",
        code="JOBINS_REQUIRED_VALUE_MISSING",
    )


def _direct_child_containing(
    ancestor: _HtmlNode, descendant: _HtmlNode
) -> _HtmlNode | None:
    current = descendant
    while current.parent is not None and current.parent is not ancestor:
        current = current.parent
    return current if current.parent is ancestor else None


def _field_value_text(node: _HtmlNode) -> str:
    if node.tag == "ul":
        items = [
            _clean_display_value(_node_text(descendant))
            for descendant in node.children
            if descendant.tag == "li"
        ]
        items = [item for item in items if item]
        if items:
            return " / ".join(items)
    return _node_text(node)


def _extract_conditions(card: _HtmlNode) -> tuple[str, str]:
    headings = [
        node
        for node in _walk(card)
        if node.tag == "h3" and _direct_text(node) == _APPLICATION_CONDITIONS_LABEL
    ]
    if not headings:
        return "", ""
    if len(headings) != 1:
        raise _condition_card_error()

    heading = headings[0]
    container = heading.parent
    if (
        container is None
        or container.tag != "div"
        or any(part.strip() for part in container.text_parts)
        or not container.children
        or container.children[0] is not heading
    ):
        raise _condition_card_error()

    value_nodes = container.children[1:]
    if not value_nodes:
        return "", ""
    if len(value_nodes) != 1 or value_nodes[0].tag != "div":
        raise _condition_card_error()

    value_node = value_nodes[0]
    if any(part.strip() for part in value_node.text_parts):
        raise _condition_card_error()
    if not value_node.children:
        return "", ""
    if len(value_node.children) != 1 or value_node.children[0].tag != "p":
        raise _condition_card_error()

    lines = _condition_lines(value_node.children[0])
    current_section = "required"
    required_seen = False
    preferred_seen = False
    pending_empty_section: str | None = None
    required_lines: list[str] = []
    preferred_lines: list[str] = []

    for line in lines:
        section, remainder = _parse_condition_marker(line)
        if section == pending_empty_section and not remainder:
            continue
        if section == "required":
            if required_seen or preferred_seen:
                raise _condition_card_error()
            required_seen = True
            current_section = "required"
        elif section == "preferred":
            if preferred_seen:
                raise _condition_card_error()
            preferred_seen = True
            current_section = "preferred"

        value = remainder if section is not None else line
        if value and value not in _EMPTY_CONDITION_LINES:
            if current_section == "required":
                required_lines.append(value)
            else:
                preferred_lines.append(value)
            pending_empty_section = None
        elif section is not None:
            pending_empty_section = section
        else:
            pending_empty_section = None

    return " / ".join(required_lines), " / ".join(preferred_lines)


def _ordered_text(node: _HtmlNode) -> str:
    if node.tag in _IGNORED_TEXT_TAGS:
        return ""
    parts: list[str] = []
    for item in node.content:
        if isinstance(item, str):
            parts.append(item)
        elif item.tag == "br":
            parts.append("\n")
        else:
            parts.append(_ordered_text(item))
    return "".join(parts)


def _condition_lines(node: _HtmlNode) -> list[str]:
    value = _ordered_text(node).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in value.split("\n")]
    return [line for line in lines if line and line not in _EMPTY_CONDITION_LINES]


def _parse_condition_marker(line: str) -> tuple[str | None, str]:
    for section, pattern in (
        ("required", _REQUIRED_CONDITION_MARKER),
        ("preferred", _PREFERRED_CONDITION_MARKER),
    ):
        match = pattern.fullmatch(line)
        if match is None:
            continue
        remainder = match.group("square")
        if remainder is None:
            remainder = match.group("bracket") or ""
        remainder = _HORIZONTAL_SPACE.sub(" ", remainder).strip()
        if remainder in _EMPTY_CONDITION_LINES:
            remainder = ""
        return section, remainder
    return None, line


def _condition_card_error() -> ImportValidationError:
    return ImportValidationError(
        "JoBins job card has an ambiguous application-conditions structure.",
        code="JOBINS_CARD_INVALID",
    )


def _required_unique_text(card: _HtmlNode, predicate) -> str:
    values = [
        _clean_display_value(_node_text(node))
        for node in _walk(card)
        if predicate(node)
    ]
    values = [value for value in values if value]
    if len(values) != 1:
        raise ImportValidationError(
            "JoBins job card has an ambiguous or missing field structure.",
            code="JOBINS_CARD_INVALID",
        )
    return values[0]


def _clean_display_value(value: str) -> str:
    normalized = " ".join(value.split()).strip()
    return "" if normalized in {"-", "－", "―"} else normalized


def _validated_detail_url(card: _HtmlNode) -> str:
    links = _detail_links(card)
    if len(links) != 1:
        raise ImportValidationError(
            "JoBins job card must contain exactly one detail link.",
            code="JOBINS_DETAIL_URL_INVALID",
        )
    parsed = urlsplit(links[0])
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != "jobins.jp"
        or _DETAIL_PATH_PATTERN.fullmatch(parsed.path) is None
    ):
        raise ImportValidationError(
            "JoBins job card contains an invalid detail link.",
            code="JOBINS_DETAIL_URL_INVALID",
        )
    return urlunsplit(("https", "jobins.jp", parsed.path, "", ""))


__all__ = [
    "EXTRACTOR_VERSION",
    "JobinsProvider",
    "JobinsSearchHtmlImporter",
    "MAX_BATCH_LIMIT",
    "MAX_IMPORT_BYTES",
]
