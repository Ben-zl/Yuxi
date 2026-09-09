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
        """WeKnora 模型与解析配置由部署统一提供,拒绝任何类型级附加参数。"""
        if additional_params:
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
        del kb_id, item, params, operator_id, additional_params
        raise _weknora_operation_error("文档登记")

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
        del kb_id, file_id
        raise _weknora_operation_error("文档删除")

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
        del kb_id, file_id
        raise _weknora_operation_error("文件信息读取")

    async def get_file_content(self, kb_id: str, file_id: str) -> dict:
        del kb_id, file_id
        raise _weknora_operation_error("文件内容读取")

    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        del kb_id, file_id
        raise _weknora_operation_error("文件信息读取")

    def get_query_params_config(self, kb_id: str, **kwargs) -> dict:
        del kb_id, kwargs
        raise _weknora_operation_error("检索参数配置")
