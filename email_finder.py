"""Recherche async d'emails de contact via multi-moteur + scraping parallèle.

Fonctionnalités :
- **asyncio + aiohttp** : scraping parallèle de toutes les pages (×10 vitesse)
- **Multi-moteur** : DuckDuckGo + Bing
- **LinkedIn** : recherche de profils RH / IT → génération d'emails candidats
- **Extraction de noms** : depuis les pages HTML, les local-parts, LinkedIn
- **Détection d'emails obfusqués** : (at), [at], {at}
- **Cache disque** : évite de re-télécharger les pages (TTL 24 h)
- **Filtrage renforcé** : bloque placeholders, hash DSN, sites d'emploi

Architecture :
    find_best_email()          ← API publique synchrone
      └─ asyncio.run(_async_find())
           ├─ DDG + Bing (recherche multi-moteur)
           ├─ LinkedIn (profils RH / IT → emails candidats)
           ├─ Identification du site officiel (HEAD parallèles)
           ├─ Scraping parallèle (aiohttp + Semaphore + cache)
           ├─ Extraction de noms (HTML context + local part + LinkedIn)
           └─ Filtrage → scoring (avec name_map) → meilleur email
"""

import asyncio
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup
from ddgs import DDGS

from email_scorer import ScoredEmail, select_best_email
from page_cache import PageCache

logger = logging.getLogger("candidature")

# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

# Email standard
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Emails obfusqués : user (at) domain.com, user [at] domain.com
_OBFUSCATED_RE = re.compile(
    r"([a-zA-Z0-9._%+\-]+)"
    r"\s*(?:\(at\)|\[at\]|\{at\}|&#64;|\s+at\s+)\s*"
    r"([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
    re.IGNORECASE,
)

# Placeholders dans les local parts
_PLACEHOLDER_LOCAL_RE = re.compile(
    r"^(?:votre[._]|your[._]|nom[._]prenom|name[._]|prenom[._]|"
    r"firstname[._]|email[._]|test[._]|exemple|example|user[._]|"
    r"utilisateur|someone|quelquun|xxx|yyy|zzz)",
    re.IGNORECASE,
)

# Regex pour détecter un nom de type « Prénom Nom » en français
# Accepte les accents, tirets composés, de/du/le etc.
_NAME_RE = re.compile(
    r"\b([A-ZÀ-ÖÙ-Ý][a-zà-öù-ÿ]{1,20}"        # Prénom
    r"(?:\s+(?:de|du|le|la|des|van|von|el))?"    # Particule optionnelle
    r"\s+[A-ZÀ-ÖÙ-Ý][a-zà-öù-ÿA-ZÀ-ÖÙ-Ý\-]{1,25})\b"  # Nom
)

