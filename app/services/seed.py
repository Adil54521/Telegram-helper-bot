from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import BotButton, BotButtonTranslation, BotPage, BotPageRevision, BotPageRevisionTranslation
from app.database.models.enums import ButtonAction, LanguageCode, PageStatus


SEED_PAGES = [
    ("main", "Главное меню", "Добро пожаловать!\n\nВыберите необходимый раздел:", 0),
    ("about", "🏫 Об университете", "Информация скоро появится.", 1),
    ("contacts", "📞 Контакты", "Контактная информация скоро появится.", 2),
]
SEED_KK = {
    "main": ("Басты мәзір", "Қош келдіңіз!\n\nҚажетті бөлімді таңдаңыз:"),
    "about": ("🏫 Университет туралы", "Ақпарат жақын арада жарияланады."),
    "contacts": ("📞 Байланыстар", "Байланыс ақпараты жақын арада жарияланады."),
}


async def seed(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session, session.begin():
        pages: dict[str, BotPage] = {}
        for key, title, text, position in SEED_PAGES:
            page = await session.scalar(select(BotPage).where(BotPage.key == key))
            if page is None:
                page = BotPage(key=key, position=position, status=PageStatus.PUBLISHED)
                session.add(page)
                await session.flush()
                revision = BotPageRevision(page_id=page.id, title=title, text=text)
                session.add(revision)
                await session.flush()
                session.add_all([
                    BotPageRevisionTranslation(revision_id=revision.id, language_code=LanguageCode.RU, title=title, text=text),
                    BotPageRevisionTranslation(revision_id=revision.id, language_code=LanguageCode.KK, title=SEED_KK[key][0], text=SEED_KK[key][1]),
                ])
                page.draft_revision_id = revision.id
                page.published_revision_id = revision.id
            pages[key] = page
            if page.draft_revision_id:
                kk_exists = await session.scalar(select(BotPageRevisionTranslation.id).where(BotPageRevisionTranslation.revision_id == page.draft_revision_id, BotPageRevisionTranslation.language_code == LanguageCode.KK))
                if kk_exists is None:
                    session.add(BotPageRevisionTranslation(revision_id=page.draft_revision_id, language_code=LanguageCode.KK, title=SEED_KK[key][0], text=SEED_KK[key][1]))
        await session.flush()
        main = pages["main"]
        defaults = [("🏫 Об университете", "🏫 Университет туралы", "about"), ("📞 Контакты", "📞 Байланыстар", "contacts"), ("❓ FAQ", "❓ Жиі қойылатын сұрақтар", "faq")]
        for position, (text, text_kk, target) in enumerate(defaults):
            exists = await session.scalar(select(BotButton.id).where(BotButton.page_id == main.id, BotButton.target == target).limit(1))
            if exists is None:
                button=BotButton(page_id=main.id, text=text, action_type=ButtonAction.PAGE, target=target, position=position, row=position);session.add(button);await session.flush()
                session.add_all([BotButtonTranslation(button_id=button.id,language_code=LanguageCode.RU,text=text),BotButtonTranslation(button_id=button.id,language_code=LanguageCode.KK,text=text_kk)])
            else:
                kk_exists = await session.scalar(select(BotButtonTranslation.id).where(BotButtonTranslation.button_id == exists, BotButtonTranslation.language_code == LanguageCode.KK))
                if kk_exists is None: session.add(BotButtonTranslation(button_id=exists, language_code=LanguageCode.KK, text=text_kk))
