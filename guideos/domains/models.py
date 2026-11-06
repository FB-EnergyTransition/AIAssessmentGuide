import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


FieldType = Literal["select", "multiselect", "integer", "number", "text", "boolean", "date", "file"]
WidgetType = Literal["dropdown", "radio", "checkboxes", "input", "textarea"]
ConditionOperator = Literal["equals", "in", "is_missing", "contains", "contains_any"]
RuleOperator = Literal["equals", "in", "lt", "gte", "is_missing", "contains", "contains_any"]

DEFAULT_WIDGETS: dict[str, str] = {
    "select": "radio",
    "multiselect": "checkboxes",
    "text": "input",
}
ALLOWED_WIDGETS: dict[str, set[str]] = {
    "select": {"dropdown", "radio"},
    "multiselect": {"checkboxes", "dropdown"},
    "text": {"input", "textarea"},
}
ALLOWED_VALIDATION: dict[str, set[str]] = {
    "select": set(),
    "multiselect": {"min_count", "max_count"},
    "integer": {"minimum", "maximum"},
    "number": {"minimum", "maximum"},
    "text": {"min_length", "max_length", "pattern"},
    "boolean": set(),
    "date": {"min_date", "max_date"},
    "file": set(),
}
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
# Arguments of the import tool that GuideOS sets itself for a file question.
RESERVED_UPLOAD_ARGUMENTS = frozenset({"owner", "filename", "content", "format_hint", "dataset_id"})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuestionOption(StrictModel):
    value: str
    label: str


class ValidationDefinition(StrictModel):
    minimum: float | None = None
    maximum: float | None = None
    min_length: int | None = Field(default=None, ge=0)
    max_length: int | None = Field(default=None, ge=1)
    pattern: str | None = None
    min_count: int | None = Field(default=None, ge=0)
    max_count: int | None = Field(default=None, ge=1)
    min_date: str | None = None
    max_date: str | None = None


class VisibilityCondition(StrictModel):
    field_key: str
    operator: ConditionOperator
    value: Any = None


