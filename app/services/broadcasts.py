import asyncio
from datetime import datetime, timezone
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.database.models import AuditLog, Broadcast, TelegramUser
from app.database.models.enums import BroadcastStatus
from app.database.repositories.cms import CMSRepository
from app.utils.presentation import html_text

async def claim(session:AsyncSession,item_id:int,actor:int)->Broadcast|None:
    item=await CMSRepository(session).broadcast(item_id,lock=True)
    if not item or item.status!=BroadcastStatus.DRAFT:return None
    item.status=BroadcastStatus.RUNNING;item.started_at=datetime.now(timezone.utc);item.total=await CMSRepository(session).recipient_count();session.add(AuditLog(admin_id=actor,action="BROADCAST_STARTED",entity_type="Broadcast",entity_id=str(item.id),old_data={"status":"DRAFT"},new_data={"status":"RUNNING","total":item.total}));await session.flush();return item

async def run_broadcast(bot:Bot,factory:async_sessionmaker[AsyncSession],item_id:int,actor:int)->tuple[int,int,int]:
    async with factory() as s:
        async with s.begin(): item=await claim(s,item_id,actor)
        if not item:return (0,0,0)
        text,photo,total=item.text,item.media_file_id,item.total
    async with factory() as s: recipients=await CMSRepository(s).recipients()
    sent=failed=0
    for user in recipients:
        try:
            if photo:await bot.send_photo(user.telegram_id,photo=photo,caption=html_text(text))
            else:await bot.send_message(user.telegram_id,html_text(text))
            sent+=1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after+0.1)
            try:
                if photo:await bot.send_photo(user.telegram_id,photo=photo,caption=html_text(text))
                else:await bot.send_message(user.telegram_id,html_text(text))
                sent+=1
            except Exception:failed+=1
        except TelegramForbiddenError:
            failed+=1
            async with factory() as s, s.begin():
                dbuser=await s.get(TelegramUser,user.id)
                if dbuser:dbuser.is_blocked=True
        except TelegramBadRequest:failed+=1
        except Exception:failed+=1
        await asyncio.sleep(0.04)
        if (sent+failed)%25==0:
            async with factory() as s, s.begin():
                db=await s.get(Broadcast,item_id);db.sent=sent;db.failed=failed
    async with factory() as s, s.begin():
        db=await s.get(Broadcast,item_id);db.sent=sent;db.failed=failed;db.finished_at=datetime.now(timezone.utc);db.status=BroadcastStatus.COMPLETED;s.add(AuditLog(admin_id=actor,action="BROADCAST_COMPLETED",entity_type="Broadcast",entity_id=str(item_id),new_data={"sent":sent,"failed":failed}))
    return total,sent,failed
