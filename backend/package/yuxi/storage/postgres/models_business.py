"""PostgreSQL 业务数据模型 - 用户、部门、对话等相关表"""

from datetime import timedelta
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from yuxi.storage.minio.client import normalize_public_minio_url
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now, utc_now_naive

Base = declarative_base()

JSON_VALUE = JSON().with_variant(JSONB, "postgresql")

MAX_LOGIN_FAILED_ATTEMPTS = 5
LOGIN_LOCK_DURATION_SECONDS = 300
AGENT_RUN_TERMINAL_STATUSES = ("completed", "failed", "cancelled", "interrupted")
AGENT_RUN_SHAPE_CONSTRAINT_NAME = "ck_agent_runs_nonterminal_shape"
AGENT_RUN_SHAPE_CONSTRAINT_SQL = """
status IN ('completed', 'failed', 'cancelled', 'interrupted')
OR (
    runtime_scope_id <> ''
 AND conversation_thread_id <> ''
 AND ((run_type = 'chat'
     AND runtime_scope_id = conversation_thread_id
     AND created_by_run_id IS NULL
     AND subagent_thread_relation_id IS NULL)
 OR (run_type = 'resume'
     AND runtime_scope_id = conversation_thread_id
     AND created_by_run_id IS NOT NULL
     AND subagent_thread_relation_id IS NULL)
 OR (run_type = 'subagent'
     AND created_by_run_id IS NOT NULL
     AND subagent_thread_relation_id IS NOT NULL))
)
"""
PROJECT_STATUS_CONSTRAINT_NAME = "ck_projects_status"
PROJECT_STATUS_CONSTRAINT_SQL = "status IN ('active', 'deleted')"
# 新建线程的初始已查看标记，用于区分"尚无任何 Run"与"上线前的历史会话"，
# 避免 startup 回填把后续新产生的未读状态误清为已读。不会与真实 Run id 冲突。
UNVIEWED_RUN_MARKER = "__unviewed__"


class Project(Base):
    """用户项目及其 Workdir 绑定。"""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("id", "uid", name="uq_projects_id_uid"),
        UniqueConstraint("uid", "idempotency_key", name="uq_projects_uid_idempotency_key"),
        CheckConstraint("selection_status IN ('implicit', 'selectable')", name="ck_projects_selection_status"),
        CheckConstraint("directory_mode IN ('managed', 'linked')", name="ck_projects_directory_mode"),
        CheckConstraint(PROJECT_STATUS_CONSTRAINT_SQL, name=PROJECT_STATUS_CONSTRAINT_NAME),
    )

    id = Column(String(64), primary_key=True, comment="Project UUID")
    uid = Column(
        String(64),
        ForeignKey("users.uid", ondelete="CASCADE", name="fk_projects_uid_users"),
        nullable=False,
        index=True,
        comment="UID",
    )
    name = Column(String(255), nullable=True, comment="项目名称；implicit Project 可为空")
    selection_status = Column(String(20), nullable=False, index=True, comment="implicit/selectable")
    workdir_path = Column(String(512), nullable=False, comment="UserWorkspace-relative Workdir path")
    directory_mode = Column(String(20), nullable=False, comment="managed/linked")
    status = Column(String(20), nullable=False, default="active", server_default="active", index=True)
    deleted_at = Column(DateTime, nullable=True, comment="软删除时间")
    idempotency_key = Column(String(128), nullable=True, comment="幂等创建键")
    created_at = Column(DateTime, default=utc_now_naive, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive, server_default=func.now(), nullable=False
    )

    conversations = relationship("Conversation", back_populates="project")

    def to_dict(self) -> dict[str, Any]:
        """序列化项目公开字段。"""
        return {
            "id": self.id,
            "uid": self.uid,
            "name": self.name,
            "selection_status": self.selection_status,
            "workdir_path": self.workdir_path,
            "directory_mode": self.directory_mode,
            "status": self.status,
            "deleted_at": format_utc_datetime(self.deleted_at),
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class Department(Base):
    """部门模型"""

    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False, unique=True, index=True)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)

    # 关联关系
    users = relationship("User", back_populates="department", cascade="all, delete-orphan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": format_utc_datetime(self.created_at),
        }


