"""AI provider abstraction with conversation history and streaming support."""
import base64
import logging
from abc import ABC, abstractmethod
from typing import Callable, Optional

from config import AI_MODEL, AI_PROMPT, AI_PROVIDER, ANTHROPIC_API_KEY, OPENAI_API_KEY

logger = logging.getLogger(__name__)

LIVE_ASSISTANT_PROMPT = """\
You are a real-time conversation assistant. Based on the conversation transcript, \
provide brief, helpful responses when there's a pause. Keep responses concise \
(2-3 sentences max). Focus on facts, suggestions, or clarifications that would \
help the speaker."""

INTERVIEW_COACH_PROMPT = """\
You are an expert interview coach with 20+ years of experience conducting mock interviews across \
software engineering, product management, consulting, finance, and general professional roles. \
You have coached thousands of candidates — fresh grads to senior executives.

Analyze the interview transcript below and produce a thorough, honest, and actionable coaching report. \
Be encouraging but direct. Do not soften critical feedback.

Structure your response EXACTLY as follows (use these headings verbatim):

## Overall Performance Score: X/10
*One-sentence verdict.*

---

## Summary
2–3 sentences on the overall arc of the interview — tone, confidence, and how well the candidate handled the conversation.

---

## Question-by-Question Breakdown
For every question you can identify in the transcript, provide:

### Q: [exact or paraphrased question]
**Answer given:** Brief summary of what the candidate said.
**Assessment:** ✅ Strong / ⚠️ Needs Work / ❌ Weak
**What worked:** ...
**What to improve:** ...
**Stronger answer:** A concrete, full example of a better response.

---

## Strengths
Specific bullet points of what the candidate did well, with evidence from the transcript.

---

## Gaps & Weaknesses
Specific bullet points of areas that need work, with evidence from the transcript.

---

## Communication Style
- **Filler words:** List any (um, uh, like, you know, so...) with estimated frequency and impact on impression.
- **Confidence level:** High / Medium / Low — cite specific moments.
- **Clarity:** How well ideas were structured and expressed.
- **Conciseness:** Were answers appropriately scoped, too long, or too short?
- **Energy & presence:** How engaging was the candidate throughout?

---

## Lessons Learned
3–5 key takeaways the candidate should internalize before their next interview.

---

## Action Items
Specific, concrete things to practice or study — numbered list, actionable within 1 week:
1. ...
2. ...
3. ...

---

## Sample Strong Answers
For the 2–3 weakest responses identified above, write out a full, polished example answer the candidate could use as a model.
"""

SYSTEM_PROMPT = """\
You are a direct, technical AI assistant embedded in a screen-capture tool.

Rules — follow them strictly:
- No filler. Never open with "Great question!", "Certainly!", "Of course!", \
"I'd be happy to help", or any similar phrase. Start with the answer.
- Be concise. Say exactly what needs to be said, nothing more.
- Use markdown for structure: ## headers, **bold** for key terms, \
`inline code` for identifiers/commands, and fenced code blocks with language tags \
(```python, ```bash, ```json, etc.) for any multi-line code.
- For technical or multi-step answers, break them down clearly with numbered \
steps or short sections.
- When analyzing a screenshot, describe what you see directly and lead with \
anything actionable or notable.
- Assume the reader is technical unless evidence in the screenshot says otherwise.\
"""


class AIService(ABC):
    @abstractmethod
    def analyze_screenshot(self, image_png: bytes, extra_prompt: Optional[str] = None) -> str:
        """Analyze a PNG screenshot and return a description."""

    @abstractmethod
    def chat(self, message: str, image_png: Optional[bytes] = None) -> str:
        """Answer a follow-up message, optionally with a screenshot for context."""

    @abstractmethod
    def stream_chat(self, message: str, image_png: Optional[bytes],
                    on_token: Callable[[str], None]) -> None:
        """Stream chat response tokens; calls on_token for each chunk."""

    @abstractmethod
    def analyze_interview(self, transcript: str) -> str:
        """Analyze a full interview transcript and return a coaching report."""

    @abstractmethod
    def live_response(self, transcript: str) -> str:
        """Generate a brief real-time response based on accumulated conversation transcript."""

    @abstractmethod
    def clear_history(self) -> None:
        """Reset conversation history."""


