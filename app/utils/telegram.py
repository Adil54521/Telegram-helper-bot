from typing import Any

from aiogram.exceptions import TelegramBadRequest


def _is_not_modified(error: TelegramBadRequest) -> bool:
    return "message is not modified" in str(error).lower()


def _is_not_deletable(error: TelegramBadRequest) -> bool:
    message = str(error).lower()
    return any(
        reason in message
        for reason in (
            "message to delete not found",
            "message can't be deleted",
            "message cannot be deleted",
        )
    )


async def safe_delete_message(message: Any) -> Any:
    try:
        return await message.delete()
    except TelegramBadRequest as error:
        if _is_not_deletable(error):
            return None
        raise


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
