"""在提交时授权并封存运行投影，执行时只消费该次封存结果。"""

import asyncio
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
from yuxi.agentscope.skill_snapshot import (
    MAX_SKILL_SNAPSHOT_BYTES,
    add_skill_tree_reference,
    capture_skill_tree,
    expand_referenced_run_skill_snapshots,
    validate_referenced_run_skill_snapshots,
    validate_run_skill_snapshots,
)
from yuxi.repositories.run_resource_snapshot_repository import get_snapshot, store_snapshot
from yuxi.storage.postgres.models_business import RunResourceSnapshot

MAX_RUN_RESOURCE_SNAPSHOT_PLAINTEXT_BYTES = 4 * ((MAX_SKILL_SNAPSHOT_BYTES + 2) // 3) + 16 * 1024 * 1024
MAX_RUN_RESOURCE_SNAPSHOT_CIPHERTEXT_BYTES = MAX_RUN_RESOURCE_SNAPSHOT_PLAINTEXT_BYTES * 2


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


def _check_plaintext_size(payload: bytes) -> None:
    """在加密前及解密后限制完整运行快照明文。"""
    if len(payload) > MAX_RUN_RESOURCE_SNAPSHOT_PLAINTEXT_BYTES:
        raise ValueError(f"Run 运行快照明文不能超过 {MAX_RUN_RESOURCE_SNAPSHOT_PLAINTEXT_BYTES} 字节")


def _check_ciphertext_size(payload: str) -> None:
    """在进入 Fernet 解密前限制数据库密文。"""
    if not isinstance(payload, str) or len(payload.encode()) > MAX_RUN_RESOURCE_SNAPSHOT_CIPHERTEXT_BYTES:
        raise ValueError(f"Run 运行快照密文不能超过 {MAX_RUN_RESOURCE_SNAPSHOT_CIPHERTEXT_BYTES} 字节")


def _preloaded_skill_prompt(preloaded_skills: list[str], contents: dict[str, str]) -> str:
    """按投影声明顺序构造 preload Skill 提示词。"""
    return "\n\n".join(f"## Preloaded Skill: {slug}\n{contents[slug]}" for slug in preloaded_skills if slug in contents)


def _strip_preloaded_skill_copies(projection: dict) -> None:
    """持久化前移除可从 Skill tree 派生的正文与提示词副本。"""
    contents = projection.pop("preloaded_skill_contents", None) or {}
    if not contents:
        return
    preload_prompt = _preloaded_skill_prompt(projection.get("preloaded_skills") or [], contents)
    if not preload_prompt:
        raise ValueError("Run 运行快照 preload Skill 正文与声明不一致")
    agent_request = projection.get("agent_request")
    system_prompt = agent_request.get("system_prompt") if isinstance(agent_request, dict) else None
    suffix = f"\n\n{preload_prompt}"
    if not isinstance(system_prompt, str) or not system_prompt.endswith(suffix):
        raise ValueError("Run 运行快照 preload Skill 提示词与正文不一致")
    agent_request["system_prompt"] = system_prompt[: -len(suffix)]


def _restore_preloaded_skill_copies(projection: dict) -> None:
    """加载新 v2 快照时从已认证 Skill tree 恢复 preload 正文和提示词。"""
    if projection.get("preloaded_skill_contents"):
        return
    preloaded_skills = projection.get("preloaded_skills") or []
    if not preloaded_skills:
        return
    skills_by_slug = {skill.get("slug"): skill for skill in projection.get("skills") or [] if isinstance(skill, dict)}
    contents: dict[str, str] = {}
    for slug in preloaded_skills:
        skill = skills_by_slug.get(slug)
        if skill is None:
            raise ValueError(f"Run 运行快照 preload Skill {slug} 不存在")
        root_manifest = next(
            (
                item
                for item in skill.get("snapshot_files") or []
                if isinstance(item, dict) and item.get("path") == "SKILL.md"
            ),
            None,
        )
        if root_manifest is None:
            raise ValueError(f"Run 运行快照 preload Skill {slug} 缺少根级 SKILL.md")
        try:
            contents[slug] = base64.b64decode(root_manifest.get("content_base64"), validate=True).decode("utf-8")
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"Run 运行快照 preload Skill {slug} 正文非法") from exc

    agent_request = projection.get("agent_request")
    system_prompt = agent_request.get("system_prompt") if isinstance(agent_request, dict) else None
    if not isinstance(system_prompt, str):
        raise ValueError("Run 运行快照缺少 Agent system prompt")
    projection["preloaded_skill_contents"] = contents
    agent_request["system_prompt"] = f"{system_prompt}\n\n{_preloaded_skill_prompt(preloaded_skills, contents)}"


