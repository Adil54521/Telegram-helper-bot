import re
from dataclasses import asdict, dataclass
from enum import StrEnum

from app.database.models import TelegramContentPost
from app.database.models.enums import LanguageCode, MediaType, TelegramContentStatus
from app.database.repositories.channel_posts import TelegramContentRepository
from app.services.telegram_content_parser import TelegramContentParser


MAX_GROUPING_LOOKAHEAD = 10
TOKEN_RE = re.compile(r"[a-zа-яәғқңөұүһі]+", re.IGNORECASE)
RU_MARKERS = frozenset({"ru", "рус", "русский", "русская", "русское", "русские"})
KK_MARKERS = frozenset({"kk", "kz", "каз", "қаз", "қазақ", "қазақша", "казахский", "казахская"})
SHORT_CAPTION_WORDS = frozenset(
    {"документ", "отчет", "отчёт", "русский", "русская", "қазақша", "файл", "үлгі", "образец"}
)


def _source_text(post: TelegramContentPost) -> str:
    return (post.text or post.caption or "").strip()


class GroupingConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"


@dataclass(frozen=True, slots=True)
class PublicationMaterialFile:
    media_id: int
    file_id: str
    file_unique_id: str | None
    file_name: str | None
    media_type: str
    file_size: int | None
    position: int


@dataclass(frozen=True, slots=True)
class PublicationMaterialCandidate:
    content_post_id: int
    message_id: int
    caption: str | None
    suggested_language: LanguageCode | None
    confidence: GroupingConfidence
    files: tuple[PublicationMaterialFile, ...]

    @property
    def display_name(self) -> str:
        names = [file.file_name for file in self.files if file.file_name]
        if names:
            return names[0] if len(names) == 1 else f"{names[0]} (+{len(names) - 1})"
        return self.caption or ("Изображение" if self.files and self.files[0].media_type == MediaType.PHOTO.value else "Файл")

    def to_state(self, *, selected: bool = True, language: LanguageCode | None = None) -> dict:
        return {
            "content_post_id": self.content_post_id,
            "message_id": self.message_id,
            "caption": self.caption,
            "suggested_language": self.suggested_language.value if self.suggested_language else None,
            "selected_language": (language or self.suggested_language).value if (language or self.suggested_language) else None,
            "confidence": self.confidence.value,
            "selected": selected,
            "files": [asdict(file) for file in self.files],
        }


@dataclass(frozen=True, slots=True)
class PublicationGroupingResult:
    source_post_id: int
    materials: tuple[PublicationMaterialCandidate, ...]


@dataclass(frozen=True, slots=True)
class SelectedPublicationMaterial:
    content_post_id: int
    selected_language: LanguageCode | None = None


def suggest_material_language(file_name: str | None, caption: str | None = None) -> LanguageCode | None:
    tokens = {token.casefold() for token in TOKEN_RE.findall(" ".join(value for value in (file_name, caption) if value))}
    has_ru = bool(tokens & RU_MARKERS)
    has_kk = bool(tokens & KK_MARKERS)
    if has_ru == has_kk:
        return None
    return LanguageCode.RU if has_ru else LanguageCode.KK


def is_short_material_caption(value: str | None, file_names: tuple[str, ...]) -> bool:
    caption = (value or "").strip()
    if not caption:
        return True
    if len(caption) > 120 or len(caption.split()) > 12 or "\n\n" in caption:
        return False
    normalized = caption.casefold().strip(" 📄🖼:;,.—-_")
    if any(normalized == file_name.casefold() for file_name in file_names if file_name):
        return True
    tokens = {token.casefold() for token in TOKEN_RE.findall(caption)}
    return len(tokens) <= 5 or bool(tokens & SHORT_CAPTION_WORDS)


class TelegramPublicationGrouper:
    def __init__(self, repository: TelegramContentRepository, parser: TelegramContentParser | None = None) -> None:
        self.repository = repository
        self.parser = parser or TelegramContentParser()

    async def group(self, source: TelegramContentPost) -> PublicationGroupingResult:
        value = _source_text(source)
        if source.status != TelegramContentStatus.NEW or not value.strip():
            return PublicationGroupingResult(source.id, ())
        source_names = tuple(media.file_name or "" for media in source.media)
        if source.media and is_short_material_caption(value, source_names):
            return PublicationGroupingResult(source.id, ())
        following = await self.repository.following_posts(
            source.chat_id,
            source.message_thread_id,
            source.message_id,
            MAX_GROUPING_LOOKAHEAD,
        )
        materials: list[PublicationMaterialCandidate] = []
        for candidate in following[:MAX_GROUPING_LOOKAHEAD]:
            if (
                candidate.status != TelegramContentStatus.NEW
                or candidate.chat_id != source.chat_id
                or candidate.message_thread_id != source.message_thread_id
                or candidate.message_id <= source.message_id
            ):
                continue
            candidate_text = _source_text(candidate)
            file_names = tuple(media.file_name or "" for media in candidate.media)
            has_media = bool(candidate.media)
            short_caption = has_media and is_short_material_caption(candidate_text, file_names)
            if candidate_text and not short_caption:
                # Recognized content and significant UNKNOWN text both start a new publication.
                self.parser.parse(candidate_text)
                break
            if not has_media:
                if candidate_text:
                    break
                continue
            files = tuple(
                PublicationMaterialFile(
                    media_id=media.id,
                    file_id=media.telegram_file_id,
                    file_unique_id=media.file_unique_id,
                    file_name=media.file_name,
                    media_type=media.type.value,
                    file_size=media.file_size,
                    position=media.position,
                )
                for media in sorted(candidate.media, key=lambda item: (item.position, item.id))
            )
            label = candidate_text or next((name for name in file_names if name), None)
            materials.append(
                PublicationMaterialCandidate(
                    content_post_id=candidate.id,
                    message_id=candidate.message_id,
                    caption=candidate.caption,
                    suggested_language=suggest_material_language(label, candidate.caption),
                    confidence=GroupingConfidence.HIGH if not candidate_text else GroupingConfidence.MEDIUM,
                    files=files,
                )
            )
        return PublicationGroupingResult(source.id, tuple(materials))