# Local parts qui sont des rôles, pas des noms de personnes
_ROLE_LOCAL_PARTS = {
    "contact", "info", "rh", "hr", "recrutement", "recruitment",
    "careers", "jobs", "job", "emploi", "stage", "intern", "talent",
    "candidature", "candidatures",
    "accueil", "admin", "support", "webmaster", "sales", "marketing",
    "commercial", "presse", "press", "pr", "communication",
    "comptabilite", "billing", "invoice", "direction", "secretariat",
    "noreply", "no-reply", "newsletter", "postmaster", "abuse",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ---------------------------------------------------------------------------
# Domaines à ignorer (faux positifs fréquents)
# ---------------------------------------------------------------------------

_IGNORED_EMAIL_DOMAINS: set[str] = {
    # Placeholders / exemples
    "example.com", "example.org", "example.net",
    "email.com", "domain.com", "domaine.com", "domaine.fr",
    "exemple.com", "exemple.fr", "test.com",
    # Infrastructure technique
    "sentry.io", "gravatar.com", "schema.org", "w3.org",
    "googleapis.com", "google.com", "gstatic.com",
    "cloudflare.com", "cloudflareinsights.com",
    "googletagmanager.com", "google-analytics.com",
    # Réseaux sociaux
    "facebook.com", "twitter.com", "x.com",
    "linkedin.com", "instagram.com", "youtube.com", "tiktok.com",
    "pinterest.com",
    # Constructeurs de sites
    "wixpress.com", "wix.com", "squarespace.com",
    "wordpress.com", "wp.com", "shopify.com", "webflow.io",
    "hubspot.com", "mailchimp.com", "sendinblue.com",
    # CDN / JS / fonts
    "bootstrapcdn.com", "jquery.com", "jsdelivr.net",
    "unpkg.com", "cdnjs.cloudflare.com", "fontawesome.com",
    # Juridique / org
    "creativecommons.org",
    # Sites d'emploi
    "jobted.com", "jobted.fr",
    "indeed.com", "indeed.fr",
    "glassdoor.com", "glassdoor.fr",
    "monster.com", "monster.fr",
    "welcometothejungle.com", "wttj.co",
    "hellowork.com", "hellowork.io",
    "apec.fr", "francetravail.fr", "pole-emploi.fr",
    "regionsjob.com", "cadremploi.fr",
    "keljob.com", "meteojob.com",
    "leboncoin.fr",
    "talent.com", "jooble.org",
    "neuvoo.fr", "optioncarriere.com",
    "emploi-store.fr", "staffme.fr",
    "choosemycompany.com", "stages.fr",
    "letudiant.fr", "studyrama.com",
}

# Sous-pages à scraper sur le site officiel
_CONTACT_PATHS: list[str] = [
    "/contact", "/contact/", "/contact-us", "/contactez-nous",
    "/nous-contacter",
    "/careers", "/carrieres", "/recrutement", "/emploi", "/jobs",
    "/rejoignez-nous", "/nous-rejoindre",
    "/about", "/about-us", "/a-propos", "/qui-sommes-nous",
    "/team", "/equipe", "/notre-equipe",
    "/mentions-legales", "/legal", "/legal-notice",
]

_LINKEDIN_RH_ROLE_RE = re.compile(
    r"\b("
    r"drh|rh|"
    r"ressources?\s+humaines?|human\s+resources?|"
    r"recrut(?:ement|eur|euse|er|ing)?|"
    r"talent\s+acquisition|talent\s+recruit(?:er|ment)|"
    r"people\s+ops?"
    r")\b",
    re.IGNORECASE,
)

_LINKEDIN_IT_SCOPE_RE = re.compile(
    r"\b(informatique|it|tech(?:nique)?|si|systemes?|engineering|digital)\b",
    re.IGNORECASE,
)

_LINKEDIN_IT_LEAD_RE = re.compile(
    r"\b(directeur|directrice|responsable|manager|head|lead|chief|dsi|cto|cio)\b",
    re.IGNORECASE,
)

_LOCATION_STOPWORDS = {
    "france", "cedex", "region", "departement", "metropole",
    "ville", "arrondissement", "centre", "nord", "sud", "est", "ouest",
}

# Mots qui ne sont PAS des prénoms/noms (détection de faux profils)
_NOT_A_NAME_WORDS = {
    # Titres de poste
    "expert", "manager", "director", "directeur", "responsable",
    "consultant", "developer", "développeur", "engineer", "ingénieur",
    "senior", "junior", "lead", "chief", "head", "officer",
    "assistant", "stagiaire", "intern", "alternant",
    "delivery", "product", "project", "programme",
    # Départements
    "it", "rh", "hr", "digital", "tech", "data", "cloud",
    "web", "design", "marketing", "commercial", "finance",
    # Génériques
    "positive", "global", "group", "groupe", "team", "équipe",
    "france", "europe", "international",
}


# ---------------------------------------------------------------------------
# Contexte de recherche
# ---------------------------------------------------------------------------

@dataclass
class _SearchContext:
    """Contexte enrichi pour la recherche d'un email."""

    company_name: str
    location: str = ""
    job_title: str = ""

    @property
    def company_slug(self) -> str:
        return re.sub(r"[\s\-_.]+", "", self.company_name.lower())

    @property
    def company_slug_hyphen(self) -> str:
        return re.sub(r"[\s_.]+", "-", self.company_name.lower())

    @property
    def location_short(self) -> str:
        """Extrait la commune nettoyée (sans code postal, cedex, département)."""
        raw = self.location.split(",")[0].strip() if self.location else ""
        if not raw:
            return ""
        # Supprimer le code postal (5 chiffres)
        raw = re.sub(r"\b\d{5}\b", "", raw).strip()
        # Supprimer le département entre parenthèses : (69), (01), etc.
        raw = re.sub(r"\(\d{1,3}\)", "", raw).strip()
        # Supprimer "cedex" et ce qui suit
        raw = re.sub(r"\bcedex\b.*", "", raw, flags=re.IGNORECASE).strip()
        return raw


# ===========================================================================
# Utilitaires d'extraction de noms
# ===========================================================================

def _remove_accents(text: str) -> str:
    """Supprime les accents d'un texte (pour générer des emails)."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize_text_for_match(text: str) -> str:
    """Normalise un texte pour les comparaisons souples."""
    if not text:
        return ""
    clean = _remove_accents(text.lower())
    clean = re.sub(r"[^a-z0-9]+", " ", clean)
    return re.sub(r"\s+", " ", clean).strip()


def _guess_name_from_local_part(local: str) -> str:
    """Devine un nom de personne à partir du local part d'un email.

    Exemples :
        jean.dupont  → Jean Dupont
        j.dupont     → J. Dupont
        jean-dupont  → Jean Dupont
        jdupont      → (vide — impossible de deviner)
        contact      → (vide — c'est un rôle)
    """
    # Ne pas essayer sur les rôles
    clean = re.sub(r"[.\-_]", "", local).lower()
    if clean in _ROLE_LOCAL_PARTS:
        return ""

    # Séparer par . ou - ou _
    parts = re.split(r"[.\-_]", local)
    if len(parts) < 2:
        return ""

    # Vérifier que les parties ressemblent à des noms (pas de chiffres)
    name_parts: list[str] = []
    for p in parts:
        p = p.strip()
        if not p or not p.isalpha():
            return ""
        if len(p) == 1:
            name_parts.append(p.upper() + ".")
        else:
            name_parts.append(p.capitalize())

    return " ".join(name_parts)


def _extract_names_near_email(html: str, email: str) -> str:
    """Cherche un nom de personne dans le voisinage d'un email dans le HTML.

    Regarde ±200 caractères autour de l'email et cherche un pattern
    « Prénom Nom ».
    """
    idx = html.find(email)
    if idx == -1:
        # Essayer sans le @
        local = email.split("@")[0]
        idx = html.find(local)
    if idx == -1:
        return ""

    # Extraire le contexte autour
    start = max(0, idx - 200)
    end = min(len(html), idx + len(email) + 200)
    context = html[start:end]

    # Nettoyer les balises HTML
    context = re.sub(r"<[^>]+>", " ", context)
    context = re.sub(r"\s+", " ", context)

    # Chercher un nom
    match = _NAME_RE.search(context)
    if match:
        candidate = match.group(1).strip()
        # Vérifier que ce n'est pas un faux positif courant
        lower = candidate.lower()
        first_word = lower.split()[0] if lower.split() else ""
        if any(w in lower for w in [
            "mentions légales", "politique de", "conditions",
            "tous droits", "copyright", "powered by",
        ]):
            return ""
        # Rejeter les groupes commençant par un verbe / mot courant
        _bad_first_words = {
            "contactez", "contacter", "appelez", "appeler",
            "ecrivez", "ecrire", "envoyez", "envoyer",
            "rejoignez", "rejoindre", "découvrez", "decouvrez",
            "bienvenue", "bonjour", "merci", "veuillez",
            "notre", "votre", "nous", "pour", "chez",
            "adresse", "siege", "siège", "site",
            "formulaire", "page", "voir",
        }
        if first_word in _bad_first_words:
            # Réessayer en sautant ce match
            remaining = context[match.end():]
            match2 = _NAME_RE.search(remaining)
            if match2:
                candidate = match2.group(1).strip()
                lower = candidate.lower()
                first_word = lower.split()[0] if lower.split() else ""
                if first_word in _bad_first_words:
                    return ""
            else:
                return ""
        return candidate

    return ""


def _generate_email_variants(
    first_name: str,
    last_name: str,
    domain: str,
) -> list[str]:
    """Génère des variantes d'email à partir d'un prénom/nom + domaine.

    Exemples :
        Jean, Dupont, company.fr → [
            jean.dupont@company.fr,
            j.dupont@company.fr,
            jdupont@company.fr,
            jean-dupont@company.fr,
        ]
    """
    if not first_name or not last_name or not domain:
        return []

    first = _remove_accents(first_name.lower().strip())
    last = _remove_accents(last_name.lower().strip())

    # Nettoyer les caractères non-alpha
    first = re.sub(r"[^a-z]", "", first)
    last = re.sub(r"[^a-z\-]", "", last)

    # Évite de générer des adresses depuis des noms trop courts/ambiguës.
    if not first or not last or len(first) < 2 or len(last) < 2:
        return []

    variants = [
        f"{first}.{last}@{domain}",
        f"{first[0]}.{last}@{domain}",
        f"{first}{last}@{domain}",
        f"{first}-{last}@{domain}",
        f"{first[0]}{last}@{domain}",
        f"{last}.{first}@{domain}",
        f"{last}{first[0]}@{domain}",
    ]
    return list(dict.fromkeys(variants))  # déduplique en gardant l'ordre


# ===========================================================================
# EmailFinder — classe principale
# ===========================================================================

class EmailFinder:
    """Recherche le meilleur email de contact pour une entreprise.

    Utilise asyncio + aiohttp pour le scraping parallèle, DuckDuckGo + Bing
    comme moteurs, LinkedIn pour identifier les bonnes personnes, et un
    cache disque pour éviter les requêtes redondantes.
    """

    def __init__(
        self,
        max_results_per_query: int = 10,
        max_pages_to_scrape: int = 20,
        request_timeout: int = 5,
        delay_between_requests: float = 0.2,
        concurrent_requests: int = 10,
        retry_count: int = 2,
        cache_ttl: int = 86400,
    ) -> None:
        self.max_results = max_results_per_query
        self.max_pages = max_pages_to_scrape
        self.timeout = request_timeout
        self.delay = delay_between_requests
        self.concurrency = concurrent_requests
        self.retries = retry_count
        self.cache = PageCache(ttl=cache_ttl)

    # ==================================================================
    # API publique (synchrone)
    # ==================================================================

    def find_best_email(
        self,
        company_name: str,
        location: str = "",
        job_title: str = "",
    ) -> Optional[ScoredEmail]:
        """Recherche le meilleur email de contact (API synchrone)."""
        ctx = _SearchContext(
            company_name=company_name,
            location=location,
            job_title=job_title,
        )

        logger.info(
            f"Recherche d'email pour : {ctx.company_name}"
            f" | lieu={ctx.location_short or '?'}"
            f" | poste={ctx.job_title[:40] or '?'}"
        )

        return asyncio.run(self._async_find(ctx))

    # ==================================================================
    # Cœur asynchrone
    # ==================================================================

    async def _async_find(self, ctx: _SearchContext) -> Optional[ScoredEmail]:
        """Pipeline complet de recherche (async)."""

        connector = aiohttp.TCPConnector(
            limit=self.concurrency,
            ssl=False,
        )
        timeout = aiohttp.ClientTimeout(total=self.timeout)

        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=_HEADERS,
        ) as session:

            # ---- Étape 1 : recherche multi-moteur --------------------
            all_urls, snippet_emails = await self._search_all_engines(
                ctx, session,
            )
            logger.info(
                f"  Recherche: {len(all_urls)} URL(s), "
                f"{len(snippet_emails)} email(s) dans les snippets"
            )

            # ---- Étape 2 : identifier le site officiel ---------------
            official_site = await self._guess_official_site(
                ctx, all_urls, session,
            )
            official_domain = ""
            if official_site:
                official_domain = (
                    urlparse(official_site).netloc.lower().replace("www.", "")
                )
                logger.info(f"  Site officiel : {official_site}")

            # ---- Étape 3 : LinkedIn → noms + emails candidats --------
            linkedin_emails, linkedin_names = await self._search_linkedin(
                ctx, official_domain, session,
            )
            if linkedin_emails:
                logger.info(
                    f"  LinkedIn: {len(linkedin_emails)} email(s) candidat(s) "
                    f"depuis {len(linkedin_names)} profil(s)"
                )

            # ---- Étape 4 : construire les cibles de scraping ---------
            targets: set[str] = set()
            if official_site:
                targets.add(official_site)
                for path in _CONTACT_PATHS:
                    targets.add(urljoin(official_site, path))
            for url in all_urls[: self.max_pages]:
                if official_site and url.startswith(official_site):
                    continue
                targets.add(url)

            # ---- Étape 5 : scraping parallèle ------------------------
            all_emails: set[str] = set(snippet_emails)
            all_emails.update(linkedin_emails)

            # name_map : email → nom de la personne
            name_map: dict[str, str] = dict(linkedin_names)

            sem = asyncio.Semaphore(self.concurrency)
            tasks = [
                self._scrape_with_sem(sem, session, url)
                for url in targets
            ]
            scrape_results = await asyncio.gather(
                *tasks, return_exceptions=True,
            )
            for result in scrape_results:
                if isinstance(result, set):
                    all_emails.update(result)

            # ---- Étape 6 : extraire les noms -------------------------
            # 6a. Depuis le local part de chaque email
            for email in all_emails:
                if email in name_map:
                    continue
                local = email.split("@")[0]
                name = _guess_name_from_local_part(local)
                if name:
                    name_map[email] = name

            # 6b. Depuis le HTML (cache) des pages scrapées
            #     Seulement pour les emails personnels (pas les rôles)
            for email in all_emails:
                if email in name_map:
                    continue
                local = email.split("@")[0].lower()
                clean_local = re.sub(r"[.\-_]", "", local)
                if clean_local in _ROLE_LOCAL_PARTS:
                    continue  # pas de nom pour contact@, rh@, etc.
                for url in list(targets)[:30]:
                    cached = self.cache.get(url)
                    if cached:
                        name = _extract_names_near_email(cached, email)
                        if name:
                            name_map[email] = name
                            break

            # ---- Étape 7 : filtrage + scoring ------------------------
            filtered = self._filter_emails(all_emails)
            logger.info(f"  {len(filtered)} email(s) après filtrage")

            for e in filtered:
                n = name_map.get(e, "")
                tag = f" ({n})" if n else ""
                logger.debug(f"    - {e}{tag}")

            best = select_best_email(
                filtered, ctx.company_name, official_domain, name_map,
            )

            if best:
                name_tag = f" [{best.person_name}]" if best.person_name else ""
                logger.info(
                    f"  => Meilleur : {best.email}{name_tag} "
                    f"(score={best.score})"
                )
                for r in best.reasons:
                    logger.debug(f"       {r}")
            else:
                logger.warning(
                    f"  => Aucun email pertinent trouvé pour "
                    f"{ctx.company_name}."
                )

            return best

    # ==================================================================
    # Recherche multi-moteur (DDG + Bing)
    # ==================================================================

    async def _search_all_engines(
        self,
        ctx: _SearchContext,
        session: aiohttp.ClientSession,
    ) -> tuple[list[str], set[str]]:
        """Lance DDG (séquentiel) + Bing (parallèle) et fusionne."""

        queries = self._build_queries(ctx)

        loop = asyncio.get_running_loop()
        ddg_urls, ddg_emails = await loop.run_in_executor(
            None, self._search_ddg_sync, queries,
        )

        bing_tasks = [
            self._search_bing(query, session)
            for query in queries[:5]
        ]
        bing_results = await asyncio.gather(
            *bing_tasks, return_exceptions=True,
        )

        all_urls = list(ddg_urls)
        all_emails: set[str] = set(ddg_emails)
        seen_urls: set[str] = set(ddg_urls)

        for result in bing_results:
            if isinstance(result, tuple):
                urls, emails = result
                all_emails.update(emails)
                for url in urls:
                    if url not in seen_urls:
                        seen_urls.add(url)
                        all_urls.append(url)

        return all_urls, all_emails

    # ------------------------------------------------------------------
    # DuckDuckGo (synchrone, dans un thread)
    # ------------------------------------------------------------------

    def _search_ddg_sync(
        self, queries: list[str],
    ) -> tuple[list[str], set[str]]:
        urls: list[str] = []
        emails: set[str] = set()
        seen: set[str] = set()

        for query in queries:
            try:
                with DDGS() as ddgs:
                    results = list(
                        ddgs.text(query, max_results=self.max_results)
                    )

                for r in results:
                    url = r.get("href") or r.get("link") or ""
                    if url and url not in seen:
                        seen.add(url)
                        urls.append(url)

                    text = " ".join(
                        filter(None, [r.get("body", ""), r.get("title", "")])
                    )
                    emails.update(self._extract_emails_from_text(text))

            except Exception as exc:
                logger.debug(f"  DDG erreur pour '{query}': {exc}")

            time.sleep(self.delay)

        return urls, emails

    # ------------------------------------------------------------------
    # Bing (asynchrone)
    # ------------------------------------------------------------------

    async def _search_bing(
        self,
        query: str,
        session: aiohttp.ClientSession,
    ) -> tuple[list[str], set[str]]:
        url = (
            f"https://www.bing.com/search"
            f"?q={quote_plus(query)}&count=10&setlang=fr"
        )

        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status >= 400:
                    return [], set()
                html = await resp.text(errors="replace")

            soup = BeautifulSoup(html, "html.parser")
            result_urls: list[str] = []
            emails: set[str] = set()

            for item in soup.select("li.b_algo"):
                link = item.select_one("h2 a")
                if link and link.get("href"):
                    href = link["href"]
                    if href.startswith("http"):
                        result_urls.append(href)

                text = item.get_text(separator=" ")
                emails.update(self._extract_emails_from_text(text))

            return result_urls, emails

        except Exception as exc:
            logger.debug(f"  Bing erreur pour '{query}': {exc}")
            return [], set()

    # ==================================================================
    # Requêtes de recherche
    # ==================================================================

    def _build_queries(self, ctx: _SearchContext) -> list[str]:
        """Construit les requêtes de recherche adaptées au contexte."""

        name = ctx.company_name
        loc = ctx.location_short
        slug = ctx.company_slug

        queries: list[str] = []

        # --- Requêtes avec lieu en priorité (désambiguïsation) ---
        if loc:
            queries.extend([
                f'"{name}" {loc} email recrutement candidature',
                f'"{name}" {loc} email RH ressources humaines',
                f'"{name}" {loc} email contact',
                f'"{name}" {loc} stage informatique contact',
            ])

        # --- Priorité haute : email RH / recrutement ---
        queries.extend([
            f'"{name}" email recrutement',
            f'"{name}" email RH ressources humaines',
            f'"{name}" contact recrutement candidature',
            f'"{name}" email contact',
            # --- Candidature / stage ---
            f'"{name}" candidature spontanée email',
            f'"{name}" stage informatique contact',
            # --- Carrières ---
            f'"{name}" careers jobs email',
            f'"{name}" rejoignez-nous contact',
            # --- Responsables ---
            f'"{name}" responsable informatique email',
            f'"{name}" directeur technique email',
            # --- Site officiel ---
            f'site:{slug}.fr contact email',
            f'site:{slug}.com contact email',
            f'site:{slug}.fr "@"',
            f'"{name}" site officiel contact',
            # --- Mentions légales ---
            f'"{name}" mentions légales email',
        ])

        return queries

    def _build_linkedin_queries(self, ctx: _SearchContext) -> list[str]:
        """Construit les requêtes pour trouver des profils LinkedIn pertinents.

        Quand la commune est connue, elle est incluse dans TOUTES les requêtes
        principales pour éviter de confondre avec une entreprise homonyme dans
        une autre ville.
        """

        name = ctx.company_name
        loc = ctx.location_short

        if loc:
            # Requêtes avec commune en priorité pour désambiguïser
            queries: list[str] = [
                # RH / recrutement + lieu
                f'site:linkedin.com/in "{name}" {loc} recrutement',
                f'site:linkedin.com/in "{name}" {loc} RH ressources humaines',
                f'site:linkedin.com/in "{name}" {loc} talent acquisition',
                # IT / tech + lieu
                f'site:linkedin.com/in "{name}" {loc} responsable informatique',
                f'site:linkedin.com/in "{name}" {loc} directeur technique DSI',
                # Fallback sans lieu (au cas où le lieu réduirait trop)
                f'site:linkedin.com/in "{name}" RH recrutement',
                f'site:linkedin.com/in "{name}" responsable informatique',
            ]
        else:
            queries = [
                f'site:linkedin.com/in "{name}" recrutement',
                f'site:linkedin.com/in "{name}" RH ressources humaines',
                f'site:linkedin.com/in "{name}" talent acquisition',
                f'site:linkedin.com/in "{name}" responsable informatique',
                f'site:linkedin.com/in "{name}" directeur technique DSI',
            ]

        return queries

    @staticmethod
    def _location_matches(full_text: str, location: str) -> bool:
        """Valide que le snippet semble bien lié à la commune recherchée."""
        if not location:
            return True

        text = _normalize_text_for_match(full_text)
        loc = _normalize_text_for_match(location)
        if not text or not loc:
            return False

        if loc in text:
            return True

        tokens = [
            tok for tok in loc.split()
            if len(tok) >= 3 and tok not in _LOCATION_STOPWORDS
        ]
        if not tokens:
            return False

        return any(
            re.search(rf"\b{re.escape(tok)}\b", text)
            for tok in tokens
        )

    @staticmethod
    def _has_target_role(full_text: str, job_title: str) -> bool:
        """Valide un rôle RH/DRH ou direction de service IT."""
        text = _normalize_text_for_match(full_text)
        if not text:
            return False

        if _LINKEDIN_RH_ROLE_RE.search(text):
            return True

        if not (
            _LINKEDIN_IT_SCOPE_RE.search(text)
            and _LINKEDIN_IT_LEAD_RE.search(text)
        ):
            return False

        # Si un intitulé de poste est fourni, on l'utilise pour confirmer
        # que le profil colle à la recherche cible (ex: "informatique").
        normalized_job = _normalize_text_for_match(job_title)
        if not normalized_job:
            return True

        job_tokens = [
            tok for tok in normalized_job.split()
            if len(tok) >= 4 and tok not in _LOCATION_STOPWORDS
        ]
        if not job_tokens:
            return True

        return any(
            re.search(rf"\b{re.escape(tok)}\b", text)
            for tok in job_tokens
        )

    # ==================================================================
    # LinkedIn : recherche de profils → génération d'emails
    # ==================================================================

    async def _search_linkedin(
        self,
        ctx: _SearchContext,
        official_domain: str,
        session: aiohttp.ClientSession,
    ) -> tuple[set[str], dict[str, str]]:
        """Recherche des profils LinkedIn pour trouver des personnes RH/IT.

        Analyse les snippets DDG/Bing pour extraire des noms, puis génère
        des emails candidats sur le domaine officiel.

        Returns:
            (emails, name_map) — emails générés et mapping email → nom
        """
        if not official_domain:
            return set(), {}

        queries = self._build_linkedin_queries(ctx)

        # Lancer DDG dans un thread pour les requêtes LinkedIn
        loop = asyncio.get_running_loop()
        profiles = await loop.run_in_executor(
            None, self._extract_linkedin_profiles_ddg, queries, ctx,
        )

        if not profiles:
            return set(), {}

        logger.info(
            f"  LinkedIn: {len(profiles)} profil(s) pertinent(s) trouvé(s)"
        )

        # Générer des emails candidats à partir des noms trouvés
        generated_emails: set[str] = set()
        name_map: dict[str, str] = {}

        for profile_name, profile_title in profiles:
            parts = profile_name.split()
            if len(parts) < 2:
                continue

            # Rejeter les noms avec des composants abrégés ("Astrid D.")
            if any(
                len(p.rstrip(".")) <= 1 for p in parts
            ):
                logger.debug(
                    f"    LinkedIn: {profile_name} ignoré (nom abrégé)"
                )
                continue

            # Rejeter les noms qui sont des titres de poste
            if any(
                p.lower().rstrip(".") in _NOT_A_NAME_WORDS for p in parts
            ):
                logger.debug(
                    f"    LinkedIn: {profile_name} ignoré (titre de poste)"
                )
                continue

            first_name = parts[0]
            last_name = " ".join(parts[1:])
            last_for_email = parts[-1]

            variants = _generate_email_variants(
                first_name, last_for_email, official_domain,
            )

            for email in variants:
                generated_emails.add(email)
                name_map[email] = profile_name

            logger.debug(
                f"    LinkedIn: {profile_name} ({profile_title}) "
                f"→ {len(variants)} email(s)"
            )

        return generated_emails, name_map

    def _extract_linkedin_profiles_ddg(
        self,
        queries: list[str],
        ctx: _SearchContext,
    ) -> list[tuple[str, str]]:
        """Extrait des noms et titres depuis les snippets DDG de LinkedIn.

        Returns:
            Liste de (nom, titre) pour les profils pertinents.
        """
        profiles: list[tuple[str, str]] = []
        seen_names: set[str] = set()

        company_text = _normalize_text_for_match(ctx.company_name)
        location_text = ctx.location_short

        for query in queries:
            try:
                with DDGS() as ddgs:
                    results = list(
                        ddgs.text(query, max_results=5)
                    )

                for r in results:
                    title = r.get("title", "")
                    body = r.get("body", "")
                    url = r.get("href") or r.get("link") or ""

                    # Ne garder que les profils LinkedIn
                    if "linkedin.com/in/" not in url:
                        continue

                    # Extraire nom depuis le titre LinkedIn
                    # Format typique : "Prénom Nom - Titre | LinkedIn"
                    # ou "Prénom Nom – Titre – Entreprise | LinkedIn"
                    name, title_text = self._parse_linkedin_snippet(
                        title, body,
                    )
                    normalized_name = _normalize_text_for_match(name)
                    if not name or normalized_name in seen_names:
                        continue

                    # Vérifier la pertinence : le snippet mentionne-t-il
                    # l'entreprise et un poste RH / IT ?
                    full_text = f"{title} {body}"
                    full_text_norm = _normalize_text_for_match(full_text)

                    # L'entreprise doit être mentionnée
                    if company_text and company_text not in full_text_norm:
                        logger.debug(
                            f"    LinkedIn: {name} ignoré "
                            f"(entreprise '{ctx.company_name}' absente du snippet)"
                        )
                        continue

                    # Le lieu est obligatoire pour éviter les homonymes.
                    if not self._location_matches(full_text, location_text):
                        logger.debug(
                            f"    LinkedIn: {name} ignoré "
                            f"(commune '{location_text}' ne correspond pas)"
                        )
                        continue

                    # Le rôle doit être RH/DRH ou direction IT.
                    if not self._has_target_role(full_text, ctx.job_title):
                        logger.debug(
                            f"    LinkedIn: {name} ignoré "
                            f"(rôle non pertinent: ni RH ni direction de service)"
                        )
                        continue

                    seen_names.add(normalized_name)
                    profiles.append((name, title_text))
                    logger.debug(
                        f"    LinkedIn profil: {name} — {title_text}"
                    )

            except Exception as exc:
                logger.debug(f"  LinkedIn DDG erreur: {exc}")

            time.sleep(self.delay)

        return profiles

    @staticmethod
    def _parse_linkedin_snippet(
        title: str,
        body: str,
    ) -> tuple[str, str]:
        """Parse le titre/body d'un snippet LinkedIn pour extraire nom + titre.

        Formats typiques :
            « Prénom Nom - Titre | LinkedIn »
            « Prénom Nom – Titre – Entreprise | LinkedIn »
            « Prénom Nom | LinkedIn »
        """
        # Nettoyer "| LinkedIn" de la fin
        clean = re.sub(r"\s*[|–\-]\s*LinkedIn\s*$", "", title, flags=re.I)

        # Séparer nom et titre
        for sep in [" - ", " – ", " — ", " | "]:
            if sep in clean:
                parts = clean.split(sep, 1)
                name_candidate = parts[0].strip()
                title_candidate = parts[1].strip() if len(parts) > 1 else ""
                # Valider que ça ressemble à un nom (2+ mots, pas trop long)
                words = name_candidate.split()
                if 2 <= len(words) <= 5 and all(
                    w[0].isupper() or w.lower() in (
                        "de", "du", "le", "la", "des", "van", "von", "el",
                    )
                    for w in words if w
                ):
                    # Rejeter si un mot est un titre de poste connu
                    if any(
                        w.lower().rstrip(".") in _NOT_A_NAME_WORDS
                        for w in words
                    ):
                        break
                    # Rejeter les noms avec composants abrégés
                    if any(len(w.rstrip(".")) <= 1 for w in words):
                        break
                    return name_candidate, title_candidate
                break

        # Pas de fallback sur le body: trop de faux positifs.
        return "", ""

    # ==================================================================
    # Détection du site officiel
    # ==================================================================

    async def _guess_official_site(
        self,
        ctx: _SearchContext,
        urls: list[str],
        session: aiohttp.ClientSession,
    ) -> Optional[str]:
        slug = ctx.company_slug

        for url in urls:
            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "")
            domain_base = domain.split(".")[0]

            if slug in domain_base or domain_base in slug:
                return f"{parsed.scheme}://{parsed.netloc}"

        candidates: list[str] = []
        for tld in (".fr", ".com", ".eu", ".io", ".net"):
            candidates.append(f"https://www.{slug}{tld}")

        slug_h = ctx.company_slug_hyphen
        if slug_h != slug:
            for tld in (".fr", ".com"):
                candidates.append(f"https://www.{slug_h}{tld}")

        tasks = [self._try_head(session, c) for c in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for candidate, ok in zip(candidates, results):
            if ok is True:
                return candidate

        return None

    async def _try_head(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> bool:
        try:
            async with session.head(
                url,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status < 400
        except Exception:
            return False

    # ==================================================================
    # Scraping parallèle
    # ==================================================================

    async def _scrape_with_sem(
        self,
        sem: asyncio.Semaphore,
        session: aiohttp.ClientSession,
        url: str,
    ) -> set[str]:
        async with sem:
            return await self._scrape_page_async(session, url)

    async def _scrape_page_async(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> set[str]:
        cached = self.cache.get(url)
        if cached is not None:
            return self._extract_emails_from_html(cached)

        for attempt in range(self.retries):
            try:
                async with session.get(
                    url,
                    allow_redirects=True,
                ) as resp:
                    if resp.status >= 400:
                        return set()

                    content_type = resp.headers.get("Content-Type", "")
                    if "text" not in content_type and "html" not in content_type:
                        return set()

                    html = await resp.text(errors="replace")
                    self.cache.set(url, html)
                    return self._extract_emails_from_html(html)

            except asyncio.TimeoutError:
                if attempt < self.retries - 1:
                    await asyncio.sleep(0.5)
            except Exception as exc:
                logger.debug(f"  Scrape erreur {url}: {exc}")
                break

        return set()

    # ==================================================================
    # Extraction d'emails
    # ==================================================================

    @staticmethod
    def _extract_emails_from_text(text: str) -> set[str]:
        emails: set[str] = set()
        for email in _EMAIL_RE.findall(text):
            emails.add(email.lower())
        for m in _OBFUSCATED_RE.finditer(text):
            emails.add(f"{m.group(1)}@{m.group(2)}".lower())
        return emails

    def _extract_emails_from_html(self, html: str) -> set[str]:
        emails: set[str] = set()
        emails.update(self._extract_emails_from_text(html))
        try:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup.find_all("a", href=True):
                href: str = tag["href"]
                if href.lower().startswith("mailto:"):
                    addr = (
                        href[7:]
                        .split("?")[0]
                        .strip()
                        .lower()
                    )
                    if _EMAIL_RE.match(addr):
                        emails.add(addr)
        except Exception:
            pass
        return emails

    # ==================================================================
    # Filtrage
    # ==================================================================

    @staticmethod
    def _filter_emails(emails: set[str]) -> list[str]:
        result: list[str] = []

        for e in emails:
            if "@" not in e:
                continue

            local, domain = e.rsplit("@", 1)
            domain = domain.lower()

            if domain in _IGNORED_EMAIL_DOMAINS:
                continue
            if any(
                domain.endswith(f".{ign}") for ign in _IGNORED_EMAIL_DOMAINS
            ):
                continue
            if re.search(
                r"\.(png|jpg|jpeg|gif|svg|css|js|woff|woff2|ttf|eot"
                r"|ico|pdf|zip|mp4|webp|avif)$",
                e,
                re.IGNORECASE,
            ):
                continue
            if len(local) < 2 or len(domain) < 4:
                continue
            if len(local) > 16 and re.fullmatch(r"[a-f0-9]+", local):
                continue
            if _PLACEHOLDER_LOCAL_RE.match(local):
                continue

            result.append(e)

        return result
