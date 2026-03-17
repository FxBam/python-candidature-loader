"""Point d'entrée — orchestre l'envoi des candidatures.

Fonctionnalités :
- Recherche automatique d'emails via DuckDuckGo + scraping
- Scoring et sélection du meilleur email (RH > responsable IT > contact)
- logging vers fichier `config.LOG_FILE`
- délai aléatoire entre `config.MIN_DELAY` et `config.MAX_DELAY`
- retry/exponential backoff (jusqu'à `config.MAX_RETRIES`)
- support `config.DRY_RUN` pour tests sans envoi réel
"""

import logging
import os
import random
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import config
from email_finder import EmailFinder
from email_sender import EmailSender
from excel_handler import ExcelHandler
from template_renderer import TemplateRenderer

EXCEL_FILE = "Liste entreprises stage.xlsx"
TEMPLATE_FILE = "message.txt"


def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("candidature")
    if logger.handlers:
        return logger

    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(level)

    fh = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(ch)

    return logger


# ------------------------------------------------------------------
# Nettoyage des artefacts d'exécution
# ------------------------------------------------------------------

@dataclass(frozen=True)
class CleanupReport:
    cache_removed: bool
    pycache_dirs_removed: int
    pyc_files_removed: int


def _on_rm_error(func, path, exc_info):  # noqa: ANN001
    """shutil.rmtree onerror handler (Windows-friendly)."""
    try:
        os.chmod(path, stat.S_IWRITE)
    except Exception:
        pass
    try:
        func(path)
    except Exception:
        pass


def remove_cache_dir(cache_dir: str | Path = "cache") -> bool:
    p = Path(cache_dir)
    if not p.exists():
        return False
    try:
        shutil.rmtree(p, onerror=_on_rm_error)
        return True
    except Exception:
        return False


def _remove_pycache(root: str | Path = ".") -> tuple[int, int]:
    base = Path(root)
    pycache_dirs_removed = 0
    pyc_files_removed = 0
    for d in list(base.rglob("__pycache__")):
        try:
            shutil.rmtree(d, onerror=_on_rm_error)
            pycache_dirs_removed += 1
        except Exception:
            pass
    for f in list(base.rglob("*.pyc")):
        try:
            f.unlink(missing_ok=True)
            pyc_files_removed += 1
        except Exception:
            pass
    return pycache_dirs_removed, pyc_files_removed


def cleanup_all(
    *,
    project_root: str | Path = ".",
    cache_dir: str | Path = "cache",
    clean_cache: bool = True,
    clean_pycache: bool = True,
) -> CleanupReport:
    cache_removed = remove_cache_dir(cache_dir) if clean_cache else False
    pycache_dirs_removed, pyc_files_removed = (
        _remove_pycache(project_root) if clean_pycache else (0, 0)
    )
    return CleanupReport(
        cache_removed=cache_removed,
        pycache_dirs_removed=pycache_dirs_removed,
        pyc_files_removed=pyc_files_removed,
    )


# ------------------------------------------------------------------
# Phase 1 : recherche automatique des emails manquants
# ------------------------------------------------------------------

def _find_missing_emails(excel: ExcelHandler, logger: logging.Logger) -> int:
    """Recherche les emails pour les lignes sans contact.

    Returns le nombre d'emails trouvés et écrits dans le DataFrame.
    """
    missing = excel.get_missing_emails()
    if missing.empty:
        logger.info("Tous les contacts ont déjà un email.")
        return 0

    logger.info(f"{len(missing)} entreprise(s) sans email — lancement de la recherche web.")

    finder = EmailFinder(
        max_results_per_query=getattr(config, "SEARCH_MAX_RESULTS", 10),
        max_pages_to_scrape=getattr(config, "SEARCH_MAX_PAGES", 20),
        request_timeout=getattr(config, "SEARCH_TIMEOUT", 5),
        delay_between_requests=getattr(config, "SEARCH_DELAY", 0.2),
        concurrent_requests=getattr(config, "SEARCH_CONCURRENT_REQUESTS", 10),
        retry_count=getattr(config, "SEARCH_RETRY", 2),
        cache_ttl=getattr(config, "SEARCH_CACHE_TTL", 86400),
    )

    min_score = getattr(config, "SEARCH_MIN_SCORE", 5)
    found_count = 0

    for idx, row in missing.iterrows():
        company = excel.get_company_name(row)
        location = excel.get_location(row)
        job_title = excel.get_job_title(row)
        result = finder.find_best_email(company, location=location, job_title=job_title)

        if result and result.score >= min_score:
            excel.set_contact_email(idx, result.email)
            excel.set_score(idx, result.score)
            if result.person_name:
                excel.set_contact_name(idx, result.person_name)
            excel.save()
            name_tag = f" [{result.person_name}]" if result.person_name else ""
            logger.info(
                f"  [TROUVÉ] {company} -> {result.email}{name_tag} "
                f"(score={result.score}) — sauvegardé"
            )
            found_count += 1
        else:
            score_info = f" (score={result.score})" if result else ""
            logger.warning(f"  [NON TROUVÉ] {company}{score_info} — sera ignoré à l'envoi.")

    # Le cache web n'est utile que pendant cette phase : on le supprime dès la fin.
    if bool(getattr(config, "CLEANUP_CACHE", True)):
        removed = remove_cache_dir("cache")
        if removed:
            logger.info("Cache web supprimé (dossier cache/).")

    if found_count > 0:
        logger.info(f"{found_count} email(s) trouvé(s) au total.")

    return found_count


