"""Correction d'email via l'API OpenAI/Google (ChatGPT/Gemini)."""

import logging
import re
from typing import Optional
from openai import OpenAI
import config
from email_scorer import ScoredEmail

logger = logging.getLogger("candidature")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

def _get_api_keys() -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    if hasattr(config, "OPENAI_API_KEY1") and config.OPENAI_API_KEY1:
        keys.append((config.OPENAI_API_KEY1, "openai"))
    if hasattr(config, "OPENAI_API_KEY2") and config.OPENAI_API_KEY2:
        keys.append((config.OPENAI_API_KEY2, "openai"))
    if hasattr(config, "OPENAI_API_KEY3") and config.OPENAI_API_KEY3:
        key3 = config.OPENAI_API_KEY3
        if key3.startswith("AIza"):
            keys.append((key3, "google"))
        else:
            keys.append((key3, "openai"))
    if not keys and hasattr(config, "OPENAI_API_KEY") and config.OPENAI_API_KEY:
        keys.append((config.OPENAI_API_KEY, "openai"))
    return keys

def _call_google_api(api_key: str, prompt: str, max_tokens: int = 100) -> str | None:
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=FutureWarning)
            import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.0-flash')
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=max_tokens, temperature=0,)
        )
        return response.text.strip()
    except Exception:
        return None

class EmailCorrector:
    def __init__(self, model: str = "gpt-4o-mini", prompt_file: str = "prompt.txt") -> None:
        self.model = model
        self._prompt_template = self._load_prompt(prompt_file)
        self._api_keys = _get_api_keys()

    @staticmethod
    def _load_prompt(path: str) -> str:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return "Email: {email}, Company: {company}, Domain: {domain}, Person: {person_name}. Renvoie uniquement l'adresse corrigée complète sans aucun autre texte."

    def _call_with_fallback(self, prompt: str) -> str | None:
        for i, (key, api_type) in enumerate(self._api_keys):
            try:
                if api_type == "google":
                    result = _call_google_api(key, prompt, max_tokens=100)
                    if result:
                        return result
                    raise Exception("Empty")
                else:
                    client = OpenAI(api_key=key)
                    response = client.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0,
                        max_tokens=100,
                    )
                    return response.choices[0].message.content.strip()
            except Exception:
                continue
        return None

    def correct_email(self, scored: ScoredEmail, company_name: str, official_domain: str = "") -> ScoredEmail:
        prompt = self._prompt_template.format(
            email=scored.email, company=company_name, domain=official_domain or "(inconnu)",
            score=scored.score, person_name=scored.person_name or "(inconnu)",
        )
        raw = self._call_with_fallback(prompt)
        if raw is None:
            return scored
        match = _EMAIL_RE.search(raw)
        if not match:
            return scored
        corrected = match.group(0).lower()
        if corrected == scored.email.lower():
            return scored
        old_email = scored.email
        scored.email = corrected
        scored.reasons.append(f"IA: corrigé de {old_email} → {corrected}")
        return scored
