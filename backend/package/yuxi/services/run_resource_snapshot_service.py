"""在提交时授权并封存运行投影，执行时只消费该次封存结果。"""

import base64
import hashlib
import hmac
import json
import os
from dataclasses import asdict
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from yuxi.agentscope.config_projection import RuntimeProjection, project_runtime
from yuxi.repositories.run_resource_snapshot_repository import get_snapshot, store_snapshot
from yuxi.storage.postgres.models_business import RunResourceSnapshot


def _derive_snapshot_key(secret: str) -> bytes:
    """从一个持久密钥派生运行快照专用密钥。"""
    if len(secret.strip()) < 32:
        raise RuntimeError("运行快照需要持久化 API_KEY_DERIVATION_SECRET")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"yuxi:run-resource-snapshot:v1").derive(
        secret.encode()
    )


def _snapshot_keys() -> list[bytes]:
    """返回当前加密密钥和仅用于解密的历史密钥。"""
    current = os.environ.get("API_KEY_DERIVATION_SECRET", "")
    keys = [_derive_snapshot_key(current)]
    raw = os.environ.get("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", "[]")
    try:
        historical = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS 必须是 JSON 字符串数组") from exc
    if not isinstance(historical, list) or any(not isinstance(secret, str) for secret in historical):
        raise RuntimeError("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS 必须是 JSON 字符串数组")
    for secret in historical:
        if len(secret.strip()) < 32:
            raise RuntimeError("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS 中的密钥至少需要 32 个字符")
        key = _derive_snapshot_key(secret)
        if key not in keys:
            keys.append(key)
    return keys


def _encode(value: dict) -> bytes:
    """输出确定性 JSON，用于加密和认证摘要。"""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


async def capture_run_resources(db, *, uid: str, agent_slug: str, model_spec: str, thread_id: str | None) -> dict:
    """解析根 Agent 及可调用子 Agent 的授权资源并在同一事务封存。"""
    projections = {}
    pending = [agent_slug]
    while pending:
        slug = pending.pop()
        if slug in projections:
            continue
        projection = await project_runtime(
            db, uid=uid, agent_slug=slug, model_spec=model_spec, thread_id=thread_id,
            is_team_worker=slug != agent_slug,
        )
        projections[slug] = asdict(projection)
        pending.extend(t["type"] for t in projection.subagent_templates if t["type"] not in projections)

    snapshot_id = str(uuid4())
    key = _snapshot_keys()[0]
    payload = _encode({"id": snapshot_id, "uid": uid, "projections": projections})
    fingerprint = hmac.new(key, payload, hashlib.sha256).hexdigest()
    await store_snapshot(db, RunResourceSnapshot(
        id=snapshot_id, uid=uid, fingerprint=fingerprint,
        encrypted_payload=Fernet(base64.urlsafe_b64encode(key)).encrypt(payload).decode(),
    ))
    # 凭据只出现在加密资产中；公开清单不包含可还原的连接参数。
    return {"id": snapshot_id, "fingerprint": fingerprint}


async def load_run_resources(db, *, uid: str, manifest: dict, agent_slug: str) -> RuntimeProjection:
    """验证身份和认证摘要后读取快照；不重新读取当前资源权限或凭据。"""
    reference = (manifest or {}).get("runtime_snapshot") or {}
    snapshot = await get_snapshot(db, snapshot_id=reference.get("id", ""), uid=uid)
    if snapshot is None or snapshot.fingerprint != reference.get("fingerprint"):
        raise ValueError("Run 运行快照不存在或不属于当前执行身份")
    payload = None
    for key in _snapshot_keys():
        try:
            candidate = Fernet(base64.urlsafe_b64encode(key)).decrypt(snapshot.encrypted_payload.encode())
        except InvalidToken:
            continue
        if hmac.compare_digest(hmac.new(key, candidate, hashlib.sha256).hexdigest(), snapshot.fingerprint):
            payload = candidate
            break
    if payload is None:
        raise ValueError("Run 运行快照认证失败")
    value = json.loads(payload)
    if value["uid"] != uid or value["id"] != reference["id"] or agent_slug not in value["projections"]:
        raise ValueError("Run 运行快照未授权当前 Agent")
    return RuntimeProjection(**value["projections"][agent_slug])