def _resolve_attachments(logger: logging.Logger) -> list[Path]:
    """Résout et valide les chemins CV et LM depuis config.

    Retourne la liste des Path valides. Log un warning pour chaque fichier absent.
    """
    attachments: list[Path] = []
    for label, attr in (("CV", "CV_PATH"), ("Lettre de motivation", "LM_PATH")):
        raw = getattr(config, attr, "").strip()
        if not raw:
            continue
        p = Path(raw)
        if p.is_file():
            attachments.append(p)
            logger.info(f"  Pièce jointe [{label}] : {p.name}")
        else:
            logger.warning(f"  Pièce jointe [{label}] introuvable, ignorée : {p}")
    return attachments


# ------------------------------------------------------------------
# Phase 2 : envoi des candidatures
# ------------------------------------------------------------------

def _send_applications(
    excel: ExcelHandler,
    renderer: TemplateRenderer,
    logger: logging.Logger,
    dry_run: bool,
    attachments: list[Path] | None = None,
) -> None:
    """Envoie les candidatures pour toutes les lignes prêtes."""

    # Re-filtrer les pending (certains ont peut-être un email maintenant)
    pending = excel.get_pending()

    # Classifier chaque ligne : entreprise vide / aucun email / texte parasite / plusieurs emails / ok
    no_company  = [i for i in pending.index if not excel.has_company_name(excel.df.loc[i])]
    no_email    = [i for i in pending.index if excel.has_company_name(excel.df.loc[i]) and excel.count_emails(excel.df.loc[i]) == 0]
    dirty_email = [i for i in pending.index
                   if excel.has_company_name(excel.df.loc[i])
                   and excel.count_emails(excel.df.loc[i]) == 1
                   and not excel.has_email(excel.df.loc[i])]
    multi_email = [i for i in pending.index if excel.has_company_name(excel.df.loc[i]) and excel.count_emails(excel.df.loc[i]) > 1]
    ready = pending[pending.index.map(
        lambda i: excel.has_company_name(excel.df.loc[i]) and excel.has_email(excel.df.loc[i])
    )]

    if no_company:
        logger.warning(f"{len(no_company)} ligne(s) ignorée(s) : case Entreprise vide.")
    if no_email:
        logger.warning(f"{len(no_email)} entreprise(s) ignorée(s) : aucun email de contact.")
    for i in dirty_email:
        company = excel.get_company_name(excel.df.loc[i])
        logger.warning(
            f"  [IGNORÉ] {company} — email accompagné de texte parasite "
            f"(à nettoyer manuellement) : {excel.get_contact_email(excel.df.loc[i])!r}"
        )
    for i in multi_email:
        company = excel.get_company_name(excel.df.loc[i])
        logger.warning(
            f"  [IGNORÉ] {company} — plusieurs emails dans la case Contact "
            f"(à corriger manuellement) : {excel.get_contact_email(excel.df.loc[i])!r}"
        )

    if ready.empty:
        logger.info("Aucune candidature prête à envoyer (emails manquants ou déjà envoyées).")
        return

    logger.info(f"{len(ready)} candidature(s) à envoyer.")

    sender: Optional[EmailSender] = None
    try:
        if not dry_run:
            sender = EmailSender(
                config.SMTP_HOST, config.SMTP_PORT,
                config.SENDER_EMAIL, config.SENDER_APP_PASSWORD,
            )
            sender.connect()
            logger.info("Connexion SMTP établie.")
        else:
            logger.info("DRY_RUN activé — aucun mail ne sera envoyé.")

        for idx, row in ready.iterrows():
            company = excel.get_company_name(row)
            email = excel.get_contact_email(row)

            body = renderer.render(company)
            subject = TemplateRenderer.render_subject(config.EMAIL_SUBJECT, company)

            success = False
            for attempt in range(1, config.MAX_RETRIES + 1):
                try:
                    if dry_run:
                        aj = f" + {len(attachments)} pj" if attachments else ""
                        logger.info(f"[DRY RUN] Envoi simulé -> {company} <{email}>{aj}")
                    else:
                        sender.send(email, subject, body, attachments or [])

                    excel.mark_sent(idx)
                    excel.save()
                    logger.info(f"[OK] {company} <{email}> — sauvegardé")
                    success = True
                    break
                except Exception as exc:
                    logger.exception(
                        f"[ERREUR] tentative {attempt} pour {company} <{email}> : {exc}"
                    )
                    if attempt == config.MAX_RETRIES:
                        logger.error(
                            f"Echec définitif pour {company} <{email}> "
                            f"après {config.MAX_RETRIES} tentatives"
                        )
                        break

                    wait = config.BACKOFF_FACTOR ** (attempt - 1)
                    jitter = random.uniform(0, 1)
                    sleep_time = wait + jitter
                    logger.info(
                        f"Nouvelle tentative dans {sleep_time:.1f}s "
                        f"(attempt {attempt}/{config.MAX_RETRIES})"
                    )
                    time.sleep(sleep_time)

                    if sender is not None:
                        try:
                            sender.disconnect()
                            sender.connect()
                            logger.info("Reconnect SMTP réussie.")
                        except Exception:
                            logger.exception("Reconnect SMTP échouée")

            # délai aléatoire entre envois
            if dry_run:
                logger.info(
                    f"[DRY RUN] Pause simulée entre "
                    f"{config.MIN_DELAY}s et {config.MAX_DELAY}s"
                )
            else:
                delay = random.uniform(config.MIN_DELAY, config.MAX_DELAY)
                logger.info(f"Pause de {delay:.1f}s avant le prochain envoi")
                time.sleep(delay)

    finally:
        if sender is not None:
            try:
                sender.disconnect()
                logger.info("Déconnecté du serveur SMTP.")
            except Exception:
                logger.exception("Erreur lors de la déconnexion SMTP")

    logger.info("Envois terminés.")


