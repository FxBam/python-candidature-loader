"""Recherche async d'emails de contact via multi-moteur + scraping parallèle.

Améliorations par rapport à la version séquentielle :
- **asyncio + aiohttp** : scraping de toutes les pages en parallèle (×10 vitesse)
- **Multi-moteur** : DuckDuckGo + Bing en même temps
- **Détection d'emails obfusqués** : (at), [at], {at}
- **Cache disque** : évite de re-télécharger les pages (TTL 24 h)
- **Filtrage renforcé** : bloque placeholders, hash DSN, sites d'emploi
- **Détection du domaine officiel** : tentative sur .fr / .com / .eu / .io / .net
  avec variantes tirets pour les noms multi-mots

Architecture :
    find_best_email()          ← API publique synchrone
      └─ asyncio.run(_async_find())
           ├─ DDG (via thread executor, séquentiel pour respect du rate limit)
           ├─ Bing (aiohttp, parallèle)
           ├─ Identification du site officiel (HEAD parallèles)
           ├─ Scraping de toutes les URLs (aiohttp + Semaphore)
           └─ Filtrage → scoring → meilleur email
"""

import asyncio
import logging
import re
import time
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

# Regex standard
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Regex pour emails obfusqués : user (at) domain.com, user [at] domain.com
_OBFUSCATED_RE = re.compile(
    r"([a-zA-Z0-9._%+\-]+)"
    r"\s*(?:\(at\)|\[at\]|\{at\}|&#64;|\s+at\s+)\s*"
    r"([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
    re.IGNORECASE,
)

# Détecte les parties locales qui ressemblent à un placeholder
_PLACEHOLDER_LOCAL_RE = re.compile(
    r"^(?:votre[._]|your[._]|nom[._]prenom|name[._]|prenom[._]|"
    r"firstname[._]|email[._]|test[._]|exemple|example|user[._]|"
    r"utilisateur|someone|quelquun|xxx|yyy|zzz)",
    re.IGNORECASE,
)

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
    # Sites d'emploi (faux positifs : ce n'est pas l'entreprise !)
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
        """Nom sans espaces / tirets, minuscules."""
        return re.sub(r"[\s\-_.]+", "", self.company_name.lower())

    @property
    def company_slug_hyphen(self) -> str:
        """Nom avec tirets (pour les domaines multi-mots)."""
        return re.sub(r"[\s_.]+", "-", self.company_name.lower())

    @property
    def location_short(self) -> str:
        """Première partie du lieu (ville)."""
        return self.location.split(",")[0].strip() if self.location else ""


# ===========================================================================
# EmailFinder — classe principale
# ===========================================================================

