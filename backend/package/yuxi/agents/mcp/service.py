"""MCP Service - Unified business logic and state management for MCP.

Responsibilities:
- Server configuration CRUD operations
- Built-in configuration synchronization (Code <-> Database)
- Unified entry point for Agent tool retrieval (auto-filtering disabled_tools)
- MCP Client and Tools management (formerly in agents/common/mcp.py)
"""

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from agentscope.mcp import HttpMCPConfig, MCPClient, StdioMCPConfig
from agentscope.tool import MCPTool
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.mcp.catalog import (
    BUILTIN_MCP_SERVER_SLUGS as _BUILTIN_MCP_SERVER_SLUGS,
    DEFAULT_MCP_SERVERS as _DEFAULT_MCP_SERVERS,
    RETIRED_BUILTIN_MCP_SERVER_SLUGS as _RETIRED_BUILTIN_MCP_SERVER_SLUGS,
    SYNCED_MCP_FIELDS as _SYNCED_MCP_FIELDS,
    is_builtin_mcp_slug,
)
from yuxi.storage.postgres.models_business import MCPServer
from yuxi.utils import logger

# =============================================================================
# === Global Cache & State ===
# =============================================================================

# Global Lock for MCP state
_mcp_lock = asyncio.Lock()

# 本地仅缓存工具对象。配置始终以数据库为准，每次按 server_slug 现查。
# cache key 使用 server_slug:config_hash，当配置变化时会自然失效。
_mcp_tools_cache: dict[str, list[Callable[..., Any]]] = {}

# MCP tools statistics (for reporting enabled/disabled counts)
_mcp_tools_stats: dict[str, dict[str, int]] = {}
_USER_CONFIGURABLE_TRANSPORTS = ("sse", "streamable_http")


class MCPServerNotFoundError(ValueError):
    """表示指定的 MCP 服务器不存在。"""


def is_builtin_mcp_server(server: MCPServer) -> bool:
    """判断 MCP 是否由代码中的内置定义管理。"""
    return is_builtin_mcp_slug(server.slug)


def requires_mcp_stdio_migration(server: MCPServer) -> bool:
    """判断 MCP 是否为升级后需要迁移的用户 stdio 配置。"""
    return server.transport == "stdio" and not is_builtin_mcp_server(server)


def _to_runtime_mcp_config(server: MCPServer) -> dict[str, Any]:
    """生成运行时 MCP 配置，内置连接字段始终以代码定义为准。"""
    if not is_builtin_mcp_server(server):
        return server.to_mcp_config()

    builtin = _DEFAULT_MCP_SERVERS[server.slug]
    config = {
        key: builtin[key]
        for key in ("transport", "url", "command", "args", "env", "headers", "timeout", "sse_read_timeout")
        if builtin.get(key) is not None
    }
    if server.disabled_tools:
        config["disabled_tools"] = server.disabled_tools
    return config


# =============================================================================
# === Core Logic (Moved from agents/common/mcp.py) ===
# =============================================================================


