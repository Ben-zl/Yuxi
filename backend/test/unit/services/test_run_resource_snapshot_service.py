import os
import shutil
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from yuxi.agentscope import runtime_resources
from yuxi.agentscope import skill_snapshot
from yuxi.agentscope.config_projection import RuntimeProjection
from yuxi.services import run_resource_snapshot_service as service
from yuxi.storage.postgres.models_business import RunResourceSnapshot


def _decrypt_snapshot(snapshot: RunResourceSnapshot, secret: str) -> tuple[bytes, dict]:
    key = service._derive_snapshot_key(secret)
    plaintext = Fernet(base64.urlsafe_b64encode(key)).decrypt(snapshot.encrypted_payload.encode())
    return plaintext, json.loads(plaintext)


async def test_snapshot_can_be_loaded_with_historical_decryption_secret(monkeypatch):
    """轮换当前密钥后，历史 keyring 仍可读取活动 Run 的旧快照。"""
    stored = {}

    async def store(_db, snapshot):
        stored[snapshot.id] = snapshot

    projection = RuntimeProjection(
        agent_slug="agent-1",
        model_spec="resource:model",
        agent_request={},
        credential_data={},
        chat_model_config={},
    )
    monkeypatch.setattr(service, "project_runtime", AsyncMock(return_value=projection))
    monkeypatch.setattr(service, "store_snapshot", store)
    monkeypatch.setattr(
        service,
        "get_snapshot",
        AsyncMock(side_effect=lambda _db, snapshot_id, uid: stored.get(snapshot_id)),
    )
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "old-snapshot-secret-that-is-at-least-32-characters")
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    reference = await service.capture_run_resources(
        object(), uid="user-1", agent_slug="agent-1", model_spec="resource:model", thread_id=None
    )

    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "new-snapshot-secret-that-is-at-least-32-characters")
    monkeypatch.setenv(
        "RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS",
        '["old-snapshot-secret-that-is-at-least-32-characters"]',
    )
    projection = await service.load_run_resources(
        object(), uid="user-1", manifest={"runtime_snapshot": reference}, agent_slug="agent-1"
    )

    assert isinstance(projection, RuntimeProjection)


@pytest.mark.parametrize("raw", ['"not-a-list"', '["short"]', "{bad-json"])
def test_snapshot_keyring_rejects_invalid_configuration(monkeypatch, raw):
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "current-snapshot-secret-that-is-at-least-32-characters")
    monkeypatch.setenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raw)

    with pytest.raises(RuntimeError, match="RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS"):
        service._snapshot_keys()


async def test_snapshot_preserves_complete_skill_tree_after_source_is_deleted(monkeypatch, tmp_path):
    """Run 必须从提交快照安装完整 Skill，而不是重新读取当前授权目录。"""
    stored = {}
    skill_dir = tmp_path / "skills" / "reporter"
    script_path = skill_dir / "scripts" / "run.sh"
    script_path.parent.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# submitted manifest\n", encoding="utf-8")
    script_path.write_text("#!/bin/sh\necho submitted\n", encoding="utf-8")
    script_path.chmod(0o755)

    projection = RuntimeProjection(
        agent_slug="agent-1",
        model_spec="resource:model",
        agent_request={},
        credential_data={},
        chat_model_config={},
        skills=[
            {
                "slug": "reporter",
                "name": "Reporter",
                "source_dir": str(skill_dir),
            }
        ],
    )

    async def store(_db, snapshot):
        stored[snapshot.id] = snapshot

    monkeypatch.setattr(service, "project_runtime", AsyncMock(return_value=projection))
    monkeypatch.setattr(service, "store_snapshot", store)
    monkeypatch.setattr(
        service,
        "get_snapshot",
        AsyncMock(side_effect=lambda _db, snapshot_id, uid: stored.get(snapshot_id)),
    )
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "skill-snapshot-secret-that-is-at-least-32-characters")
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    reference = await service.capture_run_resources(
        object(),
        uid="user-1",
        agent_slug="agent-1",
        model_spec="resource:model",
        thread_id=None,
    )
    shutil.rmtree(skill_dir)
    loaded = await service.load_run_resources(
        object(),
        uid="user-1",
        manifest={"runtime_snapshot": reference},
        agent_slug="agent-1",
    )

    captured = {}

    async def add_skill(source_dir: str, *, agent_id: str):
        root = os.path.abspath(source_dir)
        captured["agent_id"] = agent_id
        captured["manifest"] = open(os.path.join(root, "SKILL.md"), encoding="utf-8").read()
        captured["script"] = open(os.path.join(root, "scripts", "run.sh"), encoding="utf-8").read()
        captured["executable"] = bool(os.stat(os.path.join(root, "scripts", "run.sh")).st_mode & 0o111)

    workspace = SimpleNamespace(
        list_skills=AsyncMock(return_value=[]),
        remove_skill=AsyncMock(),
        add_skill=add_skill,
    )
    await runtime_resources.sync_runtime_skills(workspace, loaded, agent_id="runtime-agent")

    assert captured == {
        "agent_id": "runtime-agent",
        "manifest": "# submitted manifest\n",
        "script": "#!/bin/sh\necho submitted\n",
        "executable": True,
    }