class EmailFinder:
    """Recherche le meilleur email de contact pour une entreprise.

    Utilise asyncio + aiohttp pour le scraping parallèle, DuckDuckGo + Bing
    comme moteurs de recherche, et un cache disque pour éviter les doublons.
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
        """Recherche le meilleur email de contact (API synchrone).

        Lancement interne de la boucle asyncio pour le scraping parallèle.
        """
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
            ssl=False,  # désactive vérif SSL pour scraping
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

            # ---- Étape 3 : construire les cibles de scraping ---------
            targets: set[str] = set()
            if official_site:
                targets.add(official_site)
                for path in _CONTACT_PATHS:
                    targets.add(urljoin(official_site, path))
            for url in all_urls[: self.max_pages]:
                if official_site and url.startswith(official_site):
                    continue  # déjà couvert
                targets.add(url)

            # ---- Étape 4 : scraping parallèle ------------------------
            all_emails: set[str] = set(snippet_emails)
            sem = asyncio.Semaphore(self.concurrency)
            tasks = [
                self._scrape_with_sem(sem, session, url)
                for url in targets
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, set):
                    all_emails.update(result)

            # ---- Étape 5 : filtrage + scoring ------------------------
            filtered = self._filter_emails(all_emails)
            logger.info(f"  {len(filtered)} email(s) après filtrage")

            for e in filtered:
                logger.debug(f"    - {e}")

            best = select_best_email(filtered, ctx.company_name, official_domain)

            if best:
                logger.info(
                    f"  => Meilleur : {best.email} (score={best.score})"
                )
                for r in best.reasons:
                    logger.debug(f"       {r}")
            else:
                logger.warning(
                    f"  => Aucun email pertinent trouvé pour {ctx.company_name}."
                )

            return best

    # ==================================================================
    # Recherche multi-moteur
    # ==================================================================

    async def _search_all_engines(
        self,
        ctx: _SearchContext,
        session: aiohttp.ClientSession,
    ) -> tuple[list[str], set[str]]:
        """Lance DDG (séquentiel) + Bing (parallèle) et fusionne."""

        queries = self._build_queries(ctx)

        # DDG dans un thread executor (évite de bloquer la boucle)
        loop = asyncio.get_running_loop()
        ddg_urls, ddg_emails = await loop.run_in_executor(
            None, self._search_ddg_sync, queries,
        )

        # Bing en parallèle (top 5 requêtes)
        bing_tasks = [
            self._search_bing(query, session)
            for query in queries[:5]
        ]
        bing_results = await asyncio.gather(
            *bing_tasks, return_exceptions=True,
        )

        # Fusionner
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
        """Recherche DDG séquentielle (appelée via ``run_in_executor``)."""

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
        """Recherche Bing via scraping HTML (best-effort)."""

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
    # Construction des requêtes
    # ==================================================================

    def _build_queries(self, ctx: _SearchContext) -> list[str]:
        """Construit les requêtes de recherche adaptées au contexte."""

        name = ctx.company_name
        loc = ctx.location_short
        slug = ctx.company_slug

        queries: list[str] = [
            # --- Priorité haute : email RH / recrutement ---
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
            # --- Mentions légales (souvent un email) ---
            f'"{name}" mentions légales email',
        ]

        # Requêtes géolocalisées
        if loc:
            queries.insert(3, f'"{name}" {loc} email contact')
            queries.insert(4, f'"{name}" {loc} recrutement')

        return queries

    # ==================================================================
    # Détection du site officiel
    # ==================================================================

    async def _guess_official_site(
        self,
        ctx: _SearchContext,
        urls: list[str],
        session: aiohttp.ClientSession,
    ) -> Optional[str]:
        """Identifie le site officiel de l'entreprise."""

        slug = ctx.company_slug

        # 1. Chercher dans les URLs trouvées par les moteurs
        for url in urls:
            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "")
            domain_base = domain.split(".")[0]

            if slug in domain_base or domain_base in slug:
                return f"{parsed.scheme}://{parsed.netloc}"

        # 2. Tenter les domaines courants en parallèle
        candidates: list[str] = []
        for tld in (".fr", ".com", ".eu", ".io", ".net"):
            candidates.append(f"https://www.{slug}{tld}")

        # Variante avec tirets (ex: « Futur Digital » → futur-digital.fr)
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
        """Tente un HEAD et renvoie True si le site existe (status < 400)."""
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
        """Scrape une page avec contrôle de concurrence."""
        async with sem:
            return await self._scrape_page_async(session, url)

    async def _scrape_page_async(
        self,
        session: aiohttp.ClientSession,
        url: str,
    ) -> set[str]:
        """Télécharge une page (ou la lit depuis le cache) et en extrait les emails."""

        # Vérifier le cache d'abord
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

                    # Lire seulement s'il s'agit de texte/HTML
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
        """Extrait les emails d'un texte brut (snippets, titres…)."""
        emails: set[str] = set()

        # Standard
        for email in _EMAIL_RE.findall(text):
            emails.add(email.lower())

        # Obfusqués : user(at)domain.com, user [at] domain.com
        for m in _OBFUSCATED_RE.finditer(text):
            emails.add(f"{m.group(1)}@{m.group(2)}".lower())

        return emails

    def _extract_emails_from_html(self, html: str) -> set[str]:
        """Extrait les emails d'un document HTML complet."""
        emails: set[str] = set()

        # Regex sur le texte brut
        emails.update(self._extract_emails_from_text(html))

        # Liens mailto:
        try:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup.find_all("a", href=True):
                href: str = tag["href"]
                if href.lower().startswith("mailto:"):
                    addr = (
                        href[7:]        # enlever « mailto: »
                        .split("?")[0]  # enlever les query params
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
        """Filtre les emails non pertinents."""
        result: list[str] = []

        for e in emails:
            if "@" not in e:
                continue

            local, domain = e.rsplit("@", 1)
            domain = domain.lower()

            # Domaines à ignorer
            if domain in _IGNORED_EMAIL_DOMAINS:
                continue

            # Sous-domaines de domaines ignorés (ex: ingest.sentry.io)
            if any(
                domain.endswith(f".{ign}") for ign in _IGNORED_EMAIL_DOMAINS
            ):
                continue

            # Extensions de fichiers (faux positifs CSS / images)
            if re.search(
                r"\.(png|jpg|jpeg|gif|svg|css|js|woff|woff2|ttf|eot"
                r"|ico|pdf|zip|mp4|webp|avif)$",
                e,
                re.IGNORECASE,
            ):
                continue

            # Trop court
            if len(local) < 2 or len(domain) < 4:
                continue

            # Partie locale en hex pur (DSN Sentry, tokens…)
            if len(local) > 16 and re.fullmatch(r"[a-f0-9]+", local):
                continue

            # Placeholders : votre.nom@, test@, example@…
            if _PLACEHOLDER_LOCAL_RE.match(local):
                continue

            result.append(e)

        return result
