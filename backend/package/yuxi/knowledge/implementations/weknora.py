"""WeKnora 托管知识库执行器。

文档管理、内容与状态等用例由 yuxi.services.weknora_kb_service 协调本地持久化与远端调用,
本执行器承载类型元数据与检索能力(检索在专用工单接入),不直接继承只读连接器。
"""

from __future__ import annotations

from typing import Any

from yuxi.knowledge.base import KBOperationError, KnowledgeBase
from yuxi.utils import logger
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

    def _remote_client(self):
        from yuxi.knowledge.weknora import WeKnoraClient, load_weknora_settings

        settings = load_weknora_settings()
        if not settings.ready:
            raise KBOperationError("WeKnora 部署配置不完整,无法执行检索")
        return WeKnoraClient(settings)

    def _require_remote_kb_id(self, config: KnowledgeBaseConfig) -> str:
        """校验实例指纹一致后返回远端库 ID;换址后旧绑定立即失效。"""

        from yuxi.knowledge.weknora import weknora_instance_fingerprint

        binding = config.remote_binding or {}
        remote_kb_id = str(binding.get("remote_kb_id") or "")
        if binding.get("status") != "confirmed" or not remote_kb_id:
            raise KBOperationError("知识库远端绑定未确认,暂不可检索")
        if binding.get("instance") != weknora_instance_fingerprint():
            raise KBOperationError("当前 WeKnora 服务地址与绑定时不同,该知识库暂不可访问")
        return remote_kb_id

    async def aquery(
        self,
        query_text: str,
        kb_id: str,
        *,
        config: KnowledgeBaseConfig,
        agent_call: bool = False,
        **kwargs,
    ) -> list[dict]:
        """远端混合检索;命中只映射到本项目绑定文档,未绑定对象不进入结果。"""
        del agent_call
        from yuxi.knowledge.weknora import WeKnoraClientError
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        remote_kb_id = self._require_remote_kb_id(config)
        merged = {**config.query_options, **kwargs}
        if merged.get("file_name"):
            raise KBOperationError("WeKnora 检索暂不支持按文件名过滤,请直接检索后按来源筛选")
        try:
            top_k = max(int(merged.get("final_top_k", 10) or 10), 1)
        except (TypeError, ValueError):
            top_k = 10

        try:
            response = await self._remote_client().request(
                "POST",
                f"knowledge-bases/{remote_kb_id}/hybrid-search",
                json={"query_text": query_text, "match_count": top_k},
            )
        except WeKnoraClientError as error:
            raise KBOperationError(f"WeKnora 检索失败: {error}") from error

        items = response.json().get("data") or []
        if not isinstance(items, list):
            items = []
        repo = KnowledgeFileRepository()
        results: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            remote_knowledge_id = str(item.get("knowledge_id") or item.get("id") or "")
            record = await repo.get_by_remote_knowledge_id(kb_id, remote_knowledge_id) if remote_knowledge_id else None
            if record is None:
                continue
            results.append(
                {
                    "content": str(item.get("content") or ""),
                    "score": float(item.get("score") or 0.0),
                    "metadata": {
                        "source": record.filename,
                        "file_id": record.file_id,
                        "chunk_id": item.get("id"),
                        "remote_knowledge_id": remote_knowledge_id,
                    },
                }
            )
        return results

    async def _remote_parsed_content(self, kb_id: str, file_id: str) -> str:
        """读取远端解析文本;description 为空时由服务层回退拼接分块。"""

        from yuxi.services.weknora_knowledge_service import load_remote_parsed_content

        file_meta = await self._load_file_meta(kb_id, file_id)
        if file_meta.get("is_folder"):
            raise Exception(f"文件 {file_id} 是文件夹")
        if not file_meta.get("remote_knowledge_id"):
            raise Exception(f"文件 {file_id} 缺少远端绑定")
        return await load_remote_parsed_content(kb_id, file_id)

    async def open_file_content(self, kb_id: str, file_id: str, offset: int = 0, limit: int = 800) -> dict:
        content = await self._remote_parsed_content(kb_id, file_id)
        return self._build_open_file_window(content, offset=offset, limit=limit)

    async def find_file_content(
        self,
        kb_id: str,
        file_id: str,
        patterns: list[str],
        *,
        use_regex: bool = False,
        case_sensitive: bool = False,
        max_windows: int = 5,
        window_size: int = 80,
    ) -> dict:
        content = await self._remote_parsed_content(kb_id, file_id)
        return self._build_find_file_windows(
            content,
            patterns=patterns,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            max_windows=max_windows,
            window_size=window_size,
        )

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
        """确认服务端预登记的文档意向并更新目录位置。

        item 只携带不可预测的本地 file_id。远端 ID 必须已由上传用例写入该行,
        客户端不能提交或替换远端资源绑定。
        """
        del additional_params

        from yuxi.knowledge.weknora import parse_weknora_file_ref

        from yuxi.knowledge.utils.kb_utils import _normalize_source_path

        file_id, ref_filename = parse_weknora_file_ref(item)
        from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

        repo = KnowledgeFileRepository()
        existing = await repo.get_by_file_id(file_id)
        if existing is None or existing.kb_id != kb_id:
            raise KBOperationError("WeKnora 文档登记引用无效或不属于当前知识库")
        if not existing.remote_knowledge_id:
            raise KBOperationError(f"文档 {file_id} 的远端写入结果尚未确认")

        params = dict(params or {})
        content_hashes = params.get("content_hashes") if isinstance(params.get("content_hashes"), dict) else {}
        file_sizes = params.get("file_sizes") if isinstance(params.get("file_sizes"), dict) else {}
        filename = str(_normalize_source_path(params.get("source_path")) or params.get("filename") or ref_filename)
        updates = {
            "filename": filename,
            "parent_id": params.get("parent_id"),
        }
        if content_hashes.get(item):
            updates["content_hash"] = content_hashes[item]
        if file_sizes.get(item) is not None:
            updates["file_size"] = file_sizes[item]
        if operator_id and not existing.created_by:
            updates["created_by"] = operator_id
        updated = await repo.update_fields(file_id=file_id, data=updates, kb_id=kb_id)
        if updated is None:
            raise KBOperationError(f"文档 {file_id} 登记失败")
        return self._file_record_to_meta(updated)

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
        """分块视图来自远端;content 为远端解析文本,明确标注非完整原文。"""

        from yuxi.knowledge.weknora import WeKnoraClientError

        file_meta = await self._load_file_meta(kb_id, file_id)
        content = await self._remote_parsed_content(kb_id, file_id)
        remote_knowledge_id = file_meta.get("remote_knowledge_id")
        chunks: list[dict] = []
        try:
            response = await self._remote_client().request("GET", f"chunks/{remote_knowledge_id}")
            raw = response.json().get("data") or []
            if isinstance(raw, dict):
                raw = raw.get("items") or []
            chunks = [
                {"chunk_index": index, "content": str(item.get("content") or "")}
                for index, item in enumerate(raw)
                if isinstance(item, dict)
            ]
        except WeKnoraClientError as error:
            # 分块视图缺失不阻塞详情,但不得静默伪装为全文
            logger.warning(f"WeKnora chunks unavailable: file_id={file_id}: {error}")
        return {
            "meta": file_meta,
            "content": content,
            "content_source": "weknora-manual-or-chunks",
            "chunks": chunks,
        }

    async def get_file_download(self, kb_id: str, file_id: str, variant: str = "original") -> dict:
        """Agent/CLI 下载:远端原件或解析文本,统一 Key 不出服务端。"""

        from yuxi.services.weknora_knowledge_service import download_weknora_document_stream

        file_meta = await self._load_file_meta(kb_id, file_id)
        if file_meta.get("is_folder"):
            raise ValueError("Cannot download a folder")
        stream, filename, media_type = await download_weknora_document_stream(kb_id, file_id, variant=variant)
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
        return {"filename": filename, "content": b"".join(chunks), "media_type": media_type}

    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        base_info = await self.get_file_basic_info(kb_id, file_id)
        content_info = await self.get_file_content(kb_id, file_id)
        return {**base_info, **content_info}

    def get_query_params_config(self, kb_id: str, **kwargs) -> dict:
        """检索参数配置;远端检索参数在检索工单接入后细化。"""
        del kb_id, kwargs
        return {"type": "weknora", "options": []}