async def test_capture_rejects_run_when_individually_valid_skills_exceed_aggregate_limit(
    monkeypatch,
    tmp_path,
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "SKILL.md").write_bytes(b"123")
    (second / "SKILL.md").write_bytes(b"456")
    projection = RuntimeProjection(
        agent_slug="agent-1",
        model_spec="resource:model",
        agent_request={},
        credential_data={},
        chat_model_config={},
        skills=[
            {"slug": "first", "source_dir": str(first)},
            {"slug": "second", "source_dir": str(second)},
        ],
    )
    monkeypatch.setattr(service, "project_runtime", AsyncMock(return_value=projection))
    monkeypatch.setattr(service, "store_snapshot", AsyncMock())
    monkeypatch.setattr(skill_snapshot, "MAX_SKILL_SNAPSHOT_BYTES", 5)
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "aggregate-snapshot-secret-that-is-at-least-32-characters")
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    with pytest.raises(ValueError, match="大小"):
        await service.capture_run_resources(
            object(),
            uid="user-1",
            agent_slug="agent-1",
            model_spec="resource:model",
            thread_id=None,
        )


async def test_capture_stops_before_next_skill_after_run_limit(monkeypatch, tmp_path):
    """累计超限必须停止捕获，不保留尚未需要的完整编码树。"""
    skills = []
    for slug in ("first", "second", "third"):
        source = tmp_path / slug
        source.mkdir()
        (source / "SKILL.md").write_bytes(b"123")
        skills.append({"slug": slug, "source_dir": str(source)})
    projection = RuntimeProjection(
        agent_slug="agent-1",
        model_spec="resource:model",
        agent_request={},
        credential_data={},
        chat_model_config={},
        skills=skills,
    )
    monkeypatch.setattr(service, "project_runtime", AsyncMock(return_value=projection))
    monkeypatch.setattr(skill_snapshot, "MAX_SKILL_SNAPSHOT_BYTES", 5)
    captured = []
    original_capture = service.capture_skill_tree

    def record_capture(skill):
        captured.append(skill["slug"])
        return original_capture(skill)

    monkeypatch.setattr(service, "capture_skill_tree", record_capture)

    with pytest.raises(ValueError, match="大小"):
        await service.capture_run_resources(
            object(), uid="user-1", agent_slug="agent-1", model_spec="resource:model", thread_id=None
        )
    assert captured == ["first", "second"]


async def test_capture_rejects_run_when_skill_entries_exceed_aggregate_limit(
    monkeypatch,
    tmp_path,
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "SKILL.md").write_bytes(b"1")
    (second / "SKILL.md").write_bytes(b"2")
    projection = RuntimeProjection(
        agent_slug="agent-1",
        model_spec="resource:model",
        agent_request={},
        credential_data={},
        chat_model_config={},
        skills=[
            {"slug": "first", "source_dir": str(first)},
            {"slug": "second", "source_dir": str(second)},
        ],
    )
    monkeypatch.setattr(service, "project_runtime", AsyncMock(return_value=projection))
    monkeypatch.setattr(service, "store_snapshot", AsyncMock())
    monkeypatch.setattr(skill_snapshot, "MAX_SKILL_SNAPSHOT_ENTRIES", 1)
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "aggregate-entry-secret-that-is-at-least-32-characters")
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    with pytest.raises(ValueError, match="条目"):
        await service.capture_run_resources(
            object(),
            uid="user-1",
            agent_slug="agent-1",
            model_spec="resource:model",
            thread_id=None,
        )


