"""Gestion du fichier Excel des entreprises."""

import re
from datetime import datetime

import pandas as pd

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_EMAIL_EXACT_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


class ExcelHandler:
    """Charge, filtre et met à jour le fichier Excel des candidatures."""

    # Noms des colonnes (doivent correspondre exactement au fichier Excel)
    COL_ENTREPRISE = "Entreprise"
    COL_LIEU = "Lieu"
    COL_CONTACT = "Contact"
    COL_NOM_PERSONNE = "Nom de la personne "  # espace trailing dans le xlsx
    COL_SCORE = "Score"
    COL_POSTE = "Intitulé de poste"
    COL_DATE_CONTACT = "Date de contact"

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.df: pd.DataFrame = pd.DataFrame()

    def load(self) -> None:
        """Charge le fichier Excel en mémoire."""
        self.df = pd.read_excel(self.filepath)
        # Convertir en object pour pouvoir y écrire des strings même si tout est NaN
        for col in (self.COL_DATE_CONTACT, self.COL_CONTACT,
                     self.COL_NOM_PERSONNE, self.COL_SCORE):
            if col in self.df.columns:
                self.df[col] = self.df[col].astype(object)

    def save(self) -> None:
        """Sauvegarde le DataFrame dans le fichier Excel."""
        self.df.to_excel(self.filepath, index=False)

    def get_pending(self) -> pd.DataFrame:
        """Renvoie les lignes dont la date de contact est vide ou non renseignée."""
        col = self.df[self.COL_DATE_CONTACT]
        return self.df[col.isna() | (col.astype(str).str.strip() == "")]

    def get_missing_emails(self) -> pd.DataFrame:
        """Renvoie les lignes sans email de contact (NaN ou vide)."""
        mask = self.df[self.COL_CONTACT].isna() | (
            self.df[self.COL_CONTACT].astype(str).str.strip() == ""
        )
        return self.df[mask]

    # ------------------------------------------------------------------
    # Écriture
    # ------------------------------------------------------------------

    def set_contact_email(self, index: int, email: str) -> None:
        """Écrit un email de contact dans une ligne donnée."""
        self.df.at[index, self.COL_CONTACT] = email

    def set_contact_name(self, index: int, name: str) -> None:
        """Écrit le nom de la personne associée à l'email trouvé."""
        self.df.at[index, self.COL_NOM_PERSONNE] = name

    def set_score(self, index: int, score: int) -> None:
        """Écrit le score de l'email trouvé."""
        self.df.at[index, self.COL_SCORE] = score

    def mark_sent(self, index: int) -> None:
        """Marque une ligne comme envoyée avec la date du jour."""
        self.df.at[index, self.COL_DATE_CONTACT] = datetime.now().strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------

    def has_company_name(self, row: pd.Series) -> bool:
        """Retourne True uniquement si la case Entreprise est renseignée."""
        val = row[self.COL_ENTREPRISE]
        if pd.isna(val):
            return False
        return str(val).strip() != ""

    def count_emails(self, row: pd.Series) -> int:
        """Retourne le nombre d'adresses email trouvées dans la case Contact."""
        val = row[self.COL_CONTACT]
        if pd.isna(val):
            return 0
        return len(_EMAIL_RE.findall(str(val)))

    def has_email(self, row: pd.Series) -> bool:
        """Retourne True uniquement si la case Contact est exactement un email (rien d'autre)."""
        val = row[self.COL_CONTACT]
        if pd.isna(val):
            return False
        return bool(_EMAIL_EXACT_RE.match(str(val).strip()))

    def get_company_name(self, row: pd.Series) -> str:
        """Extrait le nom de l'entreprise d'une ligne."""
        return str(row[self.COL_ENTREPRISE])

    def get_contact_email(self, row: pd.Series) -> str:
        """Extrait l'email de contact d'une ligne."""
        return str(row[self.COL_CONTACT])

    def get_location(self, row: pd.Series) -> str:
        """Extrait le lieu d'une ligne (vide si NaN)."""
        val = row[self.COL_LIEU]
        if pd.isna(val):
            return ""
        return str(val).strip()

    def get_job_title(self, row: pd.Series) -> str:
        """Extrait l'intitulé de poste d'une ligne (vide si NaN)."""
        val = row[self.COL_POSTE]
        if pd.isna(val):
            return ""
        return str(val).strip()
