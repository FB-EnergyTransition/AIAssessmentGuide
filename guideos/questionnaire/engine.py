from guideos.assessment.models import AssessmentSession
from guideos.domains.models import DomainPack, QuestionDefinition


def is_question_visible(
    pack: DomainPack,
    session: AssessmentSession,
    question: QuestionDefinition,
) -> bool:
    condition = question.visible_if
    if condition is None:
        return True

    answer = session.answers.get(condition.field_key)
    if condition.operator == "is_missing":
        return answer is None or answer.normalized_value is None
    if answer is None:
        return False

    value = answer.normalized_value
    if condition.operator == "equals":
        return value == condition.value
    if condition.operator == "in":
        return value in condition.value
    if condition.operator == "contains":
        return isinstance(value, list) and condition.value in value
    if condition.operator == "contains_any":
        return isinstance(value, list) and any(item in value for item in condition.value)
    return False


def get_next_question(
    pack: DomainPack,
    session: AssessmentSession,
) -> QuestionDefinition | None:
    unanswered = [
        question
        for question in pack.questions
        if is_question_visible(pack, session, question)
        if question.field_key not in session.answers
    ]
    for question in unanswered:
        if question.required:
            return question
    return unanswered[0] if unanswered else None
