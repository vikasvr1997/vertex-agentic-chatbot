"""LLM-as-judge correctness grading against a reference answer.

This is the same technique DeepEval/Ragas/OpenAI model-graded evals use to
score free-text answers that exact string matching is too brittle for. The
judge model is resolved through :func:`bigquery_agent_kit.runtime.model_resolver.resolve_model`,
so it can be Gemini or any LiteLLM ``provider/model`` string — independent of
whatever model the agent being graded uses.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

_JUDGE_INSTRUCTION = (
    "You grade whether a candidate answer to a data question is correct, given "
    "a reference answer. Reply with strict JSON only, no markdown fences: "
    '{"correct": true or false, "rationale": "<one short sentence>"}. '
    "Minor wording or formatting differences are fine. Numbers, entity names, "
    "and any fact present in the reference answer must match; a candidate that "
    "contradicts or omits one is incorrect."
)


@dataclass(frozen=True)
class JudgeVerdict:
    """An LLM judge's verdict on whether an answer matches the reference answer."""

    correct: bool
    rationale: str


def judge_correctness(
    question: str, expected_answer: str, actual_answer: str, *, judge_model: Any
) -> JudgeVerdict:
    """Grade ``actual_answer`` against ``expected_answer`` with an LLM judge."""
    from google.adk.agents import Agent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from bigquery_agent_kit.runtime.model_resolver import resolve_model

    app_name = "bigquery_agent_kit_judge"
    agent = Agent(
        name="bigquery_eval_judge",
        model=resolve_model(judge_model),
        description="Grades a candidate answer against a reference answer.",
        instruction=_JUDGE_INSTRUCTION,
        generate_content_config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=256),
    )
    session_service = InMemorySessionService()
    runner = Runner(agent=agent, app_name=app_name, session_service=session_service)

    user_id, session_id = "judge", str(uuid.uuid4())
    session_service.create_session_sync(app_name=app_name, user_id=user_id, session_id=session_id)
    prompt = (
        f"Question: {question}\n"
        f"Reference answer: {expected_answer}\n"
        f"Candidate answer: {actual_answer}"
    )
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    raw_text = ""
    for event in runner.run(user_id=user_id, session_id=session_id, new_message=message):
        if not event.is_final_response():
            continue
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) or []
        raw_text = "".join(part.text or "" for part in parts if getattr(part, "text", None)).strip()
    return _parse_judge_verdict(raw_text)


def _parse_judge_verdict(raw_text: str) -> JudgeVerdict:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            cleaned = cleaned.split("\n", 1)[1]
    try:
        data = json.loads(cleaned)
        return JudgeVerdict(
            correct=bool(data.get("correct")), rationale=str(data.get("rationale", ""))
        )
    except (json.JSONDecodeError, AttributeError):
        return JudgeVerdict(
            correct=False, rationale=f"Could not parse judge response: {raw_text!r}"
        )