async def test_capture_counts_reused_skill_tree_once_across_projections(monkeypatch, tmp_path):
    source = tmp_path / "shared"
    source.mkdir()
    (source / "SKILL.md").write_bytes(b"1234")
    leader = RuntimeProjection(
        agent_slug="leader",
        model_spec="resource:model",
        agent_request={},
        credential_data={},
        chat_model_config={},
        skills=[{"slug": "shared", "source_dir": str(source)}],
        subagent_templates=[{"type": "worker"}],
    )
    worker = RuntimeProjection(
        agent_slug="worker",
        model_spec="resource:model",
        agent_request={},
        credential_data={},
        chat_model_config={},
        skills=[{"slug": "shared", "source_dir": str(source)}],
    )
    stored = {}

    async def store(_db, snapshot):
        stored[snapshot.id] = snapshot

    monkeypatch.setattr(
        service,
        "project_runtime",
        AsyncMock(side_effect=lambda _db, **kwargs: leader if kwargs["agent_slug"] == "leader" else worker),
    )
    monkeypatch.setattr(service, "store_snapshot", store)
    monkeypatch.setattr(skill_snapshot, "MAX_SKILL_SNAPSHOT_BYTES", 5)
    secret = "deduplicated-snapshot-secret-that-is-at-least-32-characters"
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", secret)
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    reference = await service.capture_run_resources(
        object(),
        uid="user-1",
        agent_slug="leader",
        model_spec="resource:model",
        thread_id=None,
    )

    snapshot = stored[reference["id"]]
    _plaintext, payload = _decrypt_snapshot(snapshot, secret)

    assert payload["format_version"] == 2
    assert len(payload["skill_trees"]) == 1
    [tree_ref] = payload["skill_trees"]
    assert payload["projections"]["leader"]["skills"] == [{"slug": "shared", "snapshot_ref": tree_ref}]
    assert payload["projections"]["worker"]["skills"] == [{"slug": "shared", "snapshot_ref": tree_ref}]
    assert payload["skill_trees"][tree_ref]["snapshot_files"][0]["content_base64"] == base64.b64encode(b"1234").decode(
        "ascii"
    )


async def test_reused_skill_tree_does_not_duplicate_plaintext_or_ciphertext(monkeypatch, tmp_path):
    """重复投影只增加引用元数据，不能再次序列化完整 preload 正文。"""
    source = tmp_path / "shared"
    source.mkdir()
    content = "# shared preload\n" + "x" * (256 * 1024)
    (source / "SKILL.md").write_text(content, encoding="utf-8")

    def projection(slug: str) -> RuntimeProjection:
        return RuntimeProjection(
            agent_slug=slug,
            model_spec="resource:model",
            agent_request={
                "system_prompt": f"base prompt\n\n## Preloaded Skill: shared\n{content}",
            },
            credential_data={},
            chat_model_config={},
            skills=[{"slug": "shared", "source_dir": str(source)}],
            preloaded_skills=["shared"],
            preloaded_skill_contents={"shared": content},
        )

    leader = projection("leader")
    worker = projection("worker")
    secret = "payload-growth-secret-that-is-at-least-32-characters"
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", secret)
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    async def capture(project_runtime):
        stored = {}

        async def store(_db, snapshot):
            stored[snapshot.id] = snapshot

        monkeypatch.setattr(service, "project_runtime", project_runtime)
        monkeypatch.setattr(service, "store_snapshot", store)
        reference = await service.capture_run_resources(
            object(),
            uid="user-1",
            agent_slug="leader",
            model_spec="resource:model",
            thread_id=None,
        )
        snapshot = stored[reference["id"]]
        plaintext, payload = _decrypt_snapshot(snapshot, secret)
        return len(plaintext), len(snapshot.encrypted_payload), payload, reference, stored

    single_plaintext, single_ciphertext, _single_payload, _single_reference, _single_stored = await capture(
        AsyncMock(return_value=leader)
    )
    leader.subagent_templates = [{"type": "worker"}]
    repeated_plaintext, repeated_ciphertext, payload, reference, stored = await capture(
        AsyncMock(side_effect=lambda _db, **kwargs: leader if kwargs["agent_slug"] == "leader" else worker)
    )

    assert repeated_plaintext - single_plaintext < 4096
    assert repeated_ciphertext - single_ciphertext < 8192
    assert "preloaded_skill_contents" not in payload["projections"]["leader"]
    assert "preloaded_skill_contents" not in payload["projections"]["worker"]
    assert payload["projections"]["leader"]["agent_request"]["system_prompt"] == "base prompt"
    assert payload["projections"]["worker"]["agent_request"]["system_prompt"] == "base prompt"

    monkeypatch.setattr(
        service,
        "get_snapshot",
        AsyncMock(side_effect=lambda _db, snapshot_id, uid: stored.get(snapshot_id)),
    )
    loaded = await service.load_run_resources(
        object(),
        uid="user-1",
        manifest={"runtime_snapshot": reference},
        agent_slug="worker",
    )

    assert loaded.preloaded_skill_contents == {"shared": content}
    assert loaded.agent_request["system_prompt"] == f"base prompt\n\n## Preloaded Skill: shared\n{content}"


