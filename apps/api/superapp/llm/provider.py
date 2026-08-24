"""The provider wrapper (architecture §5): every model call in the app goes through here.

- Model-per-task routing via settings (big model for drafting, small for classification).
- Cost + token logging into `events` so "what does the nutrition agent cost per month?"
  is always answerable with SQL.
- Deterministic stub mode when no API key is configured, so the whole spine runs offline
  (and the golden-set tests never depend on the network).
"""
import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..config import get_settings
from ..substrate.events import append_event


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    stubbed: bool = False


class LLMProvider:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _model_for_task(self, task: str) -> str:
        return {
            "routing": self.settings.model_routing,
            "classification": self.settings.model_routing,
        }.get(task, self.settings.model_default)

    def complete(
        self,
        db: Session,
        *,
        user_id: str,
        agent: str,
        task: str,
        system: str,
        prompt: str,
    ) -> LLMResponse:
        model = self._model_for_task(task)

        if not self.settings.anthropic_api_key:
            response = LLMResponse(
                text=json.dumps({"stub": True, "task": task, "echo": prompt[:200]}),
                model=model,
                input_tokens=len(system + prompt) // 4,
                output_tokens=32,
                stubbed=True,
            )
        else:
            # Lazy import so the API key path is the only one needing the SDK installed.
            import anthropic

            client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
            msg = client.messages.create(
                model=model,
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            response = LLMResponse(
                text="".join(b.text for b in msg.content if b.type == "text"),
                model=model,
                input_tokens=msg.usage.input_tokens,
                output_tokens=msg.usage.output_tokens,
            )

        append_event(
            db,
            user_id=user_id,
            type="llm_call",
            agent=agent,
            payload={
                "task": task,
                "model": response.model,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "stubbed": response.stubbed,
            },
        )
        return response
