from typing import Any

from aiogram.exceptions import TelegramBadRequest


def _is_not_modified(error: TelegramBadRequest) -> bool:
    return "message is not modified" in str(error).lower()


async def safe_edit_text(message: Any, text: str, reply_markup: Any = None, **kwargs: Any) -> Any:
    try:
        return await message.edit_text(text, reply_markup=reply_markup, **kwargs)
    except TelegramBadRequest as error:
        if _is_not_modified(error):
            return None
        raise


async def safe_edit_caption(message: Any, caption: str | None = None, reply_markup: Any = None, **kwargs: Any) -> Any:
    try:
        return await message.edit_caption(caption=caption, reply_markup=reply_markup, **kwargs)
    except TelegramBadRequest as error:
        if _is_not_modified(error):
            return None
        raise


async def safe_edit_reply_markup(message: Any, reply_markup: Any = None, **kwargs: Any) -> Any:
    try:
        return await message.edit_reply_markup(reply_markup=reply_markup, **kwargs)
    except TelegramBadRequest as error:
        if _is_not_modified(error):
            return None
        raise
