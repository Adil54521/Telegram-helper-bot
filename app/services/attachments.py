from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import AuditLog, PageAttachment
from app.database.models.enums import MediaType
from app.database.repositories.pages import PageRepository
from app.services.pages import PageService


class AttachmentService:
    def __init__(self, session: AsyncSession) -> None: self.session=session
    async def create(self,page_id:int,actor:int,type_:MediaType,file_id:str,file_name:str|None)->PageAttachment:
        page=await PageService(self.session).ensure_editable_draft(page_id,actor)
        if not page or not page.draft_revision:raise ValueError("Страница не найдена")
        item=PageAttachment(revision_id=page.draft_revision.id,type=type_,telegram_file_id=file_id,file_name=file_name,position=len(page.draft_revision.attachments))
        self.session.add(item);await self.session.flush();self.session.add(AuditLog(admin_id=actor,action="ATTACHMENT_CREATED",entity_type="PageAttachment",entity_id=str(item.id),new_data={"page_id":page_id,"type":type_.value,"file_name":file_name}));return item
    async def update(self,item:PageAttachment,actor:int,**values)->None:
        old={k:getattr(item,k) for k in values}
        for k,v in values.items():setattr(item,k,v)
        self.session.add(AuditLog(admin_id=actor,action="ATTACHMENT_UPDATED",entity_type="PageAttachment",entity_id=str(item.id),old_data=old,new_data=values))
    async def move(self,item:PageAttachment,actor:int,delta:int)->None:
        items=list((await self.session.scalars(select(PageAttachment).where(PageAttachment.revision_id==item.revision_id).order_by(PageAttachment.position,PageAttachment.id))).all());i=items.index(item);j=max(0,min(len(items)-1,i+delta));items[i],items[j]=items[j],items[i]
        for pos,x in enumerate(items):x.position=pos
        self.session.add(AuditLog(admin_id=actor,action="ATTACHMENT_UPDATED",entity_type="PageAttachment",entity_id=str(item.id),new_data={"position":j}))
    async def delete(self,item:PageAttachment,actor:int)->None:
        revision_id=item.revision_id;old={"file_name":item.file_name,"type":item.type.value};await self.session.delete(item);await self.session.flush()
        items=list((await self.session.scalars(select(PageAttachment).where(PageAttachment.revision_id==revision_id).order_by(PageAttachment.position,PageAttachment.id))).all())
        for pos,x in enumerate(items):x.position=pos
        self.session.add(AuditLog(admin_id=actor,action="ATTACHMENT_DELETED",entity_type="PageAttachment",entity_id=str(item.id),old_data=old))
