import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias

from fastapi import Body, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from guideos.assessment.contract import (
    AssessmentContractError,
    AssessmentContractResponse,
    AssessmentErrorCode,
    ContractAssessmentResult,
    ContractReport,
    _contract_result,
    to_contract_response,
)
from guideos.assessment.factory import (
    assessment_repository_from_env,
    assessment_storage_path_from_env,
    build_assessment,
)
from guideos.assessment.orchestrator import (
    AssessmentOrchestrator,
    AssessmentWorkflowError,
)
from guideos.assessment.repository import (
    AssessmentRepository,
    AssessmentRepositoryError,
    ParticipantAssessmentRepository,
)
from guideos.assessment.service import handle_assessment_payload
from guideos.recommendations.llm import LLMRecommendation
from guideos.recommendations.service import (
    generate_profile_recommendations,
    RecommendationServiceError,
    generate_llm_recommendations,
)
from guideos.config import DEFAULT_OLLAMA_MODEL, DEFAULT_PROVIDER, configure_logging
from guideos.conversations.factory import (
    conversation_repository_from_env,
    conversation_storage_path_from_env,
)
from guideos.conversations.models import Conversation
from guideos.conversations.repository import (
    ConversationRepository,
    ConversationRepositoryError,
    ParticipantConversationRepository,
)
from guideos.conversations.service import ConversationError, ConversationService
from guideos.domains.catalog import (
    DomainCatalogError,
    DomainPackRef,
    list_active_domain_versions,
    load_active_domain_pack,
    load_domain_pack_version,
)
from guideos.domains.loader import DomainPackError
from guideos.domains.models import DomainPack, QuestionDefinition
from guideos.knowledge.factory import knowledge_service_from_env
from guideos.pilot.access import (
    LoginThrottle,
    SESSION_COOKIE,
    local_participant_id,
    SESSION_MAX_AGE,
    normalize_participant_name,
    password_matches,
    pilot_mode_enabled,
    session_participant,
    sign_session,
)
from guideos.profiles.factory import profile_repository_from_env, profile_storage_path_from_env
from guideos.profiles.models import Profile
from guideos.profiles.repository import (
    ParticipantProfileRepository,
    ProfileRepository,
    ProfileRepositoryError,
)
from guideos.tools.factory import tool_service_from_env
from guideos.tools.models import DatasetRef, ToolError, ToolResult
from guideos.profiles.service import (
    ProfileService,
    ProfileServiceError,
    delete_profile_data,
    delete_session_data,
)
from guideos.providers.base import LLMProvider, ProviderError
from guideos.reports.renderer import render_profile_report
from guideos.domains.models import display_value
from guideos.providers.registry import get_provider


logger = logging.getLogger(__name__)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DomainResponse(ApiModel):
    domain_id: str
    version: str
    name: str | None = None
    source: Literal["bundled", "local"]


class StartSessionRequest(ApiModel):
    domain_id: str
    locale: str | None = None


class SubmitAnswerRequest(ApiModel):
    kind: Literal["submit_answer"]
    question_id: str
    value: Any
    raw_answer: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class SubmitTextAnswerRequest(ApiModel):
    kind: Literal["submit_text_answer"]
    question_id: str
    text: str


class ReviseAnswerRequest(ApiModel):
    kind: Literal["revise_answer"]
    question_id: str
    value: Any
    raw_answer: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class CompleteRequest(ApiModel):
    kind: Literal["complete"]
    # Given together, the first knowledge recommendations are generated at
    # completion and persisted; omitted, completion stays deterministic.
    endpoint: str | None = None
    model: str | None = None
    # True generates them with the configured default provider instead, so
    # callers (the test-runner suite) need not know endpoint and model.
    knowledge_recommendations: bool = False


class AcceptProposedAnswerRequest(ApiModel):
    kind: Literal["accept_proposed_answer"]
    message_id: str


class DiscardProposedAnswerRequest(ApiModel):
    kind: Literal["discard_proposed_answer"]
    message_id: str


V1InputRequest: TypeAlias = (
    SubmitAnswerRequest
    | SubmitTextAnswerRequest
    | ReviseAnswerRequest
    | CompleteRequest
    | AcceptProposedAnswerRequest
    | DiscardProposedAnswerRequest
)

V1InputRequestAdapter = TypeAdapter(
    Annotated[V1InputRequest, Field(discriminator="kind")]
)


class QuestionHelpChatRequest(ApiModel):
    scope: Literal["question_help"]
    question_id: str
    text: str


V1ChatRequest: TypeAlias = QuestionHelpChatRequest


ConversationApiErrorCode = Literal[
    "invalid_input",
    "invalid_state",
    "required",
    "conversation_not_found",
    "profile_not_found",
    "profile_unavailable",
    "domain_version_unavailable",
    "provider_error",
    "knowledge_unavailable",
]


LLMRecommendationsErrorCode = Literal[
    "session_not_found",
    "invalid_state",
    "domain_version_unavailable",
    "provider_error",
    "knowledge_unavailable",
]


class LLMRecommendationsError(ApiModel):
    code: LLMRecommendationsErrorCode
    message: str


class LLMRecommendationsResponse(ApiModel):
    ok: bool
    session_id: str
    recommendations: list[LLMRecommendation] = Field(default_factory=list)
    errors: list[LLMRecommendationsError] = Field(default_factory=list)


ProfileRecommendationsErrorCode = Literal[
    "profile_not_found",
    "domain_version_unavailable",
    "provider_error",
    "knowledge_unavailable",
]


class ProfileRecommendationsError(ApiModel):
    code: ProfileRecommendationsErrorCode
    message: str


class ProfileRecommendationsResponse(ApiModel):
    """Knowledge recommendations regenerated on request for the
    participant's profile of a domain and persisted on it."""

    ok: bool
    domain_id: str
    recommendations: list[LLMRecommendation] = Field(default_factory=list)
    errors: list[ProfileRecommendationsError] = Field(default_factory=list)


class CreateConversationRequest(ApiModel):
    domain_id: str
    title: str | None = None


class ConversationMessageRequest(ApiModel):
    text: str


class SessionDeleteError(ApiModel):
    code: Literal["session_not_found", "session_conflict"]
    message: str


