from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message

from app.utils.telegram import safe_delete_message

WIZARD_KEYS = {"wizard_chat_id", "wizard_message_id"}


async def remember_wizard(state: FSMContext, message: Message) -> None:
    await state.update_data(wizard_chat_id=message.chat.id, wizard_message_id=message.message_id)


async def edit_wizard(bot: Bot, state: FSMContext, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    data = await state.get_data()
    try:
        await bot.edit_message_text(text, chat_id=data["wizard_chat_id"], message_id=data["wizard_message_id"], reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise


async def consume_input(message: Message) -> None:
    await safe_delete_message(message)


async def finish_wizard(state: FSMContext, *keys: str) -> None:
    data = await state.get_data()
    for key in (*WIZARD_KEYS, *keys):
        data.pop(key, None)
    await state.set_data(data)
    await state.set_state(None)
