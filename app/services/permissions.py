from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database.models import Admin
from app.database.models.enums import AdminRole


class Permission(StrEnum):
    CONTENT = "CONTENT"
    FAQ = "FAQ"
    BROADCAST = "BROADCAST"
    STATISTICS = "STATISTICS"
    ADMINS = "ADMINS"


ROLE_PERMISSIONS = {
    AdminRole.SUPER_ADMIN: set(Permission),
    AdminRole.ADMIN: {Permission.CONTENT, Permission.FAQ, Permission.BROADCAST, Permission.STATISTICS},
    AdminRole.EDITOR: {Permission.CONTENT, Permission.FAQ},
}


class PermissionService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def role_for(self, telegram_id: int) -> AdminRole | None:
        if telegram_id == self.settings.super_admin_id:
            return AdminRole.SUPER_ADMIN
        admin = await self.session.scalar(select(Admin).where(Admin.telegram_id == telegram_id, Admin.is_active.is_(True)))
        return admin.role if admin else None

    async def has(self, telegram_id: int, permission: Permission) -> bool:
        role = await self.role_for(telegram_id)
        if not isinstance(permission, Permission):
            return False
        return bool(role and permission in ROLE_PERMISSIONS.get(role, set()))
