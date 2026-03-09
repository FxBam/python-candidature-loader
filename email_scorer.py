"""Scoring et sélection du meilleur email de contact pour une candidature.

L'algorithme attribue un score à chaque email trouvé, en favorisant :
1. Les emails RH / recrutement
2. Les emails de responsables informatiques / techniques
3. Les emails de contact génériques
4. Pénalise les emails noreply, spam, etc.

Un bonus est ajouté si le domaine de l'email correspond au nom de l'entreprise.
"""

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Règles de scoring — chaque tuple = (regex compilé, score)
# Plus le score est élevé, plus l'email est pertinent pour une candidature.
# ---------------------------------------------------------------------------

# Mots-clés très pertinents (RH / recrutement / stage)
_HIGH_KEYWORDS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"\brh\b", re.I), 50),
    (re.compile(r"\bhr\b", re.I), 50),
    (re.compile(r"recrutement", re.I), 50),
    (re.compile(r"recruitment", re.I), 50),
    (re.compile(r"recrut", re.I), 45),
    (re.compile(r"emploi", re.I), 45),
    (re.compile(r"career|carriere|carrieres", re.I), 45),
    (re.compile(r"\bjob", re.I), 40),
    (re.compile(r"\bstage", re.I), 40),
    (re.compile(r"\bintern", re.I), 40),
    (re.compile(r"talent", re.I), 40),
]

# Mots-clés moyennement pertinents (responsable IT / tech / direction)
_MED_KEYWORDS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"informatique", re.I), 30),
    (re.compile(r"\bit[.-_]", re.I), 25),
    (re.compile(r"tech", re.I), 25),
    (re.compile(r"dev", re.I), 20),
    (re.compile(r"responsable", re.I), 20),
    (re.compile(r"manager", re.I), 20),
    (re.compile(r"directeur|direction", re.I), 15),
]

# Mots-clés faiblement pertinents (contact générique)
_LOW_KEYWORDS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"^contact$", re.I), 10),
    (re.compile(r"^info$", re.I), 5),
    (re.compile(r"^accueil$", re.I), 5),
]

# Mots-clés à pénaliser
_PENALTY_KEYWORDS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"no[-_.]?reply", re.I), -100),
    (re.compile(r"newsletter", re.I), -80),
    (re.compile(r"unsubscribe", re.I), -80),
    (re.compile(r"spam", re.I), -80),
    (re.compile(r"notification", re.I), -60),
    (re.compile(r"support", re.I), -20),
    (re.compile(r"webmaster", re.I), -15),
    (re.compile(r"abuse", re.I), -50),
    (re.compile(r"postmaster", re.I), -50),
    (re.compile(r"mailer[-_]?daemon", re.I), -80),
    (re.compile(r"^sales@", re.I), -10),
    (re.compile(r"^marketing@", re.I), -10),
    (re.compile(r"^commercial@", re.I), -5),
    (re.compile(r"^pr@", re.I), -15),
    (re.compile(r"^press@", re.I), -15),
    (re.compile(r"^presse@", re.I), -15),
    (re.compile(r"^billing@", re.I), -30),
    (re.compile(r"^invoice", re.I), -30),
    (re.compile(r"^comptabilite@", re.I), -20),
    (re.compile(r"^admin@", re.I), -5),
    (re.compile(r"alerte", re.I), -40),
    (re.compile(r"fraude", re.I), -60),
    (re.compile(r"bounce", re.I), -60),
    (re.compile(r"do[-_.]?not[-_.]?reply", re.I), -100),
    (re.compile(r"privacy", re.I), -30),
    (re.compile(r"rgpd", re.I), -20),
    (re.compile(r"dpo@", re.I), -20),
]

# Domaines d'email gratuits / non professionnels
_FREE_PROVIDERS = {
    "gmail.com", "yahoo.com", "yahoo.fr", "hotmail.com", "hotmail.fr",
    "outlook.com", "outlook.fr", "live.com", "live.fr", "aol.com",
    "protonmail.com", "icloud.com", "free.fr", "orange.fr", "sfr.fr",
    "laposte.net", "wanadoo.fr",
}


@dataclass
class ScoredEmail:
    """Un email avec son score de pertinence."""
    email: str
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"ScoredEmail({self.email!r}, score={self.score})"


def _normalize_company(name: str) -> str:
    """Normalise le nom pour la comparaison (minuscules, sans espaces/tirets)."""
    return re.sub(r"[\s\-_.]+", "", name.lower())


def score_email(
    email: str,
    company_name: str,
    official_domain: str = "",
) -> ScoredEmail:
    """Calcule le score de pertinence d'un email pour une candidature.

    Args:
        email: Adresse email à scorer.
        company_name: Nom de l'entreprise recherchée.
        official_domain: Domaine du site officiel (ex: ``safran.fr``).
    """
    result = ScoredEmail(email=email, score=0)

    local_part = email.split("@")[0].lower()
    domain_part = email.split("@")[1].lower() if "@" in email else ""
    domain_name = domain_part.split(".")[0] if domain_part else ""

    # --- Bonus mots-clés ---
    for groups in (_HIGH_KEYWORDS, _MED_KEYWORDS, _LOW_KEYWORDS):
        for pattern, pts in groups:
            if pattern.search(local_part):
                result.score += pts
                result.reasons.append(f"+{pts} mot-clé '{pattern.pattern}'")

    # --- Pénalités ---
    for pattern, pts in _PENALTY_KEYWORDS:
        if pattern.search(local_part):
            result.score += pts  # pts est négatif
            result.reasons.append(f"{pts} pénalité '{pattern.pattern}'")

    # --- Bonus domaine = site officiel ---
    if official_domain and domain_part == official_domain:
        result.score += 25
        result.reasons.append("+25 domaine = site officiel")

    # --- Bonus domaine entreprise ---
    norm_company = _normalize_company(company_name)
    norm_domain = _normalize_company(domain_name)
    if norm_company and norm_domain and (
        norm_company in norm_domain or norm_domain in norm_company
    ):
        result.score += 30
        result.reasons.append("+30 domaine correspond à l'entreprise")

    # --- Pénalité fournisseur gratuit ---
    if domain_part in _FREE_PROVIDERS:
        result.score -= 15
        result.reasons.append("-15 fournisseur email gratuit")

    # --- Bonus email pro (non gratuit, non personnel) ---
    if domain_part and domain_part not in _FREE_PROVIDERS:
        result.score += 10
        result.reasons.append("+10 domaine professionnel")

    # --- Pénalité domaine sans rapport avec l'entreprise ---
    if (
        domain_part
        and domain_part not in _FREE_PROVIDERS
        and norm_company
        and norm_domain
        and norm_company not in norm_domain
        and norm_domain not in norm_company
    ):
        result.score -= 10
        result.reasons.append("-10 domaine sans rapport avec l'entreprise")

    return result


def select_best_email(
    emails: list[str],
    company_name: str,
    official_domain: str = "",
) -> ScoredEmail | None:
    """Classe les emails et renvoie le meilleur (score le plus élevé).

    Args:
        emails: Liste des adresses trouvées.
        company_name: Nom de l'entreprise.
        official_domain: Domaine du site officiel (bonus de score).

    Returns:
        Le ``ScoredEmail`` le plus pertinent, ou ``None``.
    """
    if not emails:
        return None

    scored = [
        score_email(e, company_name, official_domain)
        for e in emails
    ]
    scored.sort(key=lambda s: (s.score, -len(s.email)), reverse=True)

    best = scored[0]
    if best.score < 0:
        return None

    return best
