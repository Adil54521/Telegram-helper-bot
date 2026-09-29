from typing import Any

from aiogram.filters import Filter
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services.permissions import Permission, PermissionService


class HasPermission(Filter):
    def __init__(self, permission: Permission) -> None:
        self.permission = permission

    async def __call__(self, event: TelegramObject, session: AsyncSession, settings: Settings) -> bool | dict[str, Any]:
        user = getattr(event, "from_user", None)
        if not user:
            return False
        service = PermissionService(session, settings)
        role = await service.role_for(user.id)
        return {"admin_role": role} if role and await service.has(user.id, self.permission) else False

