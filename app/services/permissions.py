from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database.models import Admin
from app.database.models.enums import AdminRole


class Permission(StrEnum):
    CONTENT = "CONTENT"
    CONTENT_VIEW = "CONTENT_VIEW"
    CONTENT_MANAGE = "CONTENT_MANAGE"
    CONTENT_PUBLISH = "CONTENT_PUBLISH"
    TELEGRAM_IMPORT_MANAGE = "TELEGRAM_IMPORT_MANAGE"
    
    FAQ = "FAQ"
    FAQ_MANAGE = "FAQ_MANAGE"
    FAQ_PUBLISH = "FAQ_PUBLISH"

    BROADCAST = "BROADCAST"
    BROADCAST_MANAGE = "BROADCAST_MANAGE"
    
    STATISTICS = "STATISTICS"
    STATISTICS_VIEW = "STATISTICS_VIEW"
    
    ADMINS = "ADMINS"
    ADMINS_MANAGE = "ADMINS_MANAGE"

    AI_ANALYTICS_VIEW = "AI_ANALYTICS_VIEW"
    AI_HISTORY_VIEW = "AI_HISTORY_VIEW"
    AI_HISTORY_EXPORT = "AI_HISTORY_EXPORT"
    AI_DIAGNOSTICS = "AI_DIAGNOSTICS"
    AI_INDEX_SYNC = "AI_INDEX_SYNC"


ROLE_PERMISSIONS: dict[AdminRole, frozenset[Permission]] = {
    AdminRole.SUPER_ADMIN: frozenset(Permission),
    
    AdminRole.ADMIN: frozenset({
        Permission.CONTENT, 
        Permission.FAQ, 
        Permission.BROADCAST, 
        Permission.STATISTICS, 
        Permission.CONTENT_VIEW,
        Permission.CONTENT_MANAGE,
        Permission.CONTENT_PUBLISH,
        Permission.TELEGRAM_IMPORT_MANAGE,
        Permission.FAQ_MANAGE,
        Permission.FAQ_PUBLISH,
        Permission.BROADCAST_MANAGE,
        Permission.STATISTICS_VIEW,
        Permission.AI_ANALYTICS_VIEW,
        Permission.AI_HISTORY_VIEW,
        Permission.AI_HISTORY_EXPORT,
        Permission.AI_DIAGNOSTICS,
    }),

    AdminRole.EDITOR: frozenset({
        Permission.CONTENT, 
        Permission.FAQ,
        Permission.CONTENT_VIEW,
        Permission.CONTENT_MANAGE,
        Permission.FAQ_MANAGE,
    }),

    AdminRole.AI_HISTORY_VIEWER: frozenset({
        Permission.AI_HISTORY_VIEW,
    }),
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

    @staticmethod
    def role_has(role: AdminRole | None, permission: Permission) -> bool:
        if not isinstance(permission, Permission):
            return False

        if role is None:
            return False

        return permission in ROLE_PERMISSIONS.get(role, frozenset())
    
    async def has(self, telegram_id: int, permission: Permission) -> bool:
        role = await self.role_for(telegram_id)

        return self.role_has(role, permission)