async def ensure_builtin_mcp_servers_in_db() -> None:
    """Ensure built-in MCP server definitions exist in the database."""
    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as session:
        any_changed = False

        result = await session.execute(
            select(MCPServer).where(
                MCPServer.transport == "stdio",
                ~MCPServer.slug.in_(_BUILTIN_MCP_SERVER_SLUGS),
                MCPServer.enabled == 1,
            )
        )
        for server in result.scalars().all():
            server.enabled = 0
            server.updated_by = "system"
            clear_mcp_server_tools_cache(server.resource_id)
            any_changed = True
            logger.warning(f"Disabled legacy user stdio MCP server '{server.slug}'")

        for slug in _RETIRED_BUILTIN_MCP_SERVER_SLUGS:
            result = await session.execute(
                select(MCPServer).filter(MCPServer.slug == slug, MCPServer.created_by == "system")
            )
            retired = result.scalar_one_or_none()
            if retired:
                await session.delete(retired)
                clear_mcp_server_tools_cache(retired.resource_id)
                any_changed = True
                logger.info(f"Removed retired built-in MCP server '{slug}' from database")

        for slug, config in _DEFAULT_MCP_SERVERS.items():
            result = await session.execute(select(MCPServer).filter(MCPServer.slug == slug))
            existing = result.scalar_one_or_none()
            if not existing:
                session.add(
                    MCPServer(
                        slug=slug,
                        name=config.get("name", slug),
                        description=config.get("description"),
                        transport=config["transport"],
                        url=config.get("url"),
                        command=config.get("command"),
                        args=config.get("args"),
                        env=config.get("env"),
                        headers=config.get("headers"),
                        timeout=config.get("timeout"),
                        sse_read_timeout=config.get("sse_read_timeout"),
                        tags=config.get("tags"),
                        icon=config.get("icon"),
                        enabled=0,
                        created_by="system",
                        updated_by="system",
                    )
                )
                any_changed = True
                logger.info(f"Added built-in MCP server '{slug}' to database")
                continue

            server_changed = False
            for field in _SYNCED_MCP_FIELDS:
                next_value = config.get(field)
                if getattr(existing, field) != next_value:
                    setattr(existing, field, next_value)
                    server_changed = True
            if existing.created_by != "system":
                existing.created_by = "system"
                server_changed = True
            if server_changed:
                existing.updated_by = "system"
                any_changed = True

        if any_changed:
            await session.commit()


async def get_mcp_client(
    server_configs: dict[str, Any] | None = None,
) -> MCPClient:
    """按数据库配置创建 AgentScope MCP 客户端。"""
    if not server_configs or len(server_configs) != 1:
        raise ValueError("每个 MCPClient 必须且只能包含一个服务器配置")

    server_slug, config = next(iter(server_configs.items()))
    logical_slug = config.get("slug", server_slug)
    transport = config.get("transport")
    if transport == "stdio":
        if logical_slug not in _BUILTIN_MCP_SERVER_SLUGS:
            raise ValueError("用户 stdio MCP 不允许在服务进程启动")
        mcp_config = StdioMCPConfig(
            command=config["command"],
            args=config.get("args"),
            env=config.get("env"),
        )
    elif transport in _USER_CONFIGURABLE_TRANSPORTS:
        if not config.get("url"):
            raise ValueError(f"MCP 服务器 {server_slug} 缺少 URL")
        mcp_config = HttpMCPConfig(
            url=config["url"],
            headers=config.get("headers"),
            timeout=float(config.get("timeout") or config.get("sse_read_timeout") or 30.0),
        )
    else:
        raise ValueError(f"MCP 服务器 {server_slug} 使用不支持的 transport: {transport}")

    return MCPClient(
        name=server_slug,
        is_stateful=transport == "stdio",
        mcp_config=mcp_config,
        disable_tools=list(config.get("disabled_tools") or []),
    )


async def _build_stdio_mcp_tools(server_slug: str, config: dict[str, Any]) -> list[MCPTool]:
    """发现内置 stdio 工具，并返回每次调用独立建连的工具对象。"""
    params = StdioServerParameters(
        command=config["command"],
        args=list(config.get("args") or []),
        env=config.get("env"),
    )

    def client_gen():
        return stdio_client(params)

    async with client_gen() as cli:
        read_stream, write_stream = cli[0], cli[1]
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            response = await session.list_tools()

    disabled_tools = set(config.get("disabled_tools") or [])
    timeout = float(config.get("timeout") or config.get("sse_read_timeout") or 30.0)
    return [
        MCPTool(
            mcp_name=server_slug,
            tool=tool,
            client_gen=client_gen,
            timeout=timeout,
        )
        for tool in response.tools
        if tool.name not in disabled_tools
    ]


def to_camel_case(s: str) -> str:
    """Convert string to lowerCamelCase."""

    # Handle - and _
    s = re.sub(r"[-_]+(.)", lambda m: m.group(1).upper(), s)
    # Lowercase first letter
    if len(s) > 0:
        s = s[0].lower() + s[1:]
    return s


