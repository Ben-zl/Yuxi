from fastapi import APIRouter, Depends, HTTPException, Query

from server.utils.auth_middleware import get_admin_user
from server.utils.knowledge_permissions import require_knowledge_base_read
from server.utils.knowledge_response import serialize_knowledge_base
from yuxi.config.runtime import KNOWLEDGE_BACKEND_WEKNORA, knowledge_backend
from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
from yuxi.knowledge.runtime import knowledge_base
from yuxi.storage.postgres.models_business import User
from yuxi.utils.logging_config import logger


def _weknora_backend_selected() -> bool:
    """当前进程是否运行在 WeKnora 知识库后端模式。"""
    return knowledge_backend() == KNOWLEDGE_BACKEND_WEKNORA


graph = APIRouter(prefix="/graph", tags=["graph"])


async def _get_graph_service(kb_id: str) -> MilvusGraphService:
    db_info = await knowledge_base.get_database_info(kb_id)
    if not db_info:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    kb_type = db_info.kb_type.lower()
    if kb_type == "weknora" and _weknora_backend_selected():
        # WeKnora 托管库走远端图谱,不构造内置 Milvus 服务
        return None
    if kb_type != "milvus":
        raise HTTPException(status_code=404, detail="Graph API only supports Milvus knowledge bases")

    return MilvusGraphService(kb_id=kb_id)


@graph.get("/list")
async def get_graphs(current_user: User = Depends(get_admin_user)):
    """获取支持图谱能力的 Milvus 知识库列表"""
    try:
        databases = await knowledge_base.get_databases_by_uid(current_user.uid)
        graphs = []
        for db in databases:
            kb_type = db.kb_type.lower()
            if _weknora_backend_selected():
                if kb_type != "weknora":
                    continue
            elif kb_type != "milvus":
                continue
            serialized = serialize_knowledge_base(db)
            graphs.append(
                {
                    "id": db.kb_id,
                    "name": db.name,
                    "type": kb_type,
                    "description": db.description,
                    "status": "已连接",
                    "created_at": serialized["created_at"],
                    "metadata": serialized,
                }
            )
        return {"success": True, "data": graphs}
    except Exception as e:
        logger.exception(f"Failed to list graphs: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list graphs: {str(e)}")


@graph.get("/subgraph")
async def get_subgraph(
    kb_id: str = Query(..., description="Milvus 知识库ID"),
    node_label: str = Query("*", description="节点标签或查询关键词"),
    max_depth: int = Query(2, description="最大深度", ge=1, le=5),
    max_nodes: int = Query(100, description="最大节点数", ge=1, le=1000),
    exclude_chunk: bool = Query(False, description="是否排除 Chunk 节点"),
    current_user: User = Depends(require_knowledge_base_read),
):
    """查询 Milvus 知识库图谱子图"""
    try:
        logger.info(f"Querying subgraph - kb_id: {kb_id}, label: {node_label}")
        service = await _get_graph_service(kb_id)
        if service is None:
            from yuxi.knowledge.base import KBOperationError
            from yuxi.services.weknora_knowledge_service import query_weknora_graph

            try:
                result_data = await query_weknora_graph(kb_id, keyword=node_label, max_nodes=max_nodes)
            except KBOperationError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            return {"success": True, "data": result_data}
        result_data = await service.query_nodes(
            keyword=node_label,
            max_depth=max_depth,
            max_nodes=max_nodes,
            exclude_chunk=exclude_chunk,
        )
        return {"success": True, "data": result_data}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get subgraph: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get subgraph: {str(e)}")


@graph.get("/labels")
async def get_graph_labels(
    kb_id: str = Query(..., description="Milvus 知识库ID"),
    current_user: User = Depends(require_knowledge_base_read),
):
    """获取 Milvus 知识库图谱的所有标签"""
    try:
        service = await _get_graph_service(kb_id)
        if service is None:
            from yuxi.knowledge.base import KBOperationError
            from yuxi.services.weknora_knowledge_service import get_weknora_graph_labels

            try:
                labels = await get_weknora_graph_labels(kb_id)
            except KBOperationError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            return {"success": True, "data": {"labels": labels}}
        labels = await service.get_labels()
        return {"success": True, "data": {"labels": labels}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get labels: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get labels: {str(e)}")


@graph.get("/stats")
async def get_graph_stats(
    kb_id: str = Query(..., description="Milvus 知识库ID"),
    current_user: User = Depends(require_knowledge_base_read),
):
    """获取 Milvus 知识库图谱统计信息"""
    try:
        service = await _get_graph_service(kb_id)
        if service is None:
            from yuxi.knowledge.base import KBOperationError
            from yuxi.services.weknora_knowledge_service import get_weknora_graph_stats

            try:
                stats_data = await get_weknora_graph_stats(kb_id)
            except KBOperationError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            return {"success": True, "data": stats_data}
        stats_data = await service.get_stats()
        return {"success": True, "data": stats_data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")
