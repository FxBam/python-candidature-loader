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
import random
import time
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
        max_results_per_query=getattr(config, "SEARCH_MAX_RESULTS", 5),
        max_pages_to_scrape=getattr(config, "SEARCH_MAX_PAGES", 8),
        request_timeout=getattr(config, "SEARCH_TIMEOUT", 10),
        delay_between_requests=getattr(config, "SEARCH_DELAY", 1.0),
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
            # Extraire un éventuel nom depuis les raisons du scoring
            excel.save()
            logger.info(
                f"  [TROUVÉ] {company} -> {result.email} "
                f"(score={result.score}) — sauvegardé"
            )
            found_count += 1
        else:
            score_info = f" (score={result.score})" if result else ""
            logger.warning(f"  [NON TROUVÉ] {company}{score_info} — sera ignoré à l'envoi.")

    if found_count > 0:
        logger.info(f"{found_count} email(s) trouvé(s) au total.")

    return found_count


# ------------------------------------------------------------------
# Phase 2 : envoi des candidatures
# ------------------------------------------------------------------

def _send_applications(
    excel: ExcelHandler,
    renderer: TemplateRenderer,
    logger: logging.Logger,
    dry_run: bool,
) -> None:
    """Envoie les candidatures pour toutes les lignes prêtes."""

    # Re-filtrer les pending (certains ont peut-être un email maintenant)
    pending = excel.get_pending()

    # Ne garder que celles qui ont un email
    ready = pending[pending.index.map(lambda i: excel.has_email(excel.df.loc[i]))]

    if ready.empty:
        logger.info("Aucune candidature prête à envoyer (emails manquants ou déjà envoyées).")
        return

    skipped = len(pending) - len(ready)
    if skipped > 0:
        logger.warning(f"{skipped} entreprise(s) ignorée(s) car sans email de contact.")

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
                        logger.info(f"[DRY RUN] Envoi simulé -> {company} <{email}>")
                    else:
                        sender.send(email, subject, body)

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

    # Phase 2 — envoi
    logger.info("=== Phase 2 : Envoi des candidatures ===")
    _send_applications(excel, renderer, logger, dry_run)


if __name__ == "__main__":
    main()
