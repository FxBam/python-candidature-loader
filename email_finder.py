"""Recherche d'emails de contact pour une entreprise via le web.

Stratégie améliorée :
1. Construire des requêtes ciblées avec le nom, le lieu et l'intitulé de poste
2. Extraire les emails des snippets DuckDuckGo directement (rapide)
3. Identifier le site officiel de l'entreprise et scraper /contact, /careers…
4. Scraper les pages résultat les plus prometteuses
5. Dédupliquer, scorer, renvoyer le meilleur email
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

from email_scorer import ScoredEmail, select_best_email

logger = logging.getLogger("candidature")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

# Domaines à ignorer (faux positifs fréquents)
_IGNORED_EMAIL_DOMAINS = {
    "example.com", "sentry.io", "gravatar.com", "schema.org",
    "w3.org", "googleapis.com", "google.com", "facebook.com",
    "twitter.com", "linkedin.com", "instagram.com", "youtube.com",
    "cloudflare.com", "gstatic.com", "wixpress.com", "squarespace.com",
    "wordpress.com", "wp.com", "bootstrapcdn.com", "jquery.com",
    "jsdelivr.net", "unpkg.com", "creativecommons.org",
}

# Sous-pages à tenter sur le site officiel
_CONTACT_PATHS = [
    "/contact", "/contact/", "/contactez-nous", "/nous-contacter",
    "/careers", "/carrieres", "/recrutement", "/emploi", "/jobs",
    "/about", "/a-propos", "/qui-sommes-nous",
    "/team", "/equipe",
]


@dataclass
class _SearchContext:
    """Contexte riche pour une recherche d'email."""
    company_name: str
    location: str = ""
    job_title: str = ""

    @property
    def company_slug(self) -> str:
        return re.sub(r"[\s\-_.]+", "", self.company_name.lower())

    @property
    def location_short(self) -> str:
        """Première partie du lieu (ville sans 'Saint-' etc.)."""
        return self.location.split(",")[0].strip() if self.location else ""


