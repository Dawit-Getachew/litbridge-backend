"""Repository for user and refresh-token persistence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import RefreshToken, User


class UserRepository:
    """Data-access layer for users and refresh tokens."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── User operations ──────────────────────────────────────────

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, user_id: UUID) -> User | None:
        stmt = select(User).where(User.id == user_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def create(self, email: str, provider: str = "email") -> User:
        user = User(email=email, auth_provider=provider, is_verified=True)
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def update_last_login(self, user_id: UUID, now: datetime) -> None:
        stmt = update(User).where(User.id == user_id).values(last_login_at=now)
        await self.db.execute(stmt)
        await self.db.commit()

    # ── Refresh-token operations ─────────────────────────────────

    async def create_refresh_token(
        self,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
        device_info: str | None = None,
    ) -> RefreshToken:
        rt = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            device_info=device_info,
        )
        self.db.add(rt)
        await self.db.commit()
        await self.db.refresh(rt)
        return rt

    async def get_refresh_token(self, token_hash: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def revoke_refresh_token(self, token_hash: str) -> None:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .values(revoked=True)
        )
        await self.db.execute(stmt)
        await self.db.commit()

    async def revoke_all_user_tokens(self, user_id: UUID) -> None:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))
            .values(revoked=True)
        )
        await self.db.execute(stmt)
        await self.db.commit()
