from uuid import uuid4

import httpx
import jwt
from astra_tui import AstraAgentApp
from textual.widgets import Button, Input, Static


async def test_tui_mounts_task_and_approval_views(monkeypatch: object) -> None:
    monkeypatch.setenv("ASTRA_AGENT_URL", "http://127.0.0.1:1")
    monkeypatch.delenv("ASTRA_TENANT_ID", raising=False)
    monkeypatch.delenv("ASTRA_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("ASTRA_TUI_DEV_LOGIN", "false")
    app = AstraAgentApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "unavailable" in str(app.query_one("#status", Static).render())
        assert "ASTRA_TENANT_ID" in str(app.query_one("#task-list", Static).render())
        assert app.query_one("#grant", Button).disabled
        assert app.query_one("#deny", Button).disabled
        assert app.query_one("#promote-memory", Button).disabled
        assert app.query_one("#reject-memory", Button).disabled
        assert app.query_one("#grant-inline", Button).disabled
        assert app.query_one("#deny-inline", Button).disabled
        assert app.query_one("#memory-list", Static)
        assert app.query_one("#persona-list", Static)
        assert app.query_one("#ara-list", Static)
        assert app.query_one("#artifact-list", Static)
        assert app.query_one("#download-artifact", Button).disabled
        message_input = app.query_one("#message-input", Input)
        assert message_input.disabled
        assert "Authentication required" in message_input.placeholder


async def test_tui_development_login_enables_message_input(monkeypatch: object) -> None:
    tenant_id = uuid4()
    token = jwt.encode(
        {"tenant_id": str(tenant_id), "sub": "local-user"},
        "unused-test-key-with-at-least-32-bytes",
        algorithm="HS256",
    )

    async def login(self: object, settings: object) -> str:
        return token

    async def request(
        self: httpx.AsyncClient, method: str, url: str, **kwargs: object
    ) -> httpx.Response:
        if url == "/health":
            return httpx.Response(
                200,
                json={"status": "ok", "version": "0.1.0", "persistence": "memory"},
                request=httpx.Request(method, "http://astra.test/health"),
            )
        if str(url).endswith("/memories"):
            payload: object = {"memories": []}
        elif str(url).endswith("/persona"):
            payload = {"persona": {"version": 1}}
        else:
            payload = []
        return httpx.Response(
            200,
            json=payload,
            request=httpx.Request(method, f"http://astra.test{url}"),
        )

    monkeypatch.setenv("ASTRA_AGENT_URL", "http://astra.test")
    monkeypatch.setenv("ASTRA_TUI_DEV_LOGIN", "true")
    monkeypatch.delenv("ASTRA_TENANT_ID", raising=False)
    monkeypatch.delenv("ASTRA_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(AstraAgentApp, "_development_login", login)
    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    app = AstraAgentApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.tenant_id == str(tenant_id)
        assert not app.query_one("#message-input", Input).disabled
