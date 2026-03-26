import logging
import re
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)


SINGLE_CHOICE_PROMPT = (
    "This image shows a single-choice question slide. "
    "Read the question and all options from the image carefully. "
    "Choose exactly ONE correct answer from: %s. "
    "Reply with ONLY the option letter, nothing else."
)

MULTI_CHOICE_PROMPT = (
    "This image shows a multiple-choice question slide. "
    "Read the question and all options from the image carefully. "
    "Choose ALL correct answers from: %s. "
    "Reply with ONLY the option letters separated by commas, nothing else. "
    "Example: A,C"
)

SHORT_ANSWER_PROMPT = (
    "This image shows a short-answer question slide. "
    "Read the question from the image carefully. "
    "Provide a concise, accurate answer to the question. "
    "Reply with ONLY the answer text, nothing else."
)


class AIProvider:
    def _fetch_image(self, url: str) -> bytes:
        resp = requests.get(url, timeout=15, proxies={"http": None, "https": None})
        resp.raise_for_status()
        return resp.content

    def answer_choice(self, cover_url: str, options: List[str], problem_type: int) -> List[str]:
        raise NotImplementedError

    def answer_short(self, cover_url: str) -> str:
        raise NotImplementedError


class GeminiProvider(AIProvider):

    def __init__(self, api_key: str, model: str = "gemini-flash-latest"):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def answer_choice(self, cover_url: str, options: List[str], problem_type: int) -> List[str]:
        from google.genai import types

        image_bytes = self._fetch_image(cover_url)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

        options_str = ", ".join(options)
        template = SINGLE_CHOICE_PROMPT if problem_type == 1 else MULTI_CHOICE_PROMPT
        instruction = template % options_str

        response = self.client.models.generate_content(
            model=self.model,
            contents=[image_part, instruction],
        )

        raw = (response.text or "").strip().upper()
        logger.info("Gemini raw response for choice: %s", raw)

        parsed = [s.strip() for s in re.split(r"[,\s]+", raw) if s.strip()]
        return [p for p in parsed if p in options]

    def answer_short(self, cover_url: str) -> str:
        from google.genai import types

        image_bytes = self._fetch_image(cover_url)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

        instruction = SHORT_ANSWER_PROMPT

        response = self.client.models.generate_content(
            model=self.model,
            contents=[image_part, instruction],
        )

        answer = (response.text or "").strip()
        logger.info("Gemini raw response for short answer: %s", answer)
        return answer


_PROVIDERS = {
    "gemini": GeminiProvider,
}


def create_provider(provider_name: str, api_key: str) -> Optional[AIProvider]:
    if not api_key:
        return None
    cls = _PROVIDERS.get(provider_name)
    if cls is None:
        return None
    return cls(api_key=api_key)
