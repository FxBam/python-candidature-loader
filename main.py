"""Point d'entrée — orchestre l'envoi des candidatures.

Ajouts:
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
from excel_handler import ExcelHandler
from email_sender import EmailSender
from template_renderer import TemplateRenderer

EXCEL_FILE = "Liste entreprises stage.xlsx"
TEMPLATE_FILE = "message.txt"


def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("candidature")
    if logger.handlers:
        return logger

    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(level)

    fh = logging.FileHandler(config.LOG_FILE)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(ch)

    return logger


def main(dry_run: Optional[bool] = None) -> None:
    """Orchestre l'envoi des candidatures.

    If `dry_run` is provided it overrides `config.DRY_RUN` for this run.
    """
    logger = _setup_logger()

    if dry_run is None:
        dry_run = bool(getattr(config, "DRY_RUN", False))

    # Charger les données
    excel = ExcelHandler(EXCEL_FILE)
    excel.load()

    renderer = TemplateRenderer(TEMPLATE_FILE)
    renderer.load()

    # Filtrer les candidatures non envoyées
    pending = excel.get_pending()

    if pending.empty:
        logger.info("Aucune candidature à envoyer.")
        return

    logger.info(f"{len(pending)} candidature(s) à envoyer.")

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

        for idx, row in pending.iterrows():
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
                    logger.info(f"[OK] {company} <{email}>")
                    success = True
                    break
                except Exception as exc:
                    logger.exception(f"[ERREUR] tentative {attempt} pour {company} <{email}> : {exc}")
                    if attempt == config.MAX_RETRIES:
                        logger.error(f"Echec définitif pour {company} <{email}> après {config.MAX_RETRIES} tentatives")
                        break

                    wait = config.BACKOFF_FACTOR ** (attempt - 1)
                    jitter = random.uniform(0, 1)
                    sleep_time = wait + jitter
                    logger.info(f"Nouvelle tentative dans {sleep_time:.1f}s (attempt {attempt}/{config.MAX_RETRIES})")
                    time.sleep(sleep_time)

                    # tenter de reconnecter le SMTP si possible
                    if sender is not None:
                        try:
                            sender.disconnect()
                            sender.connect()
                            logger.info("Reconnect SMTP réussie.")
                        except Exception:
                            logger.exception("Reconnect SMTP échouée — tentative suivante lancée")

            # délai aléatoire entre envois pour limiter le risque d'être marqué comme spam
            if dry_run:
                logger.info(f"[DRY RUN] Pause simulée entre {config.MIN_DELAY}s et {config.MAX_DELAY}s")
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

    # Sauvegarder le fichier Excel
    excel.save()
    logger.info("Fichier Excel mis à jour.")


if __name__ == "__main__":
    main()
