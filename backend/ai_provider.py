import logging
import re
from typing import Optional

from config import http_request

logger = logging.getLogger(__name__)


SHORT_ANSWER_PROMPT = (
    "This image shows a short-answer question slide. "
    "Read the question from the image carefully. "
    "Provide a concise, accurate answer to the question. "
    "Reply with ONLY the answer text, nothing else."
)


def _single_choice_prompt(options_str: str) -> str:
    return (
        f"This image shows a single-choice question slide. "
        f"Read the question and all options from the image carefully. "
        f"Choose exactly ONE correct answer from: {options_str}. "
        f"Reply with ONLY the option letter, nothing else."
    )


def _multi_choice_prompt(options_str: str) -> str:
    return (
        f"This image shows a multiple-choice question slide. "
        f"Read the question and all options from the image carefully. "
        f"Choose ALL correct answers from: {options_str}. "
        f"Reply with ONLY the option letters separated by spaces, nothing else. "
        f"Example: A C"
    )


def _vote_prompt(max_count: int, options_str: str) -> str:
    return (
        f"This image shows a voting question slide. "
        f"Read the question and all options from the image carefully. "
        f"Pick up to {max_count} option(s) you judge most reasonable / most likely to be supported from: {options_str}. "
        f"Reply with ONLY the option letter(s) separated by spaces, nothing else. "
        f"Example: A C"
    )


def _parse_letters(raw: str, options: list[str], max_count: Optional[int] = None) -> list[str]:
    option_map = {opt.upper(): opt for opt in options}
    parsed = [s.strip().upper() for s in re.split(r"[,\s]+", raw) if s.strip()]
    picked = [option_map[p] for p in parsed if p in option_map]
    # Preserve first occurrence only.
    seen: set = set()
    deduped = []
    for p in picked:
        if p not in seen:
            deduped.append(p)
            seen.add(p)
    if max_count is not None and max_count > 0:
        deduped = deduped[:max_count]
    return deduped


def _option_prompt(problem_type: int, options: list[str], max_count: int) -> str:
    options_str = ", ".join(options)
    if problem_type == 1:
        return _single_choice_prompt(options_str)
    if problem_type == 2:
        return _multi_choice_prompt(options_str)
    if problem_type == 3:
        return _vote_prompt(max_count, options_str)
    raise ValueError(f"Unsupported option problem type: {problem_type}")


class AIProvider:
    def _chat(self, cover_url: str, text: str) -> str:
        raise NotImplementedError

    def answer_options(self, cover_url: str, options: list[str], problem_type: int, max_count: int) -> list[str]:
        instruction = _option_prompt(problem_type, options, max_count)
        picked = _parse_letters(self._chat(cover_url, instruction), options, max_count=max_count)
        if not picked:
            raise ValueError("AI response did not contain any valid option letter")
        return picked

    def answer_short(self, cover_url: str) -> str:
        return self._chat(cover_url, SHORT_ANSWER_PROMPT)


class GeminiProvider(AIProvider):

    def __init__(self, api_key: str, model: str = "gemini-flash-latest"):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def _fetch_image(self, url: str) -> bytes:
        resp = http_request("GET", url)
        resp.raise_for_status()
        return resp.content

    def _image_part(self, cover_url: str):
        from google.genai import types
        return types.Part.from_bytes(data=self._fetch_image(cover_url), mime_type="image/jpeg")

    def _chat(self, cover_url: str, text: str) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=[self._image_part(cover_url), text],
        )
        content = (response.text or "").strip()
        logger.info(f"Gemini response content: {content}")
        return content


class QwenProvider(AIProvider):
    BASE_URL = "https://api-inference.modelscope.cn/v1"
    DEFAULT_MODEL = "Qwen/Qwen3.5-397B-A17B"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        from openai import OpenAI
        self.client = OpenAI(base_url=self.BASE_URL, api_key=api_key)
        self.model = model

    def _chat(self, cover_url: str, text: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": cover_url}},
                {"type": "text", "text": text},
            ]}],
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        content = (response.choices[0].message.content or "").strip()
        logger.info(f"Qwen response content: {content}")
        return content


_PROVIDERS = {
    "google": GeminiProvider,
    "qwen": QwenProvider,
}


def create_provider(provider_name: str, api_key: str) -> AIProvider:
    cls = _PROVIDERS.get(provider_name)
    if cls is None:
        raise ValueError(f"Unknown AI provider: {provider_name!r}")
    return cls(api_key=api_key)
