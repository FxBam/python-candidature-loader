"""Gestion du fichier Excel des entreprises."""

from datetime import datetime

import pandas as pd


class ExcelHandler:
    """Charge, filtre et met à jour le fichier Excel des candidatures."""

    # Noms des colonnes
    COL_ENTREPRISE = "Entreprise"
    COL_CONTACT = "contact"
    COL_DATE_CONTACT = "date de contact"

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.df: pd.DataFrame = pd.DataFrame()

    def load(self) -> None:
        """Charge le fichier Excel en mémoire."""
        self.df = pd.read_excel(self.filepath)

    def save(self) -> None:
        """Sauvegarde le DataFrame dans le fichier Excel."""
        self.df.to_excel(self.filepath, index=False)

    def get_pending(self) -> pd.DataFrame:
        """Renvoie les lignes dont la date de contact est vide (non envoyées)."""
        return self.df[self.df[self.COL_DATE_CONTACT].isna()]

    def mark_sent(self, index: int) -> None:
        """Marque une ligne comme envoyée avec la date/heure actuelle."""
        self.df.at[index, self.COL_DATE_CONTACT] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    def get_company_name(self, row: pd.Series) -> str:
        """Extrait le nom de l'entreprise d'une ligne."""
        return str(row[self.COL_ENTREPRISE])

    def get_contact_email(self, row: pd.Series) -> str:
        """Extrait l'email de contact d'une ligne."""
        return str(row[self.COL_CONTACT])