class SessionDeleteResponse(ApiModel):
    ok: bool
    session_id: str
    errors: list[SessionDeleteError] = Field(default_factory=list)


class ResultConversationRequest(ApiModel):
    """The "Ergebnis" chat of the participant's profile in a domain: created
    with intro and recommendations on the first call, extended when a newer
    assessment replaced the profile, otherwise returned unchanged."""

    domain_id: str


class ConversationContractError(ApiModel):
    code: ConversationApiErrorCode
    message: str


class ConversationContractResponse(ApiModel):
    ok: bool
    conversation: Conversation | None = None
    conversations: list[Conversation] = Field(default_factory=list)
    errors: list[ConversationContractError] = Field(default_factory=list)


PARTICIPANT_HEADER = "X-GuideOS-Participant"
MODEL_HEADER = "X-GuideOS-Model"


def _pilot_mode() -> bool:
    return pilot_mode_enabled(os.environ.get("GUIDEOS_PILOT_MODE"))


def _model_selection_enabled() -> bool:
    """GUIDEOS_MODEL_SELECTION=1 lets clients pick the model of an LLM call
    through the header X-GuideOS-Model (test phases only; read per request)."""
    return os.environ.get("GUIDEOS_MODEL_SELECTION", "").strip().lower() in {"1", "true", "yes"}


def _session_key() -> str:
    """Key of the session signature: GUIDEOS_SESSION_SECRET, or the shared
    test password (whoever knows it can log in as any name anyway)."""
    return os.environ.get("GUIDEOS_SESSION_SECRET") or os.environ.get("GUIDEOS_TEST_PASSWORD") or ""


ParticipantHeader = Annotated[str | None, Header(alias=PARTICIPANT_HEADER)]
ModelHeader = Annotated[str | None, Header(alias=MODEL_HEADER)]

# Routes that need no login in pilot mode: the page itself, health, domains
# and the login routes.
PUBLIC_API_PATHS = {"/api/v1/health", "/api/v1/session", "/api/v1/login", "/api/v1/logout", "/api/v1/domains"}


def install_pilot_login(app: FastAPI) -> None:
    """Pilot mode (GUIDEOS_PILOT_MODE=1): every /api/v1 request except the
    public paths needs the signed session cookie of the login; the
    participant header is then set by GuideOS from the cookie and a value
    sent by the client is discarded. Without a valid cookie: 401."""

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/v1") and path not in PUBLIC_API_PATHS:
            participant = session_participant(request.cookies.get(SESSION_COOKIE), _session_key())
            if participant is None:
                return JSONResponse(status_code=401, content={"detail": "Anmeldung erforderlich."})
            header = PARTICIPANT_HEADER.lower().encode()
            request.scope["headers"] = [
                (name, value) for name, value in request.scope["headers"] if name != header
            ] + [(header, participant.encode())]
        return await call_next(request)


class LoginRequest(ApiModel):
    username: str
    password: str


class LoginError(ApiModel):
    code: Literal["invalid_input", "invalid_credentials", "invalid_state", "too_many_attempts"]
    message: str


# Five failed logins per source lock it for five minutes (per process).
LOGIN_THROTTLE = LoginThrottle()


def _login_source(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class LoginResponse(ApiModel):
    ok: bool
    participant_id: str | None = None
    errors: list[LoginError] = Field(default_factory=list)


class SessionResponse(ApiModel):
    ok: bool = True
    pilot_mode: bool
    participant_id: str | None = None


class ModelSelectionResponse(ApiModel):
    """Whether clients may choose the model per request, and what they may choose from."""

    enabled: bool
    default_model: str
    models: list[str] = Field(default_factory=list)


class ParticipantRequiredError(Exception):
    """Raised when PostgreSQL is configured and a request carries no participant."""


@dataclass
class RequestScope:
    """Repositories for one request, participant-scoped when a header is present.

    This is request scoping, not authentication: the header is trusted as-is.
    """

    assessments: AssessmentRepository
    conversations: ConversationRepository
    profiles: ProfileRepository
    participant_id: str | None


ProfileApiErrorCode = Literal[
    "profile_not_found", "domain_version_unavailable", "invalid_state", "invalid_input", "tool_error",
    "profile_unavailable",
]


class ProfileApiError(ApiModel):
    code: ProfileApiErrorCode
    message: str


class ProfileAnswerView(ApiModel):
    question_id: str
    normalized_value: Any
    display_value: str | None = None
    # The question text of the profile's pack version, for clients without the pack.
    label: str | None = None
    # measurement: derived from the dataset of the assessment (pack block tools.derive).
    origin: Literal["assessment", "chat_update", "measurement"]
    calculation_id: str | None = None
    updated_at: datetime


class ProfileFeedbackView(ApiModel):
    recommendation_id: str
    kind: Literal["rule", "llm"]
    title: str
    stance: Literal["accepted", "rejected"]
    origin: Literal["button", "chat"]
    created_at: datetime


class ProfileFeedbackRequest(ApiModel):
    stance: Literal["accepted", "rejected"]


ProfileFeedbackErrorCode = Literal[
    "invalid_input",
    "profile_not_found",
    "domain_version_unavailable",
    "invalid_recommendation",
]


class ProfileFeedbackError(ApiModel):
    code: ProfileFeedbackErrorCode
    message: str


class ProfileFeedbackResponse(ApiModel):
    ok: bool
    profile: "ProfileView | None" = None
    errors: list[ProfileFeedbackError] = Field(default_factory=list)


class ProfileView(ApiModel):
    profile_id: str
    domain_id: str
    domain_version: str
    domain_content_hash: str
    source_session_id: str
    revision: int
    answers: dict[str, ProfileAnswerView]
    result: ContractAssessmentResult
    knowledge_stale: bool = False
    feedback: dict[str, ProfileFeedbackView] = Field(default_factory=dict)
    dataset: DatasetRef | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)
    report: ContractReport | None = None
    created_at: datetime
    updated_at: datetime


class DatasetUploadRequest(ApiModel):
    """A measurement file for the participant's profile: the decoded text
    content (CSV), the file name without path and the format hint of the
    pack's dataset tool."""

    filename: str = Field(min_length=1)
    content: str = Field(min_length=1)
    format_hint: str = "auto"


