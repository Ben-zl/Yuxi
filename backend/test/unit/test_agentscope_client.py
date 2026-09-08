import pytest


@pytest.mark.asyncio
async def test_transport_error_is_wrapped_as_service_error(monkeypatch):
    """连接失败必须进入可补偿的 AgentScopeServiceError 边界。"""
    import httpx

    from yuxi.agentscope.client import AgentScopeServiceClient, AgentScopeServiceError

    async def failing_request(self, *args, **kwargs):
        del self, args, kwargs
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.AsyncClient, "request", failing_request)

    with pytest.raises(AgentScopeServiceError, match="transport failure"):
        await AgentScopeServiceClient("http://agentscope.invalid").clear_memory_agent("system", "agent-a")
