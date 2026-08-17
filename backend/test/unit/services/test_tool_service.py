from __future__ import annotations

from yuxi.agents.toolkits import service as tool_service


def test_get_tool_metadata_uses_static_management_catalog():
    tool_service._metadata_cache.clear()

    result = tool_service.get_tool_metadata()

    assert {item["slug"] for item in result} == {
        "web_search",
        "present_artifacts",
        "ocr_parse_file",
        "ask_user_question",
        "install_skill",
    }
    assert all("config_guide" in item for item in result)

    tool_service._metadata_cache.clear()
