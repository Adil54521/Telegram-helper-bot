from enum import StrEnum


class AdminRole(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    EDITOR = "EDITOR"


class PageStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class MediaType(StrEnum):
    PHOTO = "PHOTO"
    DOCUMENT = "DOCUMENT"


class LanguageCode(StrEnum):
    RU = "ru"
    KK = "kk"


class ButtonAction(StrEnum):
    PAGE = "PAGE"
    URL = "URL"
    BACK = "BACK"
    HOME = "HOME"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    TELEGRAM = "TELEGRAM"


class BroadcastStatus(StrEnum):
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