async def load_enabled_mcp_server_configs(
    *,
    names: list[str] | None = None,
    db: AsyncSession | None = None,
    user=None,
    use_resource_ids: bool = False,
) -> dict[str, dict[str, Any]]:
    """按当前用户读取启用的 MCP 运行配置。"""
    if user is None:
        raise PermissionError("MCP 配置入口需要当前用户授权上下文")
    if db is not None:
        from yuxi.repositories.mcp_repository import list_enabled_visible_mcp_servers

        servers = await list_enabled_visible_mcp_servers(
            db,
            user=user,
            names=names,
            builtin_slugs=_BUILTIN_MCP_SERVER_SLUGS,
        )
        return {
            (server.resource_id if use_resource_ids else server.slug): {
                "resource_id": server.resource_id,
                "slug": server.slug,
                **_to_runtime_mcp_config(server),
            }
            for server in servers
        }

    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as session:
        return await load_enabled_mcp_server_configs(
            names=names, db=session, user=user, use_resource_ids=use_resource_ids
        )


async def get_enabled_mcp_server_config(
    server_slug: str, *, db: AsyncSession | None = None, user=None
) -> dict[str, Any] | None:
    """Get the latest enabled MCP server config from the database."""
    configs = await load_enabled_mcp_server_configs(names=[server_slug], db=db, user=user, use_resource_ids=True)
    direct = configs.get(server_slug)
    if direct is not None:
        return direct
    matches = [config for config in configs.values() if config.get("slug") == server_slug]
    return matches[0] if len(matches) == 1 else None


async def get_enabled_mcp_server_slugs(
    *, db: AsyncSession | None = None, user=None, use_resource_ids: bool = False
) -> list[str]:
    """按当前用户返回启用的 MCP 引用。"""
    if user is None:
        raise PermissionError("MCP 配置入口需要当前用户授权上下文")
    if db is not None:
        configs = await load_enabled_mcp_server_configs(
            db=db,
            user=user,
            use_resource_ids=use_resource_ids,
        )
        return list(configs)

    from yuxi.storage.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as session:
        return await get_enabled_mcp_server_slugs(db=session, user=user, use_resource_ids=use_resource_ids)


