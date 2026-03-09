"""Point d'entrée — orchestre l'envoi des candidatures."""

import time

import config
from excel_handler import ExcelHandler
from email_sender import EmailSender
from template_renderer import TemplateRenderer

EXCEL_FILE = "Liste entreprises stage.xlsx"
TEMPLATE_FILE = "message.txt"
DELAY_BETWEEN_EMAILS = 5  # secondes entre chaque envoi


def main() -> None:
    # Charger les données
    excel = ExcelHandler(EXCEL_FILE)
    excel.load()

    renderer = TemplateRenderer(TEMPLATE_FILE)
    renderer.load()

    # Filtrer les candidatures non envoyées
    pending = excel.get_pending()

    if pending.empty:
        print("Aucune candidature à envoyer.")
        return

    print(f"{len(pending)} candidature(s) à envoyer.\n")

    # Envoyer les mails
    with EmailSender(
        config.SMTP_HOST, config.SMTP_PORT,
        config.SENDER_EMAIL, config.SENDER_APP_PASSWORD,
    ) as sender:
        for idx, row in pending.iterrows():
            company = excel.get_company_name(row)
            email = excel.get_contact_email(row)

            body = renderer.render(company)
            subject = TemplateRenderer.render_subject(config.EMAIL_SUBJECT, company)

            try:
                sender.send(email, subject, body)
                excel.mark_sent(idx)
                print(f"  [OK] {company} <{email}>")
            except Exception as e:
                print(f"  [ERREUR] {company} <{email}> : {e}")

            time.sleep(DELAY_BETWEEN_EMAILS)

    # Sauvegarder le fichier Excel
    excel.save()
    print("\nFichier Excel mis à jour.")


if __name__ == "__main__":
    main()
