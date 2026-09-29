from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Admin, AuditLog, BotButton, BotButtonTranslation, FAQItem, FAQItemTranslation
from app.database.models.enums import AdminRole, ButtonAction, LanguageCode
from app.database.repositories.cms import CMSRepository


def audit(session: AsyncSession, actor: int, action: str, kind: str, entity_id: int, old: dict[str, Any] | None = None, new: dict[str, Any] | None = None) -> None:
    session.add(AuditLog(admin_id=actor, action=action, entity_type=kind, entity_id=str(entity_id), old_data=old, new_data=new))


class ButtonService:
    def __init__(self, session: AsyncSession) -> None:
        self.session, self.repo = session, CMSRepository(session)

    async def normalize(self, page_id: int) -> None:
        for index, item in enumerate(await self.repo.buttons(page_id)):
            item.position = index

    async def create(self, actor: int, page_id: int, text: str, action: ButtonAction, target: str | None, text_kk: str | None = None) -> BotButton:
        position = len(await self.repo.buttons(page_id))
        item = BotButton(page_id=page_id, text=text, action_type=action, target=target, position=position, row=position)
        self.session.add(item); await self.session.flush()
        self.session.add(BotButtonTranslation(button_id=item.id, language_code=LanguageCode.RU, text=text))
        if text_kk: self.session.add(BotButtonTranslation(button_id=item.id, language_code=LanguageCode.KK, text=text_kk))
        audit(self.session, actor, "BUTTON_CREATED", "BotButton", item.id, new={"text": text, "action": action.value, "target": target})
        return item

    async def update(self, actor: int, item: BotButton, **values: Any) -> None:
        old = {key: getattr(item, key).value if hasattr(getattr(item, key), "value") else getattr(item, key) for key in values}
        for key, value in values.items(): setattr(item, key, value)
        audit(self.session, actor, "BUTTON_UPDATED", "BotButton", item.id, old=old, new={k: v.value if hasattr(v, "value") else v for k, v in values.items()})

    async def update_translation(self, actor: int, item: BotButton, language: LanguageCode, text: str) -> None:
        translation = next((x for x in item.translations if x.language_code == language), None)
        old = {"text": translation.text} if translation else None
        if translation: translation.text = text
        else: self.session.add(BotButtonTranslation(button_id=item.id, language_code=language, text=text))
        if language == LanguageCode.RU: item.text = text
        audit(self.session, actor, "BUTTON_TRANSLATION_UPDATED", "BotButton", item.id, old=old, new={"language": language.value, "text": text})

    async def move(self, actor: int, item: BotButton, delta: int) -> None:
        items = await self.repo.buttons(item.page_id); index = items.index(item); target = max(0, min(len(items)-1, index+delta))
        if target != index: items[index], items[target] = items[target], items[index]
        for pos, value in enumerate(items): value.position = pos; value.row = pos
        audit(self.session, actor, "BUTTON_UPDATED", "BotButton", item.id, new={"position": target})

    async def delete(self, actor: int, item: BotButton) -> None:
        page_id, old = item.page_id, {"text": item.text, "action": item.action_type.value}
        await self.session.delete(item); await self.session.flush(); await self.normalize(page_id)
        audit(self.session, actor, "BUTTON_DELETED", "BotButton", item.id, old=old)


