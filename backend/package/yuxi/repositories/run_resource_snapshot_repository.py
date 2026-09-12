"""运行快照的事务持久化和执行身份查询。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import RunResourceSnapshot


async def store_snapshot(db: AsyncSession, snapshot: RunResourceSnapshot) -> None:
    """在调用者提交事务中插入快照，不单独提交。"""
    db.add(snapshot)
    await db.flush()


async def get_snapshot(db: AsyncSession, *, snapshot_id: str, uid: str) -> RunResourceSnapshot | None:
    """仅返回执行身份拥有的指定快照。"""
    return await db.scalar(
        select(RunResourceSnapshot).where(RunResourceSnapshot.id == snapshot_id, RunResourceSnapshot.uid == uid)
    )
