"""Formatage du texte via OpenAI — salutation et accord du nom d'entreprise."""

import logging
import re
from functools import lru_cache

from openai import OpenAI

import config

logger = logging.getLogger("candidature")

# Clé API active (on bascule sur la 2e si la 1re échoue)
_active_api_key: str | None = None


def _get_api_keys() -> list[str]:
    """Retourne la liste des clés API disponibles."""
    keys = []
    if hasattr(config, "OPENAI_API_KEY1") and config.OPENAI_API_KEY1:
        keys.append(config.OPENAI_API_KEY1)
    if hasattr(config, "OPENAI_API_KEY2") and config.OPENAI_API_KEY2:
        keys.append(config.OPENAI_API_KEY2)
    # Fallback sur l'ancienne variable si les nouvelles n'existent pas
    if not keys and hasattr(config, "OPENAI_API_KEY") and config.OPENAI_API_KEY:
        keys.append(config.OPENAI_API_KEY)
    return keys


def _get_client() -> OpenAI:
    """Retourne un client OpenAI configuré avec la clé active."""
    global _active_api_key
    if _active_api_key is None:
        keys = _get_api_keys()
        _active_api_key = keys[0] if keys else ""
    return OpenAI(api_key=_active_api_key)


def _call_openai_with_fallback(messages: list[dict], max_tokens: int = 50) -> str | None:
    """Appelle OpenAI avec fallback sur la clé de secours si la première échoue."""
    global _active_api_key
    keys = _get_api_keys()

    for i, key in enumerate(keys):
        try:
            client = OpenAI(api_key=key)
            response = client.chat.completions.create(
                model=getattr(config, "OPENAI_MODEL", "gpt-4o-mini"),
                messages=messages,
                max_tokens=max_tokens,
                temperature=0,
            )
            # Si succès, mémoriser cette clé comme active
            _active_api_key = key
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"Erreur API OpenAI (clé {i+1}/{len(keys)}): {e}")
            if i < len(keys) - 1:
                logger.info(f"Basculement sur la clé API de secours...")
            continue

    return None


@lru_cache(maxsize=256)
def get_salutation(contact_name: str) -> str:
    """Retourne la salutation appropriée selon le prénom.

    - Si contact_name est vide → "Madame, Monsieur,"
    - Sinon, demande à OpenAI si le prénom est féminin ou masculin
      → "Madame," ou "Monsieur,"
    """
    name = contact_name.strip() if contact_name else ""
    if not name:
        return "Madame, Monsieur,"

    # Extraire le prénom (premier mot du nom complet)
    first_name = name.split()[0]

    messages = [
        {
            "role": "system",
            "content": (
                "Tu es un assistant qui détermine le genre d'un prénom. "
                "Réponds UNIQUEMENT par 'M' pour masculin ou 'F' pour féminin. "
                "Si tu n'es pas sûr, réponds 'M' par défaut."
            ),
        },
        {
            "role": "user",
            "content": f"Quel est le genre du prénom '{first_name}' ?",
        },
    ]

    answer = _call_openai_with_fallback(messages, max_tokens=5)
    if answer and "F" in answer.upper():
        return "Madame,"
    if answer:
        return "Monsieur,"

    logger.warning(f"Échec OpenAI pour salutation ({first_name}) — fallback 'Madame, Monsieur,'")
    return "Madame, Monsieur,"


@lru_cache(maxsize=256)
def format_company_with_preposition(company_name: str) -> str:
    """Retourne le nom d'entreprise avec la bonne préposition française.

    Exemples :
    - "Google" → "de Google"
    - "Apple" → "d'Apple"
    - "L'Oréal" → "de L'Oréal"
    - "Orange" → "d'Orange"
    - "La Poste" → "de La Poste"
    """
    name = company_name.strip() if company_name else ""
    if not name:
        return "de l'entreprise"

    messages = [
        {
            "role": "system",
            "content": (
                "Tu es un assistant qui formate les noms d'entreprises en français. "
                "Ton rôle est de retourner le nom d'entreprise précédé de la bonne préposition "
                "pour compléter la phrase 'au sein de [entreprise]'. "
                "Règles :\n"
                "- Voyelle ou H muet au début → d' (ex: d'Apple, d'Orange, d'Hermès)\n"
                "- Consonne ou H aspiré → de (ex: de Google, de Microsoft, de Huawei)\n"
                "- Article défini → de + article (ex: de La Poste, de L'Oréal)\n"
                "Réponds UNIQUEMENT avec la préposition et le nom, rien d'autre."
            ),
        },
        {
            "role": "user",
            "content": f"Formate : {name}",
        },
    ]

    result = _call_openai_with_fallback(messages, max_tokens=50)
    if result:
        # Nettoyer les guillemets éventuels
        result = re.sub(r'^["\']|["\']$', '', result)
        return result

    # Fallback basique si OpenAI échoue
    logger.warning(f"Échec OpenAI pour entreprise ({name}) — fallback basique")
    if name and name[0].lower() in "aeiouyàâäéèêëïîôùûü":
        return f"d'{name}"
    return f"de {name}"
