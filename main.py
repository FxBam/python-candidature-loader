import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import pandas as pd

import config

EXCEL_FILE = "companies.xlsx"
TEMPLATE_FILE = "template.txt"
DELAY_BETWEEN_EMAILS = 5  # secondes


def load_template() -> str:
    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        return f.read()


def load_companies() -> pd.DataFrame:
    return pd.read_excel(EXCEL_FILE)


def save_companies(df: pd.DataFrame) -> None:
    df.to_excel(EXCEL_FILE, index=False)


def render_template(template: str, company_name: str) -> str:
    return template.replace("{company_name}", company_name)


def send_email(smtp: smtplib.SMTP, to_email: str, subject: str, body: str) -> None:
    msg = MIMEMultipart()
    msg["From"] = config.SENDER_EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    smtp.sendmail(config.SENDER_EMAIL, to_email, msg.as_string())


def main() -> None:
    template = load_template()
    df = load_companies()

    # Normaliser la colonne 'sent' pour comparer en minuscules
    pending = df[df["sent"].astype(str).str.upper() == "FALSE"]

    if pending.empty:
        print("Aucune candidature à envoyer.")
        return

    print(f"{len(pending)} candidature(s) à envoyer.")

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(config.SENDER_EMAIL, config.SENDER_APP_PASSWORD)

        for idx, row in pending.iterrows():
            company_name = row["company_name"]
            to_email = row["email"]

            body = render_template(template, company_name)

            try:
                send_email(smtp, to_email, config.EMAIL_SUBJECT, body)
                df.at[idx, "sent"] = "TRUE"
                df.at[idx, "date_sent"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[OK] {company_name} <{to_email}>")
            except Exception as e:
                print(f"[ERREUR] {company_name} <{to_email}> : {e}")

            time.sleep(DELAY_BETWEEN_EMAILS)

    save_companies(df)
    print("Fichier Excel mis à jour.")


if __name__ == "__main__":
    main()