class UploadDefinition(StrictModel):
    """How a file question is imported: the tool GuideOS calls on the
    domain's registered server (None: the pack's tools.dataset_tool), fixed
    extra arguments, the allowed file extensions and size, and whether the
    dataset becomes the profile's dataset for the advisory. The file itself
    is never stored in GuideOS, only the reference the tool returns."""

    tool: str | None = Field(default=None, min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    extensions: list[str] = Field(default_factory=lambda: ["csv", "txt"], min_length=1)
    max_bytes: int = Field(default=MAX_UPLOAD_BYTES, ge=1)
    use_in_advisory: bool = True

    @field_validator("extensions")
    @classmethod
    def _lowercase_extensions(cls, extensions: list[str]) -> list[str]:
        return [extension.strip().lower() for extension in extensions]


class QuestionDefinition(StrictModel):
    question_id: str
    field_key: str
    text: str
    help_text: str | None = None
    field_type: FieldType
    widget: WidgetType | None = None
    required: bool
    allow_unknown: bool = False
    options: list[QuestionOption] = Field(default_factory=list)
    validation: ValidationDefinition = Field(default_factory=ValidationDefinition)
    visible_if: VisibilityCondition | None = None
    upload: UploadDefinition | None = None


RuleKind = Literal["recommend", "exclude", "prioritize"]
RuleOrigin = Literal["expert", "imported"]
RuleStatus = Literal["draft", "review", "approved"]


class TopicDefinition(StrictModel):
    """A topic knowledge recommendations are assigned to, so that rules can
    exclude or prioritize them without matching free text."""

    topic_id: str
    label: str


class MetricSource(StrictModel):
    """A figure from a tool result instead of an answer: the tool of the
    pack's first analysis and a JSON pointer into its structured output
    (for example /totals/months_above)."""

    tool: str = Field(min_length=1)
    pointer: str = Field(pattern=r"^/")
    # German label of the figure for evidence lines ("Monate mit Spitze über 10 kW").
    label: str | None = Field(default=None, min_length=1, max_length=120)


class RuleDefinition(StrictModel):
    """kind recommend (default): the condition issues recommendation_ids.
    exclude: knowledge recommendations with one of the topics are dropped.
    prioritize: knowledge recommendations with one of the topics get priority.
    With metric the condition compares a figure of a tool result; field_key
    then names the file question whose dataset the figure comes from."""

    rule_id: str
    kind: RuleKind = "recommend"
    field_key: str
    operator: RuleOperator
    value: Any = None
    divisor_field_key: str | None = None
    metric: MetricSource | None = None
    reason_code: str
    recommendation_ids: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    priority: int | None = Field(default=None, ge=1)
    # Lifecycle: who wrote the rule, which professional source it rests on, and
    # whether it is approved. Only approved rules may be activated; the
    # default keeps bundled packs approved as they are.
    origin: RuleOrigin = "expert"
    source_reference: str | None = None
    status: RuleStatus = "approved"

    @model_validator(mode="before")
    @classmethod
    def drop_legacy_score(cls, data: Any) -> Any:
        # Older bundled pack versions carry a per-rule score. They are
        # immutable, so the key is still read, but it has no meaning any more.
        if isinstance(data, dict) and "score" in data:
            return {key: value for key, value in data.items() if key != "score"}
        return data


class FigureSource(StrictModel):
    """A figure a recommendation text quotes: the value under pointer in the
    last structured output of tool (the dataset tool or a first-analysis
    tool), optionally mapped through map (unmapped values give nothing) and
    rounded to decimals; rendered in German notation."""

    tool: str = Field(min_length=1)
    pointer: str = Field(pattern=r"^/")
    decimals: int | None = Field(default=None, ge=0, le=6)
    map: dict[str, Any] | None = None


FIGURE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")


class RecommendationDefinition(StrictModel):
    """A catalog entry. message is the generic text; with figures and
    message_with_figures GuideOS fills the {name} placeholders from the
    participant's tool results and falls back to message when one is missing."""

    recommendation_id: str
    priority: int = Field(ge=1)
    title: str
    message: str
    source_reference: str | None = None
    figures: dict[str, FigureSource] | None = None
    message_with_figures: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _figure_names(self) -> "RecommendationDefinition":
        if self.figures is not None and not self.figures:
            raise ValueError("figures darf nicht leer sein.")
        for name in self.figures or {}:
            if not FIGURE_NAME.match(name):
                raise ValueError(f"Der Kennzahlen-Name {name!r} darf nur Kleinbuchstaben, Ziffern und _ enthalten.")
        return self

    def placeholders(self) -> list[str]:
        """The placeholder names used in message_with_figures, in order."""
        if self.message_with_figures is None:
            return []
        return list(dict.fromkeys(PLACEHOLDER.findall(self.message_with_figures)))


class WebSourceSettings(StrictModel):
    """Whether the advisory may use the curated web pages of the domain's
    knowledge base, optionally limited to host names (subdomains included)."""

    enabled: bool = False
    allowed_domains: list[str] = Field(default_factory=list)


class KnowledgeRecommendationSettings(StrictModel):
    """Knowledge recommendations generated at completion: switched off for a
    domain, or limited to at most max_count items (default 5)."""

    enabled: bool = True
    max_count: int = Field(default=5, ge=1, le=5)


class AnswerArgument(StrictModel):
    """A tool argument taken from an answer when the step runs: the stored
    value of field_key, translated through map (keys are the answer values as
    strings; an unmapped value yields nothing), or default when there is no
    usable answer; without default the argument is left out."""

    field_key: str
    map: dict[str, Any] | None = None
    default: Any = None

    def has_default(self) -> bool:
        return "default" in self.model_fields_set


class AfterImportSettings(StrictModel):
    """A deterministic analysis step GuideOS runs itself: one allowed tool,
    called with the dataset_id plus these arguments (and, for steps of
    tools.at_completion, arguments taken from the answers); its summary and
    the note are posted to the result chat."""

    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    arguments_from_answers: dict[str, AnswerArgument] = Field(default_factory=dict)
    note: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _no_reserved_arguments(self) -> "AfterImportSettings":
        names = set(self.arguments) | set(self.arguments_from_answers)
        reserved = {"dataset_id", "owner"} & names
        if reserved:
            raise ValueError("Analyse-Argumente dürfen dataset_id und owner nicht enthalten; "
                             "GuideOS setzt beide selbst.")
        twice = set(self.arguments) & set(self.arguments_from_answers)
        if twice:
            raise ValueError(f"Argument {', '.join(sorted(twice))} steht in arguments und arguments_from_answers.")
        return self


DerivationMode = Literal["answer", "propose"]


class DerivationCondition(StrictModel):
    """When a derivation applies: a figure of the same tool result compared
    with a value (equals, in, gte, lt)."""

    pointer: str = Field(pattern=r"^/")
    operator: Literal["equals", "in", "gte", "lt"]
    value: Any = None


class DerivationDefinition(StrictModel):
    """An answer GuideOS takes from measurement data right after the import
    of the advisory dataset: the value under pointer in the structured output
    of tool (the dataset tool or one of the first-analysis tools), or the
    constant value, optionally mapped through map (unmapped values give
    nothing) and rounded to decimals. mode answer stores it as the answer
    (origin measurement, the question is skipped and stays editable), mode
    propose shows it as a proposal the participant confirms or changes.
    confidence_pointer names a confidence level shown with the proposal."""

    field_key: str
    tool: str = Field(min_length=1)
    pointer: str | None = Field(default=None, pattern=r"^/")
    value: Any = None
    map: dict[str, Any] | None = None
    when: DerivationCondition | None = None
    mode: DerivationMode = "answer"
    decimals: int | None = Field(default=None, ge=0, le=6)
    confidence_pointer: str | None = Field(default=None, pattern=r"^/")
    note: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def _pointer_or_value(self) -> "DerivationDefinition":
        has_value = "value" in self.model_fields_set
        if (self.pointer is None) == (not has_value):
            raise ValueError("Eine Ableitung braucht entweder pointer oder value.")
        return self


class ToolSettings(StrictModel):
    """MCP tools in the advisory of a pack version: whether the model may call
    the tools registered for the domain, which tools GuideOS itself calls for
    dataset upload and deletion, the budget per message, the optional first
    analysis after an import (one step or a list of steps, run in order) and
    the answers derived from its results."""

    enabled: bool = False
    dataset_tool: str = Field(default="import_dataset", min_length=1)
    delete_tool: str = Field(default="delete_dataset", min_length=1)
    # Read-only tool for day and quarter-hour series of the profile's dataset
    # (GET /api/v1/profiles/{domain_id}/series); None: no series endpoint.
    series_tool: str | None = Field(default=None, min_length=1)
    max_calls_per_message: int = Field(default=6, ge=1, le=10)
    max_seconds_per_message: int = Field(default=90, ge=5, le=600)
    after_import: AfterImportSettings | list[AfterImportSettings] | None = None
    # Steps that need answers (arguments_from_answers) run when the assessment
    # completes, and after a panel upload with the profile's answers.
    at_completion: list[AfterImportSettings] | None = None
    derive: list[DerivationDefinition] = Field(default_factory=list)

    def analysis_steps(self) -> list[AfterImportSettings]:
        """The first analysis as a list, whatever shape the pack wrote."""
        if self.after_import is None:
            return []
        if isinstance(self.after_import, list):
            return list(self.after_import)
        return [self.after_import]

    def completion_steps(self) -> list[AfterImportSettings]:
        return list(self.at_completion or [])


class ResultChatSettings(StrictModel):
    """How the result chat presents recommendations after a completed
    assessment: detailed (every item with source, reasons and evidence lines,
    the default) or compact (one line per item with title and short text;
    the sources stay on the message as evidence, the report shows all).
    intro, updated_intro and offer replace the chat's standard texts (the
    first message after a completion, the one after a newer assessment, and
    the offer to generate knowledge recommendations) while set."""

    recommendations: Literal["detailed", "compact"] = "detailed"
    intro: str | None = Field(default=None, min_length=1, max_length=500)
    updated_intro: str | None = Field(default=None, min_length=1, max_length=500)
    offer: str | None = Field(default=None, min_length=1, max_length=500)


class DomainPack(StrictModel):
    domain_id: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    name: str
    description: str
    default_locale: str
    questions: list[QuestionDefinition]
    rules: list[RuleDefinition]
    recommendations: list[RecommendationDefinition]
    web_sources: WebSourceSettings | None = None
    advisory_guidance: str | None = Field(default=None, min_length=1, max_length=4000)
    knowledge_recommendations: KnowledgeRecommendationSettings | None = None
    topics: list[TopicDefinition] | None = None
    tools: ToolSettings | None = None
    result_chat: ResultChatSettings | None = None
    content_hash: str = ""


def advisory_file_question(pack: DomainPack) -> QuestionDefinition | None:
    """The file question whose dataset the advisory uses (at most one per
    pack), None for packs without one."""
    return next(
        (question for question in pack.questions
         if question.field_type == "file" and question.upload is not None and question.upload.use_in_advisory),
        None,
    )


def effective_widget(question: QuestionDefinition) -> str | None:
    """The widget to render: the explicit one or the default for the field type."""
    return question.widget or DEFAULT_WIDGETS.get(question.field_type)


def format_date_de(iso_date: str) -> str:
    """Render an ISO date (JJJJ-MM-TT) as TT.MM.JJJJ; other text is returned unchanged."""
    try:
        parsed = date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return str(iso_date)
    return parsed.strftime("%d.%m.%Y")


def display_value(question: QuestionDefinition, value: object) -> str:
    """Human-readable form of a normalized answer: labels, Ja/Nein, TT.MM.JJJJ or plain value."""
    if value is None:
        return "Unbekannt"
    if isinstance(value, bool):
        return "Ja" if value else "Nein"
    if isinstance(value, list):
        return ", ".join(display_value(question, item) for item in value)
    if question.field_type == "date" and isinstance(value, str):
        return format_date_de(value)
    if question.field_type == "file" and isinstance(value, dict):
        return display_file_value(value)
    for option in question.options:
        if option.value == value:
            return option.label
    return str(value)


def display_file_value(value: dict[str, Any]) -> str:
    """A file answer as the user sees it: the (masked) file name and, once
    imported, the summary the tool returned; never the content."""
    name = str(value.get("filename") or "Datei")
    summary = str(value.get("summary") or "").strip()
    return f"{name} ({summary})" if summary else name