async def capture_run_resources(db, *, uid: str, agent_slug: str, model_spec: str, thread_id: str | None) -> dict:
    """解析根 Agent 及可调用子 Agent 的授权资源并在同一事务封存。"""
    projections = {}
    skill_trees: dict[str, dict] = {}
    total_skill_entries = 0
    total_skill_bytes = 0
    pending = [agent_slug]
    while pending:
        slug = pending.pop()
        if slug in projections:
            continue
        projection = await project_runtime(
            db,
            uid=uid,
            agent_slug=slug,
            model_spec=model_spec,
            thread_id=thread_id,
            is_team_worker=slug != agent_slug,
        )
        projection_value = asdict(projection)
        referenced_skills = []
        for item in projection_value["skills"]:
            skill = await asyncio.to_thread(capture_skill_tree, item)
            referenced, total_skill_entries, total_skill_bytes = add_skill_tree_reference(
                skill,
                skill_trees,
                total_entries=total_skill_entries,
                total_bytes=total_skill_bytes,
            )
            referenced_skills.append(referenced)
        projection_value["skills"] = referenced_skills
        _strip_preloaded_skill_copies(projection_value)
        projections[slug] = projection_value
        pending.extend(t["type"] for t in projection.subagent_templates if t["type"] not in projections)

    validate_referenced_run_skill_snapshots(projections, skill_trees)
    snapshot_id = str(uuid4())
    key = _snapshot_keys()[0]
    payload = _encode(
        {
            "format_version": 2,
            "id": snapshot_id,
            "uid": uid,
            "projections": projections,
            "skill_trees": skill_trees,
        }
    )
    _check_plaintext_size(payload)
    fingerprint = hmac.new(key, payload, hashlib.sha256).hexdigest()
    encrypted_payload = Fernet(base64.urlsafe_b64encode(key)).encrypt(payload).decode()
    _check_ciphertext_size(encrypted_payload)
    await store_snapshot(
        db,
        RunResourceSnapshot(
            id=snapshot_id,
            uid=uid,
            fingerprint=fingerprint,
            encrypted_payload=encrypted_payload,
        ),
    )
    # 凭据只出现在加密资产中；公开清单不包含可还原的连接参数。
    return {"id": snapshot_id, "fingerprint": fingerprint}


async def load_run_resources(db, *, uid: str, manifest: dict, agent_slug: str) -> RuntimeProjection:
    """验证身份和认证摘要后读取快照；不重新读取当前资源权限或凭据。"""
    reference = (manifest or {}).get("runtime_snapshot") or {}
    snapshot = await get_snapshot(db, snapshot_id=reference.get("id", ""), uid=uid)
    if snapshot is None or snapshot.fingerprint != reference.get("fingerprint"):
        raise ValueError("Run 运行快照不存在或不属于当前执行身份")
    _check_ciphertext_size(snapshot.encrypted_payload)
    payload = None
    for key in _snapshot_keys():
        try:
            candidate = Fernet(base64.urlsafe_b64encode(key)).decrypt(snapshot.encrypted_payload.encode())
        except InvalidToken:
            continue
        _check_plaintext_size(candidate)
        if hmac.compare_digest(hmac.new(key, candidate, hashlib.sha256).hexdigest(), snapshot.fingerprint):
            payload = candidate
            break
    if payload is None:
        raise ValueError("Run 运行快照认证失败")
    value = json.loads(payload)
    if value["uid"] != uid or value["id"] != reference["id"] or agent_slug not in value["projections"]:
        raise ValueError("Run 运行快照未授权当前 Agent")
    format_version = value.get("format_version", 1)
    if format_version == 2 and isinstance(value.get("skill_trees"), dict):
        skill_trees = value["skill_trees"]
        validate_referenced_run_skill_snapshots(value["projections"], skill_trees)
        projections = expand_referenced_run_skill_snapshots(value["projections"], skill_trees)
        for projection in projections.values():
            _restore_preloaded_skill_copies(projection)
    elif format_version in {1, 2} and "skill_trees" not in value:
        projections = value["projections"]
        validate_run_skill_snapshots(projections)
    else:
        raise ValueError("Run 运行快照格式版本不受支持")
    return RuntimeProjection(**projections[agent_slug])
