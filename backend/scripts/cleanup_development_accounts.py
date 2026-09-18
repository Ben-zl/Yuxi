"""一次性开发账号清理脚本：软删除非超管账号并撤销其凭证与部门成员关系。

默认仅预览各分类计数；显式传入 ``--apply`` 才执行写入。所有写入都发生在
调用方开启的单个事务内，中途异常整体回滚，不产生部分提交。清理不删除部门、
不删除任何业务历史数据，也不调用通用账号硬删除逻辑。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

APP_ROOT = Path(__file__).resolve().parents[1]
for import_path in (APP_ROOT, APP_ROOT / "package"):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

from yuxi.storage.postgres.models_business import (  # noqa: E402
    APIKey,
    AuthSession,
    CLIAuthSession,
    DepartmentMembership,
    User,
)
from yuxi.utils.datetime_utils import utc_now_naive  # noqa: E402


async def _soft_delete_user(db: AsyncSession, user: User) -> None:
    """软删除单个已锁定账号：仅置删除标记，不修改角色与业务数据。"""
    user.is_deleted = 1
    user.deleted_at = utc_now_naive()


async def _remove_memberships(db: AsyncSession, user_id: int) -> int:
    """删除账号的全部部门成员关系，返回删除行数。"""
    result = await db.execute(delete(DepartmentMembership).where(DepartmentMembership.user_id == user_id))
    return result.rowcount or 0


async def _revoke_auth_sessions(db: AsyncSession, user_id: int) -> int:
    """撤销账号尚未撤销的交互会话，返回新撤销行数。"""
    result = await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utc_now_naive())
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


async def _revoke_api_keys(db: AsyncSession, user_id: int) -> int:
    """撤销账号直接持有及经 CLI 授权会话关联的 API Key，返回新撤销行数。"""
    cli_linked_key_ids = (
        select(CLIAuthSession.api_key_id)
        .where(CLIAuthSession.approved_user_id == user_id, CLIAuthSession.api_key_id.isnot(None))
        .scalar_subquery()
    )
    result = await db.execute(
        update(APIKey)
        .where(
            (APIKey.user_id == user_id) | APIKey.id.in_(cli_linked_key_ids),
            (APIKey.revoked_at.is_(None)) | APIKey.is_enabled.is_(True),
        )
        .values(is_enabled=False, revoked_at=utc_now_naive())
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


def _candidate_filter():
    """清理候选：全部未删除的非超管账号；超管在任何分支都不可命中。"""
    return (User.role != "superadmin") & (User.is_deleted == 0)


async def cleanup_accounts(db: AsyncSession, *, apply: bool) -> dict[str, int]:
    """统计（apply=True 时执行清理）非超管账号处置结果。

    返回 ``{candidate_accounts, deleted_accounts, revoked_credentials, removed_memberships}``。
    预览只做只读统计，不获取行锁也不产生任何写入；执行时先以 FOR UPDATE 锁定
    候选账号再逐个处置，全部写入依赖调用方事务提交或回滚。
    """
    if not apply:
        candidate_ids = select(User.id).where(_candidate_filter())
        cli_linked_key_ids = select(CLIAuthSession.api_key_id).where(
            CLIAuthSession.approved_user_id.in_(candidate_ids), CLIAuthSession.api_key_id.isnot(None)
        )
        active_sessions = (
            await db.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.user_id.in_(candidate_ids), AuthSession.revoked_at.is_(None))
            )
        ) or 0
        revocable_keys = (
            await db.scalar(
                select(func.count())
                .select_from(APIKey)
                .where(
                    (APIKey.user_id.in_(candidate_ids)) | APIKey.id.in_(cli_linked_key_ids),
                    (APIKey.revoked_at.is_(None)) | APIKey.is_enabled.is_(True),
                )
            )
        ) or 0
        memberships = (
            await db.scalar(
                select(func.count())
                .select_from(DepartmentMembership)
                .where(DepartmentMembership.user_id.in_(candidate_ids))
            )
        ) or 0
        candidate_total = (await db.scalar(select(func.count()).select_from(User).where(_candidate_filter()))) or 0
        return {
            "candidate_accounts": candidate_total,
            "deleted_accounts": 0,
            "revoked_credentials": active_sessions + revocable_keys,
            "removed_memberships": memberships,
        }

    candidates = (
        (await db.execute(select(User).where(_candidate_filter()).order_by(User.id).with_for_update())).scalars().all()
    )
    report = {
        "candidate_accounts": len(candidates),
        "deleted_accounts": 0,
        "revoked_credentials": 0,
        "removed_memberships": 0,
    }
    for user in candidates:
        await _soft_delete_user(db, user)
        report["deleted_accounts"] += 1
        report["removed_memberships"] += await _remove_memberships(db, user.id)
        report["revoked_credentials"] += await _revoke_auth_sessions(db, user.id)
        report["revoked_credentials"] += await _revoke_api_keys(db, user.id)
    return report


async def _run(apply: bool) -> dict[str, int]:
    """以独立直连引擎执行一次清理，事务内提交或整体回滚。"""
    postgres_url = os.environ.get("POSTGRES_URL")
    if not postgres_url:
        raise SystemExit("POSTGRES_URL 未设置：请在 api 容器内运行本脚本。")
    engine = create_async_engine(postgres_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                return await cleanup_accounts(session, apply=apply)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="一次性清理开发环境非超管账号（软删除并撤销凭证与成员关系）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行清理；缺省仅输出各分类预览计数，不写入任何数据",
    )
    args = parser.parse_args()

    report = asyncio.run(_run(apply=args.apply))
    mode = "已执行" if args.apply else "预览（未写入任何数据）"
    print(
        f"[{mode}] candidate_accounts={report['candidate_accounts']} "
        f"deleted_accounts={report['deleted_accounts']} "
        f"revoked_credentials={report['revoked_credentials']} "
        f"removed_memberships={report['removed_memberships']}"
    )
    print("超级管理员账号与其业务数据不受影响；部门与历史业务数据保持不变。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
