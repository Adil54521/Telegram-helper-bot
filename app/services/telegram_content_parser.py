import re
from dataclasses import dataclass
from enum import StrEnum


KAZAKH_CHARACTERS = frozenset("әғқңөұүһі")
KAZAKH_WORDS = frozenset(
    {
        "арналған", "бойынша", "болады", "және", "жүктеп", "қазақ", "құрметті",
        "оқитын", "оқу", "студенттер", "туралы", "төменнен", "үшін", "үлгі", "үлгілерін",
    }
)
CYRILLIC_RE = re.compile(r"[а-яёәғқңөұүһі]", re.IGNORECASE)
WORD_RE = re.compile(r"[а-яёәғқңөұүһі]+", re.IGNORECASE)
SEPARATOR_RE = re.compile(r"^\s*[━—\-_=─]{3,}\s*$")
URL_RE = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)
FILENAME_RE = re.compile(r"^\S+\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|jpg|jpeg|png)$", re.IGNORECASE)
MARKER_RE = re.compile(r"^\s*((?:\d+\ufe0f?\u20e3?|[^\w\s«]){1,4})\s+")


class TelegramContentParseKind(StrEnum):
    BILINGUAL = "BILINGUAL"
    RU_ONLY = "RU_ONLY"
    KK_ONLY = "KK_ONLY"
    UNKNOWN = "UNKNOWN"


class TelegramContentConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class ParsedTelegramContent:
    kind: TelegramContentParseKind
    title_ru: str | None = None
    text_ru: str | None = None
    title_kk: str | None = None
    text_kk: str | None = None
    confidence: TelegramContentConfidence = TelegramContentConfidence.LOW


def normalize_source_text(value: str | None) -> str:
    normalized = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    result: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if result and not blank:
                result.append("")
            blank = True
        else:
            result.append(line.strip())
            blank = False
    return "\n".join(result)


def _paragraphs(value: str) -> list[str | None]:
    paragraphs: list[str | None] = []
    current: list[str] = []
    current_marker: str | None = None
    for line in value.splitlines():
        if SEPARATOR_RE.fullmatch(line):
            if current:
                paragraphs.append("\n".join(current))
                current = []
                current_marker = None
            paragraphs.append(None)
        elif line.strip():
            marker_match = MARKER_RE.match(line)
            marker = marker_match.group(1) if marker_match else None
            if current and marker and marker == current_marker:
                paragraphs.append("\n".join(current))
                current = []
            current.append(line.strip())
            if len(current) == 1:
                current_marker = marker
        elif current:
            paragraphs.append("\n".join(current))
            current = []
            current_marker = None
    if current:
        paragraphs.append("\n".join(current))
    return paragraphs


def _language(value: str) -> str | None:
    letters = CYRILLIC_RE.findall(value.casefold())
    if len(letters) < 3:
        return None
    words = [word.casefold() for word in WORD_RE.findall(value)]
    kazakh_count = sum(character in KAZAKH_CHARACTERS for character in letters)
    kazakh_words = sum(word in KAZAKH_WORDS for word in words)
    kazakh_ratio = kazakh_count / len(letters)
    if kazakh_words >= 1 and (kazakh_count >= 1 or kazakh_words >= 2):
        return "kk"
    if kazakh_count >= 2 and kazakh_ratio >= 0.08:
        return "kk"
    if kazakh_ratio < 0.08:
        return "ru"
    return None


def _split_language_sections(value: str) -> list[tuple[str, str]]:
    paragraphs = _paragraphs(value)
    labelled = [(paragraph, _language(paragraph) if paragraph else None) for paragraph in paragraphs]
    sections: list[tuple[str, list[str]]] = []
    separator_pending = False
    for index, (paragraph, language) in enumerate(labelled):
        if paragraph is None:
            separator_pending = True
            continue
        if language is None:
            following = next((item[1] for item in labelled[index + 1 :] if item[0] is not None and item[1]), None)
            language = sections[-1][0] if sections and not separator_pending else following
        if language is None:
            continue
        if sections and sections[-1][0] == language and not separator_pending:
            sections[-1][1].append(paragraph)
        else:
            sections.append((language, [paragraph]))
        separator_pending = False
    return [(language, "\n\n".join(parts)) for language, parts in sections]


def _safe_title(line: str) -> bool:
    stripped = line.strip()
    if not stripped or SEPARATOR_RE.fullmatch(stripped) or URL_RE.fullmatch(stripped) or FILENAME_RE.fullmatch(stripped):
        return False
    words = stripped.split()
    if len(stripped) > 160 or len(words) > 20:
        return False
    if len(words) > 10 and stripped.endswith((".", "!", "?")):
        return False
    return True


def extract_title_body(section: str) -> tuple[str | None, str | None]:
    paragraphs = [part.strip() for part in section.split("\n\n") if part.strip()]
    if not paragraphs:
        return None, None
    first_lines = paragraphs[0].splitlines()
    candidate = first_lines[0].strip()
    if not _safe_title(candidate):
        return None, section.strip() or None
    remainder = [line.strip() for line in first_lines[1:] if line.strip()]
    body_parts = (["\n".join(remainder)] if remainder else []) + paragraphs[1:]
    return candidate, "\n\n".join(body_parts).strip() or None


class TelegramContentParser:
    def parse(self, text: str) -> ParsedTelegramContent:
        source = normalize_source_text(text)
        if not source or len(CYRILLIC_RE.findall(source)) < 18:
            return ParsedTelegramContent(TelegramContentParseKind.UNKNOWN)
        if "\n" not in source and len(source.split()) < 5:
            return ParsedTelegramContent(TelegramContentParseKind.UNKNOWN)
        sections = _split_language_sections(source)
        markers = [match.group(1) for line in source.splitlines() if (match := MARKER_RE.match(line))]
        repeated_marker = len(markers) != len(set(markers))
        has_separator = any(SEPARATOR_RE.fullmatch(line) for line in source.splitlines())
        by_language: dict[str, list[str]] = {"ru": [], "kk": []}
        for language, section in sections:
            by_language[language].append(section)
        ru_section = "\n\n".join(by_language["ru"]).strip()
        kk_section = "\n\n".join(by_language["kk"]).strip()
        if ru_section and kk_section:
            title_ru, text_ru = extract_title_body(ru_section)
            title_kk, text_kk = extract_title_body(kk_section)
            confidence = (
                TelegramContentConfidence.HIGH
                if repeated_marker or has_separator
                else TelegramContentConfidence.MEDIUM
            )
            return ParsedTelegramContent(
                TelegramContentParseKind.BILINGUAL,
                title_ru=title_ru,
                text_ru=text_ru,
                title_kk=title_kk,
                text_kk=text_kk,
                confidence=confidence,
            )
        if ru_section:
            title_ru, text_ru = extract_title_body(ru_section)
            return ParsedTelegramContent(
                TelegramContentParseKind.RU_ONLY,
                title_ru=title_ru,
                text_ru=text_ru,
                confidence=TelegramContentConfidence.MEDIUM,
            )
        if kk_section:
            title_kk, text_kk = extract_title_body(kk_section)
            return ParsedTelegramContent(
                TelegramContentParseKind.KK_ONLY,
                title_kk=title_kk,
                text_kk=text_kk,
                confidence=TelegramContentConfidence.MEDIUM,
            )
        return ParsedTelegramContent(TelegramContentParseKind.UNKNOWN)