class DatasetApiResponse(ApiModel):
    ok: bool
    domain_id: str
    dataset: DatasetRef | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)
    errors: list[ProfileApiError] = Field(default_factory=list)


SeriesApiErrorCode = Literal[
    "invalid_input", "profile_not_found", "no_dataset", "series_unavailable", "domain_version_unavailable",
    "tool_error",
]


class SeriesApiError(ApiModel):
    code: SeriesApiErrorCode
    message: str


class SeriesPointView(ApiModel):
    start: str
    value: str
    complete: bool | None = None


class SeriesApiResponse(ApiModel):
    """Day or quarter-hour series of the profile's dataset from the pack's
    read-only series tool (tools.series_tool), called with the participant
    as owner; the browser names neither."""

    ok: bool
    domain_id: str
    resolution: str | None = None
    unit: str | None = None
    period_from: str | None = None
    period_to: str | None = None
    points: list[SeriesPointView] = Field(default_factory=list)
    calculation_id: str | None = None
    errors: list[SeriesApiError] = Field(default_factory=list)


class ProfileFactRequest(ApiModel):
    field_key: str
    value: Any
    raw_answer: str | None = None


ProfileFactErrorCode = Literal[
    "invalid_input",
    "profile_not_found",
    "domain_version_unavailable",
    "invalid_question",
    "validation_failed",
]


class ProfileFactError(ApiModel):
    code: ProfileFactErrorCode
    message: str
    validation_code: str | None = None


class ProfileFactResponse(ApiModel):
    """Outcome of changing one answer of the profile: the saved profile,
    the previous value, the required questions still open and whether the
    rule result changed."""

    ok: bool
    profile: ProfileView | None = None
    changed_field: str | None = None
    previous_value: Any = None
    open_questions: list[QuestionDefinition] = Field(default_factory=list)
    result_changed: bool = False
    errors: list[ProfileFactError] = Field(default_factory=list)


class ProfileApiResponse(ApiModel):
    ok: bool
    profile: ProfileView | None = None
    profiles: list[ProfileView] = Field(default_factory=list)
    errors: list[ProfileApiError] = Field(default_factory=list)


ProfileFeedbackResponse.model_rebuild()


