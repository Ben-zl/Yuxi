"""WeKnora 托管知识库执行器。

文档管理、内容与状态等用例由 yuxi.services.weknora_kb_service 协调本地持久化与远端调用,
本执行器承载类型元数据与检索能力(检索在专用工单接入),不直接继承只读连接器。
"""

from __future__ import annotations

from typing import Any

from yuxi.knowledge.base import KBOperationError, KnowledgeBase
from yuxi.knowledge.read_models import KnowledgeBaseConfig


def _weknora_operation_error(operation: str) -> KBOperationError:
    return KBOperationError(f"WeKnora 知识库的{operation}由服务层用例协调,不支持直接调用执行器接口")


class WeKnoraKB(KnowledgeBase):
    """WeKnora 远端托管知识库实现。

    内容、分块、索引与处理状态以远端为准;目录树与绑定由本地 PostgreSQL 持有。
    """

    kb_type = "weknora"
    name = "WeKnora"
    description = "Yuxi 托管的 WeKnora 远端知识库"
    requires_embedding_model = False
    supports_documents = True
    apply_chunk_defaults = False

    def __init__(self, work_dir: str, **kwargs):
        del kwargs
        super().__init__(work_dir)

    @classmethod
    def validate_additional_params(cls, additional_params: dict | None) -> dict:
        """WeKnora 模型与解析配置由部署统一提供,拒绝类型级附加参数。

        stats 是 Manager 维护的统计投影,不是类型配置,允许透传。
        """
        params = dict(additional_params or {})
        foreign_keys = [key for key in params if key != "stats"]
        if foreign_keys:
            raise ValueError("WeKnora 知识库不接受类型级附加参数,模型与解析配置由部署统一提供")
        return {}

    async def _create_kb_instance(self, kb_id: str, embedding_model_spec: str | None) -> Any:
        del kb_id, embedding_model_spec
        return None

    async def _initialize_kb_instance(self, instance: Any) -> None:
        del instance
        return None

    async def cleanup_database_resources(self, kb_id: str) -> dict:
        """远端资源由删除用例确认后清理,本地无 MinIO 对象可回收。"""
        del kb_id
        return {"message": "WeKnora 资源清理由删除用例协调"}

    async def aquery(
        self,
        query_text: str,
        kb_id: str,
        *,
        config: KnowledgeBaseConfig,
        agent_call: bool = False,
        **kwargs,
    ) -> list[dict]:
        del query_text, kb_id, config, agent_call, kwargs
        raise _weknora_operation_error("检索")

    # 文件与目录用例在文档管理工单接入;在此之前显式拒绝,不落入内置本地解析链路。
    async def add_file_record(
        self,
        kb_id: str,
        item: str,
        params: dict | None = None,
        operator_id: str | None = None,
        *,
        additional_params: dict[str, Any],
    ) -> dict:
        """登记 WeKnora 托管文档:远端已持有内容,本地仅保存目录位置与远端绑定。

        item 必须是 weknora:// 引用(由上传用例生成);content_hash、file_size 经
        params.content_hashes / params.file_sizes 按引用传入,状态取远端当前值。
        """
        del additional_params
        import time

        from yuxi.knowledge.weknora import parse_weknora_file_ref
        from yuxi.utils import hashstr
        from yuxi.utils.datetime_utils import utc_isoformat

        from yuxi.knowledge.utils.kb_utils import _normalize_source_path

        remote_knowledge_id, ref_filename = parse_weknora_file_ref(item)
        params = dict(params or {})
        content_hashes = params.get("content_hashes") if isinstance(params.get("content_hashes"), dict) else {}
        file_sizes = params.get("file_sizes") if isinstance(params.get("file_sizes"), dict) else {}
        # source_path 仅作展示层级,与 builtin 相同的归一化边界(拒绝绝对路径与 .. 等)
        filename = str(_normalize_source_path(params.get("source_path")) or params.get("filename") or ref_filename)
        file_type = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        metadata = {
            "kb_id": kb_id,
            "filename": filename,
            "path": item,
            "file_type": file_type,
            "status": params.get("remote_status") or "pending",
            "created_at": utc_isoformat(),
            "file_id": f"file_{hashstr(str(item) + str(time.time()), 6)}",
            "content_hash": content_hashes.get(item),
            "size": file_sizes.get(item),
            "parent_id": params.get("parent_id"),
            "remote_knowledge_id": remote_knowledge_id,
        }
        if operator_id:
            metadata["created_by"] = operator_id
        await self._persist_file_meta(metadata["file_id"], metadata)
        return metadata

    async def parse_file(
        self,
        kb_id: str,
        file_id: str,
        operator_id: str | None = None,
        *,
        additional_params: dict[str, Any],
    ) -> dict:
        del kb_id, file_id, operator_id, additional_params
        raise _weknora_operation_error("文档解析")

    async def index_file(
        self,
        kb_id: str,
        file_id: str,
        operator_id: str | None = None,
        params: dict | None = None,
        *,
        embedding_model_spec: str | None,
        additional_params: dict[str, Any],
    ) -> dict:
        del kb_id, file_id, operator_id, params, embedding_model_spec, additional_params
        raise _weknora_operation_error("向量索引")

    async def delete_file(self, kb_id: str, file_id: str) -> None:
        """删除托管文档:远端确认(404 视为已删)后删除本地行;文件夹仅本地删除。"""

        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
        from yuxi.services.weknora_knowledge_service import delete_weknora_file

        repo = KnowledgeFileRepository()
        record = await repo.get_by_file_id(file_id)
        if record is not None and record.kb_id != kb_id:
            # 跨库 file_id 拒绝删除,防止绕过前置校验的调用方误删其他知识库的行
            raise KBOperationError(f"文档 {file_id} 不属于知识库 {kb_id}")
        if record is not None and not record.is_folder:
            await delete_weknora_file(kb_id, file_id, record=record)
        await repo.delete(file_id)

    async def update_content(
        self,
        kb_id: str,
        file_ids: list[str],
        params: dict | None = None,
        *,
        embedding_model_spec: str | None,
        additional_params: dict[str, Any],
    ) -> list[dict]:
        del kb_id, file_ids, params, embedding_model_spec, additional_params
        raise _weknora_operation_error("文档内容更新")

    async def get_file_basic_info(self, kb_id: str, file_id: str) -> dict:
        """返回本地文件元数据;内容与分块视图由文档详情工单接入。"""
        return {"meta": await self._load_file_meta(kb_id, file_id)}

    async def get_file_content(self, kb_id: str, file_id: str) -> dict:
        del kb_id, file_id
        raise _weknora_operation_error("文件内容读取")

    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        del kb_id, file_id
        raise _weknora_operation_error("文件信息读取")

    def get_query_params_config(self, kb_id: str, **kwargs) -> dict:
        """检索参数配置;远端检索参数在检索工单接入后细化。"""
        del kb_id, kwargs
        return {"type": "weknora", "options": []}
