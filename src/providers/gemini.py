"""Unified LLM gateway: Gemini first, Groq as fallback.

* ``text()`` returns free text. JSON mode is used ONLY by ``json()`` (the old
  code forced ``application/json`` on the narration script too).
* Truncated outputs are rejected, never silently accepted as a finished script.
* Every call is metered by the shared ``Budget`` and errors are classified.
"""
from __future__ import annotations

import json
import time
from typing import Any

from src.errors import (
    AuthError, BudgetExceeded, InvalidResponseError, PipelineError, RateLimitError, RetryableError,
    classify_exception, classify_http_status,
)
from src.utils import http
from src.utils.log import get_logger

log = get_logger("llm")


def _classify_gemini(exc: Exception) -> PipelineError | None:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int):
        return classify_http_status(code, str(exc))
    return classify_exception(exc)


class Gemini:
    def __init__(self, settings, budget, gemini_key: str = "", groq_key: str = "", client=None, http_session=None,
                 sleep=time.sleep):
        self.cfg = settings.llm
        self.budget = budget
        self.gemini_key = (gemini_key or "").strip()
        self.groq_key = (groq_key or "").strip()
        if not self.gemini_key and not self.groq_key and client is None:
            raise AuthError("Set GEMINI_API_KEY or GROQ_API_KEY")
        self.client = client
        if self.client is None and self.gemini_key:
            from google import genai

            self.client = genai.Client(api_key=self.gemini_key)
        self.http = http_session or http.session()
        self._sleep = sleep

    # ------------------------------------------------------------- public API
    def text(self, prompt: str, system: str | None = None, max_tokens: int = 8192, temperature: float = 0.5) -> str:
        return self._complete(prompt, system, max_tokens, temperature, json_mode=False)

    def json(self, prompt: str, system: str | None = None, max_tokens: int = 8192, temperature: float = 0.2,
             attempts: int = 2) -> Any:
        system = (system or "") + "\nReturn ONLY valid JSON. No markdown fences. No commentary."
        last: Exception | None = None
        for attempt in range(attempts):
            hint = "" if attempt == 0 else "\n\nYour previous reply was not valid JSON. Reply with JSON only."
            raw = self._complete(prompt + hint, system, max_tokens, temperature, json_mode=True)
            try:
                return self._parse_json(raw)
            except InvalidResponseError as exc:
                last = exc
                log.warning("invalid JSON from LLM; retrying", attempt=attempt + 1)
        raise last  # type: ignore[misc]

    def vision_json(self, prompt: str, images: list[bytes], mime: str = "image/jpeg", max_tokens: int = 4000) -> Any:
        """Ask Gemini about images and parse a JSON answer. Gemini only (Groq has no vision here)."""
        if not self.client:
            raise AuthError("Vision requires GEMINI_API_KEY")
        from google.genai import types

        parts: list[Any] = [prompt + "\nReturn ONLY valid JSON. No markdown fences."]
        parts += [types.Part.from_bytes(data=img, mime_type=mime) for img in images]
        last: Exception | None = None
        for model in self.cfg.gemini_models:
            try:
                self.budget.spend("gemini_calls")
                resp = self.client.models.generate_content(
                    model=model, contents=parts,
                    config={"temperature": 0.1, "max_output_tokens": max_tokens, "response_mime_type": "application/json"})
                if not getattr(resp, "text", None):
                    raise InvalidResponseError(f"{model} returned empty vision output")
                return self._parse_json(resp.text)
            except BudgetExceeded:
                raise
            except PipelineError as exc:
                if not exc.retryable and not isinstance(exc, InvalidResponseError):
                    raise
                last = exc
            except Exception as exc:  # noqa: BLE001 - classified below
                classified = _classify_gemini(exc)
                if classified is None:
                    raise
                if isinstance(classified, AuthError):
                    raise classified from exc
                last = classified
        raise RetryableError(f"Vision request failed on all models: {last}")

    # --------------------------------------------------------------- internals
    def _complete(self, prompt: str, system: str | None, max_tokens: int, temperature: float, json_mode: bool) -> str:
        last: Exception | None = None
        if self.client:
            for model in self.cfg.gemini_models:
                for attempt in range(self.cfg.max_retries + 1):
                    try:
                        return self._gemini_once(model, prompt, system, max_tokens, temperature, json_mode)
                    except BudgetExceeded:
                        raise  # a hard stop: never try another model on an exhausted budget
                    except Exception as exc:  # noqa: BLE001 - classified below
                        classified = _classify_gemini(exc) if not isinstance(exc, PipelineError) else exc
                        if classified is None:
                            raise
                        last = classified
                        if isinstance(classified, (InvalidResponseError,)) or (
                                not classified.retryable and not isinstance(classified, AuthError)):
                            log.warning("gemini model unusable for this request; next model", model=model,
                                        error=str(classified)[:160])
                            break
                        if isinstance(classified, AuthError):
                            log.warning("gemini auth/permission error; next model", model=model)
                            break
                        if attempt < self.cfg.max_retries:
                            delay = classified.retry_after if isinstance(classified, RateLimitError) and classified.retry_after \
                                else self.cfg.retry_base_seconds * (2 ** attempt)
                            self._sleep(min(delay, 60))
                        else:
                            log.warning("gemini model failed; next model", model=model, error=str(classified)[:160])
        if self.groq_key:
            for model in self.cfg.groq_models:
                try:
                    return self._groq(model, prompt, system, max_tokens, temperature, json_mode)
                except BudgetExceeded:
                    raise
                except Exception as exc:  # noqa: BLE001 - classified below
                    classified = classify_exception(exc)
                    if classified is None:
                        raise
                    last = classified
                    log.warning("groq model failed; next fallback", model=model, error=str(classified)[:160])
        if isinstance(last, PipelineError) and not last.retryable:
            # Auth, truncation and other permanent faults keep their own type so
            # the caller can tell "try again later" from "this will never work".
            raise last
        raise RetryableError(f"All writing LLMs failed. Last error: {last}")

    def _gemini_once(self, model, prompt, system, max_tokens, temperature, json_mode) -> str:
        self.budget.spend("gemini_calls")
        contents = f"{system}\n\n{prompt}" if system else prompt
        config: dict[str, Any] = {"temperature": temperature, "max_output_tokens": max_tokens}
        if json_mode:
            config["response_mime_type"] = "application/json"
        response = self.client.models.generate_content(model=model, contents=contents, config=config)
        text = getattr(response, "text", None)
        if not text or not text.strip():
            raise InvalidResponseError(f"Gemini {model} returned empty output")
        reason = ""
        try:
            reason = str(response.candidates[0].finish_reason)
        except (AttributeError, IndexError, TypeError):
            pass
        if "MAX_TOKENS" in reason.upper():
            raise InvalidResponseError(f"Gemini {model} output was truncated (max_tokens={max_tokens})")
        log.info("llm ok", provider="gemini", model=model)
        return text.strip()

    def _groq(self, model, prompt, system, max_tokens, temperature, json_mode) -> str:
        self.budget.spend("gemini_calls")
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        resp = self.http.post("https://api.groq.com/openai/v1/chat/completions", timeout=self.cfg.request_timeout,
                              headers={"Authorization": f"Bearer {self.groq_key}", "Content-Type": "application/json"},
                              json=body)
        http.check_status(resp, f"groq {model}")
        choice = (resp.json().get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content", "")
        if not content:
            raise InvalidResponseError("Groq returned empty content")
        if choice.get("finish_reason") == "length":
            raise InvalidResponseError(f"Groq {model} output was truncated")
        log.info("llm ok", provider="groq", model=model)
        return content.strip()

    @staticmethod
    def _parse_json(raw: str) -> Any:
        text = (raw or "").strip()
        if not text:
            raise InvalidResponseError("Invalid JSON from LLM: empty response")
        if text.startswith("```"):
            lines = text.splitlines()[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        candidates = [text]
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            a, b = text.find(open_ch), text.rfind(close_ch)
            if 0 <= a < b:
                candidates.append(text[a:b + 1])
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        raise InvalidResponseError(f"Invalid JSON from LLM: {text[:300]}")
