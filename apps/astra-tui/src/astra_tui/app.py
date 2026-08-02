import json
from uuid import UUID, uuid4

import httpx
import jwt
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

from astra_tui.settings import TUISettings

WELCOME_TAGLINE = "Astra Agent · Distributed Enriched-Persona Agent"
WELCOME_HINT = "Type a message and press Enter · Ctrl+T shows activity"


class AstraAgentApp(App[None]):
    CSS = """
    Screen { background: #0b0f17; color: #cbd5e1; }
    Header { background: #0b0f17; color: #64748b; }
    #status {
        height: 1; padding: 0 2; background: #0b0f17; color: #475569;
        border-bottom: solid #11161f;
    }
    #conversation { height: 1fr; width: 1fr; padding: 0 2; }
    #conversation-log { height: 1fr; border: none; background: #0b0f17; }
    #turn-approval {
        height: auto; padding: 0 2; dock: bottom;
        color: #f59e0b;
    }
    #turn-approval-text { width: 1fr; }
    #turn-approval.hidden { display: none; }
    #side {
        layer: side;
        position: absolute;
        offset: 0 0;
        width: 36; height: 100%;
        background: #0b0f17;
        border-left: solid #11161f;
        padding: 1 1 1 1;
        overflow: auto;
        display: block;
    }
    #side.hidden { display: none; }
    .panel { height: auto; padding: 1 0; margin: 0; }
    .panel .title { color: #475569; text-style: bold; padding: 0; }
    .panel Static { color: #94a3b8; }
    #approvals .title { color: #f59e0b; }
    #memories .title { color: #a78bfa; }
    #message-input {
        dock: bottom; margin: 0 2; height: 3;
        border: none;
    }
    #message-input:focus { border: none; }
    Button { min-width: 6; height: 1; margin: 0 1 0 0; }
    Footer { background: #0b0f17; color: #475569; }
    """

    TITLE = "Astra Agent"
    SUB_TITLE = "D.E.P.A."

    BINDINGS = [
        ("ctrl+t", "toggle_side", "Activity"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.base_url: str = ""
        self.tenant_id: str | None = None
        self.user_id: str | None = None
        self.access_token: str | None = None
        self.pending_approval_id: str | None = None
        self.conversation_id: str | None = None
        self.candidate_memory_id: str | None = None
        self.active_turn_id: str | None = None
        self.active_turn_paused = False
        self.latest_artifact_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Astra Agent · starting…", id="status")
        with Vertical(id="conversation"):
            yield RichLog(id="conversation-log", wrap=True, markup=True)
            yield Static("", id="live-response")
            with Horizontal(id="turn-approval", classes="hidden"):
                yield Static("", id="turn-approval-text")
                yield Button("Grant", id="grant-inline", variant="success", disabled=True)
                yield Button("Deny", id="deny-inline", variant="error", disabled=True)
        with Vertical(id="side", classes="hidden"):
            with Vertical(id="tasks", classes="panel"):
                yield Label("Tasks", classes="title")
                yield Static("No active delegations", id="task-list")
            with Vertical(id="approvals", classes="panel"):
                yield Label("Approvals", classes="title")
                yield Static("No pending approvals", id="approval-list")
                with Horizontal():
                    yield Button("Grant", id="grant", variant="success", disabled=True)
                    yield Button("Deny", id="deny", variant="error", disabled=True)
            with Vertical(id="memories", classes="panel"):
                yield Label("Memory", classes="title")
                yield Static("No approved memory", id="memory-list")
                with Horizontal():
                    yield Button("Promote", id="promote-memory", disabled=True)
                    yield Button("Reject", id="reject-memory", disabled=True)
            with Vertical(id="jobs", classes="panel"):
                yield Label("Failed jobs", classes="title")
                yield Static("No failed jobs", id="job-list")
            with Vertical(id="persona", classes="panel"):
                yield Label("Active persona", classes="title")
                yield Static("Loading…", id="persona-list")
            with Vertical(id="aras", classes="panel"):
                yield Label("ARA health", classes="title")
                yield Static("Loading…", id="ara-list")
            with Vertical(id="artifacts", classes="panel"):
                yield Label("Artifacts", classes="title")
                yield Static("No artifacts", id="artifact-list")
                yield Button("Download latest", id="download-artifact", disabled=True)
        yield Input(placeholder="Message Astra…", id="message-input", disabled=True)
        yield Footer()

    async def on_mount(self) -> None:
        self._render_welcome()
        settings = TUISettings()
        self.base_url = settings.agent_url
        self.tenant_id = str(settings.tenant_id) if settings.tenant_id else None
        self.user_id = str(settings.user_id) if settings.user_id else None
        self.access_token = settings.access_token
        self.conversation_id = str(settings.conversation_id) if settings.conversation_id else None
        status = self.query_one("#status", Static)
        if not self.access_token and settings.tui_dev_login:
            try:
                self.access_token = await self._development_login(settings)
            except (httpx.HTTPError, KeyError, ValueError) as error:
                status.update(f"Astra Agent · local login failed · {error}")
        if self.access_token and not self.tenant_id:
            self.tenant_id = self._tenant_from_token(self.access_token)
        try:
            headers = self._user_headers()
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=3, headers=headers
            ) as client:
                response = await client.get("/health")
                response.raise_for_status()
            health = response.json()
            status.update(
                f"Astra Agent · online · API {health['version']} · {health['persistence']}"
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            status.update(f"Astra Agent · unavailable · {error}")
        await self.refresh_activity()
        message_input = self.query_one("#message-input", Input)
        message_input.disabled = not self._is_authenticated()
        if message_input.disabled:
            message_input.placeholder = (
                "Authentication required: configure ASTRA_ACCESS_TOKEN or ASTRA_TUI_DEV_LOGIN"
            )
        else:
            message_input.focus()
        self.set_interval(2, self.refresh_activity)

    def _render_welcome(self) -> None:
        log = self.query_one("#conversation-log", RichLog)
        log.write(f"[bold #93c5fd]{WELCOME_TAGLINE}[/]")
        log.write(f"[#64748b]{WELCOME_HINT}[/]")
        log.write("")

    def action_toggle_side(self) -> None:
        side = self.query_one("#side", Vertical)
        side.set_class(not side.has_class("hidden"), "hidden")

    async def _development_login(self, settings: TUISettings) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                settings.tui_oidc_token_url,
                data={
                    "client_id": settings.tui_oidc_client_id,
                    "username": settings.tui_oidc_username,
                    "password": settings.tui_oidc_password,
                    "grant_type": "password",
                },
            )
            response.raise_for_status()
        token = response.json()["access_token"]
        if not isinstance(token, str) or not token:
            raise ValueError("identity provider returned no access token")
        return token

    @staticmethod
    def _tenant_from_token(token: str) -> str:
        claims = jwt.decode(token, options={"verify_signature": False})
        tenant_id = UUID(claims["tenant_id"])
        return str(tenant_id)

    def _is_authenticated(self) -> bool:
        return bool(self.tenant_id and (self.user_id or self.access_token))

    async def refresh_activity(self) -> None:
        if not self.tenant_id:
            self.query_one("#task-list", Static).update("Set ASTRA_TENANT_ID to view tasks")
            return
        enabled = False
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=3, headers=self._user_headers()
            ) as client:
                tasks_response = await client.get(f"/api/v1/tenants/{self.tenant_id}/tasks")
                approvals_response = await client.get(f"/api/v1/tenants/{self.tenant_id}/approvals")
                memories_response = await client.get(f"/api/v1/tenants/{self.tenant_id}/memories")
                jobs_response = await client.get(f"/api/v1/tenants/{self.tenant_id}/jobs")
                persona_response = await client.get(f"/api/v1/tenants/{self.tenant_id}/persona")
                aras_response = await client.get(f"/api/v1/tenants/{self.tenant_id}/aras")
                artifacts_response = await client.get(f"/api/v1/tenants/{self.tenant_id}/artifacts")
                tasks_response.raise_for_status()
                approvals_response.raise_for_status()
                memories_response.raise_for_status()
                jobs_response.raise_for_status()
                persona_response.raise_for_status()
                aras_response.raise_for_status()
                artifacts_response.raise_for_status()
            tasks = tasks_response.json()
            approvals = [item for item in approvals_response.json() if item["state"] == "pending"]
            memories = memories_response.json()["memories"]
            failures = [item for item in jobs_response.json() if item["state"] == "failed"]
            persona = persona_response.json()["persona"]
            aras = aras_response.json()
            artifacts = artifacts_response.json()
            candidates = [item for item in memories if item["state"] == "candidate"]
            self.candidate_memory_id = candidates[0]["id"] if candidates else None
            task_lines = [
                f"{item['state']:>9}  {item['objective']}"
                + (f" [{item['target_ara_id'][:8]}]" if item.get("target_ara_id") else "")
                + (" [partial failure]" if item["state"] in {"failed", "cancelled"} else "")
                for item in tasks[-8:]
            ]
            self.query_one("#task-list", Static).update("\n".join(task_lines) or "No tasks")
            self.pending_approval_id = approvals[0]["id"] if approvals else None
            if approvals:
                pending = approvals[0]
                text = f"{pending['capability']['kind']}\n{pending['reason']}"
            else:
                text = "No pending approvals"
            self.query_one("#approval-list", Static).update(text)
            memory_lines = [f"{item['state']:>9}  {item['content']}" for item in memories[-5:]]
            self.query_one("#memory-list", Static).update(
                "\n".join(memory_lines) or "No approved memory"
            )
            failure_lines = [
                f"{item['kind']}: {item.get('last_error', {}).get('message', 'failed')}"
                for item in failures[:3]
            ]
            self.query_one("#job-list", Static).update("\n".join(failure_lines) or "No failed jobs")
            self.query_one("#persona-list", Static).update(f"Version {persona['version']}")
            ara_lines = [f"{item['status']:>8}  {item['name']}" for item in aras[-5:]]
            self.query_one("#ara-list", Static).update("\n".join(ara_lines) or "No registered ARAs")
            self.latest_artifact_id = artifacts[-1]["id"] if artifacts else None
            artifact_lines = [item["name"] for item in artifacts[-5:]]
            self.query_one("#artifact-list", Static).update(
                "\n".join(artifact_lines) or "No artifacts"
            )
            self.query_one("#download-artifact", Button).disabled = self.latest_artifact_id is None
            can_review = bool(self.candidate_memory_id and (self.user_id or self.access_token))
            self.query_one("#promote-memory", Button).disabled = not can_review
            self.query_one("#reject-memory", Button).disabled = not can_review
            enabled = bool(self.pending_approval_id and (self.user_id or self.access_token))
            self.query_one("#grant", Button).disabled = not enabled
            self.query_one("#deny", Button).disabled = not enabled
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            self.query_one("#job-list", Static).update(f"Status unavailable: {error}")
        await self._refresh_active_turn()
        inline_enabled = bool(enabled and self.active_turn_paused)
        self.query_one("#grant-inline", Button).disabled = not inline_enabled
        self.query_one("#deny-inline", Button).disabled = not inline_enabled
        approval_row = self.query_one("#turn-approval", Horizontal)
        approval_row.set_class(not inline_enabled, "hidden")
        self.query_one("#turn-approval-text", Static).update(
            "Approval required for this turn" if inline_enabled else ""
        )

    async def _refresh_active_turn(self) -> None:
        if not self.active_turn_id:
            return
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=3, headers=self._user_headers()
            ) as client:
                response = await client.get(f"/api/v1/conversation-turns/{self.active_turn_id}")
                response.raise_for_status()
            payload = response.json()
            self.active_turn_paused = payload["turn"]["state"] == "paused"
            if payload["turn"]["state"] != "completed":
                return
            assistant = payload.get("assistant_message")
            if isinstance(assistant, dict) and isinstance(assistant.get("content"), str):
                self.query_one("#conversation-log", RichLog).write(
                    f"[bold #86efac]Astra[/]: {assistant['content']}"
                )
            self.active_turn_id = None
            self.active_turn_paused = False
        except (httpx.HTTPError, KeyError, ValueError):
            return

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "download-artifact":
            await self._download_artifact()
            return
        if event.button.id in {"promote-memory", "reject-memory"}:
            await self._review_memory(event.button.id == "promote-memory")
            return
        if event.button.id not in {"grant", "deny", "grant-inline", "deny-inline"}:
            return
        if not self.pending_approval_id or not self.tenant_id:
            return
        granted = event.button.id in {"grant", "grant-inline"}
        headers = self._user_headers()
        try:
            UUID(self.tenant_id)
            if self.user_id:
                UUID(self.user_id)
            async with httpx.AsyncClient(base_url=self.base_url, timeout=3) as client:
                response = await client.post(
                    f"/api/v1/approvals/{self.pending_approval_id}/decision",
                    headers=headers,
                    json={"tenant_id": self.tenant_id, "granted": granted},
                )
                response.raise_for_status()
            await self.refresh_activity()
        except (httpx.HTTPError, ValueError):
            return

    async def _review_memory(self, promote: bool) -> None:
        if not self.candidate_memory_id or not self.tenant_id:
            return
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=3, headers=self._user_headers()
            ) as client:
                response = await client.post(
                    f"/api/v1/memories/{self.candidate_memory_id}/review",
                    json={"tenant_id": self.tenant_id, "promote": promote},
                )
                response.raise_for_status()
            await self.refresh_activity()
        except httpx.HTTPError:
            return

    async def _download_artifact(self) -> None:
        if not self.latest_artifact_id:
            return
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=3, headers=self._user_headers()
            ) as client:
                response = await client.post(
                    f"/api/v1/artifacts/{self.latest_artifact_id}/download"
                )
                response.raise_for_status()
            payload = response.json()
            self.query_one("#conversation-log", RichLog).write(
                f"[bold #86efac]Artifact URL ({payload['expires_in_seconds']}s)[/]: "
                f"{payload['download_url']}"
            )
        except (httpx.HTTPError, KeyError, ValueError) as error:
            self.query_one("#artifact-list", Static).update(f"Download failed: {error}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        content = event.value.strip()
        if not content or not self.tenant_id:
            return
        event.input.value = ""
        log = self.query_one("#conversation-log", RichLog)
        log.write(f"[bold #93c5fd]You[/]: {content}")
        self.run_worker(self._submit_message(content), exclusive=False)

    async def _submit_message(self, content: str) -> None:
        log = self.query_one("#conversation-log", RichLog)
        live = self.query_one("#live-response", Static)
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=65, headers=self._user_headers()
            ) as client:
                if not self.conversation_id:
                    response = await client.post(
                        "/api/v1/conversations",
                        json={"tenant_id": self.tenant_id, "title": content[:80]},
                    )
                    response.raise_for_status()
                    self.conversation_id = response.json()["conversation"]["id"]
                reasoning, answer = "", ""
                async with client.stream(
                    "POST",
                    f"/api/v1/conversations/{self.conversation_id}/messages/stream",
                    json={
                        "tenant_id": self.tenant_id,
                        "client_request_id": str(uuid4()),
                        "content": content,
                    },
                ) as response:
                    response.raise_for_status()
                    event = ""
                    async for line in response.aiter_lines():
                        if line.startswith("event: "):
                            event = line[7:]
                        elif line.startswith("data: ") and event in {"reasoning", "content"}:
                            payload = json.loads(line[6:])
                            chunk = payload["content"]
                            if event == "reasoning":
                                reasoning += chunk
                            else:
                                answer += chunk
                            display = ""
                            if reasoning:
                                display += f"Thinking\n{reasoning}\n\n"
                            display += f"Astra\n{answer}"
                            live.update(Text(display))
                        elif line.startswith("data: ") and event == "tool_call":
                            tool = json.loads(line[6:]).get("tool", "tool")
                            live.update(Text(f"Astra\nUsing {tool}..."))
            if answer:
                log.write(f"[bold #86efac]Astra[/]: {answer}")
            live.update("")
        except (httpx.HTTPError, KeyError, ValueError) as error:
            log.write(f"[bold #fca5a5]Error[/]: {error}")

    def _user_headers(self) -> dict[str, str]:
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        if self.tenant_id and self.user_id:
            return {
                "X-Astra-Tenant-ID": self.tenant_id,
                "X-Astra-User-ID": self.user_id,
            }
        return {}


def run() -> None:
    AstraAgentApp().run()
