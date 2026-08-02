import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, NoReturn, Protocol
from uuid import UUID

from astra_domain import (
    ActorType,
    Approval,
    ApprovalState,
    AuditEvent,
    Conversation,
    ConversationMessage,
    ConversationTurn,
    ConversationTurnState,
    EventType,
    MemoryRecord,
    MemoryState,
    RemoteAgent,
    Task,
    TaskState,
)
from astra_memory import (
    ContextCompiler,
    DeterministicMemoryExtractor,
    MemoryContextCompiler,
    MemoryExtractor,
    MemoryPipeline,
    ModelBackedMemoryExtractor,
    NullVectorIndex,
    QdrantVectorIndex,
    VectorIndex,
    deterministic_embedding,
)
from astra_model_providers import (
    DevelopmentModelProvider,
    MainModelProvider,
    ModelProviderError,
    OpenAICompatibleLocalModelProvider,
    OpenRouterModelProvider,
)
from astra_protocol import (
    ApprovalDecisionRequest,
    ApprovalRequest,
    ApprovalResponse,
    ARAEventRequest,
    ArtifactUploadRequest,
    ArtifactUploadResponse,
    CancelTaskRequest,
    CompleteTaskRequest,
    ConversationResponse,
    ConversationTurnQueryResponse,
    ConversationTurnResponse,
    ConversationTurnsResponse,
    CreateConversationRequest,
    LeaseRequest,
    LeaseResponse,
    MemoryExplanationResponse,
    MemoryListResponse,
    MemoryReviewRequest,
    RegisterARARequest,
    RenewLeaseRequest,
    SendMessageRequest,
    TaskLifecycleResponse,
)
from astra_runtime import (
    InMemoryRuntimeStore,
    LifecycleConflictError,
    LifecycleNotFoundError,
    MariaDBRuntimeStore,
    RuntimeStore,
    create_schema,
)
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import create_async_engine

from astra_agent.artifacts import ArtifactStore, S3ArtifactStore
from astra_agent.auth import (
    ARAPrincipal,
    DevelopmentUserAuthenticator,
    OIDCUserAuthenticator,
    UserAuthenticator,
    UserPrincipal,
    development_ara_principal,
    trusted_mtls_ara_principal,
)
from astra_agent.conversations import ConversationOrchestrator
from astra_agent.settings import Settings
from astra_agent.tools import LocalToolRegistry

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    version: str
    persistence: str


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: Task


class ARAMessage(Protocol):
    @property
    def tenant_id(self) -> UUID: ...

    @property
    def ara_id(self) -> UUID: ...


def _assistant_message_for_turn(
    messages: tuple[ConversationMessage, ...], turn: ConversationTurn
) -> ConversationMessage | None:
    try:
        user_index = next(
            index for index, item in enumerate(messages) if item.id == turn.user_message_id
        )
    except StopIteration:
        return None
    for item in messages[user_index + 1 :]:
        if item.role.value == "user":
            return None
        if item.role.value == "assistant":
            return item
    return None


def _store(request: Request) -> RuntimeStore:
    return request.app.state.store  # type: ignore[no-any-return]


def _principal_dependency(settings: Settings) -> Callable[..., object]:
    if settings.auth_backend == "mtls":
        return trusted_mtls_ara_principal
    return development_ara_principal


async def _user_principal(request: Request) -> UserPrincipal:
    authenticator: UserAuthenticator = request.app.state.user_authenticator
    return await authenticator.authenticate(request)


