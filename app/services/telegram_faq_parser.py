import re
from dataclasses import dataclass

from app.services.telegram_content_parser import (
    SEPARATOR_RE,
    TelegramContentConfidence,
    _language,
    normalize_source_text,
)

QUESTION_PREFIX_RE = re.compile(r"^[❓?]+\s*")


@dataclass(frozen=True, slots=True)
class ParsedTelegramFAQ:
    question_ru: str | None = None
    answer_ru: str | None = None
    question_kk: str | None = None
    answer_kk: str | None = None
    confidence: TelegramContentConfidence = TelegramContentConfidence.LOW
    diagnostic: str = "FAQ_INCOMPLETE"

    @property
    def is_complete(self) -> bool:
        return all((self.question_ru, self.answer_ru, self.question_kk, self.answer_kk))


def _clean_question(value: str) -> str:
    return QUESTION_PREFIX_RE.sub("", value.strip()).strip()


def _question_answer(section: str) -> tuple[str | None, str | None]:
    lines = section.strip().splitlines()

    if len(lines) < 2:
        return None, None

    question = _clean_question(lines[0])

    if not question.endswith("?"):
        return None, None

    answer = "\n".join(lines[1:]).strip()

    if not answer:
        return None, None

    if any(line.strip().startswith("❓") for line in lines[1:]):
        return None, None

    return question, answer


class TelegramFAQParser:
    def parse(self, value: str | None) -> ParsedTelegramFAQ:
        source = normalize_source_text(value)
        if not source:
            return ParsedTelegramFAQ()
        lines = source.splitlines()
        separator_indexes = [index for index, line in enumerate(lines) if SEPARATOR_RE.fullmatch(line)]
        if len(separator_indexes) == 1:
            index = separator_indexes[0]
            sections = ["\n".join(lines[:index]).strip(), "\n".join(lines[index + 1:]).strip()]
            return self._from_sections(sections, require_questions=True)

        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", source) if part.strip()]
        if len(paragraphs) == 3 and paragraphs[0].count("|") == 1:
            left, right = (_clean_question(part) for part in paragraphs[0].split("|", 1))
            left_language, right_language = _language(left), _language(right)
            answer_languages = (_language(paragraphs[1]), _language(paragraphs[2]))
            if left_language and right_language and left_language != right_language and set(answer_languages) == {"ru", "kk"}:
                questions = {left_language: left, right_language: right}
                answers = {answer_languages[0]: paragraphs[1], answer_languages[1]: paragraphs[2]}
                return ParsedTelegramFAQ(
                    question_ru=questions.get("ru"), answer_ru=answers.get("ru"),
                    question_kk=questions.get("kk"), answer_kk=answers.get("kk"),
                    confidence=TelegramContentConfidence.HIGH, diagnostic="FAQ_COMPLETE",
                )
        # Without an explicit separator, accept exactly two clear Q/A blocks.
        question_starts = [index for index, line in enumerate(lines) if line.strip().startswith("❓")]
        if len(question_starts) == 2:
            sections = ["\n".join(lines[question_starts[0]:question_starts[1]]).strip(), "\n".join(lines[question_starts[1]:]).strip()]
            return self._from_sections(sections, require_questions=True)
        diagnostic = "FAQ_AMBIGUOUS" if source.count("?") > 2 or "|" in source else "FAQ_INCOMPLETE"
        return ParsedTelegramFAQ(diagnostic=diagnostic)

    def _from_sections(self, sections: list[str], *, require_questions: bool) -> ParsedTelegramFAQ:
        values: dict[str, tuple[str, str]] = {}
        for section in sections:
            question, answer = _question_answer(section)
            if not question or not answer:
                return ParsedTelegramFAQ(diagnostic="FAQ_INCOMPLETE")
            language = _language(f"{question}\n{answer}")
            if not language or language in values:
                return ParsedTelegramFAQ(diagnostic="FAQ_AMBIGUOUS")
            values[language] = (question, answer)
        if set(values) != {"ru", "kk"}:
            return ParsedTelegramFAQ(diagnostic="FAQ_INCOMPLETE")
        return ParsedTelegramFAQ(
            question_ru=values["ru"][0], answer_ru=values["ru"][1],
            question_kk=values["kk"][0], answer_kk=values["kk"][1],
            confidence=TelegramContentConfidence.HIGH, diagnostic="FAQ_COMPLETE",
        )
