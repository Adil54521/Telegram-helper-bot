import csv
import io
import re
from dataclasses import asdict, dataclass

from app.database.models import FAQItem
from app.database.models.enums import LanguageCode

CSV_COLUMNS = ("question_ru", "answer_ru", "question_kk", "answer_kk", "is_active")
REQUIRED_COLUMNS = {"question_ru", "answer_ru"}
MAX_CSV_BYTES = 1_000_000
MAX_CSV_ROWS = 500
MAX_QUESTION_LENGTH = 500
MAX_ANSWER_LENGTH = 10_000


def normalize_question(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


@dataclass(slots=True)
class FAQCSVRow:
    row_number: int
    question_ru: str
    answer_ru: str
    question_kk: str = ""
    answer_kk: str = ""
    is_active: bool = True

    def to_fsm(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class FAQCSVError:
    row_number: int
    message: str

    def to_fsm(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class FAQCSVPreview:
    total_rows: int
    new_rows: list[FAQCSVRow]
    duplicates: list[FAQCSVRow]
    errors: list[FAQCSVError]


def _decode(data: bytes) -> str:
    if len(data) > MAX_CSV_BYTES:
        raise ValueError("CSV-файл слишком большой (максимум 1 МБ).")
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV должен быть в кодировке UTF-8.")


def _active(value: str) -> bool | None:
    normalized = value.strip().casefold()
    if not normalized:
        return True
    if normalized in {"true", "1", "yes", "да"}:
        return True
    if normalized in {"false", "0", "no", "нет"}:
        return False
    return None


def parse_faq_csv(data: bytes, existing_questions: set[str]) -> FAQCSVPreview:
    text = _decode(data)
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        headers = {str(value).strip() for value in (reader.fieldnames or [])}
        if not headers:
            raise ValueError("CSV не содержит header.")
        missing = REQUIRED_COLUMNS - headers
        if missing:
            raise ValueError(f"Отсутствуют обязательные столбцы: {', '.join(sorted(missing))}.")
        rows = list(reader)
    except csv.Error as error:
        raise ValueError("CSV имеет некорректную структуру.") from error
    if len(rows) > MAX_CSV_ROWS:
        raise ValueError(f"Слишком много строк (максимум {MAX_CSV_ROWS}).")

    known = {normalize_question(value) for value in existing_questions}
    seen: set[str] = set()
    new_rows: list[FAQCSVRow] = []
    duplicates: list[FAQCSVRow] = []
    errors: list[FAQCSVError] = []
    total_rows = 0
    for number, raw in enumerate(rows, start=2):
        values = {key: (raw.get(key) or "").strip() for key in CSV_COLUMNS}
        if not any(values.values()):
            continue
        total_rows += 1
        question, answer = values["question_ru"], values["answer_ru"]
        row_errors: list[str] = []
        if not question:
            row_errors.append("question_ru пустой")
        elif len(question) > MAX_QUESTION_LENGTH:
            row_errors.append(f"question_ru длиннее {MAX_QUESTION_LENGTH} символов")
        if not answer:
            row_errors.append("answer_ru пустой")
        elif len(answer) > MAX_ANSWER_LENGTH:
            row_errors.append(f"answer_ru длиннее {MAX_ANSWER_LENGTH} символов")
        if len(values["question_kk"]) > MAX_QUESTION_LENGTH:
            row_errors.append(f"question_kk длиннее {MAX_QUESTION_LENGTH} символов")
        if len(values["answer_kk"]) > MAX_ANSWER_LENGTH:
            row_errors.append(f"answer_kk длиннее {MAX_ANSWER_LENGTH} символов")
        active = _active(values["is_active"])
        if active is None:
            row_errors.append("неверное значение is_active")
        if bool(values["question_kk"]) != bool(values["answer_kk"]):
            row_errors.append("question_kk и answer_kk должны быть заполнены вместе")
        if row_errors:
            errors.append(FAQCSVError(number, "; ".join(row_errors)))
            continue
        item = FAQCSVRow(number, question, answer, values["question_kk"], values["answer_kk"], bool(active))
        normalized = normalize_question(question)
        if normalized in known or normalized in seen:
            duplicates.append(item)
        else:
            seen.add(normalized)
            new_rows.append(item)
    return FAQCSVPreview(total_rows, new_rows, duplicates, errors)


def export_faq_csv(items: list[FAQItem]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    for item in items:
        ru = next((value for value in item.translations if value.language_code == LanguageCode.RU), None)
        kk = next((value for value in item.translations if value.language_code == LanguageCode.KK), None)
        writer.writerow({
            "question_ru": _spreadsheet_safe(ru.question if ru else item.question),
            "answer_ru": _spreadsheet_safe(ru.answer if ru else item.answer),
            "question_kk": _spreadsheet_safe(kk.question if kk else ""),
            "answer_kk": _spreadsheet_safe(kk.answer if kk else ""),
            "is_active": "true" if item.is_active else "false",
        })
    return stream.getvalue().encode("utf-8-sig")


def _spreadsheet_safe(value: str) -> str:
    return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value


def faq_template_csv() -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerow({
        "question_ru": "Как поступить?",
        "answer_ru": "Ответ...",
        "question_kk": "Қалай оқуға түсуге болады?",
        "answer_kk": "Жауап...",
        "is_active": "true",
    })
    return stream.getvalue().encode("utf-8-sig")