async def test_load_preserves_historical_inline_v2_skill_snapshot(monkeypatch):
    """历史 inline-v2 快照必须按提交内容恢复，不能强制要求 skill_trees。"""
    secret = "historical-inline-v2-secret-that-is-at-least-32-characters"
    key = service._derive_snapshot_key(secret)
    snapshot_id = "snapshot-inline-v2"
    content = "# legacy preload\nUse the submitted workflow.\n"
    system_prompt = f"base prompt\n\n## Preloaded Skill: legacy\n{content}"
    payload = service._encode(
        {
            "format_version": 2,
            "id": snapshot_id,
            "uid": "user-1",
            "projections": {
                "leader": {
                    "agent_slug": "leader",
                    "model_spec": "resource:model",
                    "agent_request": {"system_prompt": system_prompt},
                    "credential_data": {},
                    "chat_model_config": {},
                    "skills": [
                        {
                            "slug": "legacy",
                            "snapshot_directories": [],
                            "snapshot_files": [
                                {
                                    "path": "SKILL.md",
                                    "content_base64": base64.b64encode(content.encode()).decode("ascii"),
                                    "executable": False,
                                }
                            ],
                        }
                    ],
                    "preloaded_skills": ["legacy"],
                    "preloaded_skill_contents": {"legacy": content},
                }
            },
        }
    )
    fingerprint = hmac.new(key, payload, hashlib.sha256).hexdigest()
    snapshot = RunResourceSnapshot(
        id=snapshot_id,
        uid="user-1",
        fingerprint=fingerprint,
        encrypted_payload=Fernet(base64.urlsafe_b64encode(key)).encrypt(payload).decode(),
    )
    monkeypatch.setattr(service, "get_snapshot", AsyncMock(return_value=snapshot))
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", secret)
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    loaded = await service.load_run_resources(
        object(),
        uid="user-1",
        manifest={"runtime_snapshot": {"id": snapshot_id, "fingerprint": fingerprint}},
        agent_slug="leader",
    )

    assert loaded.preloaded_skill_contents == {"legacy": content}
    assert loaded.agent_request["system_prompt"] == system_prompt


