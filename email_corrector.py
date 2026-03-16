"""Correction d'email via l'API OpenAI (ChatGPT).

Charge un prompt template depuis un fichier texte, l'enrichit avec le
contexte de recherche (entreprise, domaine, email trouvé…), et demande à
ChatGPT de corriger l'email si nécessaire.

En cas d'erreur API, l'email original est retourné tel quel (fail-safe).
"""

import logging
import re
from typing import Optional

from openai import OpenAI

from email_scorer import ScoredEmail

logger = logging.getLogger("candidature")

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


class EmailCorrector:
    """Corrige / valide un email trouvé automatiquement via ChatGPT."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        prompt_file: str = "prompt.txt",
    ) -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self._prompt_template = self._load_prompt(prompt_file)

    @staticmethod
    def _load_prompt(path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def correct_email(
        self,
        scored: ScoredEmail,
        company_name: str,
        official_domain: str = "",
    ) -> ScoredEmail:
        """Demande à ChatGPT de vérifier / corriger l'email.

        Retourne le ScoredEmail mis à jour (email corrigé si modifié).
        En cas d'erreur, retourne l'original inchangé.
        """
        prompt = self._prompt_template.format(
            email=scored.email,
            company=company_name,
            domain=official_domain or "(inconnu)",
            score=scored.score,
            person_name=scored.person_name or "(inconnu)",
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=100,
            )

            raw = response.choices[0].message.content.strip()
            match = _EMAIL_RE.search(raw)

            if not match:
                logger.warning(
                    f"  ChatGPT: réponse inattendue ({raw!r}) "
                    f"— email original conservé"
                )
                return scored

            corrected = match.group(0).lower()

            if corrected == scored.email.lower():
                logger.info(
                    f"  ChatGPT: email confirmé → {scored.email}"
                )
                return scored

            old_email = scored.email
            scored.email = corrected
            scored.reasons.append(
                f"ChatGPT: corrigé de {old_email} → {corrected}"
            )
            logger.info(
                f"  ChatGPT: email corrigé {old_email} → {corrected}"
            )
            return scored

        except Exception as exc:
            logger.warning(
                f"  ChatGPT: erreur API ({exc}) — email original conservé"
            )
            return scored