class FAQService:
    def __init__(self, session: AsyncSession) -> None: self.session, self.repo = session, CMSRepository(session)
    async def create(self, actor: int, question: str, answer: str, question_kk: str | None = None, answer_kk: str | None = None) -> FAQItem:
        item = FAQItem(question=question, answer=answer, position=len(await self.repo.faqs()))
        self.session.add(item); await self.session.flush(); self.session.add(FAQItemTranslation(faq_id=item.id,language_code=LanguageCode.RU,question=question,answer=answer))
        if question_kk and answer_kk:self.session.add(FAQItemTranslation(faq_id=item.id,language_code=LanguageCode.KK,question=question_kk,answer=answer_kk))
        audit(self.session, actor, "FAQ_CREATED", "FAQItem", item.id, new={"question": question}); return item
    async def update(self, actor: int, item: FAQItem, **values: Any) -> None:
        old = {k: getattr(item, k) for k in values}
        for k, v in values.items(): setattr(item, k, v)
        audit(self.session, actor, "FAQ_UPDATED", "FAQItem", item.id, old=old, new=values)
    async def update_translation(self,actor:int,item:FAQItem,language:LanguageCode,**values:Any)->None:
        translation=next((x for x in item.translations if x.language_code==language),None);old=None if not translation else {"question":translation.question,"answer":translation.answer}
        if not translation:
            translation=FAQItemTranslation(faq_id=item.id,language_code=language,question=values.get("question") or item.question,answer=values.get("answer") or "");self.session.add(translation)
        else:
            for k,v in values.items():setattr(translation,k,v)
        if language==LanguageCode.RU:
            for k,v in values.items():setattr(item,k,v)
        audit(self.session,actor,"FAQ_TRANSLATION_UPDATED","FAQItem",item.id,old=old,new={"language":language.value,**values})
    async def move(self, actor: int, item: FAQItem, delta: int) -> None:
        items=await self.repo.faqs(); i=items.index(item); j=max(0,min(len(items)-1,i+delta)); items[i],items[j]=items[j],items[i]
        for pos,value in enumerate(items): value.position=pos
        audit(self.session, actor, "FAQ_UPDATED", "FAQItem", item.id, new={"position": j})
    async def delete(self, actor: int, item: FAQItem) -> None:
        old={"question":item.question}; await self.session.delete(item); await self.session.flush()
        for pos,value in enumerate(await self.repo.faqs()): value.position=pos
        audit(self.session, actor, "FAQ_DELETED", "FAQItem", item.id, old=old)


class AdminService:
    def __init__(self, session: AsyncSession, protected_id: int) -> None: self.session,self.repo,self.protected_id=session,CMSRepository(session),protected_id
    async def _ensure_removable(self, item: Admin) -> None:
        if item.telegram_id == self.protected_id: raise ValueError("Администратор из SUPER_ADMIN_ID защищён")
        if item.role == AdminRole.SUPER_ADMIN and item.is_active:
            count=await self.session.scalar(select(func.count(Admin.id)).where(Admin.role==AdminRole.SUPER_ADMIN, Admin.is_active.is_(True))) or 0
            if count <= 1: raise ValueError("Нельзя удалить, отключить или понизить последнего SUPER_ADMIN")
    async def create(self, actor:int, telegram_id:int, role:AdminRole)->Admin:
        if await self.repo.admin_by_telegram(telegram_id): raise ValueError("Администратор уже существует")
        item=Admin(telegram_id=telegram_id,role=role,is_active=True); self.session.add(item); await self.session.flush(); audit(self.session,actor,"ADMIN_CREATED","Admin",item.id,new={"telegram_id":telegram_id,"role":role.value}); return item
    async def role(self,actor:int,item:Admin,role:AdminRole)->None:
        if item.role==AdminRole.SUPER_ADMIN and role!=AdminRole.SUPER_ADMIN: await self._ensure_removable(item)
        old=item.role; item.role=role; audit(self.session,actor,"ADMIN_ROLE_CHANGED","Admin",item.id,old={"role":old.value},new={"role":role.value})
    async def toggle(self,actor:int,item:Admin)->None:
        if item.is_active: await self._ensure_removable(item)
        item.is_active=not item.is_active; audit(self.session,actor,"ADMIN_ENABLED" if item.is_active else "ADMIN_DISABLED","Admin",item.id,new={"is_active":item.is_active})
    async def delete(self,actor:int,item:Admin)->None:
        await self._ensure_removable(item); old={"telegram_id":item.telegram_id,"role":item.role.value}; await self.session.delete(item); audit(self.session,actor,"ADMIN_DELETED","Admin",item.id,old=old)