async def test_load_rejects_inline_v2_with_invalid_skill_trees_field(monkeypatch):
    """skill_trees 字段存在但非法时必须拒绝，不能降级为历史内联格式。"""
    secret = "invalid-inline-v2-trees-secret-that-is-at-least-32-characters"
    key = service._derive_snapshot_key(secret)
    snapshot_id = "snapshot-inline-v2-invalid-trees"
    payload = service._encode(
        {
            "format_version": 2,
            "id": snapshot_id,
            "uid": "user-1",
            "skill_trees": None,
            "projections": {
                "leader": {
                    "agent_slug": "leader",
                    "model_spec": "resource:model",
                    "agent_request": {},
                    "credential_data": {},
                    "chat_model_config": {},
                    "skills": [],
                }
            },
        }
    )
    fingerprint = hmac.new(key, payload, hashlib.sha256).hexdigest()
    snapshot = RunResourceSnapshot(
        id=snapshot_id,
        uid="user-1",
        fingerprint=fingerprint,
        encrypted_payload=Fernet(base64.urlsafe_b64encode(key)).encrypt(payload).decode(),
    )
    monkeypatch.setattr(service, "get_snapshot", AsyncMock(return_value=snapshot))
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", secret)
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    with pytest.raises(ValueError, match="格式版本不受支持"):
        await service.load_run_resources(
            object(),
            uid="user-1",
            manifest={"runtime_snapshot": {"id": snapshot_id, "fingerprint": fingerprint}},
            agent_slug="leader",
        )


@pytest.mark.parametrize(
    ("tree_ref", "tree_slug", "message"),
    [
        ("f" * 64, None, "引用不存在"),
        ("a" * 64, "different", "身份不一致"),
    ],
)
async def test_load_rejects_invalid_skill_tree_reference(monkeypatch, tree_ref, tree_slug, message):
    """执行期必须拒绝未知引用和 projection/tree slug 身份错配。"""
    secret = "invalid-reference-secret-that-is-at-least-32-characters"
    key = service._derive_snapshot_key(secret)
    snapshot_id = "snapshot-invalid-ref"
    skill_trees = {}
    if tree_slug is not None:
        tree = {
            "slug": tree_slug,
            "snapshot_directories": [],
            "snapshot_files": [
                {
                    "path": "SKILL.md",
                    "content_base64": base64.b64encode(b"# skill\n").decode("ascii"),
                    "executable": False,
                }
            ],
        }
        identity = skill_snapshot._snapshot_tree_usage(tree)[0]
        skill_trees[identity] = tree
        tree_ref = identity
    payload_value = {
        "format_version": 2,
        "id": snapshot_id,
        "uid": "user-1",
        "skill_trees": skill_trees,
        "projections": {
            "leader": {
                "agent_slug": "leader",
                "model_spec": "resource:model",
                "agent_request": {},
                "credential_data": {},
                "chat_model_config": {},
                "skills": [{"slug": "shared", "snapshot_ref": tree_ref}],
            }
        },
    }
    payload = service._encode(payload_value)
    fingerprint = hmac.new(key, payload, hashlib.sha256).hexdigest()
    snapshot = RunResourceSnapshot(
        id=snapshot_id,
        uid="user-1",
        fingerprint=fingerprint,
        encrypted_payload=Fernet(base64.urlsafe_b64encode(key)).encrypt(payload).decode(),
    )
    monkeypatch.setattr(service, "get_snapshot", AsyncMock(return_value=snapshot))
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", secret)
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    with pytest.raises(ValueError, match=message):
        await service.load_run_resources(
            object(),
            uid="user-1",
            manifest={"runtime_snapshot": {"id": snapshot_id, "fingerprint": fingerprint}},
            agent_slug="leader",
        )


async def test_capture_rejects_plaintext_over_snapshot_limit(monkeypatch):
    projection = RuntimeProjection(
        agent_slug="leader",
        model_spec="resource:model",
        agent_request={"system_prompt": "x" * 1024},
        credential_data={},
        chat_model_config={},
    )
    monkeypatch.setattr(service, "project_runtime", AsyncMock(return_value=projection))
    monkeypatch.setattr(service, "store_snapshot", AsyncMock())
    monkeypatch.setattr(service, "MAX_RUN_RESOURCE_SNAPSHOT_PLAINTEXT_BYTES", 256, raising=False)
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "plaintext-limit-secret-that-is-at-least-32-characters")
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    with pytest.raises(ValueError, match="明文"):
        await service.capture_run_resources(
            object(),
            uid="user-1",
            agent_slug="leader",
            model_spec="resource:model",
            thread_id=None,
        )


