"""MCP 配置数据访问与可见性查询。"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.mcp.catalog import is_builtin_mcp_slug
from yuxi.permissions.resource_permission import ResourcePermission, resolve_mcp_permission
from yuxi.storage.postgres.models_business import MCPServer


async def get_mcp_server_by_resource_id(db: AsyncSession, resource_id: str) -> MCPServer | None:
    """按全局不可变资源 ID 获取 MCP。"""
    return await db.scalar(select(MCPServer).where(MCPServer.resource_id == resource_id))


async def resolve_mcp_server_reference(
    db: AsyncSession,
    reference: str,
    *,
    allow_slug: bool,
) -> MCPServer | None:
    """解析资源 ID；旧 slug 仅在唯一时转换。"""
    server = await get_mcp_server_by_resource_id(db, reference)
    if server is not None or not allow_slug:
        return server
    result = await db.execute(select(MCPServer).where(MCPServer.slug == reference).limit(2))
    matches = list(result.scalars().all())
    return matches[0] if len(matches) == 1 else None


async def list_visible_mcp_servers(db: AsyncSession, *, user) -> list[MCPServer]:
    """返回当前用户可读的 MCP；缺少身份时拒绝查询。"""
    if user is None:
        raise PermissionError("MCP 查询需要当前用户授权上下文")
    result = await db.execute(select(MCPServer).order_by(MCPServer.name.asc(), MCPServer.id.asc()))
    return [
        server for server in result.scalars().all() if resolve_mcp_permission(user, server) != ResourcePermission.NONE
    ]


async def list_enabled_visible_mcp_servers(
    db: AsyncSession,
    *,
    user,
    names: list[str] | None = None,
    builtin_slugs: tuple[str, ...] = (),
) -> list[MCPServer]:
    """返回当前用户可读且可运行的 MCP 配置。"""
    if user is None:
        raise PermissionError("MCP 查询需要当前用户授权上下文")
    stmt = select(MCPServer).where(
        MCPServer.enabled == 1,
        or_(MCPServer.transport != "stdio", MCPServer.slug.in_(builtin_slugs)),
    )
    if names:
        stmt = stmt.where(or_(MCPServer.resource_id.in_(names), MCPServer.slug.in_(names)))
    result = await db.execute(stmt.order_by(MCPServer.name.asc(), MCPServer.id.asc()))
    return [
        server for server in result.scalars().all() if resolve_mcp_permission(user, server) != ResourcePermission.NONE
    ]


def _is_runnable_mcp_server(server: MCPServer) -> bool:
    """判断 MCP 是否允许进入 Agent 运行时。"""
    return server.transport != "stdio" or is_builtin_mcp_slug(server.slug)


async def list_authorizable_mcp_servers(db: AsyncSession, *, user) -> list[MCPServer]:
    """返回 Agent 保存期可固化的可见、启用 MCP 资源。"""
    if user is None:
        raise PermissionError("MCP 查询需要当前用户授权上下文")
    result = await db.execute(
        select(MCPServer).where(MCPServer.enabled == 1).order_by(MCPServer.name.asc(), MCPServer.id.asc())
    )
    return [
        server
        for server in result.scalars().all()
        if _is_runnable_mcp_server(server) and resolve_mcp_permission(user, server) != ResourcePermission.NONE
    ]


async def get_authorizable_mcp_server(db: AsyncSession, reference: str, *, user) -> MCPServer | None:
    """解析 Agent MCP 引用，并在 repository 边界执行可见性与运行资格校验。"""
    if user is None:
        raise PermissionError("MCP 查询需要当前用户授权上下文")
    server = await resolve_mcp_server_reference(db, reference, allow_slug=True)
    if (
        server is None
        or not bool(server.enabled)
        or not _is_runnable_mcp_server(server)
        or resolve_mcp_permission(user, server) == ResourcePermission.NONE
    ):
        return None
    return server
