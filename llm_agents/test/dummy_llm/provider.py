"""llm_agents.test.dummy_llm.provider — the DUMMY chat completions engine.

``DummyLlmProvider`` is a drop-in stand-in for
``deep_agents.core.provider.ZhipuChatProvider``: the same ONE method the
whole harness touches (``async chat_complete(messages, *, model=,
tools=, temperature=, max_tokens=)`` returning the assistant message
dict), satisfied from CANNED replies instead of an HTTP call — zero API
key, zero network, zero spend, fully deterministic. It is also the
ENGINE behind the sibling FastAPI server (``server.py``): the server
simulates the real provider's HTTP surface (Bearer auth, the chat
completions envelope, HTTP 429 rate-limit errors) over THIS object, so
the real ``ZhipuChatProvider`` transport — retries, error codes, the
repair lane's classification — runs against a fake.

Reply strategy (``DummyReplyBook``, the sibling module):

* a rule catalog mapping DIFF query texts to DIFF canned replies
  (trend / volume / momentum / ... findings, the four review seats'
  lenses, the synthesis, the supervisor's no-action verdict, a few
  well-formed tool-call demos),
* shape fallbacks so ANY conversation settles (planning shape → a
  plan JSON the real ``parse_plan_reply`` accepts; tools declared → a
  finding; else → a final answer),
* the ``TOOL:name {json}`` directive inside a query text, driving any
  declared tool from a test without writing a rule.

Two scripted layers on top, for tests that need EXACT sequences rather
than text matching:

* ``script=`` — per-call replies popped in order (plain str → content,
  ReplySpec → rendered, dict → verbatim assistant message, Exception →
  raised);
* ``fail_matching=`` — a query regex that RAISES (the repair lane's
  transient-failure probes inject a 429 through a real provider object
  this way).

Every call is recorded (``calls``) so a test asserts on the messages it
handed over, not only on what came back.

Imported ONLY by tests, temp_scripts probes, the dummy llm server, and
the provider factory (``deep_agents.llm_config.build_chat_provider``) —
never by the harness's own modules.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Union

from pydantic import BaseModel, Field

from llm_agents.test.dummy_llm.replies import (
    DummyReplyBook, ReplySpec, ToolCallSpec,
)

logger = logging.getLogger(__name__)

# One scripted entry: plain text → a content reply; ReplySpec →
# rendered by the book; a dict → the assistant message verbatim; an
# Exception instance → raised (one injected failure).
ScriptEntry = Union[str, Dict[str, Any], ReplySpec, BaseException]


class DummyCall(BaseModel):
    """One recorded chat_complete call — what a test asserts on."""
    index: int
    model: Optional[str] = None
    n_messages: int
    system_chars: int
    query: str = ""
    tool_names: List[str] = Field(default_factory=list)
    reply_kind: str = ""
    reply: Dict[str, Any] = Field(default_factory=dict)


class DummyLlmProvider:
    """The offline chat provider / completions engine — the harness's
    duck-typed ``chat_complete`` contract over canned replies, plus the
    full-ENVELOPE method the simulated HTTP endpoint (``server.py``)
    serves. In-process it replaces ``ZhipuChatProvider`` directly; over
    HTTP the real provider class (retries, error codes, audit rows)
    runs against a server built on this engine — the most faithful
    simulation available offline.

    Args:
        reply_book: the reply strategy (a fresh default
            ``DummyReplyBook`` when None).
        script: optional per-call sequence (see module docstring) —
            takes precedence over the book until exhausted.
        script_exhausted: what happens after the script runs dry —
            ``"book"`` (default: fall through to the reply book, so a
            live run keeps completing) or ``"error"`` (a test that must
            not silently drift off its scenario).
        fail_matching: optional regex — a query matching it raises
            ``RuntimeError(fail_message)`` BEFORE any reply (injected
            provider failure; default message text says ``429 rate
            limited`` so the repair lane's transient classification
            treats it as retryable).
        fail_message: the raised failure's text.
        delay_sec: artificial per-call latency (timeout probes; 0 = the
            default instant reply).

    Naming/class attributes mirror ``ZhipuChatProvider`` so config and
    logs read identically; ``name = "dummy"`` is also the
    ``ext_llm_provider`` label the secret registry row carries.
    """

    name = "dummy"
    API_KEY_ENV_VARS: Sequence[str] = ()
    DEFAULT_MODEL = "dummy-1"

    def __init__(
        self,
        *,
        reply_book: Optional[DummyReplyBook] = None,
        script: Optional[Sequence[ScriptEntry]] = None,
        script_exhausted: str = "book",
        fail_matching: Optional[str] = None,
        fail_message: str = "dummy provider: 429 rate limited (injected)",
        delay_sec: float = 0.0,
    ) -> None:
        if script_exhausted not in ("book", "error"):
            raise ValueError(
                "script_exhausted must be 'book' or 'error'")
        self.reply_book = reply_book or DummyReplyBook()
        self.script: List[ScriptEntry] = list(script or [])
        self.script_exhausted = script_exhausted
        self.fail_re = (re.compile(fail_matching, re.IGNORECASE)
                        if fail_matching else None)
        self.fail_message = fail_message
        self.delay_sec = max(0.0, float(delay_sec))
        self.calls: List[DummyCall] = []
        self._call_seq = 0

    # ------------------------------------------------------------------
    # The harness contract (ZhipuChatProvider.chat_complete's signature)
    # ------------------------------------------------------------------

    async def chat_complete(
        self, messages: Sequence[Dict[str, Any]], *,
        model: Optional[str] = None,
        tools: Optional[Sequence[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """One canned chat completion — the assistant message dict.

        Resolution order: an injected failure (``fail_matching`` on the
        last user text) → the script's next entry → the reply book
        (rule catalog, then the provider's shape fallback). No network,
        no key, no spend; the call is recorded on ``self.calls``.
        """
        tool_names = [
            str((tool.get("function") or {}).get("name") or "")
            for tool in (tools or [])]
        tool_names = [name for name in tool_names if name]
        query = self._last_user_text(messages)

        if self.delay_sec:
            await asyncio.sleep(self.delay_sec)
        if self.fail_re is not None and self.fail_re.search(query):
            raise RuntimeError(self.fail_message)

        reply: Dict[str, Any]
        if self.script:
            entry = self.script.pop(0)
            if isinstance(entry, BaseException):
                raise entry
            if isinstance(entry, str):
                reply = {"role": "assistant", "content": entry}
            elif isinstance(entry, ReplySpec):
                reply = self._render(entry)
            else:
                reply = dict(entry)
        elif self.script_exhausted == "error":
            raise RuntimeError(
                f"dummy provider: script exhausted at call "
                f"{self._call_seq + 1} (script_exhausted='error')")
        else:
            spec = self.reply_book.match(messages, tool_names)
            if spec is None:
                spec = self.reply_book.fallback(messages, tool_names)
            reply = self._render(spec)

        self._record(messages, model, tool_names, reply)
        return reply

    async def complete_from_payload(
        self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """One full chat-completion ENVELOPE from an OpenAI-style
        request payload — the method the simulated HTTP endpoint calls
        (``server.py``). Accepts the request body verbatim (``messages``
        required; ``model`` / ``tools`` / ``temperature`` / ``max_tokens``
        / ``stream`` / ``request_id`` tolerated), resolves the reply
        through the same engine as :meth:`chat_complete`, and wraps it
        in the provider response shape the transport parses
        (``choices[0].message``): ``id`` / ``created`` / ``model`` /
        ``choices`` with ``finish_reason`` ``tool_calls`` vs ``stop`` /
        a ROUGH deterministic ``usage`` estimate (chars/4 — the token
        counts are test payload, not an accounting source).
        """
        message = await self.chat_complete(
            payload.get("messages") or [],
            model=payload.get("model"),
            tools=payload.get("tools"),
            temperature=payload.get("temperature"),
            max_tokens=payload.get("max_tokens"))
        prompt_chars = sum(len(str(m.get("content") or ""))
                           for m in (payload.get("messages") or []))
        completion_chars = len(str(message.get("content") or ""))
        return {
            "id": f"chatcmpl-dummy-{self._call_seq:06d}",
            "created": int(time.time()),
            "model": str(payload.get("model") or self.DEFAULT_MODEL),
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": ("tool_calls" if message.get("tool_calls")
                                  else "stop"),
            }],
            "usage": {
                "prompt_tokens": prompt_chars // 4,
                "completion_tokens": completion_chars // 4,
                "total_tokens": (prompt_chars + completion_chars) // 4,
            },
            "request_id": payload.get("request_id"),
        }

    # ------------------------------------------------------------------
    # Introspection — what a test asserts on
    # ------------------------------------------------------------------

    @property
    def n_calls(self) -> int:
        return len(self.calls)

    def queries(self) -> List[str]:
        return [call.query for call in self.calls]

    def reply_texts(self) -> List[str]:
        return [str(call.reply.get("content") or "") for call in self.calls]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _render(self, spec: ReplySpec) -> Dict[str, Any]:
        if spec.kind == "error":
            raise RuntimeError(spec.error or "dummy provider: error reply")
        return self.reply_book.render(spec, call_seq=self._call_seq)

    def _record(self, messages: Sequence[Dict[str, Any]],
                model: Optional[str], tool_names: Sequence[str],
                reply: Dict[str, Any]) -> None:
        system = ""
        for message in messages:
            if message.get("role") == "system":
                system = str(message.get("content") or "")
                break
        self.calls.append(DummyCall(
            index=self._call_seq,
            model=model,
            n_messages=len(messages),
            system_chars=len(system),
            query=self._last_user_text(messages)[:400],
            tool_names=list(tool_names),
            reply_kind=("tool_calls" if reply.get("tool_calls")
                        else "content"),
            reply=reply,
        ))
        self._call_seq += 1

    @staticmethod
    def _last_user_text(messages: Sequence[Dict[str, Any]]) -> str:
        for message in reversed(list(messages)):
            if message.get("role") == "user":
                return str(message.get("content") or "")
        return ""


__all__ = ["DummyLlmProvider", "DummyCall", "ReplySpec", "ToolCallSpec"]
