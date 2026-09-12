from types import SimpleNamespace

import pytest

from yuxi.agents.skills import catalog


async def test_authorizable_skill_catalog_preserves_personal_override(monkeypatch, tmp_path):
    """权限目录保留个人 Skill 覆盖同名共享 Skill 的语义。"""
    shared = SimpleNamespace(
        slug="same-skill",
        created_by="shared-owner",
        share_config={
            "version": 2,
            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
            "manage_scope": None,
        },
        skill_dependencies=["shared-dependency"],
        mcp_dependencies=["shared-mcp"],
    )

    async def list_enabled(_self):
        return [shared]

    monkeypatch.setattr(catalog.SkillRepository, "list_enabled", list_enabled)
    monkeypatch.setattr(catalog, "resolve_skill_permission", lambda _user, _skill: catalog.ResourcePermission.READ)
    monkeypatch.setattr(catalog, "user_workspace_dir", lambda _uid: tmp_path)
    personal_dir = tmp_path / "agents" / "skills" / "same-skill"
    personal_dir.mkdir(parents=True)
    (personal_dir / "SKILL.md").write_text(
        "---\nname: same-skill\ndescription: Personal override\n---\n",
        encoding="utf-8",
    )

    result = await catalog.list_authorizable_skills(
        object(),
        user=SimpleNamespace(uid="user-1"),
    )

    assert result == [
        catalog.AuthorizableSkill(
            slug="same-skill",
            created_by="user-1",
            share_config=None,
            skill_dependencies=[],
            mcp_dependencies=[],
        )
    ]


@pytest.mark.parametrize(
    "content",
    [
        "not-frontmatter",
        "---\nname: another-skill\ndescription: mismatch\n---\n",
        "---\nname: invalid-skill\n---\n",
        "---\nslug: invalid-skill\ndescription: missing name\n---\n",
    ],
)
async def test_authorizable_skill_catalog_skips_invalid_personal_skills(monkeypatch, tmp_path, content):
    async def list_enabled(_self):
        return []

    monkeypatch.setattr(catalog.SkillRepository, "list_enabled", list_enabled)
    monkeypatch.setattr(catalog, "user_workspace_dir", lambda _uid: tmp_path)
    personal_dir = tmp_path / "agents" / "skills" / "invalid-skill"
    personal_dir.mkdir(parents=True)
    (personal_dir / "SKILL.md").write_text(content, encoding="utf-8")

    assert await catalog.list_authorizable_skills(object(), user=SimpleNamespace(uid="user-1")) == []
