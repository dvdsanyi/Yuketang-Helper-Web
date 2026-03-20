"""AI provider abstraction for answering problems from slide images."""

import logging
import re
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)


class AIProvider:
    """Base class for AI providers."""

    def answer_choice(
        self,
        cover_url: str,
        options: List[str],
        problem_type: int,
    ) -> List[str]:
        """Analyse the slide image and return chosen option keys.

        Args:
            cover_url: URL of the slide cover image.
            options: Available option keys, e.g. ["A", "B", "C", "D"].
            problem_type: 1=single, 2=multiple.

        Returns:
            List of selected option keys.
        """
        raise NotImplementedError

    def answer_short(self, cover_url: str) -> str:
        """Analyse the slide image and return a short-answer text.

        Args:
            cover_url: URL of the slide cover image.

        Returns:
            Answer text.
        """
        raise NotImplementedError


class GeminiProvider(AIProvider):
    """Google Gemini API provider."""

    def __init__(self, api_key: str, model: str = "gemini-3-flash-preview"):
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.model = model

    def _fetch_image(self, url: str) -> bytes:
        resp = requests.get(url, timeout=15, proxies={"http": None, "https": None})
        resp.raise_for_status()
        return resp.content

    def answer_choice(
        self,
        cover_url: str,
        options: List[str],
        problem_type: int,
    ) -> List[str]:
        from google.genai import types

        image_bytes = self._fetch_image(cover_url)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

        options_str = ", ".join(options)
        if problem_type == 1:
            instruction = (
                "This image shows a single-choice question slide. "
                "Read the question and all options from the image carefully. "
                "Choose exactly ONE correct answer from: %s. "
                "Reply with ONLY the option letter, nothing else."
                % options_str
            )
        else:
            instruction = (
                "This image shows a multiple-choice question slide. "
                "Read the question and all options from the image carefully. "
                "Choose ALL correct answers from: %s. "
                "Reply with ONLY the option letters separated by commas, nothing else. "
                "Example: A,C"
                % options_str
            )

        response = self.client.models.generate_content(
            model=self.model,
            contents=[image_part, instruction],
        )

        raw = (response.text or "").strip().upper()
        logger.info("Gemini raw response for choice: %s", raw)

        parsed = [s.strip() for s in re.split(r"[,\s]+", raw) if s.strip()]
        valid = [p for p in parsed if p in options]

        if not valid:
            logger.warning("Gemini returned no valid options from: %s", raw)
            return []
        return valid

    def answer_short(self, cover_url: str) -> str:
        from google.genai import types

        image_bytes = self._fetch_image(cover_url)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

        instruction = (
            "This image shows a short-answer question slide. "
            "Read the question from the image carefully. "
            "Provide a concise, accurate answer to the question. "
            "Reply with ONLY the answer text, nothing else."
        )

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
    """Create an AI provider instance.

    Args:
        provider_name: Provider name, e.g. "gemini".
        api_key: API key for the provider.

    Returns:
        AIProvider instance or None if provider unknown or key missing.
    """
    if not api_key:
        return None
    cls = _PROVIDERS.get(provider_name)
    if cls is None:
        logger.warning("Unknown AI provider: %s", provider_name)
        return None
    try:
        return cls(api_key=api_key)
    except Exception as e:
        logger.error("Failed to create AI provider %s: %s", provider_name, e)
        return None
