"""The provider wrapper (architecture §5): every model call in the app goes through here.

What every call gets for free:
- Model-per-task routing (Opus 5 for cognition, Haiku for routing/classification)
  with per-task effort defaults (`output_config.effort`) — routing runs at "low".
- Prompt caching: the agent's system prompt is marked as a cached prefix. For this
  to pay, system prompts must be FROZEN — no timestamps, no per-run values. Volatile
  content (the context slice, the trigger) belongs in `prompt`.
- Refusal fallbacks on the default model (server-side `fallbacks: "default"`), and
  `stop_reason` surfaced so agents can handle a refusal instead of parsing garbage.
- Cost + token logging into `events` (input/output/cache tokens and estimated USD)
  so "what does the nutrition agent cost per month?" is always answerable with SQL.
- Deterministic stub mode when no API key is configured, so the whole spine runs
  offline (and the golden-set tests never depend on the network).

Two entry points:
- complete()        one call, request path or think runs a user is waiting on.
- complete_batch()  many calls via the Batches API at 50% price — for cron think
  runs (evening summary, weekly insights, outfit precompute) where nobody waits.
"""
import json
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..config import get_settings
from ..substrate.events import append_event

# First-party API prices per 1M tokens (input, output). Cache reads bill at 0.1x
# input, cache writes (5-min TTL) at 1.25x, batches at 50% of everything.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Tasks that route to the small model, and their spend profile.
ROUTING_TASKS = {"routing", "classification", "triage", "voice_intent"}


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    stop_reason: str = "end_turn"
    cost_usd: float = 0.0
    stubbed: bool = False
    batched: bool = False

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


def _cost_usd(model: str, *, input_tokens: int, output_tokens: int,
              cache_read: int, cache_creation: int, batched: bool) -> float:
    in_price, out_price = PRICES_PER_MTOK.get(model, (0.0, 0.0))
    cost = (
        input_tokens * in_price
        + cache_creation * in_price * 1.25
        + cache_read * in_price * 0.10
        + output_tokens * out_price
    ) / 1_000_000
    return round(cost * (0.5 if batched else 1.0), 6)


