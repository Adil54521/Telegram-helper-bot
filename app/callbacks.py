from aiogram.filters.callback_data import CallbackData


class UserPageCallback(CallbackData, prefix="p"):
    key: str


class LanguageCallback(CallbackData, prefix="lang"):
    code: str


class UserAICallback(CallbackData, prefix="uai"):
    action: str


class UserNavigationCallback(CallbackData, prefix="unav"):
    action: str


class AdminAICallback(CallbackData, prefix="aai"):
    action: str


class AdminPageCallback(CallbackData, prefix="ap"):
    action: str
    page_id: int


class AdminMenuCallback(CallbackData, prefix="am"):
    section: str


class ButtonCallback(CallbackData, prefix="bt"):
    action: str
    item_id: int = 0
    page_id: int = 0
    value: str = ""


class FAQCallback(CallbackData, prefix="fq"):
    action: str
    item_id: int = 0
    page: int = 0


class UserFAQCallback(CallbackData, prefix="uf"):
    item_id: int = 0


class AdminCallback(CallbackData, prefix="ad"):
    action: str
    item_id: int = 0
    value: str = ""
    page: int = 0


class BroadcastCallback(CallbackData, prefix="bc"):
    action: str
    item_id: int = 0
    page: int = 0


class AttachmentCallback(CallbackData, prefix="att"):
    action: str
    item_id: int = 0
    page_id: int = 0
    language: str = ""
