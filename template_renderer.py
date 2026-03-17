"""Chargement et rendu du template de mail."""


class TemplateRenderer:
    """Charge un fichier texte et remplace les placeholders."""

    PLACEHOLDER_ENTREPRISE = "{entreprise}"
    PLACEHOLDER_ENTREPRISE_DE = "{entreprise_de}"
    PLACEHOLDER_SALUTATION = "{salutation}"

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self._template: str = ""

    def load(self) -> None:
        """Charge le contenu du fichier template."""
        with open(self.filepath, "r", encoding="utf-8") as f:
            self._template = f.read()

    def render(
        self,
        company_name: str,
        salutation: str = "Madame, Monsieur,",
        company_with_preposition: str = "",
    ) -> str:
        """Remplace les placeholders dans le template.

        Placeholders :
        - {salutation} : "Madame," / "Monsieur," / "Madame, Monsieur,"
        - {entreprise_de} : "de Google" / "d'Apple" / "de L'Oréal"
        - {entreprise} : nom brut de l'entreprise (fallback)
        """
        if not self._template:
            raise RuntimeError("Template non chargé. Appelez load() d'abord.")

        # Fallback pour {entreprise_de} si non fourni
        if not company_with_preposition:
            company_with_preposition = f"de {company_name}"

        result = self._template
        result = result.replace(self.PLACEHOLDER_SALUTATION, salutation)
        result = result.replace(self.PLACEHOLDER_ENTREPRISE_DE, company_with_preposition)
        result = result.replace(self.PLACEHOLDER_ENTREPRISE, company_name)
        return result

    @staticmethod
    def render_subject(subject_template: str, company_name: str) -> str:
        """Remplace {entreprise} dans l'objet du mail."""
        return subject_template.replace("{entreprise}", company_name)