# ------------------------------------------------------------------
# Orchestration
# ------------------------------------------------------------------

def main(dry_run: Optional[bool] = None) -> None:
    """Point d'entrée principal.

    Phase 1 : recherche automatique des emails manquants
    Phase 2 : envoi des candidatures
    """
    logger = _setup_logger()
    try:
        if dry_run is None:
            dry_run = bool(getattr(config, "DRY_RUN", False))

        # Charger les données
        excel = ExcelHandler(EXCEL_FILE)
        excel.load()

        renderer = TemplateRenderer(TEMPLATE_FILE)
        renderer.load()

        # Phase 1 — recherche d'emails
        auto_find = bool(getattr(config, "AUTO_FIND_EMAILS", True))
        if auto_find:
            logger.info("=== Phase 1 : Recherche des emails manquants ===")
            _find_missing_emails(excel, logger)
        else:
            logger.info("Recherche automatique désactivée (AUTO_FIND_EMAILS=False).")

        # Phase 1.5 — résolution des pièces jointes
        logger.info("=== Pièces jointes ===")
        attachments = _resolve_attachments(logger)
        if not attachments:
            logger.info("  Aucune pièce jointe configurée (CV_PATH / LM_PATH vides).")

        # Phase 2 — envoi
        logger.info("=== Phase 2 : Envoi des candidatures ===")
        _send_applications(excel, renderer, logger, dry_run, attachments)
    finally:
        # Nettoyage best-effort : cache web + fichiers d'exécution Python.
        report = cleanup_all(
            project_root=".",
            cache_dir="cache",
            clean_cache=bool(getattr(config, "CLEANUP_CACHE", True)),
            clean_pycache=bool(getattr(config, "CLEANUP_PYCACHE", True)),
        )
        logger.info(
            "Nettoyage terminé: "
            f"cache_removed={report.cache_removed}, "
            f"pycache_dirs_removed={report.pycache_dirs_removed}, "
            f"pyc_files_removed={report.pyc_files_removed}"
        )


if __name__ == "__main__":
    main()