def create_app(
    assessment_path: Path | None = None,
    conversation_path: Path | None = None,
    knowledge_path: Path | None = None,
    profile_path: Path | None = None,
) -> FastAPI:
    configure_logging()
    if _pilot_mode() and not os.environ.get("GUIDEOS_TEST_PASSWORD"):
        raise RuntimeError("Der Pilotmodus der API braucht GUIDEOS_TEST_PASSWORD.")
    app = FastAPI(title="GuideOS API")
    if _pilot_mode():
        install_pilot_login(app)
    repository_path = assessment_path or assessment_storage_path_from_env()
    conversations_path = conversation_path or conversation_storage_path_from_env()
    profiles_path = profile_path or profile_storage_path_from_env()

    def scope_for(participant: str | None) -> RequestScope:
        return _request_scope(repository_path, conversations_path, profiles_path, participant)

    @app.exception_handler(ParticipantRequiredError)
    def participant_required(request, exc) -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content={
                "ok": False,
                "session_id": None,
                "errors": [
                    {
                        "code": "invalid_input",
                        "message": (
                            f"Der Header {PARTICIPANT_HEADER} ist erforderlich, "
                            "wenn PostgreSQL konfiguriert ist."
                        ),
                    }
                ],
            },
        )

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/domains", response_model=list[DomainResponse])
    def domains() -> list[DomainResponse]:
        return [_domain_response(ref) for ref in _active_domain_refs()]

    @app.get("/api/v1/health")
    def v1_health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/v1/session", response_model=SessionResponse)
    def v1_session(request: Request, header: Annotated[str | None, Header(alias=PARTICIPANT_HEADER)] = None) -> SessionResponse:
        """Who the API takes the caller for: in pilot mode the login, otherwise the header."""
        if _pilot_mode():
            return SessionResponse(pilot_mode=True,
                                   participant_id=session_participant(request.cookies.get(SESSION_COOKIE), _session_key()))
        return SessionResponse(pilot_mode=False, participant_id=header)

    @app.get("/api/v1/models", response_model=ModelSelectionResponse)
    def v1_models() -> ModelSelectionResponse:
        """Models of the configured endpoint, offered only with GUIDEOS_MODEL_SELECTION=1.
        While disabled the provider is not touched: the configured default model is enough."""
        if not _model_selection_enabled():
            return ModelSelectionResponse(enabled=False, default_model=DEFAULT_OLLAMA_MODEL)
        provider = _default_provider()
        return ModelSelectionResponse(
            enabled=True, default_model=provider.default_model, models=_available_models(provider)
        )

    @app.post("/api/v1/login", response_model=LoginResponse)
    def v1_login(request: Request, response: Response, payload: Any = Body(...)) -> LoginResponse:
        """Pilot login: user name + shared test password -> signed HttpOnly
        session cookie. The stripped user name is the participant key, as in
        the Streamlit pilot login."""
        if not _pilot_mode():
            return LoginResponse(ok=False, errors=[LoginError(code="invalid_state", message="Die API läuft ohne Pilot-Login.")])
        source = _login_source(request)
        if not LOGIN_THROTTLE.allowed(source):
            logger.warning("api_login_throttled")
            response.status_code = 429
            return LoginResponse(ok=False, errors=[LoginError(
                code="too_many_attempts",
                message="Zu viele Fehlversuche. Bitte warte ein paar Minuten und versuche es erneut.")])
        try:
            login = LoginRequest.model_validate(payload)
        except ValidationError as exc:
            return LoginResponse(ok=False, errors=[LoginError(code="invalid_input", message=str(exc))])
        participant = normalize_participant_name(login.username)
        if participant is None or not password_matches(login.password, os.environ.get("GUIDEOS_TEST_PASSWORD")):
            LOGIN_THROTTLE.record_failure(source)
            logger.info("api_login_rejected")
            return LoginResponse(ok=False, errors=[LoginError(
                code="invalid_credentials", message="Benutzername oder Testpasswort sind nicht korrekt.")])
        LOGIN_THROTTLE.record_success(source)
        response.set_cookie(
            SESSION_COOKIE, sign_session(participant, _session_key()), max_age=SESSION_MAX_AGE,
            httponly=True, samesite="lax", secure=os.environ.get("GUIDEOS_COOKIE_SECURE") == "1", path="/",
        )
        logger.info("api_login participant=%s", participant)
        return LoginResponse(ok=True, participant_id=participant)

    @app.post("/api/v1/logout")
    def v1_logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/v1/domains", response_model=list[DomainResponse])
    def v1_domains() -> list[DomainResponse]:
        return [_domain_response(ref) for ref in _active_domain_refs()]

    @app.post(
        "/api/v1/assessment-sessions",
        response_model=AssessmentContractResponse,
    )
    def v1_start_assessment_session(
        payload: Any = Body(...),
        participant: ParticipantHeader = None,
    ) -> AssessmentContractResponse:
        scope = scope_for(participant)
        try:
            request = StartSessionRequest.model_validate(payload)
        except ValidationError as exc:
            return _contract_error(None, "invalid_input", str(exc))

        _ensure_active_domain(request.domain_id)
        orchestrator = _orchestrator_for_active_domain(knowledge_path, request.domain_id, scope)
        return handle_assessment_payload(
            orchestrator,
            {"kind": "start", "locale": request.locale},
        )

    @app.get(
        "/api/v1/assessment-sessions/{session_id}",
        response_model=AssessmentContractResponse,
    )
    def v1_assessment_session(
        session_id: str,
        participant: ParticipantHeader = None,
    ) -> AssessmentContractResponse:
        orchestrator = _orchestrator_for_session(knowledge_path, session_id, scope_for(participant))
        if isinstance(orchestrator, AssessmentContractResponse):
            return orchestrator
        try:
            return to_contract_response(orchestrator.get(session_id))
        except AssessmentWorkflowError as exc:
            return _contract_error(session_id, exc.code, str(exc))

    @app.post(
        "/api/v1/assessment-sessions/{session_id}/inputs",
        response_model=AssessmentContractResponse,
    )
    def v1_assessment_input(
        session_id: str,
        payload: Any = Body(...),
        participant: ParticipantHeader = None,
        model: ModelHeader = None,
    ) -> AssessmentContractResponse:
        scope = scope_for(participant)
        request = _parse_v1_input(session_id, payload)
        if isinstance(request, AssessmentContractResponse):
            return request
        orchestrator = _orchestrator_for_session(knowledge_path, session_id, scope)
        if isinstance(orchestrator, AssessmentContractResponse):
            return orchestrator
        return handle_assessment_payload(
            orchestrator,
            _v1_input_payload(session_id, request, model),
        )

    @app.post(
        "/api/v1/assessment-sessions/{session_id}/chat",
        response_model=AssessmentContractResponse,
    )
    def v1_assessment_chat(
        session_id: str,
        payload: Any = Body(...),
        participant: ParticipantHeader = None,
        model: ModelHeader = None,
    ) -> AssessmentContractResponse:
        scope = scope_for(participant)
        request = _parse_v1_chat(session_id, payload)
        if isinstance(request, AssessmentContractResponse):
            return request
        orchestrator = _orchestrator_for_session(knowledge_path, session_id, scope)
        if isinstance(orchestrator, AssessmentContractResponse):
            return orchestrator
        return handle_assessment_payload(
            orchestrator,
            _v1_chat_payload(session_id, request, model),
        )

    @app.get(
        "/api/v1/assessment-sessions/{session_id}/report",
        response_model=AssessmentContractResponse,
    )
    def v1_assessment_report(
        session_id: str,
        participant: ParticipantHeader = None,
    ) -> AssessmentContractResponse:
        orchestrator = _orchestrator_for_session(knowledge_path, session_id, scope_for(participant))
        if isinstance(orchestrator, AssessmentContractResponse):
            return orchestrator
        try:
            response = to_contract_response(orchestrator.get(session_id))
        except AssessmentWorkflowError as exc:
            return _contract_error(session_id, exc.code, str(exc))
        if response.report is not None:
            return response
        return _contract_error(
            session_id,
            "invalid_state",
            "Für diese Assessment-Session ist noch kein Report verfügbar.",
        )

    @app.delete("/api/v1/assessment-sessions/{session_id}", response_model=SessionDeleteResponse)
    def v1_delete_assessment_session(
        session_id: str, participant: ParticipantHeader = None
    ) -> SessionDeleteResponse:
        """Delete one assessment session of the participant with the datasets
        its file answers reference (same path as the Streamlit button
        "Ausgewähltes Assessment löschen"); a profile that used them forgets
        them and its result chat is told."""
        scope = scope_for(participant)
        try:
            session = scope.assessments.get(session_id)
            deletion = delete_session_data(
                scope.assessments, scope.profiles, session_id, tools=tool_service_from_env()
            )
        except LookupError:
            return SessionDeleteResponse(
                ok=False, session_id=session_id,
                errors=[SessionDeleteError(code="session_not_found", message="Unbekannte Session.")],
            )
        except AssessmentRepositoryError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if deletion.profile_changed:
            try:
                _conversation_service(scope, knowledge_path).post_dataset_deleted(session.domain_id)
            except (ConversationError, ConversationRepositoryError, ProfileRepositoryError) as exc:
                logger.warning("dataset_deleted_notice_failed session_id=%s error=%s", session_id, exc)
        return SessionDeleteResponse(ok=True, session_id=session_id)

    @app.post(
        "/api/v1/assessment-sessions/{session_id}/llm-recommendations",
        response_model=LLMRecommendationsResponse,
    )
    def v1_llm_recommendations(
        session_id: str,
        participant: ParticipantHeader = None,
        model: ModelHeader = None,
    ) -> LLMRecommendationsResponse:
        provider = _default_provider()
        try:
            recommendations = generate_llm_recommendations(
                scope_for(participant).assessments,
                session_id,
                provider,
                provider.default_endpoint,
                _request_model(model, provider),
                knowledge=knowledge_service_from_env(provider, knowledge_path),
            )
        except LookupError as exc:
            return _llm_recommendations_error(session_id, "session_not_found", str(exc))
        except RecommendationServiceError as exc:
            return _llm_recommendations_error(session_id, exc.code, str(exc))
        except AssessmentRepositoryError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return LLMRecommendationsResponse(
            ok=True, session_id=session_id, recommendations=recommendations
        )

    @app.get("/api/v1/profiles", response_model=ProfileApiResponse)
    def v1_profiles(participant: ParticipantHeader = None) -> ProfileApiResponse:
        try:
            profiles = scope_for(participant).profiles.list()
        except ProfileRepositoryError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ProfileApiResponse(
            ok=True, profiles=[_profile_view(item, with_report=False) for item in profiles]
        )

    @app.get("/api/v1/profiles/{domain_id}", response_model=ProfileApiResponse)
    def v1_profile(domain_id: str, participant: ParticipantHeader = None) -> ProfileApiResponse:
        try:
            profile = scope_for(participant).profiles.get(domain_id)
        except LookupError as exc:
            return _profile_error("profile_not_found", str(exc))
        except ProfileRepositoryError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ProfileApiResponse(ok=True, profile=_profile_view(profile, with_report=True))

    @app.post("/api/v1/profiles/{domain_id}/facts", response_model=ProfileFactResponse)
    def v1_update_profile_fact(
        domain_id: str,
        payload: Any = Body(...),
        participant: ParticipantHeader = None,
    ) -> ProfileFactResponse:
        scope = scope_for(participant)
        try:
            request = ProfileFactRequest.model_validate(payload)
        except ValidationError as exc:
            return ProfileFactResponse(
                ok=False, errors=[ProfileFactError(code="invalid_input", message=str(exc))]
            )
        try:
            update = ProfileService(scope.profiles).update_fact(
                domain_id,
                scope.participant_id,
                request.field_key,
                request.value,
                raw_answer=request.raw_answer,
            )
        except ProfileServiceError as exc:
            return ProfileFactResponse(
                ok=False,
                errors=[
                    ProfileFactError(
                        code=exc.code, message=str(exc), validation_code=exc.validation_code
                    )
                ],
            )
        except ProfileRepositoryError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ProfileFactResponse(
            ok=True,
            profile=_profile_view(update.profile, with_report=False),
            changed_field=update.changed_field,
            previous_value=update.previous_value,
            open_questions=update.open_questions,
            result_changed=update.result_changed,
        )

    @app.post(
        "/api/v1/profiles/{domain_id}/recommendations/{recommendation_id}/feedback",
        response_model=ProfileFeedbackResponse,
    )
    def v1_recommendation_feedback(
        domain_id: str,
        recommendation_id: str,
        payload: Any = Body(...),
        participant: ParticipantHeader = None,
    ) -> ProfileFeedbackResponse:
        scope = scope_for(participant)
        try:
            request = ProfileFeedbackRequest.model_validate(payload)
        except ValidationError as exc:
            return ProfileFeedbackResponse(
                ok=False, errors=[ProfileFeedbackError(code="invalid_input", message=str(exc))]
            )
        try:
            profile = ProfileService(scope.profiles).set_feedback(
                domain_id, scope.participant_id, recommendation_id, request.stance, origin="button"
            )
        except ProfileServiceError as exc:
            if exc.code == "validation_failed" or exc.code == "invalid_question":  # not on this path
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return ProfileFeedbackResponse(
                ok=False, errors=[ProfileFeedbackError(code=exc.code, message=str(exc))]
            )
        except ProfileRepositoryError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ProfileFeedbackResponse(ok=True, profile=_profile_view(profile, with_report=False))

    @app.post(
        "/api/v1/profiles/{domain_id}/knowledge-recommendations",
        response_model=ProfileRecommendationsResponse,
    )
    def v1_profile_recommendations(
        domain_id: str, participant: ParticipantHeader = None, model: ModelHeader = None
    ) -> ProfileRecommendationsResponse:
        scope = scope_for(participant)
        provider = _default_provider()
        try:
            recommendations = generate_profile_recommendations(
                scope.profiles,
                domain_id,
                scope.participant_id,
                provider,
                provider.default_endpoint,
                _request_model(model, provider),
                knowledge=knowledge_service_from_env(provider, knowledge_path),
            )
        except RecommendationServiceError as exc:
            if exc.code == "invalid_state":  # not raised on the profile path
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return ProfileRecommendationsResponse(
                ok=False,
                domain_id=domain_id,
                errors=[ProfileRecommendationsError(code=exc.code, message=str(exc))],
            )
        except ProfileRepositoryError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ProfileRecommendationsResponse(
            ok=True, domain_id=domain_id, recommendations=recommendations
        )

    @app.post("/api/v1/profiles/{domain_id}/dataset", response_model=ProfileApiResponse)
    def v1_upload_dataset(
        domain_id: str, payload: Any = Body(...), participant: ParticipantHeader = None
    ) -> ProfileApiResponse:
        """Measurement data for the profile: GuideOS calls the pack's dataset
        tool on the registered server and remembers the dataset."""
        scope = scope_for(participant)
        try:
            request = DatasetUploadRequest.model_validate(payload)
        except ValidationError as exc:
            return _profile_error("invalid_input", str(exc))
        service = _conversation_service(scope, knowledge_path)
        try:
            profile = service.import_dataset(
                domain_id, request.filename, request.content.encode("utf-8"), request.format_hint
            )
        except ConversationError as exc:
            return _profile_error(exc.code, str(exc))
        except (ProfileRepositoryError, ConversationRepositoryError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ProfileApiResponse(ok=True, profile=_profile_view(profile, with_report=False))

    @app.get("/api/v1/profiles/{domain_id}/dataset", response_model=DatasetApiResponse)
    def v1_get_dataset(domain_id: str, participant: ParticipantHeader = None) -> DatasetApiResponse:
        try:
            profile = scope_for(participant).profiles.get(domain_id)
        except LookupError as exc:
            return DatasetApiResponse(ok=False, domain_id=domain_id,
                                      errors=[ProfileApiError(code="profile_not_found", message=str(exc))])
        except ProfileRepositoryError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return DatasetApiResponse(ok=True, domain_id=domain_id, dataset=profile.dataset,
                                  tool_results=list(profile.tool_results))

    @app.get("/api/v1/profiles/{domain_id}/series", response_model=SeriesApiResponse)
    def v1_profile_series(
        domain_id: str,
        resolution: str = "day",
        period_from: str | None = None,
        period_to: str | None = None,
        participant: ParticipantHeader = None,
    ) -> SeriesApiResponse:
        """The series of the profile's dataset from the pack's `tools.series_tool`
        in one of the two resolutions a series tool offers (day, quarter_hour);
        unit and points come from the tool."""
        def error(code: SeriesApiErrorCode, message: str) -> SeriesApiResponse:
            return SeriesApiResponse(ok=False, domain_id=domain_id, errors=[SeriesApiError(code=code, message=message)])

        if resolution not in ("day", "quarter_hour"):
            return error("invalid_input", "resolution muss day oder quarter_hour sein.")
        for label, value in (("period_from", period_from), ("period_to", period_to)):
            if value is not None:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    return error("invalid_input", f"{label} muss ein Datum JJJJ-MM-TT sein.")
        scope = scope_for(participant)
        try:
            profile = scope.profiles.get(domain_id)
        except LookupError as exc:
            return error("profile_not_found", str(exc))
        except ProfileRepositoryError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if profile.dataset is None:
            return error("no_dataset", "Zu diesem Profil gibt es keine Messdaten.")
        try:
            pack = load_domain_pack_version(profile.domain_id, profile.domain_version)
        except (DomainCatalogError, DomainPackError) as exc:
            return error("domain_version_unavailable", str(exc))
        if pack.tools is None or not pack.tools.series_tool:
            return error("series_unavailable", "Diese Pack-Version bietet keine Zeitreihen an.")
        arguments: dict[str, Any] = {"dataset_id": profile.dataset.dataset_id, "resolution": resolution}
        if period_from is not None:
            arguments["period_from"] = period_from
        if period_to is not None:
            arguments["period_to"] = period_to
        try:
            result = tool_service_from_env().call(
                domain_id, scope.participant_id or local_participant_id(), pack.tools.series_tool, arguments
            )
        except ToolError as exc:
            return error("tool_error", str(exc))
        content = result.structured_content or {}
        points = [
            SeriesPointView(start=str(point.get("start")), value=str(point.get("value")), complete=point.get("complete"))
            for point in content.get("points", [])
        ]
        return SeriesApiResponse(
            ok=True, domain_id=domain_id, resolution=str(content.get("resolution") or resolution),
            unit=content.get("unit"), period_from=content.get("period_from"), period_to=content.get("period_to"),
            points=points, calculation_id=result.calculation_id,
        )

    @app.delete("/api/v1/profiles/{domain_id}/dataset", response_model=DatasetApiResponse)
    def v1_delete_dataset(domain_id: str, participant: ParticipantHeader = None) -> DatasetApiResponse:
        service = _conversation_service(scope_for(participant), knowledge_path)
        try:
            profile = service.delete_dataset(domain_id)
        except ConversationError as exc:
            return DatasetApiResponse(ok=False, domain_id=domain_id,
                                      errors=[ProfileApiError(code=exc.code, message=str(exc))])
        except (ProfileRepositoryError, ConversationRepositoryError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return DatasetApiResponse(ok=True, domain_id=domain_id, dataset=profile.dataset,
                                  tool_results=list(profile.tool_results))

    @app.delete("/api/v1/profiles/{domain_id}", response_model=ProfileApiResponse)
    def v1_delete_profile(
        domain_id: str, participant: ParticipantHeader = None
    ) -> ProfileApiResponse:
        scope = scope_for(participant)
        try:
            delete_profile_data(scope.profiles, scope.conversations, scope.assessments, domain_id,
                                tools=tool_service_from_env())
        except LookupError as exc:
            return _profile_error("profile_not_found", str(exc))
        except (ProfileRepositoryError, ConversationRepositoryError, AssessmentRepositoryError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ProfileApiResponse(ok=True)

    @app.post("/api/v1/conversations", response_model=ConversationContractResponse)
    def v1_create_conversation(
        payload: Any = Body(...),
        participant: ParticipantHeader = None,
    ) -> ConversationContractResponse:
        scope = scope_for(participant)
        try:
            request = CreateConversationRequest.model_validate(payload)
        except ValidationError as exc:
            return _conversation_error("invalid_input", str(exc))
        service = _conversation_service(scope, knowledge_path)
        try:
            conversation = service.create(
                request.domain_id,
                title=request.title,
                participant_id=scope.participant_id,
            )
        except ConversationError as exc:
            return _conversation_error(exc.code, str(exc))
        except (ProfileRepositoryError, ConversationRepositoryError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ConversationContractResponse(ok=True, conversation=conversation)

    @app.post("/api/v1/conversations/result", response_model=ConversationContractResponse)
    def v1_result_conversation(
        payload: Any = Body(...),
        participant: ParticipantHeader = None,
        model: ModelHeader = None,
    ) -> ConversationContractResponse:
        scope = scope_for(participant)
        try:
            request = ResultConversationRequest.model_validate(payload)
        except ValidationError as exc:
            return _conversation_error("invalid_input", str(exc))
        provider = _default_provider()
        service = _conversation_service(scope, knowledge_path, provider)
        try:
            conversation = service.ensure_result_conversation(
                request.domain_id,
                provider.default_endpoint,
                _request_model(model, provider),
                participant_id=scope.participant_id,
            )
        except ConversationError as exc:
            return _conversation_error(exc.code, str(exc))
        except (ProfileRepositoryError, ConversationRepositoryError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ConversationContractResponse(ok=True, conversation=conversation)

    @app.get("/api/v1/conversations", response_model=ConversationContractResponse)
    def v1_list_conversations(
        participant: ParticipantHeader = None,
    ) -> ConversationContractResponse:
        service = _conversation_service(scope_for(participant), knowledge_path)
        try:
            conversations = service.list()
        except ConversationRepositoryError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ConversationContractResponse(ok=True, conversations=conversations)

    @app.get(
        "/api/v1/conversations/{conversation_id}",
        response_model=ConversationContractResponse,
    )
    def v1_get_conversation(
        conversation_id: str,
        participant: ParticipantHeader = None,
    ) -> ConversationContractResponse:
        service = _conversation_service(scope_for(participant), knowledge_path)
        try:
            conversation = service.get(conversation_id)
        except LookupError as exc:
            return _conversation_error("conversation_not_found", str(exc))
        except ConversationRepositoryError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ConversationContractResponse(ok=True, conversation=conversation)

    @app.post(
        "/api/v1/conversations/{conversation_id}/messages",
        response_model=ConversationContractResponse,
    )
    def v1_send_conversation_message(
        conversation_id: str,
        payload: Any = Body(...),
        participant: ParticipantHeader = None,
        model: ModelHeader = None,
    ) -> ConversationContractResponse:
        scope = scope_for(participant)
        try:
            request = ConversationMessageRequest.model_validate(payload)
        except ValidationError as exc:
            return _conversation_error("invalid_input", str(exc))
        provider = _default_provider()
        service = _conversation_service(scope, knowledge_path, provider)
        try:
            conversation = service.send_message(
                conversation_id,
                request.text,
                provider.default_endpoint,
                _request_model(model, provider),
            )
        except LookupError as exc:
            return _conversation_error("conversation_not_found", str(exc))
        except ConversationError as exc:
            return _conversation_error(exc.code, str(exc))
        except (AssessmentRepositoryError, ConversationRepositoryError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ConversationContractResponse(ok=True, conversation=conversation)

    @app.delete(
        "/api/v1/conversations/{conversation_id}",
        response_model=ConversationContractResponse,
    )
    def v1_delete_conversation(
        conversation_id: str,
        participant: ParticipantHeader = None,
    ) -> ConversationContractResponse:
        service = _conversation_service(scope_for(participant), knowledge_path)
        try:
            service.delete(conversation_id)
        except LookupError as exc:
            return _conversation_error("conversation_not_found", str(exc))
        except ConversationRepositoryError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ConversationContractResponse(ok=True)

    @app.post(
        "/domains/{domain_id}/assessment",
        response_model=AssessmentContractResponse,
    )
    def assessment(
        domain_id: str,
        payload: dict[str, Any] = Body(...),
        participant: ParticipantHeader = None,
    ) -> AssessmentContractResponse:
        scope = scope_for(participant)
        _ensure_active_domain(domain_id)
        try:
            pack = load_active_domain_pack(domain_id)
            provider = get_provider(DEFAULT_PROVIDER)
        except (DomainCatalogError, DomainPackError, KeyError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        orchestrator = build_assessment(pack, provider, repository=scope.assessments,
                                        tools=tool_service_from_env())
        return handle_assessment_payload(orchestrator, payload)

    # A built web client (frontend/dist or GUIDEOS_FRONTEND_DIST) next to the
    # API under one origin, so the browser needs no proxy and sets no participant header.
    # Mounted last: the API routes take precedence.
    frontend_dist = frontend_dist_from_env()
    if frontend_dist is not None:
        app.mount("/", FrontendFiles(directory=str(frontend_dist), html=True), name="frontend")
    return app


class FrontendFiles(StaticFiles):
    """The web client's build with cache headers: the page itself is revalidated on
    every load (a browser must pick up a new release), the hashed assets
    under /assets are immutable."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        name = str(full_path).replace("\\", "/")
        if "/assets/" in name:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


DEFAULT_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def frontend_dist_from_env() -> Path | None:
    """GUIDEOS_FRONTEND_DIST, or the repository's frontend/dist when it holds
    a build; None (nothing mounted) otherwise."""
    configured = os.environ.get("GUIDEOS_FRONTEND_DIST")
    candidate = Path(configured) if configured else DEFAULT_FRONTEND_DIST
    return candidate if (candidate / "index.html").is_file() else None


def _active_domain_refs() -> list[DomainPackRef]:
    try:
        return list_active_domain_versions()
    except DomainCatalogError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _ensure_active_domain(domain_id: str) -> None:
    if not any(ref.domain_id == domain_id for ref in _active_domain_refs()):
        raise HTTPException(
            status_code=404,
            detail=f"Keine aktive Domain-Version für {domain_id}.",
        )


def _domain_response(ref: DomainPackRef) -> DomainResponse:
    return DomainResponse(
        domain_id=ref.domain_id,
        version=ref.version,
        name=ref.name,
        source=ref.source,
    )


def _request_scope(
    repository_path: Path,
    conversations_path: Path,
    profiles_path: Path,
    participant: str | None,
) -> RequestScope:
    participant_id = (participant or "").strip() or None
    if participant_id is None and os.environ.get("GUIDEOS_DATABASE_URL"):
        raise ParticipantRequiredError()
    assessments = assessment_repository_from_env(repository_path)
    conversations = conversation_repository_from_env(conversations_path)
    profiles = profile_repository_from_env(profiles_path)
    if participant_id:
        assessments = ParticipantAssessmentRepository(assessments, participant_id)
        conversations = ParticipantConversationRepository(conversations, participant_id)
        profiles = ParticipantProfileRepository(profiles, participant_id)
    return RequestScope(assessments, conversations, profiles, participant_id)


def _orchestrator_for_active_domain(
    knowledge_path: Path | None,
    domain_id: str,
    scope: RequestScope,
) -> AssessmentOrchestrator:
    try:
        pack = load_active_domain_pack(domain_id)
    except (DomainCatalogError, DomainPackError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _orchestrator_for_pack(pack, scope, knowledge_path)


def _orchestrator_for_session(
    knowledge_path: Path | None,
    session_id: str,
    scope: RequestScope,
) -> AssessmentOrchestrator | AssessmentContractResponse:
    repository = scope.assessments
    try:
        session = repository.get(session_id)
    except LookupError as exc:
        return _contract_error(session_id, "session_not_found", str(exc))
    except AssessmentRepositoryError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        pack = load_domain_pack_version(session.domain_id, session.domain_version)
    except (DomainCatalogError, DomainPackError) as exc:
        return _contract_error(session_id, "domain_version_unavailable", str(exc))
    return _orchestrator_for_pack(pack, scope, knowledge_path)


def _orchestrator_for_pack(
    pack: DomainPack,
    scope: RequestScope,
    knowledge_path: Path | None,
) -> AssessmentOrchestrator:
    provider = _default_provider()
    return build_assessment(
        pack,
        provider,
        repository=scope.assessments,
        knowledge=knowledge_service_from_env(provider, knowledge_path),
        profiles=scope.profiles,
        tools=tool_service_from_env(),
    )


def _profile_view(profile: Profile, with_report: bool) -> ProfileView:
    """Profile as the API shows it: answers in pack order with display
    values, the result, and on request the rendered profile report."""
    try:
        pack = load_domain_pack_version(profile.domain_id, profile.domain_version)
        if pack.content_hash != profile.domain_content_hash:
            raise DomainPackError("Die Domain-Version des Profils ist nicht mehr verfügbar.")
    except (DomainCatalogError, DomainPackError) as exc:
        if with_report:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        pack = None
    questions = {q.field_key: q for q in pack.questions} if pack else {}
    ordered = [key for key in questions if key in profile.answers] if pack else list(profile.answers)
    answers = {
        key: ProfileAnswerView(
            question_id=profile.answers[key].question_id,
            normalized_value=profile.answers[key].normalized_value,
            calculation_id=profile.answers[key].calculation_id,
            display_value=(
                display_value(questions[key], profile.answers[key].normalized_value)
                if key in questions
                else None
            ),
            label=questions[key].text if key in questions else None,
            origin=profile.answers[key].origin,
            updated_at=profile.answers[key].updated_at,
        )
        for key in ordered
    }
    report = None
    if with_report and pack is not None:
        rendered = render_profile_report(pack, profile)
        report = ContractReport(
            format=rendered.format, content=rendered.content, created_at=rendered.created_at
        )
    return ProfileView(
        profile_id=profile.profile_id,
        domain_id=profile.domain_id,
        domain_version=profile.domain_version,
        domain_content_hash=profile.domain_content_hash,
        source_session_id=profile.source_session_id,
        revision=profile.revision,
        answers=answers,
        result=_contract_result(profile.result),
        knowledge_stale=profile.knowledge_stale,
        feedback={
            key: ProfileFeedbackView(**item.model_dump()) for key, item in profile.feedback.items()
        },
        dataset=profile.dataset,
        tool_results=list(profile.tool_results),
        report=report,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _profile_error(code: ProfileApiErrorCode, message: str) -> ProfileApiResponse:
    return ProfileApiResponse(ok=False, errors=[ProfileApiError(code=code, message=message)])


def _conversation_service(
    scope: RequestScope,
    knowledge_path: Path | None,
    provider: LLMProvider | None = None,
) -> ConversationService:
    provider = provider or _default_provider()
    return ConversationService(
        scope.conversations,
        scope.profiles,
        provider,
        knowledge=knowledge_service_from_env(provider, knowledge_path),
        tools=tool_service_from_env(),
    )


def _llm_recommendations_error(
    session_id: str,
    code: LLMRecommendationsErrorCode,
    message: str,
) -> LLMRecommendationsResponse:
    return LLMRecommendationsResponse(
        ok=False,
        session_id=session_id,
        errors=[LLMRecommendationsError(code=code, message=message)],
    )


def _conversation_error(
    code: ConversationApiErrorCode,
    message: str,
) -> ConversationContractResponse:
    return ConversationContractResponse(
        ok=False,
        errors=[ConversationContractError(code=code, message=message)],
    )


def _default_provider() -> LLMProvider:
    try:
        return get_provider(DEFAULT_PROVIDER)
    except KeyError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _available_models(provider: LLMProvider) -> list[str]:
    """The models the configured endpoint lists; empty (and logged) while it
    cannot be reached."""
    try:
        return provider.list_models(provider.default_endpoint)
    except ProviderError as exc:
        logger.warning("model_list_failed error=%s", exc)
        return []


def _request_model(header: str | None, provider: LLMProvider) -> str:
    """The model of this request: the header X-GuideOS-Model while the
    selection is enabled, otherwise the provider's default model. The list the
    endpoint reports is the allowlist: a model it does not list (or an
    unreachable list) is refused with HTTP 400, so no client can load an
    arbitrary model on the shared endpoint."""
    requested = (header or "").strip()
    if not requested or not _model_selection_enabled():
        return provider.default_model
    if requested not in _available_models(provider):
        logger.warning("model_header_rejected")
        raise HTTPException(
            status_code=400, detail="Das gewählte KI-Modell steht am Endpoint nicht zur Auswahl."
        )
    return requested


def _parse_v1_input(
    session_id: str,
    payload: Any,
) -> V1InputRequest | AssessmentContractResponse:
    try:
        return V1InputRequestAdapter.validate_python(payload)
    except ValidationError as exc:
        return _contract_error(session_id, "invalid_input", str(exc))


def _parse_v1_chat(
    session_id: str,
    payload: Any,
) -> V1ChatRequest | AssessmentContractResponse:
    try:
        return QuestionHelpChatRequest.model_validate(payload)
    except ValidationError as exc:
        return _contract_error(session_id, "invalid_input", str(exc))


def _v1_input_payload(
    session_id: str, request: V1InputRequest, model_header: str | None = None
) -> dict[str, Any]:
    payload = request.model_dump(mode="json")
    payload["session_id"] = session_id
    if isinstance(request, SubmitTextAnswerRequest):
        provider = _default_provider()
        payload["endpoint"] = provider.default_endpoint
        payload["model"] = _request_model(model_header, provider)
    if isinstance(request, CompleteRequest):
        del payload["knowledge_recommendations"]
        # An explicit endpoint + model in the body wins over the header.
        if request.knowledge_recommendations and not (request.endpoint and request.model):
            provider = _default_provider()
            payload["endpoint"] = provider.default_endpoint
            payload["model"] = _request_model(model_header, provider)
    return payload


def _v1_chat_payload(
    session_id: str, request: V1ChatRequest, model_header: str | None = None
) -> dict[str, Any]:
    provider = _default_provider()
    return {
        "kind": "question_help",
        "session_id": session_id,
        "question_id": request.question_id,
        "text": request.text,
        "endpoint": provider.default_endpoint,
        "model": _request_model(model_header, provider),
    }


def _contract_error(
    session_id: str | None,
    code: AssessmentErrorCode,
    message: str,
) -> AssessmentContractResponse:
    return AssessmentContractResponse(
        ok=False,
        session_id=session_id,
        assistant_message=message,
        errors=[
            AssessmentContractError(
                code=code,
                message=message,
            )
        ],
    )