class ClaudeService(AIService):
    def __init__(self) -> None:
        import anthropic
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self._model = AI_MODEL
        self._history: list[dict] = []

    def _image_block(self, image_png: bytes) -> dict:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(image_png).decode(),
            },
        }

    def clear_history(self) -> None:
        self._history.clear()

    def analyze_screenshot(self, image_png: bytes, extra_prompt: Optional[str] = None) -> str:
        prompt = extra_prompt or AI_PROMPT
        content = [self._image_block(image_png), {"type": "text", "text": prompt}]
        response = self.client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        return response.content[0].text or ""

    def chat(self, message: str, image_png: Optional[bytes] = None) -> str:
        content: list = []
        if image_png:
            content.append(self._image_block(image_png))
        content.append({"type": "text", "text": message})
        self._history.append({"role": "user", "content": content})
        response = self.client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=self._history,
        )
        result = response.content[0].text or ""
        self._history.append({"role": "assistant", "content": result})
        return result

    def stream_chat(self, message: str, image_png: Optional[bytes],
                    on_token: Callable[[str], None]) -> None:
        content: list = []
        if image_png:
            content.append(self._image_block(image_png))
        content.append({"type": "text", "text": message})
        self._history.append({"role": "user", "content": content})
        full_text = ""
        with self.client.messages.stream(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=self._history,
        ) as stream:
            for text_chunk in stream.text_stream:
                full_text += text_chunk
                on_token(text_chunk)
        self._history.append({"role": "assistant", "content": full_text})

    def analyze_interview(self, transcript: str) -> str:
        response = self.client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=INTERVIEW_COACH_PROMPT,
            messages=[{"role": "user", "content": f"Here is the interview transcript:\n\n{transcript}"}],
        )
        return response.content[0].text or ""

    def live_response(self, transcript: str) -> str:
        response = self.client.messages.create(
            model=self._model,
            max_tokens=256,
            system=LIVE_ASSISTANT_PROMPT,
            messages=[{"role": "user", "content": f"Conversation so far:\n\n{transcript}"}],
        )
        return response.content[0].text or ""


class OpenAIService(AIService):
    def __init__(self) -> None:
        import openai
        self.client = openai.OpenAI(api_key=OPENAI_API_KEY)
        self._model = "gpt-4o"
        self._history: list[dict] = []

    def _image_block(self, image_png: bytes) -> dict:
        b64 = base64.b64encode(image_png).decode()
        return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}

    def clear_history(self) -> None:
        self._history.clear()

    def analyze_screenshot(self, image_png: bytes, extra_prompt: Optional[str] = None) -> str:
        prompt = extra_prompt or AI_PROMPT
        content = [self._image_block(image_png), {"type": "text", "text": prompt}]
        response = self.client.chat.completions.create(
            model=self._model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        )
        return response.choices[0].message.content or ""

    def chat(self, message: str, image_png: Optional[bytes] = None) -> str:
        content: list = []
        if image_png:
            content.append(self._image_block(image_png))
        content.append({"type": "text", "text": message})
        self._history.append({"role": "user", "content": content})
        response = self.client.chat.completions.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + self._history,
        )
        result = response.choices[0].message.content or ""
        self._history.append({"role": "assistant", "content": result})
        return result

    def stream_chat(self, message: str, image_png: Optional[bytes],
                    on_token: Callable[[str], None]) -> None:
        content: list = []
        if image_png:
            content.append(self._image_block(image_png))
        content.append({"type": "text", "text": message})
        self._history.append({"role": "user", "content": content})
        full_text = ""
        stream = self.client.chat.completions.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + self._history,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                delta = chunk.choices[0].delta.content
                full_text += delta
                on_token(delta)
        self._history.append({"role": "assistant", "content": full_text})

    def analyze_interview(self, transcript: str) -> str:
        response = self.client.chat.completions.create(
            model=self._model,
            max_tokens=4096,
            messages=[
                {"role": "system", "content": INTERVIEW_COACH_PROMPT},
                {"role": "user", "content": f"Here is the interview transcript:\n\n{transcript}"},
            ],
        )
        return response.choices[0].message.content or ""

    def live_response(self, transcript: str) -> str:
        response = self.client.chat.completions.create(
            model=self._model,
            max_tokens=256,
            messages=[
                {"role": "system", "content": LIVE_ASSISTANT_PROMPT},
                {"role": "user", "content": f"Conversation so far:\n\n{transcript}"},
            ],
        )
        return response.choices[0].message.content or ""


def get_ai_service() -> AIService:
    if AI_PROVIDER == "openai":
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not set in .env")
        logger.info("Using OpenAI (gpt-4o)")
        return OpenAIService()
    else:
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is not set in .env")
        logger.info("Using Claude (%s)", AI_MODEL)
        return ClaudeService()
