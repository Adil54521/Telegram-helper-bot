from aiogram import Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks import LanguageCallback, UserPageCallback
from app.database.models import PageView
from app.database.models.enums import ButtonAction
from app.database.repositories.cms import CMSRepository
from app.database.repositories.pages import PageRepository
from app.database.repositories.users import upsert_user
from app.keyboards.user import language_keyboard, page_keyboard
from app.utils.i18n import (
    attachment_caption,
    normalize_language,
    t,
    translated,
)
from app.utils.telegram import safe_edit_text
import logging

logger = logging.getLogger(__name__)

router = Router(name="user_pages")


async def send_page(
    target: Message | CallbackQuery,
    session: AsyncSession,
    key: str,
    language: str | None = None,
) -> None:
    repo = PageRepository(session)

    page = await repo.by_key(key)

    user = (
        await upsert_user(
            session,
            target.from_user,
        )
        if target.from_user
        else None
    )

    language = normalize_language(
        language
        or (
            user.content_language_code
            if user
            else None
        )
    )

    if (
        not page
        or not page.is_active
        or not page.published_revision
    ):
        text = t(
            "unavailable",
            language,
        )

        markup = page_keyboard(
            [],
            language,
        )

    else:
        revision = page.published_revision

        translation = translated(
            revision.translations,
            language,
        )

        if translation:
            title = translation.title
            body = translation.text
        else:
            title = revision.title
            body = revision.text

        text = (
            f"<b>{title}</b>\n\n"
            f"{body}"
        )

        buttons = await repo.buttons(
            page.id
        )

        markup = page_keyboard(
            buttons,
            language,
        )

        if user:
            session.add(
                PageView(
                    user_id=user.id,
                    page_id=page.id,
                )
            )

    if isinstance(
        target,
        CallbackQuery,
    ):
        if target.message:
            await safe_edit_text(
                target.message,
                text,
                reply_markup=markup,
            )

        await target.answer()

    else:
        await target.answer(
            text,
            reply_markup=markup,
        )

    if (
        page
        and page.published_revision
    ):
        attachments = sorted(
            (
                attachment
                for attachment
                in page.published_revision.attachments
                if attachment.is_active
            ),
            key=lambda attachment: attachment.position,
        )

        recipient = (
            target.message
            if isinstance(
                target,
                CallbackQuery,
            )
            else target
        )

        if not recipient:
            return

        for attachment in attachments:
            caption = attachment_caption(
                attachment,
                language,
            )

            if attachment.type.value == "PHOTO":
                await recipient.answer_photo(
                    attachment.telegram_file_id,
                    caption=caption,
                )

            else:
                await recipient.answer_document(
                    attachment.telegram_file_id,
                    caption=caption,
                )


async def handle_phone(
    callback: CallbackQuery,
    session: AsyncSession,
    button_id: int,
    language: str,
) -> None:
    button = await CMSRepository(session).button(button_id)

    if (
        not button
        or not button.is_active
        or button.action_type != ButtonAction.PHONE
        or not button.target
    ):
        await callback.answer(
            t(
                "unavailable",
                language,
            ),
            show_alert=True,
        )
        return

    phone = button.target

    if language == "kk":
        text = (
            "📞 Телефон нөмірі:\n\n"
            f"<code>{phone}</code>\n\n"
            "Нөмірді көшіру үшін оны басып тұрыңыз."
        )

    else:
        text = (
            "📞 Номер телефона:\n\n"
            f"<code>{phone}</code>\n\n"
            "Нажмите и удерживайте номер, чтобы скопировать."
        )

    if callback.message:
        await callback.message.answer(
            text
        )

    await callback.answer()

async def handle_email(
    callback: CallbackQuery,
    session: AsyncSession,
    button_id: int,
    language: str,
) -> None:
    button = await CMSRepository(
        session
    ).button(
        button_id
    )

    if (
        not button
        or not button.is_active
        or button.action_type != ButtonAction.EMAIL
        or not button.target
    ):
        await callback.answer(
            t(
                "unavailable",
                language,
            ),
            show_alert=True,
        )
        return

    email = button.target

    if language == "kk":
        text = (
            "✉️ Электрондық пошта:\n\n"
            f"<code>{email}</code>\n\n"
            "Мекенжайды көшіру үшін оны басып тұрыңыз."
        )

    else:
        text = (
            "✉️ Электронная почта:\n\n"
            f"<code>{email}</code>\n\n"
            "Нажмите и удерживайте адрес, чтобы скопировать."
        )

    if callback.message:
        await callback.message.answer(
            text
        )

    await callback.answer()


@router.callback_query(UserPageCallback.filter())
async def open_page(
    callback: CallbackQuery,
    callback_data: UserPageCallback,
    session: AsyncSession,
) -> None:
    logger.debug(
        "UserPageCallback received: user_id=%s key=%r",
        callback.from_user.id,
        callback_data.key,
    )

    user = await upsert_user(
        session,
        callback.from_user,
    )

    language = normalize_language(
        user.content_language_code
    )

    key = callback_data.key

    if key == "language":
        if callback.message:
            await safe_edit_text(
                callback.message,
                "Тілді таңдаңыз / Выберите язык",
                reply_markup=language_keyboard(),
            )

        await callback.answer()
        return

    if key.startswith("phone_"):
        raw_id = key.removeprefix("phone_")

        try:
            button_id = int(raw_id)
        except ValueError:
            await callback.answer(
                t(
                    "unavailable",
                    language,
                ),
                show_alert=True,
            )
            return

        await handle_phone(
            callback,
            session,
            button_id,
            language,
        )
        return

    if key.startswith("email_"):
        raw_id = key.removeprefix("email_")

        try:
            button_id = int(raw_id)
        except ValueError:
            await callback.answer(
                t(
                    "unavailable",
                    language,
                ),
                show_alert=True,
            )
            return

        await handle_email(
            callback,
            session,
            button_id,
            language,
        )
        return

    await send_page(
        callback,
        session,
        key,
        language,
    )


@router.callback_query(
    LanguageCallback.filter()
)
async def select_language(
    callback: CallbackQuery,
    callback_data: LanguageCallback,
    session: AsyncSession,
) -> None:
    if callback_data.code not in {
        "ru",
        "kk",
    }:
        await callback.answer(
            "Unsupported language",
            show_alert=True,
        )
        return

    user = await upsert_user(
        session,
        callback.from_user,
    )

    user.content_language_code = (
        callback_data.code
    )

    await send_page(
        callback,
        session,
        "main",
        callback_data.code,
    )