class User(Base):
    """用户模型"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, nullable=False, unique=True, index=True)  # 显示名称
    uid = Column(String, nullable=False, unique=True, index=True)  # 登录标识
    phone_number = Column(String, nullable=True, unique=True, index=True)  # 手机号
    avatar = Column(String, nullable=True)  # 头像URL
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="user")  # 角色: superadmin, admin, user
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True)  # 部门ID
    created_at = Column(DateTime, default=utc_now_naive)
    last_login = Column(DateTime, nullable=True)

    # 登录失败限制相关字段
    login_failed_count = Column(Integer, nullable=False, default=0)  # 登录失败次数
    last_failed_login = Column(DateTime, nullable=True)  # 最后一次登录失败时间
    login_locked_until = Column(DateTime, nullable=True)  # 锁定到什么时候

    # 软删除相关字段
    is_deleted = Column(Integer, nullable=False, default=0, index=True)  # 是否已删除：0=否，1=是
    deleted_at = Column(DateTime, nullable=True)  # 删除时间

    # 关联操作日志
    operation_logs = relationship("OperationLog", back_populates="user", cascade="all, delete-orphan")

    # 关联部门
    department = relationship("Department", back_populates="users")

    # 关联 API Keys
    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")

    agent_env = relationship("AgentEnv", back_populates="user", cascade="all, delete-orphan", uselist=False)
    user_config = relationship("UserConfig", back_populates="user", cascade="all, delete-orphan", uselist=False)

    def to_dict(self, include_password: bool = False) -> dict[str, Any]:
        result = {
            "id": self.id,
            "username": self.username,
            "uid": self.uid,
            "phone_number": self.phone_number,
            "avatar": normalize_public_minio_url(self.avatar),
            "role": self.role,
            "department_id": self.department_id,
            "created_at": format_utc_datetime(self.created_at),
            "last_login": format_utc_datetime(self.last_login),
            "login_failed_count": self.login_failed_count,
            "last_failed_login": format_utc_datetime(self.last_failed_login),
            "login_locked_until": format_utc_datetime(self.login_locked_until),
            "is_deleted": self.is_deleted,
            "deleted_at": format_utc_datetime(self.deleted_at),
        }
        if include_password:
            result["password_hash"] = self.password_hash
        return result

    def is_login_locked(self) -> bool:
        """检查用户是否处于登录锁定状态"""
        if self.login_locked_until is None:
            return False
        return utc_now_naive() < self.login_locked_until

    def get_remaining_lock_time(self) -> int:
        """获取剩余锁定时间（秒）"""
        if self.login_locked_until is None:
            return 0
        remaining = int((self.login_locked_until - utc_now_naive()).total_seconds())
        return max(0, remaining)

    def increment_failed_login(self):
        """增加登录失败计数，并在达到阈值后锁定登录"""
        self.login_failed_count += 1
        self.last_failed_login = utc_now_naive()
        if self.login_failed_count >= MAX_LOGIN_FAILED_ATTEMPTS:
            self.login_locked_until = self.last_failed_login + timedelta(seconds=LOGIN_LOCK_DURATION_SECONDS)

    def reset_failed_login(self):
        """重置登录失败相关字段"""
        self.login_failed_count = 0
        self.last_failed_login = None
        self.login_locked_until = None


class AgentEnv(Base):
    """用户级 Agent 沙盒环境变量"""

    __tablename__ = "agent_envs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(String, ForeignKey("users.uid"), nullable=False, unique=True, index=True)
    env = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    user = relationship("User", back_populates="agent_env")

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "env": self.env or {},
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class UserConfig(Base):
    """用户级配置"""

    __tablename__ = "user_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(String, ForeignKey("users.uid"), nullable=False, unique=True, index=True)
    enable_memory = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    user = relationship("User", back_populates="user_config")

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "enable_memory": bool(self.enable_memory),
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class AgentMemoryScope(Base):
    """用户与 Agent 共享的 ReMe Workspace 及 Dream 进度。"""

    __tablename__ = "agent_memory_scopes"

    uid = Column(String, ForeignKey("users.uid", ondelete="CASCADE"), primary_key=True)
    agent_slug = Column(String(80), primary_key=True)
    workspace_id = Column(String(64), nullable=False, unique=True, index=True)
    last_memory_at = Column(DateTime(timezone=True), nullable=True)
    last_dream_date = Column(Date, nullable=True)
    dream_status = Column(String(16), nullable=True)
    dream_attempted_at = Column(DateTime(timezone=True), nullable=True)
    dream_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """返回管理 API 使用的 scope 状态。"""
        return {
            "uid": self.uid,
            "agent_slug": self.agent_slug,
            "workspace_id": self.workspace_id,
            "last_memory_at": format_utc_datetime(self.last_memory_at),
            "last_dream_date": self.last_dream_date.isoformat() if self.last_dream_date else None,
            "dream_status": self.dream_status,
            "dream_attempted_at": format_utc_datetime(self.dream_attempted_at),
            "dream_error": self.dream_error,
        }


class Agent(Base):
    """用户可管理、可授权、可切换的智能体。"""

    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(80), nullable=False, unique=True, index=True)
    backend_id = Column(String(64), nullable=False, index=True)

    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    icon = Column(String(255), nullable=True)

    pics = Column(JSON, nullable=False, default=list)
    config_json = Column(JSON, nullable=False, default=dict)
    share_config = Column(JSON_VALUE, nullable=False)

    is_default = Column(Boolean, nullable=False, default=False, index=True)
    is_subagent = Column(Boolean, nullable=False, default=False, index=True)

    created_by = Column(String(64), nullable=True, index=True)
    updated_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    __table_args__ = (
        Index(
            "uq_agents_default",
            "is_default",
            unique=True,
            postgresql_where=is_default.is_(True),
            sqlite_where=is_default.is_(True),
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "agent_id": self.slug,
            "backend_id": self.backend_id,
            "name": self.name,
            "description": self.description,
            "icon": normalize_public_minio_url(self.icon),
            "pics": [normalize_public_minio_url(pic) for pic in (self.pics or [])],
            "config_json": self.config_json or {},
            "share_config": self.share_config or {},
            "is_default": bool(self.is_default),
            "is_subagent": bool(self.is_subagent),
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class Skill(Base):
    """Skill 元数据模型（内容存文件系统，索引存数据库）"""

    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(128), nullable=False, unique=True, index=True, comment="技能唯一标识（目录名）")
    name = Column(String(128), nullable=False, comment="技能名称（来自 SKILL.md frontmatter.name）")
    description = Column(Text, nullable=False, comment="技能描述（来自 SKILL.md frontmatter.description）")
    source_type = Column(
        String(32), nullable=False, default="upload", index=True, comment="来源: builtin/upload/remote"
    )
    tool_dependencies = Column(JSON, nullable=False, default=list, comment="依赖的内置工具名列表")
    mcp_dependencies = Column(JSON, nullable=False, default=list, comment="依赖的 MCP 服务名列表")
    skill_dependencies = Column(JSON, nullable=False, default=list, comment="依赖的其他 skill slug 列表")
    dir_path = Column(String(512), nullable=False, comment="技能目录路径（相对 save_dir）")
    version = Column(String(64), nullable=True, comment="技能版本（内置 skill 使用语义化版本）")
    content_hash = Column(String(128), nullable=True, comment="技能目录内容哈希（内置 skill 安装时计算）")
    share_config = Column(JSON_VALUE, nullable=False, comment="共享权限配置")
    enabled = Column(Boolean, nullable=False, default=True, comment="是否启用")
    created_by = Column(String(64), nullable=True)
    updated_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "source_type": self.source_type,
            "tool_dependencies": self.tool_dependencies or [],
            "mcp_dependencies": self.mcp_dependencies or [],
            "skill_dependencies": self.skill_dependencies or [],
            "dir_path": self.dir_path,
            "version": self.version,
            "content_hash": self.content_hash,
            "share_config": self.share_config or {},
            "enabled": bool(self.enabled),
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class Conversation(Base):
    """Conversation table - 对话表"""

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    thread_id = Column(String(64), unique=True, index=True, nullable=False, comment="Thread ID (UUID)")
    creation_request_id = Column(String(64), nullable=True, comment="新建 Conversation 幂等请求 ID")
    uid = Column(String(64), index=True, nullable=False, comment="UID")
    # 历史字段名，实际保存的是 Agent.slug。
    agent_id = Column(String(64), index=True, nullable=False, comment="Agent slug (legacy column name: agent_id)")
    title = Column(String(255), nullable=True, comment="Conversation title")
    status = Column(String(20), default="active", comment="Status: active/archived/deleted")
    is_pinned = Column(Boolean, default=False, nullable=False, index=True, comment="Is pinned to top")
    last_viewed_run_id = Column(String(64), nullable=True, comment="Latest top-level run id viewed by user")
    project_id = Column(String(64), nullable=False, index=True, comment="Conversation 绑定的 Project ID")
    created_at = Column(DateTime, default=utc_now_naive, comment="Creation time")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, comment="Update time")
    extra_metadata = Column(JSON, nullable=True, comment="Additional metadata")

    # Relationships
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    stats = relationship(
        "ConversationStats", back_populates="conversation", uselist=False, cascade="all, delete-orphan"
    )
    project = relationship("Project", back_populates="conversations")

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "uid"],
            ["projects.id", "projects.uid"],
            name="fk_conversations_project_uid",
        ),
        UniqueConstraint("uid", "creation_request_id", name="uq_conversations_uid_creation_request_id"),
    )

    def to_dict(self) -> dict[str, Any]:
        metadata = self.extra_metadata or {}
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "creation_request_id": self.creation_request_id,
            "uid": self.uid,
            "agent_id": self.agent_id,
            "title": self.title,
            "status": self.status,
            "is_pinned": bool(self.is_pinned),
            "project_id": self.project_id,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
            "metadata": metadata,
        }


class SubagentThread(Base):
    """SubagentThread table - 子智能体长期线程归属关系表"""

    __tablename__ = "subagent_threads"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    uid = Column(String(64), index=True, nullable=False, comment="UID")
    parent_conversation_id = Column(
        Integer, ForeignKey("conversations.id"), nullable=False, index=True, comment="Parent conversation ID"
    )
    child_conversation_id = Column(
        Integer,
        ForeignKey("conversations.id"),
        nullable=False,
        unique=True,
        index=True,
        comment="Child conversation ID",
    )
    child_thread_id = Column(String(64), nullable=False, unique=True, index=True, comment="Child thread ID")
    subagent_slug = Column(String(64), nullable=False, index=True, comment="Subagent slug")
    created_by_run_id = Column(String(64), nullable=False, index=True, comment="Run that created this subagent thread")
    created_at = Column(DateTime, default=utc_now_naive, comment="Creation time")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, comment="Update time")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "uid": self.uid,
            "parent_conversation_id": self.parent_conversation_id,
            "child_conversation_id": self.child_conversation_id,
            "child_thread_id": self.child_thread_id,
            "subagent_slug": self.subagent_slug,
            "created_by_run_id": self.created_by_run_id,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class Message(Base):
    """Message table - 消息表"""

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    conversation_id = Column(
        Integer, ForeignKey("conversations.id"), nullable=False, index=True, comment="Conversation ID"
    )
    role = Column(String(20), nullable=False, comment="Message role: user/assistant/system/tool")
    content = Column(Text, nullable=False, comment="Message content")
    message_type = Column(String(30), default="text", comment="Message type: text/tool_call/tool_result")
    created_at = Column(DateTime, default=utc_now_naive, comment="Creation time")
    token_count = Column(Integer, nullable=True, comment="Token count (optional)")
    extra_metadata = Column(JSON, nullable=True, comment="Additional metadata (complete message dump)")
    image_content = Column(Text, nullable=True, comment="Base64 encoded image content for multimodal messages")
    run_id = Column(String(64), ForeignKey("agent_runs.id"), nullable=True, index=True, comment="Agent run ID")
    request_id = Column(String(64), nullable=True, index=True, comment="Request ID for idempotency")
    delivery_status = Column(String(32), nullable=False, default="complete", comment="Message status")

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")
    tool_calls = relationship("ToolCall", back_populates="message", cascade="all, delete-orphan")
    feedbacks = relationship("MessageFeedback", back_populates="message", cascade="all, delete-orphan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "message_type": self.message_type,
            "created_at": format_utc_datetime(self.created_at),
            "token_count": self.token_count,
            "metadata": self.extra_metadata or {},
            "image_content": self.image_content,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "status": self.delivery_status,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls] if self.tool_calls else [],
        }

    def to_simple_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
        }


class ToolCall(Base):
    """ToolCall table - 工具调用表"""

    __tablename__ = "tool_calls"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False, index=True, comment="Message ID")
    langgraph_tool_call_id = Column(String(100), nullable=True, index=True, comment="LangGraph tool_call_id")
    tool_name = Column(String(100), nullable=False, comment="Tool name")
    tool_input = Column(JSON, nullable=True, comment="Tool input parameters")
    tool_output = Column(Text, nullable=True, comment="Tool execution result")
    status = Column(String(20), default="pending", comment="Status: pending/success/error")
    error_message = Column(Text, nullable=True, comment="Error message if failed")
    created_at = Column(DateTime, default=utc_now_naive, comment="Creation time")

    # Relationships
    message = relationship("Message", back_populates="tool_calls")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "langgraph_tool_call_id": self.langgraph_tool_call_id,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input or {},
            "tool_output": self.tool_output,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": format_utc_datetime(self.created_at),
        }


class ConversationStats(Base):
    """ConversationStats table - 对话统计表"""

    __tablename__ = "conversation_stats"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    conversation_id = Column(
        Integer, ForeignKey("conversations.id"), unique=True, nullable=False, comment="Conversation ID"
    )
    message_count = Column(Integer, default=0, comment="Total message count")
    total_tokens = Column(Integer, default=0, comment="Total tokens used")
    model_used = Column(String(100), nullable=True, comment="Model used")
    user_feedback = Column(JSON, nullable=True, comment="User feedback")
    created_at = Column(DateTime, default=utc_now_naive, comment="Creation time")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, comment="Update time")

    # Relationships
    conversation = relationship("Conversation", back_populates="stats")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            "model_used": self.model_used,
            "user_feedback": self.user_feedback or {},
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class OperationLog(Base):
    """操作日志模型"""

    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    operation = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)
    timestamp = Column(DateTime, default=utc_now_naive)

    # 关联用户
    user = relationship("User", back_populates="operation_logs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "operation": self.operation,
            "details": self.details,
            "ip_address": self.ip_address,
            "timestamp": format_utc_datetime(self.timestamp),
        }


class MessageFeedback(Base):
    """Message feedback table - 消息反馈表"""

    __tablename__ = "message_feedbacks"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    message_id = Column(
        Integer, ForeignKey("messages.id"), nullable=False, index=True, comment="Message ID being rated"
    )
    uid = Column(String(64), nullable=False, index=True, comment="UID who provided feedback")
    rating = Column(String(10), nullable=False, comment="Feedback rating: like or dislike")
    reason = Column(Text, nullable=True, comment="Optional reason for dislike feedback")
    created_at = Column(DateTime, default=utc_now_naive, comment="Feedback creation time")

    # Relationships
    message = relationship("Message", back_populates="feedbacks")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "uid": self.uid,
            "rating": self.rating,
            "reason": self.reason,
            "created_at": format_utc_datetime(self.created_at),
        }


class MCPServer(Base):
    """MCP 服务器配置模型"""

    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(100), nullable=False, unique=True, index=True, comment="稳定标识")
    name = Column(String(100), nullable=False, comment="展示名称")
    description = Column(String(500), nullable=True, comment="描述")

    # 连接配置
    transport = Column(String(20), nullable=False, comment="传输类型：sse/streamable_http/stdio")
    url = Column(String(500), nullable=True, comment="服务器 URL（sse/streamable_http）")
    command = Column(String(500), nullable=True, comment="命令（stdio）")
    args = Column(JSON, nullable=True, comment="命令参数数组（stdio）")
    env = Column(JSON, nullable=True, comment="环境变量（stdio）")
    headers = Column(JSON, nullable=True, comment="HTTP 请求头")
    timeout = Column(Integer, nullable=True, comment="HTTP 超时时间（秒）")
    sse_read_timeout = Column(Integer, nullable=True, comment="SSE 读取超时（秒）")

    # UI 增强字段
    tags = Column(JSON, nullable=True, comment="标签数组")
    icon = Column(String(50), nullable=True, comment="图标（emoji）")

    # 状态字段
    enabled = Column(Integer, nullable=False, default=1, comment="是否启用：1=是，0=否")
    disabled_tools = Column(JSON, nullable=True, comment="禁用的工具名称列表")

    # 用户追踪
    created_by = Column(String(100), nullable=False, comment="创建人用户名")
    updated_by = Column(String(100), nullable=False, comment="修改人用户名")

    # 时间戳
    created_at = Column(DateTime, default=utc_now_naive, comment="创建时间")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, comment="更新时间")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "transport": self.transport,
            "url": self.url,
            "command": self.command,
            "args": self.args or [],
            "env": self.env or {},
            "headers": self.headers or {},
            "timeout": self.timeout,
            "sse_read_timeout": self.sse_read_timeout,
            "tags": self.tags or [],
            "icon": self.icon,
            "enabled": bool(self.enabled),
            "disabled_tools": self.disabled_tools or [],
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }

    def to_mcp_config(self) -> dict[str, Any]:
        """转换为 MCP 配置格式（用于加载到 MCP_SERVERS 缓存）"""
        import json

        config = {"transport": self.transport}
        if self.transport in ("sse", "streamable_http") and self.url:
            config["url"] = self.url
        if self.transport == "stdio":
            if self.command:
                config["command"] = self.command
            if self.args:
                if isinstance(self.args, list):
                    config["args"] = self.args
                elif isinstance(self.args, str):
                    try:
                        config["args"] = json.loads(self.args)
                    except json.JSONDecodeError:
                        pass
            if self.env and isinstance(self.env, dict):
                config["env"] = self.env
            elif isinstance(self.env, str):
                try:
                    config["env"] = json.loads(self.env)
                except json.JSONDecodeError:
                    pass
        # headers 只用于 sse/streamable_http 传输类型
        if self.transport in ("sse", "streamable_http") and self.headers:
            if isinstance(self.headers, dict):
                config["headers"] = self.headers
            elif isinstance(self.headers, str):
                try:
                    config["headers"] = json.loads(self.headers)
                except json.JSONDecodeError:
                    pass
        if self.timeout is not None:
            config["timeout"] = self.timeout
        if self.sse_read_timeout is not None:
            config["sse_read_timeout"] = self.sse_read_timeout
        if self.disabled_tools:
            config["disabled_tools"] = self.disabled_tools
        return config


class ModelProvider(Base):
    """模型供应商配置，存储 provider 基础信息、模型端点和可用模型。"""

    __tablename__ = "model_providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(String(100), nullable=False, unique=True, index=True, comment="供应商稳定标识")
    display_name = Column(String(100), nullable=False, comment="展示名称")
    provider_type = Column(String(32), nullable=False, default="openai", comment="供应商适配类型，默认 openai")

    default_protocol = Column(String(64), nullable=True, comment="默认协议，如 openai_compatible")
    base_url = Column(String(500), nullable=False, comment="API 基础 URL")
    embedding_base_url = Column(String(500), nullable=True, comment="Embedding 模型请求基础 URL")
    rerank_base_url = Column(String(500), nullable=True, comment="Rerank 模型请求基础 URL")
    models_endpoint = Column(String(200), nullable=True, comment="聊天/通用模型列表端点")
    embedding_models_endpoint = Column(String(200), nullable=True, comment="Embedding 模型列表端点")
    rerank_models_endpoint = Column(String(200), nullable=True, comment="Rerank 模型列表端点")
    api_key_env = Column(String(128), nullable=True, comment="API Key 环境变量名")
    api_key = Column(String(500), nullable=True, comment="直接配置的 API Key")

    capabilities = Column(JSON, nullable=False, default=list, comment="支持能力：chat/embedding/rerank")
    enabled_models = Column(JSON, nullable=False, default=list, comment="已启用模型配置对象")
    headers_json = Column(JSON, nullable=True, comment="额外请求头")
    extra_json = Column(JSON, nullable=True, comment="扩展配置")

    is_enabled = Column(Boolean, nullable=False, default=True, index=True, comment="供应商是否启用")
    is_builtin = Column(Boolean, nullable=False, default=False, comment="是否内置")

    created_by = Column(String(100), nullable=True)
    updated_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, comment="创建时间")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, comment="更新时间")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "provider_type": self.provider_type,
            "default_protocol": self.default_protocol,
            "base_url": self.base_url,
            "embedding_base_url": self.embedding_base_url,
            "rerank_base_url": self.rerank_base_url,
            "models_endpoint": self.models_endpoint,
            "embedding_models_endpoint": self.embedding_models_endpoint,
            "rerank_models_endpoint": self.rerank_models_endpoint,
            "api_key_env": self.api_key_env,
            "api_key": self.api_key,
            "capabilities": self.capabilities or [],
            "enabled_models": self.enabled_models or [],
            "headers_json": self.headers_json or {},
            "extra_json": self.extra_json or {},
            "is_enabled": bool(self.is_enabled),
            "is_builtin": bool(self.is_builtin),
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ConfigOption(Base):
    """系统定义、管理员维护值的通用配置项。"""

    __tablename__ = "config_options"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), nullable=False, unique=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=False, default="")
    params = Column(JSON, nullable=False, default=dict)
    value = Column(JSON, nullable=False, default=dict)
    created_by = Column(String(100), nullable=True)
    updated_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class AgentScopeThreadSession(Base):
    """线程与 agentscope session 的一一映射事实（uid+thread 唯一）。"""

    __tablename__ = "agentscope_thread_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(String(64), nullable=False)
    thread_id = Column(String(64), nullable=False)
    agent_slug = Column(String(64), nullable=False)
    model_spec = Column(String(200), nullable=False)
    agentscope_agent_id = Column(String(64), nullable=False)
    agentscope_credential_id = Column(String(64), nullable=False)
    agentscope_session_id = Column(String(64), nullable=False)
    agentscope_workspace_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class AgentScopeChannelBinding(Base):
    """Yuxi Agent 与 AgentScope Channel 的控制面同步事实。"""

    __tablename__ = "agentscope_channel_bindings"

    id = Column(String(36), primary_key=True)
    owner_uid = Column(String(64), nullable=False, index=True)
    agent_slug = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    channel_type = Column(String(32), nullable=False, default="wps_xiezuo")
    app_id = Column(String(128), nullable=False, unique=True, index=True)
    encrypted_app_secret = Column(Text, nullable=False)
    allow_from = Column(JSON, nullable=False, default=list)
    group_reply_policy = Column(String(32), nullable=False, default="mention_only")
    enabled = Column(Boolean, nullable=False, default=True)
    model_spec = Column(String(200), nullable=True)
    agentscope_channel_id = Column(String(64), nullable=True, unique=True, index=True)
    agentscope_agent_id = Column(String(64), nullable=True)
    agentscope_credential_id = Column(String(64), nullable=True)
    sync_status = Column(String(16), nullable=False, default="pending", index=True)
    last_error = Column(Text, nullable=True)
    created_by = Column(String(64), nullable=False)
    updated_by = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self) -> dict[str, Any]:
        """返回不包含凭据密文的管理视图。"""
        return {
            "id": self.id,
            "owner_uid": self.owner_uid,
            "agent_slug": self.agent_slug,
            "name": self.name,
            "channel_type": self.channel_type,
            "app_id": self.app_id,
            "allow_from": self.allow_from or [],
            "group_reply_policy": self.group_reply_policy,
            "enabled": bool(self.enabled),
            "sync_status": self.sync_status,
            "last_error": self.last_error,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


class ChannelDelivery(Base):
    """外部 Channel 回复投递事实，独立于 Agent Run 终态。"""

    __tablename__ = "channel_deliveries"
    __table_args__ = (
        UniqueConstraint("binding_id", "channel_message_id", name="uq_channel_deliveries_binding_message"),
        Index("ix_channel_deliveries_ready", "status", "next_attempt_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    binding_id = Column(
        String(36),
        ForeignKey("agentscope_channel_bindings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    request_id = Column(String(64), nullable=False, unique=True, index=True)
    channel_message_id = Column(String(128), nullable=False)
    chat_id = Column(String(128), nullable=False)
    status = Column(String(16), nullable=False, default="pending", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)


class AgentScopeTeamWorkerBinding(Base):
    """AgentScope Team worker 与 Yuxi 子线程的长期绑定。"""

    __tablename__ = "agentscope_team_worker_bindings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uid = Column(String(64), nullable=False, index=True)
    parent_thread_id = Column(String(64), nullable=False, index=True)
    child_thread_id = Column(String(64), nullable=False, unique=True, index=True)
    subagent_slug = Column(String(64), nullable=False, index=True)
    created_by_run_id = Column(String(64), nullable=False, index=True)
    subagent_thread_relation_id = Column(
        Integer,
        ForeignKey("subagent_threads.id"),
        nullable=False,
        unique=True,
    )
    team_id = Column(String(64), nullable=False, index=True)
    worker_agent_id = Column(String(64), nullable=False)
    worker_session_id = Column(String(64), nullable=False)
    agentscope_workspace_id = Column(String(64), nullable=True)
    active_run_id = Column(String(64), nullable=True, index=True)
    last_reply_id = Column(String(64), nullable=True)
    runtime_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    __table_args__ = (
        UniqueConstraint(
            "uid",
            "worker_session_id",
            name="uq_agentscope_team_worker_session",
        ),
    )


class TaskRecord(Base):
    __tablename__ = "tasks"

    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    type = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    progress = Column(Float, nullable=False, default=0.0)
    message = Column(Text, nullable=False, default="")
    payload = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    cancel_requested = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utc_now_naive, index=True)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
            "started_at": format_utc_datetime(self.started_at),
            "completed_at": format_utc_datetime(self.completed_at),
            "payload": self.payload or {},
            "result": self.result,
            "error": self.error,
            "cancel_requested": bool(self.cancel_requested),
        }

    def to_summary_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("payload", None)
        data.pop("result", None)
        return data


class APIKey(Base):
    """API Key 模型"""

    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_hash = Column(String(64), nullable=False, unique=True, index=True)
    key_prefix = Column(String(16), nullable=False)
    request_id = Column(String(64), nullable=True, unique=True, index=True)
    intent_hash = Column(String(64), nullable=True)
    name = Column(String(100), nullable=False)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True)

    expires_at = Column(DateTime, nullable=True)
    is_enabled = Column(Boolean, nullable=False, default=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    last_used_at = Column(DateTime, nullable=True)

    created_by = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=utc_now_naive)

    # 关联
    user = relationship("User", back_populates="api_keys")
    department = relationship("Department")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key_prefix": self.key_prefix,
            "name": self.name,
            "user_id": self.user_id,
            "department_id": self.department_id,
            "expires_at": format_utc_datetime(self.expires_at),
            "is_enabled": bool(self.is_enabled),
            "last_used_at": format_utc_datetime(self.last_used_at),
            "created_by": self.created_by,
            "created_at": format_utc_datetime(self.created_at),
        }

    def is_valid(self) -> bool:
        """检查 Key 是否有效"""
        if not self.is_enabled:
            return False
        if self.revoked_at is not None:
            return False
        if self.expires_at and utc_now_naive() > self.expires_at:
            return False
        return True


class CLIAuthSession(Base):
    """CLI 浏览器授权会话。"""

    __tablename__ = "cli_auth_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_code_hash = Column(String(64), nullable=False, unique=True, index=True)
    user_code = Column(String(16), nullable=False, unique=True, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    key_name = Column(String(100), nullable=False)

    approved_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id"), nullable=True, index=True)

    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    approved_at = Column(DateTime, nullable=True)
    consumed_at = Column(DateTime, nullable=True)

    approved_user = relationship("User")
    api_key = relationship("APIKey")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_code": self.user_code,
            "status": self.status,
            "key_name": self.key_name,
            "approved_user_id": self.approved_user_id,
            "api_key_id": self.api_key_id,
            "created_at": format_utc_datetime(self.created_at),
            "expires_at": format_utc_datetime(self.expires_at),
            "approved_at": format_utc_datetime(self.approved_at),
            "consumed_at": format_utc_datetime(self.consumed_at),
        }


class AgentRun(Base):
    """AgentRun table - 运行任务表"""

    __tablename__ = "agent_runs"

    id = Column(String(64), primary_key=True, comment="Run ID (UUID)")
    conversation_thread_id = Column(String(64), index=True, nullable=False, comment="Conversation thread ID snapshot")
    runtime_scope_id = Column(String(64), index=True, nullable=False, comment="Root conversation runtime scope")
    runtime_cleanup_pending = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        index=True,
        comment="Root terminal Run still owns execution runtime cleanup",
    )
    agent_slug = Column(String(64), index=True, nullable=False, comment="Agent slug")
    uid = Column(String(64), index=True, nullable=False, comment="UID")
    status = Column(
        String(32),
        index=True,
        nullable=False,
        default="pending",
        comment="Run status: pending/running/completed/failed/cancel_requested/cancelled/interrupted",
    )
    request_id = Column(String(64), unique=True, index=True, nullable=False, comment="Idempotency request ID")
    source = Column(String(32), nullable=False, default="chat", comment="Run source snapshot")
    channel = Column(String(32), nullable=False, default="web", comment="Run channel snapshot")
    external_id = Column(String(128), nullable=True, index=True, comment="Source-specific external ID snapshot")
    origin_metadata = Column(JSON, nullable=False, default=dict, comment="Immutable origin metadata snapshot")
    conversation_id = Column(
        Integer, ForeignKey("conversations.id"), nullable=True, index=True, comment="Conversation ID"
    )
    created_by_run_id = Column(String(64), nullable=True, index=True, comment="Run that created this run")
    subagent_thread_relation_id = Column(
        Integer,
        ForeignKey("subagent_threads.id"),
        nullable=True,
        index=True,
        comment="Subagent thread relation record ID",
    )
    run_type = Column(
        String(32),
        nullable=False,
        default="chat",
        comment="Run type: chat/resume/subagent",
    )
    input_message_id = Column(Integer, nullable=True, comment="Input message ID")
    output_message_id = Column(Integer, nullable=True, comment="Output message ID")
    last_event_id = Column(String(64), nullable=True, comment="Last Redis stream event ID")
    input_payload = Column(JSON, nullable=False, default=dict, comment="Original input payload")
    token_usage = Column(JSON_VALUE, nullable=False, default=dict, comment="Run token usage grouped by model")
    error_type = Column(String(64), nullable=True, comment="Error type")
    error_message = Column(Text, nullable=True, comment="Error message")
    worker_id = Column(String(128), nullable=True, comment="当前执行 ownership token")
    heartbeat_at = Column(DateTime, nullable=True, comment="当前 owner 最近一次续租时间")
    lease_expires_at = Column(DateTime, nullable=True, comment="当前执行 ownership 的到期时间")
    manifest = Column(JSON_VALUE, nullable=True, comment="首次执行前固化的运行清单（脱敏）")
    manifest_fingerprint = Column(String(64), nullable=True, comment="运行清单规范化 JSON 的 SHA-256 指纹")
    manifest_recorded_at = Column(DateTime, nullable=True, comment="运行清单固化时间")
    started_at = Column(DateTime, nullable=True, comment="Start time")
    finished_at = Column(DateTime, nullable=True, comment="Finish time")
    created_at = Column(DateTime, default=utc_now_naive, comment="Creation time")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, comment="Update time")

    __table_args__ = (CheckConstraint(AGENT_RUN_SHAPE_CONSTRAINT_SQL, name=AGENT_RUN_SHAPE_CONSTRAINT_NAME),)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_thread_id": self.conversation_thread_id,
            "runtime_scope_id": self.runtime_scope_id,
            "runtime_cleanup_pending": bool(self.runtime_cleanup_pending),
            "agent_slug": self.agent_slug,
            "uid": self.uid,
            "status": self.status,
            "request_id": self.request_id,
            "source": self.source,
            "channel": self.channel,
            "external_id": self.external_id,
            "origin_metadata": self.origin_metadata or {},
            "conversation_id": self.conversation_id,
            "created_by_run_id": self.created_by_run_id,
            "subagent_thread_relation_id": self.subagent_thread_relation_id,
            "run_type": self.run_type,
            "input_message_id": self.input_message_id,
            "output_message_id": self.output_message_id,
            "last_event_id": self.last_event_id,
            "input_payload": self.input_payload or {},
            "token_usage": self.token_usage or {},
            "error_type": self.error_type,
            "error_message": self.error_message,
            "manifest": self.manifest,
            "manifest_fingerprint": self.manifest_fingerprint,
            "started_at": format_utc_datetime(self.started_at),
            "finished_at": format_utc_datetime(self.finished_at),
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


Index(
    "uq_agent_runs_one_active_per_thread",
    AgentRun.uid,
    AgentRun.agent_slug,
    AgentRun.conversation_thread_id,
    unique=True,
    postgresql_where=AgentRun.status.notin_(AGENT_RUN_TERMINAL_STATUSES),
    sqlite_where=AgentRun.status.notin_(AGENT_RUN_TERMINAL_STATUSES),
)
Index("ix_agent_runs_status_lease_expires", AgentRun.status, AgentRun.lease_expires_at)


class AgentRunAttempt(Base):
    """AgentRun 单次执行占有的不可变事实记录。"""

    __tablename__ = "agent_run_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "attempt_no", name="uq_agent_run_attempts_run_attempt_no"),
        Index("ix_agent_run_attempts_open", "run_id", "finished_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    attempt_no = Column(Integer, nullable=False)
    worker_id = Column(String(128), nullable=False)
    started_at = Column(DateTime, nullable=False)
    heartbeat_at = Column(DateTime, nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    outcome = Column(String(32), nullable=True)
    error_type = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class AgentRunRequest(Base):
    """AgentRunRequest table - 智能体线程请求队列表。

    表示一次用户/外部请求；派发后由对应 AgentRun 表达执行状态。
    外部统一以 request_id 作为幂等键引用；id 为自增主键，仅用于 FIFO 排序。
    """

    __tablename__ = "agent_run_requests"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="Primary key")
    request_id = Column(String(64), unique=True, index=True, nullable=False, comment="幂等请求 ID")
    uid = Column(String(64), nullable=False, comment="UID")
    agent_slug = Column(String(64), nullable=False, comment="Agent slug")
    conversation_thread_id = Column(String(64), nullable=False, comment="Conversation thread ID")
    source = Column(String(32), nullable=False, default="chat", comment="请求来源: chat/agent_call/eval")
    channel = Column(String(32), nullable=False, default="web", comment="请求通道: web/api/im/internal")
    external_id = Column(String(128), nullable=True, index=True, comment="来源侧消息或调用 ID")
    origin_metadata = Column(JSON, nullable=False, default=dict, comment="来源 metadata 快照")
    queue_policy = Column(
        String(16),
        nullable=False,
        default="enqueue",
        comment="排队策略: enqueue/reject/steer",
    )
    status = Column(
        String(32),
        nullable=False,
        default="queued",
        comment="请求状态: queued/dispatched/cancelled/rejected/failed",
    )
    input_message_id = Column(Integer, ForeignKey("messages.id"), nullable=False, comment="关联输入消息 ID")
    dispatched_run_id = Column(String(64), ForeignKey("agent_runs.id"), nullable=True, comment="已派发的 AgentRun ID")
    input_payload = Column(JSON, nullable=False, default=dict, comment="原始输入载荷快照")
    error_message = Column(Text, nullable=True, comment="rejected/failed 时的错误信息")
    created_at = Column(DateTime, nullable=False, default=utc_now_naive, comment="创建时间")
    dispatched_at = Column(DateTime, nullable=True, comment="派发时间")
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        comment="更新时间",
    )

    # Relationships
    input_message = relationship("Message", foreign_keys=[input_message_id])
    dispatched_run = relationship("AgentRun", foreign_keys=[dispatched_run_id])

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "uid": self.uid,
            "agent_slug": self.agent_slug,
            "thread_id": self.conversation_thread_id,
            "source": self.source,
            "channel": self.channel,
            "external_id": self.external_id,
            "origin_metadata": self.origin_metadata or {},
            "queue_policy": self.queue_policy,
            "status": self.status,
            "input_message_id": self.input_message_id,
            "dispatched_run_id": self.dispatched_run_id,
            "error_message": self.error_message,
            "created_at": format_utc_datetime(self.created_at),
            "dispatched_at": format_utc_datetime(self.dispatched_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


Index(
    "ix_agent_run_requests_queue",
    AgentRunRequest.uid,
    AgentRunRequest.agent_slug,
    AgentRunRequest.conversation_thread_id,
    AgentRunRequest.status,
    AgentRunRequest.created_at,
    AgentRunRequest.id,
)


class AgentTask(Base):
    """AgentTask table - 可重复触发的智能体任务定义（任务中心）"""

    __tablename__ = "agent_tasks"

    id = Column(String(36), primary_key=True, comment="Task ID (UUID)")
    name = Column(String(128), nullable=False, comment="任务名称")
    owner_uid = Column(String(64), index=True, nullable=False, comment="创建者 UID")
    agent_id = Column(Integer, nullable=True, index=True, comment="引用智能体 ID；删除后置空")
    agent_name_snapshot = Column(String(128), nullable=True, comment="智能体名称展示快照")
    agent_slug_snapshot = Column(String(64), nullable=True, comment="智能体 slug 展示快照")
    prompt = Column(Text, nullable=False, comment="任务提示词")
    share_config = Column(JSON, nullable=False, default=dict, comment="个人/部门可见范围 v2")
    enabled = Column(Boolean, nullable=False, default=True, comment="启用状态")
    archived_at = Column(DateTime, nullable=True, comment="归档时间；非空即归档")
    api_enabled = Column(Boolean, nullable=False, default=False, comment="是否允许 API 触发")
    schedule_mode = Column(String(16), nullable=True, comment="定时模式: daily/weekly/cron；空表示未启用")
    schedule_cron = Column(String(64), nullable=True, comment="5 段 POSIX Cron 表达式")
    schedule_timezone = Column(String(64), nullable=True, comment="IANA 时区")
    next_run_at = Column(DateTime, nullable=True, index=True, comment="下次计划运行时间（UTC naive）")
    tool_approval_mode = Column(String(16), nullable=False, default="always_trust", comment="工具审批模式")
    created_at = Column(DateTime, default=utc_now_naive, comment="Creation time")
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive, comment="Update time")

    executions = relationship("TaskExecution", back_populates="task", cascade="all, delete-orphan")

    @property
    def created_by(self) -> str | None:
        """权限协议字段：任务创建者即所有者。"""
        return self.owner_uid

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "owner_uid": self.owner_uid,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name_snapshot,
            "agent_slug": self.agent_slug_snapshot,
            "prompt": self.prompt,
            "share_config": self.share_config or {},
            "enabled": bool(self.enabled),
            "archived_at": format_utc_datetime(self.archived_at),
            "api_enabled": bool(self.api_enabled),
            "schedule": (
                {
                    "mode": self.schedule_mode,
                    "cron": self.schedule_cron,
                    "timezone": self.schedule_timezone,
                    "next_run_at": format_utc_datetime(self.next_run_at),
                }
                if self.schedule_mode
                else None
            ),
            "tool_approval_mode": self.tool_approval_mode,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }


# TaskExecution 状态集：queued/running/interrupted/succeeded/failed/cancelled/skipped/missed
TASK_EXECUTION_ACTIVE_STATUSES = ("queued", "running", "interrupted")
TASK_EXECUTION_TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "skipped", "missed")


class TaskExecution(Base):
    """TaskExecution table - 一次任务触发事实（任务中心）"""

    __tablename__ = "task_executions"

    id = Column(String(36), primary_key=True, comment="Execution ID (UUID)，兼作标准 Run 的 request_id")
    task_id = Column(String(36), ForeignKey("agent_tasks.id", ondelete="CASCADE"), index=True, nullable=False)
    trigger_type = Column(String(16), nullable=False, comment="触发方式: manual/api/schedule")
    triggered_by_uid = Column(String(64), nullable=False, comment="触发者 UID")
    execution_principal_uid = Column(String(64), nullable=False, comment="执行身份 UID")
    agent_id = Column(Integer, nullable=True, comment="触发时固定的智能体 ID；删除后置空")
    agent_slug = Column(String(64), nullable=False, comment="触发时固定的智能体 slug")
    prompt = Column(Text, nullable=False, comment="触发时固定的提示词")
    tool_approval_mode = Column(String(16), nullable=False, comment="触发时固定的审批模式")
    scheduled_at = Column(DateTime, nullable=True, comment="计划执行时间（定时触发；UTC naive）")
    idempotency_key = Column(String(128), nullable=False, comment="幂等键：任务+执行身份+key 唯一")
    agent_run_id = Column(String(64), unique=True, nullable=True, index=True, comment="关联 AgentRun ID")
    conversation_id = Column(Integer, unique=True, nullable=True, comment="关联 Conversation ID")
    thread_id = Column(String(64), nullable=True, comment="执行线程 ID（确定性派生）")
    status = Column(String(16), nullable=False, default="queued", index=True, comment="执行状态")
    error_summary = Column(String(500), nullable=True, comment="错误摘要")
    queued_at = Column(DateTime, default=utc_now_naive, comment="入队时间")
    started_at = Column(DateTime, nullable=True, comment="开始时间")
    finished_at = Column(DateTime, nullable=True, comment="结束时间")

    task = relationship("AgentTask", back_populates="executions")

    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "execution_principal_uid",
            "idempotency_key",
            name="uq_task_execution_idempotency",
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "trigger_type": self.trigger_type,
            "triggered_by_uid": self.triggered_by_uid,
            "execution_principal_uid": self.execution_principal_uid,
            "agent_slug": self.agent_slug,
            "prompt": self.prompt,
            "tool_approval_mode": self.tool_approval_mode,
            "scheduled_at": format_utc_datetime(self.scheduled_at),
            "agent_run_id": self.agent_run_id,
            "thread_id": self.thread_id,
            "status": self.status,
            "error_summary": self.error_summary,
            "queued_at": format_utc_datetime(self.queued_at),
            "started_at": format_utc_datetime(self.started_at),
            "finished_at": format_utc_datetime(self.finished_at),
        }
