from enum import StrEnum


class AdminRole(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    EDITOR = "EDITOR"
    AI_HISTORY_VIEWER = "AI_HISTORY_VIEWER"


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


class TelegramContentStatus(StrEnum):
    NEW = "NEW"
    DRAFT_CREATED = "DRAFT_CREATED"
    IGNORED = "IGNORED"


class TelegramProcessingMode(StrEnum):
    MANUAL = "MANUAL"
    AUTO_DRAFT = "AUTO_DRAFT"
    AUTO_PUBLISH = "AUTO_PUBLISH"


class TelegramEditSyncMode(StrEnum):
    OFF = "OFF"
    REVIEW = "REVIEW"
    AUTO = "AUTO"


class TelegramFileSyncMode(StrEnum):
    OFF = "OFF"
    REVIEW = "REVIEW"
    AUTO = "AUTO"


class TelegramContentTarget(StrEnum):
    PAGE = "PAGE"
    FAQ = "FAQ"