def create_app(
    settings: Settings | None = None,
    store: RuntimeStore | None = None,
    artifact_store: ArtifactStore | None = None,
    user_authenticator: UserAuthenticator | None = None,
    model_provider: MainModelProvider | None = None,
    context_compiler: ContextCompiler | None = None,
    vector_index: VectorIndex | None = None,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime_store = store
        if runtime_store is None and settings.persistence_backend == "mariadb":
            if not settings.database_url:
                raise RuntimeError("ASTRA_DATABASE_URL is required for the MariaDB backend")
            engine = create_async_engine(settings.database_url, pool_pre_ping=True)
            if settings.create_schema_on_startup:
                await create_schema(engine)
            runtime_store = MariaDBRuntimeStore(engine)
        app.state.store = runtime_store or InMemoryRuntimeStore()
        app.state.settings = settings
        app.state.artifact_store = artifact_store
        if app.state.artifact_store is None and settings.artifact_bucket:
            app.state.artifact_store = S3ArtifactStore(settings)
        app.state.user_authenticator = user_authenticator
        if app.state.user_authenticator is None:
            app.state.user_authenticator = (
                OIDCUserAuthenticator(settings)
                if settings.user_auth_backend == "oidc"
                else DevelopmentUserAuthenticator()
            )
        app.state.model_provider = model_provider
        if app.state.model_provider is None:
            if settings.model_backend == "openrouter":
                if not settings.openrouter_api_key:
                    raise RuntimeError("ASTRA_OPENROUTER_API_KEY is required")
                app.state.model_provider = OpenRouterModelProvider(
                    settings.openrouter_api_key,
                    settings.openrouter_model,
                    base_url=settings.openrouter_base_url,
                )
            else:
                app.state.model_provider = DevelopmentModelProvider()
        app.state.vector_index = vector_index
        if app.state.vector_index is None:
            app.state.vector_index = (
                QdrantVectorIndex(
                    settings.qdrant_url,
                    settings.qdrant_collection,
                    settings.qdrant_api_key,
                )
                if settings.memory_backend == "qdrant"
                else NullVectorIndex()
            )
        app.state.local_model_provider = None
        extractor: MemoryExtractor = DeterministicMemoryExtractor()
        if settings.memory_extractor_backend == "local_model":
            app.state.local_model_provider = OpenAICompatibleLocalModelProvider(
                settings.local_model_url,
                settings.local_model_name,
                settings.local_model_api_key,
            )
            extractor = ModelBackedMemoryExtractor(app.state.local_model_provider)
        app.state.memory_pipeline = MemoryPipeline(
            app.state.store,
            extractor,
            app.state.vector_index,
        )
        app.state.context_compiler = context_compiler or MemoryContextCompiler(
            app.state.store,
            app.state.vector_index,
            settings.persona_kernel,
            settings.persona_max_tokens,
            settings.memory_max_records,
        )
        app.state.tool_registry = LocalToolRegistry(settings)
        app.state.orchestrator = ConversationOrchestrator(
            app.state.store,
            app.state.context_compiler,
            app.state.model_provider,
            settings.conversation_history_messages,
            app.state.memory_pipeline,
            settings.delegation_wait_seconds,
            settings.delegation_poll_seconds,
            settings.delegation_enabled,
            app.state.tool_registry,
        )
        runner_tasks: dict[UUID, asyncio.Task[None]] = {}

        async def run_pending_turns(tenant_id: UUID) -> None:
            try:
                while await app.state.orchestrator.advance_one(tenant_id) is not None:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("conversation turn runner failed for tenant %s", tenant_id)
            finally:
                task = asyncio.current_task()
                if task is not None and runner_tasks.get(tenant_id) is task:
                    del runner_tasks[tenant_id]

        def advance_turn(tenant_id: UUID) -> asyncio.Task[None]:
            task = runner_tasks.get(tenant_id)
            if task is None or task.done():
                task = asyncio.create_task(
                    run_pending_turns(tenant_id), name=f"conversation-turns-{tenant_id}"
                )
                runner_tasks[tenant_id] = task
            return task

        app.state.advance_turn = advance_turn
        await app.state.vector_index.ensure_ready()
        yield
        tasks = tuple(runner_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await app.state.vector_index.close()
        if app.state.local_model_provider is not None:
            await app.state.local_model_provider.close()
        await app.state.model_provider.close()
        await app.state.user_authenticator.close()
        await app.state.store.close()

    app = FastAPI(title="Astra Agent", version="0.1.0", lifespan=lifespan)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok", version=app.version, persistence=settings.persistence_backend
        )

    api = APIRouter(prefix="/api/v1")
    ara_api = APIRouter(prefix="/aras", tags=["aras"])
    principal_dependency = _principal_dependency(settings)

    def raise_lifecycle_error(error: Exception) -> NoReturn:
        if isinstance(error, LifecycleNotFoundError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error

    def verify_ara_message(body: ARAMessage, principal: ARAPrincipal) -> None:
        if body.tenant_id != principal.tenant_id or body.ara_id != principal.ara_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "ARA identity mismatch")

    @ara_api.post("/register", response_model=RemoteAgent, status_code=status.HTTP_201_CREATED)
    async def register_ara(
        body: RegisterARARequest,
        principal: Annotated[ARAPrincipal, Depends(principal_dependency)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> RemoteAgent:
        if body.tenant_id != principal.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant identity mismatch")
        ara = RemoteAgent(id=principal.ara_id, **body.model_dump())
        event = AuditEvent(
            tenant_id=body.tenant_id,
            event_type=EventType.ARA_REGISTERED,
            actor_type=ActorType.ARA,
            actor_id=ara.id,
            payload={"ara_name": ara.name},
        )
        return await runtime_store.register_ara(ara, event)

    @ara_api.post(
        "/lease",
        response_model=LeaseResponse,
        responses={204: {"description": "No eligible task available"}},
    )
    async def lease_task(
        body: LeaseRequest,
        principal: Annotated[ARAPrincipal, Depends(principal_dependency)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> LeaseResponse | Response:
        if body.tenant_id != principal.tenant_id or body.ara_id != principal.ara_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "ARA identity mismatch")
        expires_at = datetime.now(UTC) + timedelta(seconds=body.duration_seconds)
        leased = await runtime_store.lease_task(body.tenant_id, body.ara_id, expires_at)
        if leased is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        task, lease = leased
        return LeaseResponse(task=task, lease=lease)

    @ara_api.post("/events", status_code=status.HTTP_202_ACCEPTED)
    async def ara_event(
        body: ARAEventRequest,
        principal: Annotated[ARAPrincipal, Depends(principal_dependency)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> None:
        verify_ara_message(body, principal)
        try:
            await runtime_store.record_progress(
                body.tenant_id,
                body.ara_id,
                body.task_id,
                body.lease_id,
                body.message,
                body.progress_percent,
            )
        except (LifecycleNotFoundError, LifecycleConflictError) as error:
            raise_lifecycle_error(error)

    @ara_api.post("/renew", response_model=LeaseResponse)
    async def renew_lease(
        body: RenewLeaseRequest,
        principal: Annotated[ARAPrincipal, Depends(principal_dependency)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> LeaseResponse:
        verify_ara_message(body, principal)
        expires_at = datetime.now(UTC) + timedelta(seconds=body.duration_seconds)
        try:
            lease = await runtime_store.renew_lease(
                body.tenant_id, body.ara_id, body.task_id, body.lease_id, expires_at
            )
            tasks = await runtime_store.list_tasks(body.tenant_id)
            task = next(item for item in tasks if item.id == body.task_id)
            return LeaseResponse(task=task, lease=lease)
        except (LifecycleNotFoundError, LifecycleConflictError) as error:
            raise_lifecycle_error(error)

    async def finish(
        body: CompleteTaskRequest | CancelTaskRequest,
        principal: ARAPrincipal,
        runtime_store: RuntimeStore,
        task_state: TaskState,
        detail: str,
        artifact_store: ArtifactStore | None = None,
    ) -> TaskLifecycleResponse:
        verify_ara_message(body, principal)
        artifacts = body.artifacts if isinstance(body, CompleteTaskRequest) else ()
        if artifacts:
            if artifact_store is None:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "artifact storage unavailable"
                )
            try:
                for artifact in artifacts:
                    artifact_store.verify(artifact)
            except ValueError as error:
                raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        try:
            task = await runtime_store.finish_task(
                body.tenant_id,
                body.ara_id,
                body.task_id,
                body.lease_id,
                task_state,
                detail,
                artifacts,
            )
            return TaskLifecycleResponse(task_id=task.id, state=task.state)
        except (LifecycleNotFoundError, LifecycleConflictError) as error:
            raise_lifecycle_error(error)

    @ara_api.post("/complete", response_model=TaskLifecycleResponse)
    async def complete_task(
        body: CompleteTaskRequest,
        principal: Annotated[ARAPrincipal, Depends(principal_dependency)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
        request: Request,
    ) -> TaskLifecycleResponse:
        return await finish(
            body,
            principal,
            runtime_store,
            TaskState.COMPLETED,
            body.result,
            request.app.state.artifact_store,
        )

    @ara_api.post("/cancel", response_model=TaskLifecycleResponse)
    async def cancel_task(
        body: CancelTaskRequest,
        principal: Annotated[ARAPrincipal, Depends(principal_dependency)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> TaskLifecycleResponse:
        return await finish(body, principal, runtime_store, TaskState.CANCELLED, body.reason)

    @ara_api.post("/approvals", response_model=ApprovalResponse, status_code=201)
    async def request_approval(
        body: ApprovalRequest,
        principal: Annotated[ARAPrincipal, Depends(principal_dependency)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> ApprovalResponse:
        verify_ara_message(body, principal)
        approval = Approval(
            tenant_id=body.tenant_id,
            task_id=body.task_id,
            capability=body.capability,
            requested_by=body.ara_id,
            reason=body.reason,
        )
        try:
            created = await runtime_store.request_approval(
                body.tenant_id, body.ara_id, body.task_id, body.lease_id, approval
            )
            return ApprovalResponse(approval=created)
        except (LifecycleNotFoundError, LifecycleConflictError) as error:
            raise_lifecycle_error(error)

    @ara_api.post("/artifacts/upload", response_model=ArtifactUploadResponse)
    async def prepare_artifact_upload(
        body: ArtifactUploadRequest,
        principal: Annotated[ARAPrincipal, Depends(principal_dependency)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
        request: Request,
    ) -> ArtifactUploadResponse:
        verify_ara_message(body, principal)
        target_store: ArtifactStore | None = request.app.state.artifact_store
        if target_store is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "artifact storage unavailable")
        try:
            await runtime_store.record_progress(
                body.tenant_id,
                body.ara_id,
                body.task_id,
                body.lease_id,
                f"Preparing artifact upload: {body.name}",
                None,
            )
        except (LifecycleNotFoundError, LifecycleConflictError) as error:
            raise_lifecycle_error(error)
        target = target_store.prepare_upload(
            body.tenant_id, body.task_id, body.name, body.media_type, body.sha256
        )
        return ArtifactUploadResponse(
            object_key=target.object_key,
            upload_url=target.upload_url,
            expires_in_seconds=target.expires_in_seconds,
        )

    @api.post("/tasks", response_model=Task, status_code=status.HTTP_201_CREATED, tags=["tasks"])
    async def create_task(
        body: TaskCreateRequest,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> Task:
        if body.task.tenant_id != principal.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant identity mismatch")
        event = AuditEvent(
            tenant_id=body.task.tenant_id,
            event_type=EventType.TASK_CREATED,
            actor_type=ActorType.USER,
            actor_id=principal.user_id,
            task_id=body.task.id,
            payload={"subject": principal.subject},
        )
        return await runtime_store.add_task(body.task, event)

    @api.post(
        "/conversations",
        response_model=ConversationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["conversations"],
    )
    async def create_conversation(
        body: CreateConversationRequest,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> ConversationResponse:
        if body.tenant_id != principal.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant identity mismatch")
        conversation = Conversation(
            tenant_id=body.tenant_id, user_id=principal.user_id, title=body.title
        )
        event = AuditEvent(
            tenant_id=body.tenant_id,
            event_type=EventType.CONVERSATION_CREATED,
            actor_type=ActorType.USER,
            actor_id=principal.user_id,
            payload={"conversation_id": str(conversation.id)},
        )
        await runtime_store.create_conversation(conversation, event)
        return ConversationResponse(conversation=conversation)

    @api.get(
        "/tenants/{tenant_id}/conversations",
        response_model=list[Conversation],
        tags=["conversations"],
    )
    async def list_conversations(
        tenant_id: UUID,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> tuple[Conversation, ...]:
        if tenant_id != principal.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant identity mismatch")
        return await runtime_store.list_conversations(tenant_id)

    @api.get(
        "/conversations/{conversation_id}",
        response_model=ConversationResponse,
        tags=["conversations"],
    )
    async def get_conversation(
        conversation_id: UUID,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> ConversationResponse:
        conversation = await runtime_store.get_conversation(principal.tenant_id, conversation_id)
        if conversation is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
        messages = await runtime_store.list_messages(principal.tenant_id, conversation_id)
        return ConversationResponse(conversation=conversation, messages=messages)

    @api.post(
        "/conversations/{conversation_id}/messages",
        response_model=ConversationTurnResponse,
        tags=["conversations"],
    )
    async def send_message(
        conversation_id: UUID,
        body: SendMessageRequest,
        request: Request,
        response: Response,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> ConversationTurnResponse:
        if body.tenant_id != principal.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant identity mismatch")
        conversation = await runtime_store.get_conversation(body.tenant_id, conversation_id)
        if conversation is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
        try:
            user_message, turn = await request.app.state.orchestrator.start_turn(
                body.tenant_id,
                principal.user_id,
                conversation_id,
                body.client_request_id,
                body.content,
            )
            await request.app.state.orchestrator.advance_one(body.tenant_id)
        except ModelProviderError as error:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "model provider failed") from error
        refreshed = await runtime_store.get_turn(body.tenant_id, turn.id)
        if refreshed is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation turn not found")
        messages = await runtime_store.list_messages(body.tenant_id, conversation_id)
        assistant_message = _assistant_message_for_turn(messages, refreshed)
        if refreshed.state is not ConversationTurnState.COMPLETED:
            response.status_code = status.HTTP_202_ACCEPTED
        request.app.state.advance_turn(body.tenant_id)
        return ConversationTurnResponse(
            turn=refreshed, user_message=user_message, assistant_message=assistant_message
        )

    @api.get(
        "/conversations/{conversation_id}/turns",
        response_model=ConversationTurnsResponse,
        tags=["conversations"],
    )
    async def list_conversation_turns(
        conversation_id: UUID,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> ConversationTurnsResponse:
        conversation = await runtime_store.get_conversation(principal.tenant_id, conversation_id)
        if conversation is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
        return ConversationTurnsResponse(
            turns=await runtime_store.list_conversation_turns(principal.tenant_id, conversation_id)
        )

    @api.get(
        "/conversation-turns/{turn_id}",
        response_model=ConversationTurnQueryResponse,
        tags=["conversations"],
    )
    async def get_conversation_turn(
        turn_id: UUID,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> ConversationTurnQueryResponse:
        turn = await runtime_store.get_turn(principal.tenant_id, turn_id)
        if turn is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation turn not found")
        messages = await runtime_store.list_messages(principal.tenant_id, turn.conversation_id)
        user_message = next((item for item in messages if item.id == turn.user_message_id), None)
        if user_message is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "turn user message not found")
        return ConversationTurnQueryResponse(
            turn=turn,
            user_message=user_message,
            assistant_message=_assistant_message_for_turn(messages, turn),
        )

    @api.get("/tenants/{tenant_id}/events", response_model=list[AuditEvent], tags=["audit"])
    async def list_events(
        tenant_id: UUID,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> tuple[AuditEvent, ...]:
        if tenant_id != principal.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant identity mismatch")
        return await runtime_store.list_events(tenant_id)

    @api.get(
        "/tenants/{tenant_id}/memories",
        response_model=MemoryListResponse,
        tags=["memory"],
    )
    async def list_memories(
        tenant_id: UUID,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> MemoryListResponse:
        if tenant_id != principal.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant identity mismatch")
        return MemoryListResponse(
            memories=await runtime_store.list_memories(tenant_id, include_candidates=True)
        )

    @api.get(
        "/memories/{memory_id}",
        response_model=MemoryExplanationResponse,
        tags=["memory"],
    )
    async def explain_memory(
        memory_id: UUID,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> MemoryExplanationResponse:
        memory = await runtime_store.get_memory(principal.tenant_id, memory_id)
        if memory is None or memory.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "memory not found")
        explanation = (
            f"Extracted from message {memory.source_message_id} as {memory.kind.value}; "
            f"confidence {memory.confidence:.2f}, state {memory.state.value}."
        )
        return MemoryExplanationResponse(memory=memory, explanation=explanation)

    @api.delete(
        "/memories/{memory_id}",
        response_model=MemoryRecord,
        tags=["memory"],
    )
    async def delete_memory(
        memory_id: UUID,
        request: Request,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> MemoryRecord:
        try:
            deleted = await runtime_store.delete_memory(
                principal.tenant_id, memory_id, principal.user_id
            )
        except (LifecycleNotFoundError, LifecycleConflictError) as error:
            raise_lifecycle_error(error)
        try:
            await request.app.state.vector_index.delete(memory_id)
        except Exception:
            pass
        return deleted

    @api.post(
        "/memories/{memory_id}/review",
        response_model=MemoryRecord,
        tags=["memory"],
    )
    async def review_memory(
        memory_id: UUID,
        body: MemoryReviewRequest,
        request: Request,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> MemoryRecord:
        if body.tenant_id != principal.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant identity mismatch")
        try:
            reviewed = await runtime_store.review_memory(
                body.tenant_id,
                memory_id,
                body.promote,
                principal.user_id,
                body.replaces_memory_id,
            )
        except (LifecycleNotFoundError, LifecycleConflictError) as error:
            raise_lifecycle_error(error)
        if reviewed.state is MemoryState.PROMOTED:
            try:
                await request.app.state.vector_index.upsert(
                    reviewed, deterministic_embedding(reviewed.content)
                )
                if body.replaces_memory_id is not None:
                    await request.app.state.vector_index.delete(body.replaces_memory_id)
            except Exception:
                pass
        return reviewed

    @api.get("/tenants/{tenant_id}/tasks", response_model=list[Task], tags=["tasks"])
    async def list_tasks(
        tenant_id: UUID,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> tuple[Task, ...]:
        if tenant_id != principal.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant identity mismatch")
        return await runtime_store.list_tasks(tenant_id)

    @api.get("/tenants/{tenant_id}/approvals", response_model=list[Approval], tags=["approvals"])
    async def list_approvals(
        tenant_id: UUID,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> tuple[Approval, ...]:
        if tenant_id != principal.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant identity mismatch")
        return await runtime_store.list_approvals(tenant_id)

    @api.post(
        "/approvals/{approval_id}/decision", response_model=ApprovalResponse, tags=["approvals"]
    )
    async def decide_approval(
        approval_id: UUID,
        body: ApprovalDecisionRequest,
        request: Request,
        principal: Annotated[UserPrincipal, Depends(_user_principal)],
        runtime_store: Annotated[RuntimeStore, Depends(_store)],
    ) -> ApprovalResponse:
        if body.tenant_id != principal.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant identity mismatch")
        state = ApprovalState.GRANTED if body.granted else ApprovalState.DENIED
        try:
            approval = await runtime_store.decide_approval(
                body.tenant_id, approval_id, state, principal.user_id
            )
            if approval.tool_invocation_id is not None:
                request.app.state.advance_turn(body.tenant_id)
            return ApprovalResponse(approval=approval)
        except (LifecycleNotFoundError, LifecycleConflictError) as error:
            raise_lifecycle_error(error)

    api.include_router(ara_api)
    app.include_router(api)
    return app
