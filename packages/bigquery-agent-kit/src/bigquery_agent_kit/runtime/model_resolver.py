"""Model-string resolution shared by the agent factory and the eval judge."""

from __future__ import annotations

from typing import Any


def resolve_model(model: Any) -> Any:
    """Wrap a ``"provider/model"`` string in ADK's LiteLLM adapter.

    A bare model name (``"gemini-2.5-flash"``) or a Vertex AI resource name
    (``"projects/..."``) is passed through untouched and runs on Vertex AI /
    the Gemini API as usual. Anything else that is a string containing ``/``
    is treated as a LiteLLM ``provider/model`` identifier (OpenAI, Anthropic,
    Groq, a self-hosted endpoint, ...), so any provider works via one input
    variable. A non-string ``model`` (e.g. an already-built ADK ``BaseLlm``)
    is passed through untouched too.
    """
    if not isinstance(model, str) or "/" not in model or model.startswith("projects/"):
        return model
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:
        raise ImportError(
            f"Model '{model}' looks like a LiteLLM 'provider/model' string, which "
            "requires the 'litellm' extra: pip install \"bigquery-agent-kit[litellm]\""
        ) from exc
    return LiteLlm(model=model)
