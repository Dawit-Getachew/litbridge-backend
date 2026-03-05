"""Unified LLM client for OpenAI/OpenRouter chat completions."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import httpx

from src.core.config import Settings, get_settings
from src.schemas.records import UnifiedRecord

TLDR_SYSTEM_PROMPT = "Generate a one-sentence TLDR summary of this biomedical paper. Be concise and precise."

ANALYSIS_SYSTEM_PROMPT = (
    "You are a biomedical research analyst. Analyze these search results and provide a comprehensive synthesis, "
    "highlighting key findings, evidence quality, consensus, contradictions, and practical implications."
)

QUICK_SUMMARY_SYSTEM_PROMPT = (
    "You are a biomedical research analyst. Provide a concise single-paragraph synthesis of these search results, "
    "highlighting the most important findings and their implications."
)

CHAT_SYSTEM_PROMPT = (
    "You are a biomedical research assistant embedded in LitBridge, a federated literature search platform. "
    "The user has performed a search and is now asking follow-up questions about the results.\n\n"
    "Guidelines:\n"
    "- When discussing specific papers, reference them by first author and year (e.g. 'Zhang et al., 2024')\n"
    "- Be precise about claims — distinguish findings from interpretations\n"
    "- When comparing papers, structure your response clearly with each paper's position\n"
    "- If the user asks to 'explain' a paper, provide a clear, accessible summary of its key findings, "
    "methodology, and implications\n"
    "- If the user asks to 'deep dive', provide detailed analysis including methodology, limitations, "
    "statistical approach, and how findings relate to the broader field\n"
    "- Cite specific details from the abstracts when available\n"
    "- If you're unsure which paper the user means, ask for clarification\n"
)


class LLMClient:
    """Lightweight async chat-completions client for configured provider."""

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.llm_base_url
        self.api_key = self.settings.llm_api_key
        self.model = self.settings.llm_model
        self.client = client or httpx.AsyncClient()

    async def generate_tldr(self, title: str, abstract: str) -> str | None:
        """Generate a one-sentence TLDR for a paper."""
        if not abstract.strip():
            return None

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": TLDR_SYSTEM_PROMPT},
                {"role": "user", "content": f"Title: {title}\n\nAbstract: {abstract}"},
            ],
            "temperature": 0.2,
            "max_tokens": 150,
        }
        try:
            response = await self.client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=30.0,
            )
            if response.status_code >= 400:
                return None
            return self._extract_message_content(response.json())
        except Exception:
            return None

    async def stream_analysis(
        self,
        query: str,
        records: list[UnifiedRecord],
    ) -> AsyncGenerator[str, None]:
        """Stream a comprehensive analysis for deep-thinking mode."""
        payload = {
            "model": self.model,
            "stream": True,
            "messages": [
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Query: {query}\n\n"
                        f"Records:\n{self._serialize_records(records)}\n\n"
                        "Provide a comprehensive synthesis."
                    ),
                },
            ],
            "temperature": 0.3,
        }

        async for chunk in self._stream_completions(payload, timeout=60.0):
            yield chunk

    async def stream_quick_summary(
        self,
        query: str,
        records: list[UnifiedRecord],
    ) -> AsyncGenerator[str, None]:
        """Stream a concise single-paragraph synthesis for light-thinking mode."""
        payload = {
            "model": self.model,
            "stream": True,
            "messages": [
                {"role": "system", "content": QUICK_SUMMARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Query: {query}\n\n"
                        f"Records:\n{self._serialize_records(records)}\n\n"
                        "Return a concise single-paragraph summary."
                    ),
                },
            ],
            "temperature": 0.3,
            "max_tokens": 350,
        }

        async for chunk in self._stream_completions(payload, timeout=30.0):
            yield chunk

    async def quick_summary(self, query: str, records: list[UnifiedRecord]) -> str | None:
        """Return a short, single-paragraph synthesis for light-thinking mode."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": QUICK_SUMMARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Query: {query}\n\n"
                        f"Records:\n{self._serialize_records(records)}\n\n"
                        "Return a concise single-paragraph summary."
                    ),
                },
            ],
            "temperature": 0.3,
            "max_tokens": 350,
        }

        try:
            response = await self.client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=30.0,
            )
            if response.status_code >= 400:
                return None
            return self._extract_message_content(response.json())
        except Exception:
            return None

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        """Stream a multi-turn chat response given a full message list.

        The caller is responsible for constructing the message list including
        the system prompt, conversation history, and the latest user message.
        """
        payload = {
            "model": self.model,
            "stream": True,
            "messages": messages,
            "temperature": 0.3,
        }

        async for chunk in self._stream_completions(payload, timeout=60.0):
            yield chunk

    async def _stream_completions(
        self,
        payload: dict,
        timeout: float = 60.0,
    ) -> AsyncGenerator[str, None]:
        """Shared streaming logic for all streaming endpoints."""
        try:
            async with self.client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    return

                async for line in response.aiter_lines():
                    chunk = self._parse_stream_line(line)
                    if chunk is None:
                        continue
                    if chunk == "[DONE]":
                        break
                    yield chunk
        except Exception:
            return

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.settings.LLM_PROVIDER == "openrouter":
            headers["HTTP-Referer"] = "https://litbridge.local"
            headers["X-Title"] = "LitBridge"
        return headers

    def _extract_message_content(self, payload: dict) -> str | None:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, dict):
            return None
        message = first.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if not isinstance(content, str):
            return None
        value = content.strip()
        return value or None

    def _parse_stream_line(self, line: str) -> str | None:
        if not line or not line.startswith("data:"):
            return None
        data = line[5:].strip()
        if not data:
            return None
        if data == "[DONE]":
            return "[DONE]"

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return None

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, dict):
            return None

        delta = first.get("delta")
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str) and content:
                return content

        message = first.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content:
                return content

        text = first.get("text")
        if isinstance(text, str) and text:
            return text
        return None

    def _serialize_records(self, records: list[UnifiedRecord]) -> str:
        trimmed = []
        for record in records[:25]:
            trimmed.append(
                {
                    "id": record.id,
                    "title": record.title,
                    "year": record.year,
                    "journal": record.journal,
                    "citation_count": record.citation_count,
                    "tldr": record.tldr,
                    "abstract": record.abstract,
                    "source": record.source.value,
                }
            )
        return json.dumps(trimmed, ensure_ascii=True)