class LLMProvider:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _model_for_task(self, task: str) -> str:
        return self.settings.model_routing if task in ROUTING_TASKS else self.settings.model_default

    def _effort_for_task(self, task: str) -> str:
        return "low" if task in ROUTING_TASKS else "high"

    def _max_tokens_for_task(self, task: str) -> int:
        return 1024 if task in ROUTING_TASKS else 16000

    def _build_params(self, *, task: str, system: str, prompt: str,
                      effort: str | None, max_tokens: int | None,
                      images: list[tuple[str, str]] | None = None,
                      schema: dict | None = None) -> dict:
        """images: list of (media_type, base64_data). schema: JSON Schema — when
        set, the response is constrained to valid JSON matching it
        (output_config.format), so callers can json.loads() without ceremony."""
        model = self._model_for_task(task)
        content: str | list = prompt
        if images:
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}}
                for mt, data in images
            ] + [{"type": "text", "text": prompt}]
        output_config: dict = {}
        if not model.startswith("claude-haiku"):  # Haiku has no effort parameter
            output_config["effort"] = effort or self._effort_for_task(task)
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        params: dict = {
            "model": model,
            "max_tokens": max_tokens or self._max_tokens_for_task(task),
            # Cached prefix: tools (none) + system. Volatile content stays in messages,
            # after the breakpoint. Below the model's minimum prefix this is a silent
            # no-op, never an error.
            "system": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": "user", "content": content}],
            # Thinking omitted on purpose: adaptive by default on Opus 5; routing
            # tasks on Haiku don't need it.
        }
        if output_config:
            params["output_config"] = output_config
        return params

    def _stub(self, *, task: str, model: str, system: str, prompt: str, batched: bool) -> LLMResponse:
        return LLMResponse(
            text=json.dumps({"stub": True, "task": task, "echo": prompt[:200]}),
            model=model,
            input_tokens=len(system + prompt) // 4,
            output_tokens=32,
            stubbed=True,
            batched=batched,
        )

    def _log(self, db: Session, *, user_id: str, agent: str, task: str, resp: LLMResponse) -> None:
        append_event(
            db,
            user_id=user_id,
            type="llm_call",
            agent=agent,
            payload={
                "task": task,
                "model": resp.model,
                "input_tokens": resp.input_tokens,
                "output_tokens": resp.output_tokens,
                "cache_read_tokens": resp.cache_read_tokens,
                "cache_creation_tokens": resp.cache_creation_tokens,
                "stop_reason": resp.stop_reason,
                "cost_usd": resp.cost_usd,
                "batched": resp.batched,
                "stubbed": resp.stubbed,
            },
        )

    def _from_message(self, msg, *, batched: bool) -> LLMResponse:
        usage = msg.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        return LLMResponse(
            text="".join(b.text for b in msg.content if b.type == "text"),
            model=msg.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            stop_reason=msg.stop_reason or "end_turn",
            cost_usd=_cost_usd(
                msg.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read=cache_read,
                cache_creation=cache_creation,
                batched=batched,
            ),
            batched=batched,
        )

    def complete(
        self,
        db: Session,
        *,
        user_id: str,
        agent: str,
        task: str,
        system: str,
        prompt: str,
        effort: str | None = None,
        max_tokens: int | None = None,
        images: list[tuple[str, str]] | None = None,
        schema: dict | None = None,
    ) -> LLMResponse:
        params = self._build_params(
            task=task, system=system, prompt=prompt, effort=effort,
            max_tokens=max_tokens, images=images, schema=schema,
        )

        if not self.settings.anthropic_api_key:
            response = self._stub(task=task, model=params["model"], system=system, prompt=prompt, batched=False)
        else:
            # Lazy import so the API key path is the only one needing the SDK installed.
            import anthropic

            client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
            if params["model"] == "claude-opus-5":
                # Server-side refusal fallbacks: on a policy decline the API re-runs
                # the request on a fallback model inside the same call. extra_body so
                # older SDK pins still forward it.
                msg = client.beta.messages.create(
                    **params,
                    betas=["server-side-fallback-2026-07-01"],
                    extra_body={"fallbacks": "default"},
                )
            else:
                msg = client.messages.create(**params)
            response = self._from_message(msg, batched=False)

        self._log(db, user_id=user_id, agent=agent, task=task, resp=response)
        return response

    def complete_batch(
        self,
        db: Session,
        *,
        user_id: str,
        agent: str,
        task: str,
        system: str,
        prompts: dict[str, str],
        effort: str | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, LLMResponse | None]:
        """Run many prompts through the Batches API at 50% price. Blocks while
        polling (fine for cron-triggered think runs; nobody is waiting on HTTP).
        Returns {custom_id: LLMResponse}, None for requests that errored
        (an llm_call event with stop_reason="errored" is logged for those).
        """
        results: dict[str, LLMResponse | None] = {}

        if not self.settings.anthropic_api_key:
            model = self._model_for_task(task)
            for custom_id, prompt in prompts.items():
                results[custom_id] = self._stub(
                    task=task, model=model, system=system, prompt=prompt, batched=True
                )
                self._log(db, user_id=user_id, agent=agent, task=task, resp=results[custom_id])
            return results

        import anthropic
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
        batch = client.messages.batches.create(
            requests=[
                Request(
                    custom_id=custom_id,
                    params=MessageCreateParamsNonStreaming(
                        **self._build_params(
                            task=task, system=system, prompt=prompt,
                            effort=effort, max_tokens=max_tokens,
                        )
                    ),
                )
                for custom_id, prompt in prompts.items()
            ]
        )

        deadline = time.monotonic() + self.settings.llm_batch_max_wait_seconds
        while True:
            batch = client.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"Batch {batch.id} still {batch.processing_status} after max wait")
            time.sleep(self.settings.llm_batch_poll_seconds)

        for result in client.messages.batches.results(batch.id):
            if result.result.type == "succeeded":
                resp = self._from_message(result.result.message, batched=True)
            else:
                resp = None
                self._log(
                    db, user_id=user_id, agent=agent, task=task,
                    resp=LLMResponse(
                        text="", model=self._model_for_task(task),
                        input_tokens=0, output_tokens=0,
                        stop_reason="errored", batched=True,
                    ),
                )
            results[result.custom_id] = resp
            if resp is not None:
                self._log(db, user_id=user_id, agent=agent, task=task, resp=resp)
        return results