async def test_load_rejects_ciphertext_before_decryption(monkeypatch):
    snapshot = RunResourceSnapshot(
        id="snapshot-large-ciphertext",
        uid="user-1",
        fingerprint="fingerprint",
        encrypted_payload="x" * 1024,
    )
    monkeypatch.setattr(service, "get_snapshot", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(service, "MAX_RUN_RESOURCE_SNAPSHOT_CIPHERTEXT_BYTES", 256, raising=False)
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", "ciphertext-limit-secret-that-is-at-least-32-characters")
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    with pytest.raises(ValueError, match="密文"):
        await service.load_run_resources(
            object(),
            uid="user-1",
            manifest={
                "runtime_snapshot": {
                    "id": snapshot.id,
                    "fingerprint": snapshot.fingerprint,
                }
            },
            agent_slug="leader",
        )


async def test_load_rejects_plaintext_after_decryption(monkeypatch):
    secret = "decrypted-limit-secret-that-is-at-least-32-characters"
    key = service._derive_snapshot_key(secret)
    snapshot_id = "snapshot-large-plaintext"
    payload = service._encode(
        {
            "id": snapshot_id,
            "uid": "user-1",
            "projections": {
                "leader": {
                    "agent_slug": "leader",
                    "model_spec": "resource:model",
                    "agent_request": {"system_prompt": "x" * 1024},
                    "credential_data": {},
                    "chat_model_config": {},
                }
            },
        }
    )
    fingerprint = hmac.new(key, payload, hashlib.sha256).hexdigest()
    snapshot = RunResourceSnapshot(
        id=snapshot_id,
        uid="user-1",
        fingerprint=fingerprint,
        encrypted_payload=Fernet(base64.urlsafe_b64encode(key)).encrypt(payload).decode(),
    )
    monkeypatch.setattr(service, "get_snapshot", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(service, "MAX_RUN_RESOURCE_SNAPSHOT_PLAINTEXT_BYTES", 256, raising=False)
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", secret)
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    with pytest.raises(ValueError, match="明文"):
        await service.load_run_resources(
            object(),
            uid="user-1",
            manifest={"runtime_snapshot": {"id": snapshot_id, "fingerprint": fingerprint}},
            agent_slug="leader",
        )


async def test_load_rejects_decrypted_payload_over_aggregate_skill_limit(monkeypatch):
    secret = "loaded-aggregate-secret-that-is-at-least-32-characters"
    key = service._derive_snapshot_key(secret)
    snapshot_id = "snapshot-1"
    skill = {
        "slug": "shared",
        "snapshot_directories": [],
        "snapshot_files": [
            {
                "path": "SKILL.md",
                "content_base64": base64.b64encode(b"123").decode("ascii"),
                "executable": False,
            }
        ],
    }
    payload_value = {
        "id": snapshot_id,
        "uid": "user-1",
        "projections": {
            "leader": {
                "agent_slug": "leader",
                "model_spec": "resource:model",
                "agent_request": {},
                "credential_data": {},
                "chat_model_config": {},
                "skills": [skill],
            },
            "worker": {
                "agent_slug": "worker",
                "model_spec": "resource:model",
                "agent_request": {},
                "credential_data": {},
                "chat_model_config": {},
                "skills": [
                    skill
                    | {
                        "slug": "other",
                        "snapshot_files": [
                            {
                                "path": "SKILL.md",
                                "content_base64": base64.b64encode(b"456").decode("ascii"),
                                "executable": False,
                            }
                        ],
                    }
                ],
            },
        },
    }
    payload = json.dumps(payload_value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    fingerprint = hmac.new(key, payload, hashlib.sha256).hexdigest()
    snapshot = RunResourceSnapshot(
        id=snapshot_id,
        uid="user-1",
        fingerprint=fingerprint,
        encrypted_payload=Fernet(base64.urlsafe_b64encode(key)).encrypt(payload).decode(),
    )
    monkeypatch.setattr(service, "get_snapshot", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(skill_snapshot, "MAX_SKILL_SNAPSHOT_BYTES", 5)
    monkeypatch.setenv("API_KEY_DERIVATION_SECRET", secret)
    monkeypatch.delenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", raising=False)

    with pytest.raises(ValueError, match="大小"):
        await service.load_run_resources(
            object(),
            uid="user-1",
            manifest={"runtime_snapshot": {"id": snapshot_id, "fingerprint": fingerprint}},
            agent_slug="leader",
        )