class EmailFinder:
    """Recherche l'email de contact le plus pertinent pour une entreprise."""

    def __init__(
        self,
        max_results_per_query: int = 5,
        max_pages_to_scrape: int = 10,
        request_timeout: int = 10,
        delay_between_requests: float = 1.0,
    ) -> None:
        self.max_results_per_query = max_results_per_query
        self.max_pages_to_scrape = max_pages_to_scrape
        self.request_timeout = request_timeout
        self.delay_between_requests = delay_between_requests

    # ==================================================================
    # Public API
    # ==================================================================

    def find_best_email(
        self,
        company_name: str,
        location: str = "",
        job_title: str = "",
    ) -> Optional[ScoredEmail]:
        """Recherche le meilleur email de contact.

        Args:
            company_name: Nom de l'entreprise
            location: Ville / lieu (colonne "lieu" du Excel)
            job_title: Intitulé du poste (colonne "Intitulé de poste")

        Returns:
            Le meilleur ScoredEmail ou None
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

        all_emails: set[str] = set()

        # --- Étape 1 : recherche DDG (snippets + URLs) ----------------
        search_urls, snippet_emails = self._search_ddg(ctx)
        all_emails.update(snippet_emails)
        logger.info(
            f"  DDG: {len(search_urls)} URL(s), "
            f"{len(snippet_emails)} email(s) dans les snippets."
        )

        # --- Étape 2 : identifier le site officiel --------------------
        official_site = self._guess_official_site(ctx, search_urls)
        if official_site:
            logger.info(f"  Site officiel probable : {official_site}")
            site_emails = self._scrape_official_site(official_site)
            all_emails.update(site_emails)
            logger.info(f"  {len(site_emails)} email(s) depuis le site officiel.")

        # --- Étape 3 : scraper les URLs les plus prometteuses ---------
        scraped = 0
        for url in search_urls:
            if scraped >= self.max_pages_to_scrape:
                break
            # Skip si c'est le site officiel déjà scrapé
            if official_site and url.startswith(official_site):
                continue
            page_emails = self._scrape_page(url)
            all_emails.update(page_emails)
            scraped += 1
            time.sleep(self.delay_between_requests)

        # --- Filtrage et scoring --------------------------------------
        filtered = self._filter_emails(all_emails)
        logger.info(f"  {len(filtered)} email(s) uniques après filtrage.")

        for e in filtered:
            logger.debug(f"    - {e}")

        best = select_best_email(filtered, company_name)

        if best:
            logger.info(f"  => Meilleur : {best.email} (score={best.score})")
            for r in best.reasons:
                logger.debug(f"       {r}")
        else:
            logger.warning(f"  => Aucun email pertinent trouvé pour {company_name}.")

        return best

    # ==================================================================
    # Recherche DuckDuckGo
    # ==================================================================

    def _build_queries(self, ctx: _SearchContext) -> list[str]:
        """Construit les requêtes de recherche adaptées au contexte."""
        name = ctx.company_name
        loc = ctx.location_short
        slug = ctx.company_slug

        queries = [
            # --- Requêtes prioritaires : email RH / recrutement ---
            f'"{name}" email recrutement',
            f'"{name}" email RH ressources humaines',
            f'"{name}" contact recrutement candidature',
            # --- Avec le lieu pour désambiguïser ---
            f'"{name}" {loc} email contact' if loc else None,
            f'"{name}" {loc} recrutement' if loc else None,
            # --- Site officiel + contact ---
            f'site:{slug}.fr contact email',
            f'site:{slug}.com contact email',
            f'"{name}" site officiel contact',
            # --- Variantes candidature / stage ---
            f'"{name}" candidature spontanée email',
            f'"{name}" stage informatique contact',
            # --- Pages carrières ---
            f'"{name}" careers jobs email',
            f'"{name}" rejoignez-nous contact',
            # --- Responsable IT ---
            f'"{name}" responsable informatique email',
            f'"{name}" directeur technique email',
        ]

        return [q for q in queries if q is not None]

    def _search_ddg(self, ctx: _SearchContext) -> tuple[list[str], set[str]]:
        """Lance les requêtes DDG.

        Returns:
            (urls, snippet_emails) — URLs dédupliquées + emails trouvés dans les snippets
        """
        seen_urls: set[str] = set()
        urls: list[str] = []
        snippet_emails: set[str] = set()

        queries = self._build_queries(ctx)

        for query in queries:
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(
                        query, max_results=self.max_results_per_query
                    ))

                for r in results:
                    # Collecter l'URL
                    url = r.get("href") or r.get("link") or ""
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        urls.append(url)

                    # Extraire les emails des snippets (très fiable !)
                    text = " ".join(filter(None, [
                        r.get("body", ""),
                        r.get("title", ""),
                    ]))
                    for email in _EMAIL_RE.findall(text):
                        snippet_emails.add(email.lower())

            except Exception as exc:
                logger.debug(f"  Erreur DDG pour '{query}': {exc}")

            time.sleep(self.delay_between_requests)

        return urls, snippet_emails

    # ==================================================================
    # Identification du site officiel
    # ==================================================================

    def _guess_official_site(
        self, ctx: _SearchContext, urls: list[str]
    ) -> Optional[str]:
        """Essaie de deviner l'URL de base du site officiel."""

        slug = ctx.company_slug

        # 1. Chercher dans les URLs déjà trouvées un domaine correspondant
        for url in urls:
            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "")
            domain_base = domain.split(".")[0]

            # Le domaine contient le slug de l'entreprise ?
            if slug in domain_base or domain_base in slug:
                base = f"{parsed.scheme}://{parsed.netloc}"
                return base

        # 2. Tenter les domaines classiques (.fr, .com)
        for tld in (".fr", ".com", ".eu", ".io"):
            candidate = f"https://www.{slug}{tld}"
            try:
                resp = requests.head(
                    candidate,
                    headers=_HEADERS,
                    timeout=5,
                    allow_redirects=True,
                )
                if resp.status_code < 400:
                    return candidate
            except Exception:
                pass

        return None

    def _scrape_official_site(self, base_url: str) -> set[str]:
        """Scrape la page d'accueil + les sous-pages contact/careers."""
        all_emails: set[str] = set()

        # Page d'accueil
        all_emails.update(self._scrape_page(base_url))
        time.sleep(self.delay_between_requests)

        # Sous-pages classiques
        for path in _CONTACT_PATHS:
            url = urljoin(base_url, path)
            emails = self._scrape_page(url)
            all_emails.update(emails)
            time.sleep(self.delay_between_requests)

            # Arrêter si on a déjà trouvé des emails
            if len(all_emails) >= 3:
                break

        return all_emails

    # ==================================================================
    # Scraping générique
    # ==================================================================

    def _scrape_page(self, url: str) -> set[str]:
        """Télécharge une page et en extrait les adresses email."""
        emails: set[str] = set()

        try:
            resp = requests.get(
                url,
                headers=_HEADERS,
                timeout=self.request_timeout,
                allow_redirects=True,
            )
            if resp.status_code >= 400:
                return emails

            # Extraction regex brute
            raw = _EMAIL_RE.findall(resp.text)
            emails.update(e.lower() for e in raw)

            # Extraction des mailto:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup.find_all("a", href=True):
                href = tag["href"]
                if href.startswith("mailto:"):
                    addr = href.replace("mailto:", "").split("?")[0].strip().lower()
                    if _EMAIL_RE.match(addr):
                        emails.add(addr)

        except Exception as exc:
            logger.debug(f"  Erreur scraping {url}: {exc}")

        return emails

    # ==================================================================
    # Filtrage
    # ==================================================================

    @staticmethod
    def _filter_emails(emails: set[str]) -> list[str]:
        """Filtre les emails non pertinents (domaines ignorés, extensions de fichiers…)."""
        result = []
        for e in emails:
            domain = e.split("@")[1].lower() if "@" in e else ""

            # Ignorer les domaines parasites
            if domain in _IGNORED_EMAIL_DOMAINS:
                continue

            # Ignorer les faux positifs (ex: image@2x.png)
            if re.search(r"\.(png|jpg|jpeg|gif|svg|css|js|woff|ttf|ico)$", e, re.I):
                continue

            # Ignorer les emails trop courts (a@b.co)
            local = e.split("@")[0]
            if len(local) < 2 or len(domain) < 4:
                continue

            result.append(e)

        return result
