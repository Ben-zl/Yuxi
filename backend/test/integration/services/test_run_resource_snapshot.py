"""真实 PostgreSQL 验证提交快照、权限变更和执行资产的一致性。"""

import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.run_resource_snapshot_service import capture_run_resources, load_run_resources
from yuxi.storage.postgres.models_business import Agent, Department, MCPServer, ModelProvider, RunResourceSnapshot, User


@pytest.mark.integration
async def test_snapshot_survives_revocation_without_exposing_credentials(monkeypatch):
    """旧执行读取提交凭据，新提交拒绝已撤销资源，事务回滚不留下测试数据。"""
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            suffix = uuid4().hex[:10]
            department = Department(name=f"snapshot-{suffix}")
            other_department = Department(name=f"snapshot-other-{suffix}")
            db.add_all([department, other_department])
            await db.flush()
            uid = f"snapshot-{suffix}"
            user = User(uid=uid, username=uid, role="user", department_id=department.id, password_hash="unused")
            db.add(user)
            await db.flush()
            share = {
                "version": 2,
                "read_scope": {
                    "access_level": "department",
                    "department_ids": [department.id],
                    "user_uids": [],
                },
                "manage_scope": None,
            }
            provider = ModelProvider(
                resource_id=str(uuid4()),
                provider_id=f"snapshot-{suffix}",
                display_name="Snapshot",
                base_url="https://old.example.test/v1",
                api_key="snapshot-original-key",
                provider_type="openai",
                enabled_models=[{"id": "chat", "type": "chat"}],
                capabilities=["chat"],
                is_enabled=True,
                share_config=share,
            )
            mcp = MCPServer(
                resource_id=str(uuid4()),
                slug=f"snapshot-{suffix}",
                name="Snapshot",
                transport="streamable_http",
                url="https://old.example.test/mcp",
                headers={"Authorization": "snapshot-original-header"},
                enabled=1,
                created_by="system",
                updated_by="system",
                share_config=share,
            )
            db.add_all([provider, mcp])
            await db.flush()
            agent = Agent(
                slug=f"snapshot-{suffix}",
                name="Snapshot",
                backend_id="ChatbotAgent",
                created_by=uid,
                share_config=share,
                config_json={
                    "context": {
                        "model": f"{provider.resource_id}:chat",
                        "skills": [],
                        "mcps": [mcp.resource_id],
                        "tools": [],
                        "subagents": [],
                    }
                },
            )
            db.add(agent)
            await db.flush()
            original_secret = os.environ["API_KEY_DERIVATION_SECRET"]
            reference = await capture_run_resources(
                db,
                uid=uid,
                agent_slug=agent.slug,
                model_spec=f"{provider.resource_id}:chat",
                thread_id=None,
            )
            row = await db.get(RunResourceSnapshot, reference["id"])
            assert "snapshot-original-key" not in row.encrypted_payload
            assert "snapshot-original-header" not in row.encrypted_payload
            assert "snapshot-original-key" not in str(reference)
            manifest = {"runtime_snapshot": reference}

            provider.api_key = "snapshot-replacement-key"
            provider.base_url = "https://new.example.test/v1"
            provider.share_config = {
                "version": 2,
                "read_scope": {
                    "access_level": "department",
                    "department_ids": [other_department.id],
                    "user_uids": [],
                },
                "manage_scope": None,
            }
            mcp.headers = {"Authorization": "snapshot-replacement-header"}
            mcp.enabled = 0
            await db.flush()
            monkeypatch.setenv(
                "API_KEY_DERIVATION_SECRET",
                "rotated-snapshot-integration-secret-at-least-32-characters",
            )
            monkeypatch.setenv("RUN_RESOURCE_SNAPSHOT_DECRYPTION_SECRETS", f'["{original_secret}"]')
            old = await load_run_resources(db, uid=uid, manifest=manifest, agent_slug=agent.slug)
            assert old.credential_data["api_key"] == "snapshot-original-key"
            assert old.credential_data["base_url"] == "https://old.example.test/v1"
            assert old.mcp_servers[0]["headers"]["Authorization"] == "snapshot-original-header"
            with pytest.raises(ValueError, match="不可访问|不存在"):
                await capture_run_resources(
                    db,
                    uid=uid,
                    agent_slug=agent.slug,
                    model_spec=f"{provider.resource_id}:chat",
                    thread_id=None,
                )
            with pytest.raises(ValueError, match="身份"):
                await load_run_resources(db, uid="other-user", manifest=manifest, agent_slug=agent.slug)
            with pytest.raises(ValueError, match="身份"):
                await load_run_resources(
                    db,
                    uid=uid,
                    manifest={
                        "runtime_snapshot": {
                            **reference,
                            "fingerprint": "tampered",
                        }
                    },
                    agent_slug=agent.slug,
                )
            with pytest.raises(ValueError, match="Agent"):
                await load_run_resources(db, uid=uid, manifest=manifest, agent_slug="unauthorized-child")
            row.encrypted_payload = "tampered"
            with pytest.raises(ValueError, match="认证失败"):
                await load_run_resources(db, uid=uid, manifest=manifest, agent_slug=agent.slug)
            await db.rollback()
    finally:
        await engine.dispose()
