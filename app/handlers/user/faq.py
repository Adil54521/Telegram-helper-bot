from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession
from app.callbacks import UserFAQCallback, UserPageCallback
from app.database.repositories.cms import CMSRepository
from app.database.repositories.users import upsert_user
from app.utils.i18n import t, translated
from app.utils.telegram import safe_edit_text
router=Router(name="user_faq")
async def show_faq(cb:CallbackQuery,session:AsyncSession):
    user=await upsert_user(session,cb.from_user);language=user.content_language_code or "ru";items=await CMSRepository(session).faqs(True);rows=[[InlineKeyboardButton(text=(translated(x.translations,language).question if translated(x.translations,language) else x.question)[:60],callback_data=UserFAQCallback(item_id=x.id).pack())] for x in items];rows.append([InlineKeyboardButton(text=t("back",language),callback_data=UserPageCallback(key="main").pack())]);await safe_edit_text(cb.message,t("faq",language),reply_markup=InlineKeyboardMarkup(inline_keyboard=rows));await cb.answer()
@router.callback_query(UserPageCallback.filter(F.key=="faq"))
async def menu(cb:CallbackQuery,session:AsyncSession):await show_faq(cb,session)
@router.callback_query(UserFAQCallback.filter(F.item_id>0))
async def answer(cb:CallbackQuery,callback_data:UserFAQCallback,session:AsyncSession):
    x=await CMSRepository(session).faq(callback_data.item_id)
    user=await upsert_user(session,cb.from_user);language=user.content_language_code or "ru"
    if not x or not x.is_active:return await cb.answer(t("faq_unavailable",language),show_alert=True)
    value=translated(x.translations,language);question,body=(value.question,value.answer) if value else (x.question,x.answer)
    await safe_edit_text(cb.message,f"❓ <b>{question}</b>\n\n{body}",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("back",language),callback_data=UserFAQCallback(item_id=0).pack())]]));await cb.answer()
@router.callback_query(UserFAQCallback.filter(F.item_id==0))
async def back(cb:CallbackQuery,session:AsyncSession):await show_faq(cb,session)