async def get_mcp_tools(
    server_slug: str,
    additional_servers: dict[str, dict[str, Any]] | None = None,
    disabled_tools: list[str] = None,
    cache: bool = True,
    force_refresh: bool = False,
    *,
    user=None,
) -> list[Callable[..., Any]]:
    """Get MCP tools for a specific server.

    Architecture:
    1. Fetching: Connects to MCP server to get ALL tools.
    2. Caching: Stores the FULL, UNFILTERED list of tools in `_mcp_tools_cache`.
    3. Filtering: Filters the return value based on `disabled_tools` argument.

    Args:
        server_slug: Server slug
        additional_servers: Additional server configurations
        disabled_tools: List of tool names to filter out from the RETURN value (does not affect cache)
        cache: Whether to use/update the cache (default: True)
        force_refresh: Whether to force a refresh from the server (default: False)
    """
    if additional_servers and server_slug in additional_servers:
        server_config = additional_servers[server_slug]
    else:
        if user is None:
            raise PermissionError("MCP 工具入口需要当前用户授权上下文")
        server_config = await get_enabled_mcp_server_config(server_slug, user=user)

    if server_config is None:
        logger.warning(f"MCP server '{server_slug}' not found in database or disabled")
        return []

    # 配置 hash 直接基于完整配置生成。只要数据库中的配置发生变化，
    # 本地工具缓存 key 就会变化，从而自然触发重建。
    config_payload = json.dumps(server_config, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    config_hash = hashlib.sha256(config_payload.encode("utf-8")).hexdigest()[:16]
    resource_id = server_config.get("resource_id")
    if not isinstance(resource_id, str) or not resource_id.strip():
        raise PermissionError("MCP 工具配置缺少 resource_id")
    server_slug = resource_id
    cache_key = f"{resource_id}:{config_hash}"

    all_processed_tools: list[Callable[..., Any]] = []

    async with _mcp_lock:
        if not force_refresh and cache and cache_key in _mcp_tools_cache:
            all_processed_tools = _mcp_tools_cache[cache_key]

    if not all_processed_tools:
        try:
            if server_config.get("transport") == "stdio":
                raw_tools = list(await _build_stdio_mcp_tools(server_slug, server_config))
            else:
                # disabled_tools 只影响返回值过滤，不参与 MCP client 建连参数。
                client_config = {k: v for k, v in server_config.items() if k not in ("disabled_tools",)}
                client = await get_mcp_client({server_slug: client_config})
                raw_tools = list(await client.list_tools())

            server_cc = to_camel_case(server_slug)
            for tool in raw_tools:
                original_name = tool.name
                namespaced_prefix = f"mcp__{server_slug}__"
                if original_name.startswith(namespaced_prefix):
                    original_name = original_name[len(namespaced_prefix) :]
                tool_cc = to_camel_case(original_name)
                unique_id = f"mcp__{server_cc}__{tool_cc}"

                metadata = dict(getattr(tool, "metadata", {}) or {})
                metadata["id"] = unique_id
                metadata["mcp_tool_name"] = original_name
                tool.metadata = metadata
                all_processed_tools.append(tool)

            if cache:
                async with _mcp_lock:
                    stale_keys = [
                        key for key in _mcp_tools_cache if key.startswith(f"{resource_id}:") and key != cache_key
                    ]
                    for stale_key in stale_keys:
                        _mcp_tools_cache.pop(stale_key, None)
                    _mcp_tools_cache[cache_key] = all_processed_tools

                global_config_disabled = server_config.get("disabled_tools") or []
                enabled_count = len(
                    [
                        tool
                        for tool in all_processed_tools
                        if (getattr(tool, "metadata", {}) or {}).get("mcp_tool_name", tool.name)
                        not in global_config_disabled
                    ]
                )
                _mcp_tools_stats[server_slug] = {
                    "total": len(all_processed_tools),
                    "enabled": enabled_count,
                    "disabled": len(all_processed_tools) - enabled_count,
                }

                logger.info(
                    f"Refreshed MCP tools cache for '{server_slug}' with key '{cache_key}': "
                    f"{len(all_processed_tools)} tools loaded."
                )

        except Exception as e:
            logger.exception(f"Failed to load tools from MCP server '{server_slug}': {e}")
            raise RuntimeError(f"MCP server '{server_slug}' is unavailable: {e}") from e

    # 3. Filtering (Apply to Return Value Only)
    if disabled_tools:
        filtered_tools = [
            tool
            for tool in all_processed_tools
            if (getattr(tool, "metadata", {}) or {}).get("mcp_tool_name", tool.name) not in disabled_tools
        ]
        logger.debug(
            f"Returning {len(filtered_tools)}/{len(all_processed_tools)} tools for '{server_slug}' "
            f"(filtered {len(disabled_tools)} by argument)"
        )
        return filtered_tools

    return all_processed_tools


async def get_tools_from_all_servers(*, user=None) -> list[Callable[..., Any]]:
    """Get all tools from all configured MCP servers."""
    if user is None:
        raise PermissionError("MCP 工具入口需要当前用户授权上下文")
    server_configs = await load_enabled_mcp_server_configs(user=user, use_resource_ids=True)
    all_tools = []
    for server_slug in server_configs:
        tools = await get_mcp_tools(server_slug, additional_servers=server_configs)
        all_tools.extend(tools)
    return all_tools


def clear_mcp_cache() -> None:
    """Clear the MCP tools cache (useful for testing)."""
    global _mcp_tools_cache, _mcp_tools_stats
    _mcp_tools_cache = {}
    _mcp_tools_stats = {}


def clear_mcp_server_tools_cache(resource_id: str) -> None:
    """Clear the tools cache for a specific MCP server."""
    global _mcp_tools_cache, _mcp_tools_stats
    server_prefix = f"{resource_id}:"
    stale_keys = [key for key in _mcp_tools_cache if key.startswith(server_prefix)]
    for stale_key in stale_keys:
        _mcp_tools_cache.pop(stale_key, None)
    _mcp_tools_stats.pop(resource_id, None)
    logger.info(f"Cleared tools cache for MCP resource '{resource_id}'")


def get_mcp_tools_stats(server_slug: str) -> dict[str, int] | None:
    """Get tools statistics for a MCP server.

    Returns:
        dict with 'total', 'enabled', 'disabled' counts, or None if not available
    """
    return _mcp_tools_stats.get(server_slug)


# =============================================================================
# === Server Config CRUD ===
# =============================================================================


async def get_mcp_server(db: AsyncSession, slug: str, *, allow_slug: bool = True) -> MCPServer | None:
    """按资源 ID获取 MCP；仅内部旧数据同步允许显式使用 slug。"""
    from yuxi.repositories.mcp_repository import resolve_mcp_server_reference

    return await resolve_mcp_server_reference(db, slug, allow_slug=allow_slug)


async def get_all_mcp_servers(db: AsyncSession, *, user=None) -> list[MCPServer]:
    """获取当前用户可读的 MCP 配置。"""
    from yuxi.repositories.mcp_repository import list_visible_mcp_servers

    return await list_visible_mcp_servers(db, user=user)


async def create_mcp_server(
    db: AsyncSession,
    slug: str,
    name: str,
    transport: str,
    url: str = None,
    description: str = None,
    headers: dict = None,
    timeout: int = None,
    sse_read_timeout: int = None,
    tags: list = None,
    icon: str = None,
    created_by: str = None,
    share_config: dict | None = None,
    operator=None,
) -> MCPServer:
    """Create server."""
    if operator is None:
        raise PermissionError("MCP 写入需要当前操作者")
    if slug in _BUILTIN_MCP_SERVER_SLUGS:
        raise ValueError("系统内置 MCP 的 slug 由代码保留，无法通过接口创建")
    if transport not in _USER_CONFIGURABLE_TRANSPORTS:
        raise ValueError("用户创建的 MCP 仅支持 sse 或 streamable_http，不允许启动 stdio 本地进程")

    from yuxi.permissions.resource_permission import normalize_permission_config

    share_config = normalize_permission_config(
        share_config, allowed_access_levels={"global", "department"}, strict=True
    )
    if share_config["read_scope"]["access_level"] == "global" and operator.role != "superadmin":
        raise PermissionError("只有超级管理员可以创建 global MCP")
    if share_config["read_scope"]["access_level"] == "department" and operator.role != "superadmin":
        if set(share_config["read_scope"]["department_ids"]) != {int(operator.department_id or 0)}:
            raise PermissionError("部门管理员只能创建本部门可见的 MCP")

    server = MCPServer(
        slug=slug,
        name=name,
        description=description,
        transport=transport,
        url=url,
        headers=headers,
        timeout=timeout,
        sse_read_timeout=sse_read_timeout,
        tags=tags,
        icon=icon,
        enabled=1,
        created_by=str(getattr(operator, "uid", None) or created_by),
        updated_by=created_by,
        share_config=share_config,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)

    clear_mcp_server_tools_cache(server.resource_id)

    logger.info(f"Created MCP server '{slug}'")
    return server


async def update_mcp_server(
    db: AsyncSession,
    slug: str,
    name: str = None,
    description: str = None,
    transport: str = None,
    url: str = None,
    headers: dict = None,
    timeout: int = None,
    sse_read_timeout: int = None,
    tags: list = None,
    icon: str = None,
    updated_by: str = None,
    share_config: dict | None = None,
    operator=None,
    resource_only: bool = False,
) -> MCPServer:
    """Update server configuration."""
    if operator is None:
        raise PermissionError("MCP 写入需要当前操作者")
    server = await get_mcp_server(db, slug, allow_slug=not resource_only)
    if not server:
        raise MCPServerNotFoundError(f"Server '{slug}' does not exist")
    if is_builtin_mcp_server(server):
        raise PermissionError("系统内置 MCP 的连接配置由代码管理，无法通过接口修改")
    from yuxi.permissions.resource_permission import (
        ResourcePermission,
        normalize_permission_config,
        require_resource_permission,
        resolve_mcp_permission,
    )

    require_resource_permission(resolve_mcp_permission(operator, server), ResourcePermission.MANAGE)
    if share_config is not None:
        normalized = normalize_permission_config(
            share_config,
            allowed_access_levels={"global", "department"},
            strict=True,
        )
        if normalized["read_scope"]["access_level"] == "global" and operator.role != "superadmin":
            raise PermissionError("只有超级管理员可以修改为 global MCP")
        if normalized["read_scope"]["access_level"] == "department" and operator.role != "superadmin":
            if set(normalized["read_scope"]["department_ids"]) != {int(operator.department_id or 0)}:
                raise PermissionError("部门管理员只能管理本部门 MCP")
        server.share_config = normalized

    next_transport = transport or server.transport
    if next_transport not in _USER_CONFIGURABLE_TRANSPORTS:
        raise ValueError("用户创建的 MCP 仅支持 sse 或 streamable_http，不允许启动 stdio 本地进程")

    next_url = url if url is not None else server.url
    if not next_url or not next_url.strip():
        raise ValueError(f"传输类型为 {next_transport} 时，url 必填")

    if name is not None:
        server.name = name
    if description is not None:
        server.description = description
    if transport is not None:
        server.transport = transport
    if url is not None:
        server.url = url
    server.command = None
    server.args = None
    server.env = None
    if headers is not None:
        server.headers = headers
    if timeout is not None:
        server.timeout = timeout
    if sse_read_timeout is not None:
        server.sse_read_timeout = sse_read_timeout
    if tags is not None:
        server.tags = tags
    if icon is not None:
        server.icon = icon
    if updated_by is not None:
        server.updated_by = updated_by

    await db.commit()
    await db.refresh(server)

    clear_mcp_server_tools_cache(server.resource_id)

    logger.info(f"Updated MCP server '{slug}'")
    return server


async def delete_mcp_server(db: AsyncSession, slug: str, *, operator=None, resource_only: bool = False) -> bool:
    """Delete server."""
    if operator is None:
        raise PermissionError("MCP 写入需要当前操作者")
    server = await get_mcp_server(db, slug, allow_slug=not resource_only)
    if not server:
        return False
    from yuxi.permissions.resource_permission import (
        ResourcePermission,
        require_resource_permission,
        resolve_mcp_permission,
    )

    require_resource_permission(resolve_mcp_permission(operator, server), ResourcePermission.MANAGE)

    await db.delete(server)
    await db.commit()

    clear_mcp_server_tools_cache(server.resource_id)

    logger.info(f"Deleted MCP server '{slug}'")
    return True


# =============================================================================
# === Tool Management ===
# =============================================================================


async def set_server_enabled(
    db: AsyncSession,
    slug: str,
    enabled: bool,
    updated_by: str = None,
    *,
    operator=None,
    resource_only: bool = False,
) -> tuple[bool, MCPServer]:
    """Set server enabled status."""
    if operator is None:
        raise PermissionError("MCP 写入需要当前操作者")
    server = await get_mcp_server(db, slug, allow_slug=not resource_only)
    if not server:
        raise MCPServerNotFoundError(f"Server '{slug}' does not exist")
    from yuxi.permissions.resource_permission import (
        ResourcePermission,
        require_resource_permission,
        resolve_mcp_permission,
    )

    require_resource_permission(resolve_mcp_permission(operator, server), ResourcePermission.MANAGE)
    if enabled and requires_mcp_stdio_migration(server):
        raise ValueError("历史 stdio MCP 已被禁用，请改为 sse 或 streamable_http 后再启用")

    server.enabled = 1 if enabled else 0
    if updated_by is not None:
        server.updated_by = updated_by
    await db.commit()

    is_enabled = bool(server.enabled)
    clear_mcp_server_tools_cache(server.resource_id)

    logger.info(f"Set MCP server '{slug}' enabled={is_enabled}")
    return is_enabled, server


async def toggle_tool_enabled(
    db: AsyncSession,
    server_slug: str,
    tool_name: str,
    updated_by: str = None,
    *,
    operator=None,
    resource_only: bool = False,
) -> tuple[bool, MCPServer]:
    """Toggle single tool enabled status.

    Args:
        db: Database session
        server_slug: Server slug
        tool_name: Tool name
        updated_by: Updater

    Returns:
        (enabled, server): Tool enabled status and updated server object
    """
    if operator is None:
        raise PermissionError("MCP 写入需要当前操作者")
    server = await get_mcp_server(db, server_slug, allow_slug=not resource_only)
    if not server:
        raise MCPServerNotFoundError(f"Server '{server_slug}' does not exist")
    from yuxi.permissions.resource_permission import (
        ResourcePermission,
        require_resource_permission,
        resolve_mcp_permission,
    )

    require_resource_permission(resolve_mcp_permission(operator, server), ResourcePermission.MANAGE)

    disabled_tools = list(server.disabled_tools or [])

    if tool_name in disabled_tools:
        disabled_tools.remove(tool_name)
        enabled = True
    else:
        disabled_tools.append(tool_name)
        enabled = False

    server.disabled_tools = disabled_tools
    if updated_by is not None:
        server.updated_by = updated_by
    await db.commit()

    # Clear tool cache (re-filtered on next fetch)
    clear_mcp_server_tools_cache(server.resource_id)

    logger.info(f"Toggled tool '{tool_name}' for server '{server_slug}' enabled={enabled}")
    return enabled, server


# =============================================================================
# === Unified Entry Points (Wrappers) ===
# =============================================================================


async def get_enabled_mcp_tools(server_slug: str, *, user=None) -> list:
    """Get MCP server tools (auto-filtering disabled_tools).

    Unified entry point for Agents, automatically:
    1. Gets the latest server config from database
    2. Gets all tools
    3. Filters out disabled_tools

    Args:
        server_slug: Server slug

    Returns:
        List of enabled tools
    """
    if user is None:
        raise PermissionError("MCP 工具入口需要当前用户授权上下文")
    config = await get_enabled_mcp_server_config(server_slug, user=user)
    if config is None:
        logger.warning(f"MCP server '{server_slug}' not found in database or disabled")
        return []

    disabled_tools = config.get("disabled_tools") or []
    return await get_mcp_tools(
        server_slug,
        additional_servers={server_slug: config},
        disabled_tools=disabled_tools,
        user=user,
    )


async def get_servers_config(names: list[str], *, user=None) -> dict[str, dict[str, Any]]:
    """Batch get server configurations.

    Args:
        names: List of server names

    Returns:
        {name: config} dictionary, containing only found servers
    """
    if user is None:
        raise PermissionError("MCP 配置入口需要当前用户授权上下文")
    return await load_enabled_mcp_server_configs(names=names, user=user, use_resource_ids=True)


async def get_all_mcp_tools(server_slug: str, *, user=None) -> list:
    """Get all tools of an MCP server (no filtering).

    For management UI to display tool list, supports viewing all tools and their enabled status.
    Does NOT update the global tools cache to avoid polluting agent's filtered view.

    Args:
        server_slug: Server slug

    Returns:
        List of all tools (unfiltered)
    """
    if user is None:
        raise PermissionError("MCP 工具入口需要当前用户授权上下文")
    config = await get_enabled_mcp_server_config(server_slug, user=user)
    if config is None:
        logger.warning(f"MCP server '{server_slug}' not found in database or disabled")
        return []

    # Get all tools (no filtering, force refresh, no cache update)
    return await get_mcp_tools(
        server_slug,
        additional_servers={server_slug: config},
        disabled_tools=[],
        cache=False,
        force_refresh=True,
        user=user,
    )
