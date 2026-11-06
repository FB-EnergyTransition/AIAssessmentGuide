from guideos.assessment.models import AssessmentSession
from guideos.context.models import (
    AdvisoryContext,
    TopicPriorityRef,
    TopicRef,
    UserFact,
    UserFeedback,
)
from guideos.domains.models import DomainPack, advisory_file_question, display_value
from guideos.profiles.models import Profile
from guideos.rules.engine import evaluate_rule_metrics, evaluate_rules
from guideos.tools.models import DatasetRef


def build_advisory_context(
    pack: DomainPack,
    source: AssessmentSession | Profile,
) -> AdvisoryContext:
    """The verified facts and deterministic results of a completed session
    or of a profile, in the same shape for every advisory LLM call."""
    questions = {question.field_key: question for question in pack.questions}
    facts = []
    for field_key, answer in source.answers.items():
        question = questions.get(field_key)
        facts.append(
            UserFact(
                field_key=field_key,
                question_id=answer.question_id,
                question_text=question.text if question else None,
                normalized_value=_fact_value(question, answer.normalized_value),
                display_value=(
                    display_value(question, answer.normalized_value) if question else None
                ),
                raw_answer=answer.raw_answer,
                confidence=answer.confidence,
            )
        )
    result = source.result
    is_profile = isinstance(source, Profile)
    evaluation = evaluate_rules(pack, source) if result else None
    return AdvisoryContext(
        domain_id=pack.domain_id,
        domain_version=pack.version,
        domain_name=pack.name,
        domain_guidance=pack.advisory_guidance,
        participant_id=source.participant_id,
        facts=facts,
        assessment_session_id=None if is_profile else source.session_id,
        profile_id=source.profile_id if is_profile else None,
        reason_codes=list(result.reason_codes) if result else [],
        rule_recommendations=list(result.recommendations) if result else [],
        rule_metrics=evaluate_rule_metrics(pack, source) if result else [],
        feedback=[
            UserFeedback(recommendation_id=item.recommendation_id, title=item.title, stance=item.stance)
            for item in (source.feedback.values() if is_profile else [])
        ],
        topics=[TopicRef(topic_id=t.topic_id, label=t.label) for t in (pack.topics or [])],
        excluded_topics=(
            list(dict.fromkeys(e.topic_id for e in evaluation.excluded_topics)) if evaluation else []
        ),
        excluding_rules=(
            {e.topic_id: e.rule_id for e in reversed(evaluation.excluded_topics)} if evaluation else {}
        ),
        topic_priorities=[
            TopicPriorityRef(topic_id=p.topic_id, priority=p.priority, rule_id=p.rule_id)
            for p in (evaluation.topic_priorities if evaluation else [])
        ],
        dataset=source.dataset if is_profile else _session_dataset(pack, source),
        # The first analysis of the dataset (and, on a profile, later chat results).
        tool_results=list(source.tool_results),
    )


def _session_dataset(pack: DomainPack, session: AssessmentSession) -> DatasetRef | None:
    """The dataset the advisory file answer of a session refers to, before
    completion moves it to the profile; None without such an answer."""
    question = advisory_file_question(pack)
    if question is None:
        return None
    answer = session.answers.get(question.field_key)
    value = answer.normalized_value if answer is not None else None
    if not isinstance(value, dict) or not value.get("dataset_id"):
        return None
    try:
        return DatasetRef.model_validate(value)
    except ValueError:
        return None


def _fact_value(question, value):
    """A file answer as a fact: id, name and import summary, not the whole
    import record (the advisory dataset is passed separately as DATASET)."""
    if question is not None and question.field_type == "file" and isinstance(value, dict):
        return {key: value.get(key) for key in ("dataset_id", "filename", "summary")}
    return value
