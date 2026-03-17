"""Envoi d'emails via SMTP."""

import mimetypes
import smtplib
from email.mime.base import MIMEBase
from email import encoders
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


class EmailSender:
    """Gère la connexion SMTP et l'envoi des mails."""

    def __init__(self, host: str, port: int, email: str, password: str) -> None:
        self.host = host
        self.port = port
        self.email = email
        self.password = password
        self._smtp: smtplib.SMTP | None = None

    def connect(self) -> None:
        """Ouvre la connexion SMTP avec authentification STARTTLS."""
        self._smtp = smtplib.SMTP(self.host, self.port)
        self._smtp.ehlo()
        self._smtp.starttls()
        self._smtp.login(self.email, self.password)

    def disconnect(self) -> None:
        """Ferme la connexion SMTP."""
        if self._smtp is not None:
            self._smtp.quit()
            self._smtp = None

    def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        attachments: list[Path] | None = None,
    ) -> None:
        """Envoie un email en texte brut avec pièces jointes optionnelles."""
        if self._smtp is None:
            raise RuntimeError("SMTP non connecté. Appelez connect() d'abord.")

        msg = MIMEMultipart()
        msg["From"] = self.email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        for path in (attachments or []):
            mime_type, _ = mimetypes.guess_type(str(path))
            maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
            with open(path, "rb") as f:
                part = MIMEBase(maintype, subtype)
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=path.name)
            msg.attach(part)

        self._smtp.sendmail(self.email, to_email, msg.as_string())

    def __enter__(self) -> "EmailSender":
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.disconnect()
