"""Chargement et rendu du template de mail."""


class TemplateRenderer:
    """Charge un fichier texte et remplace les placeholders."""

    PLACEHOLDER = "{entreprise}"

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self._template: str = ""

    def load(self) -> None:
        """Charge le contenu du fichier template."""
        with open(self.filepath, "r", encoding="utf-8") as f:
            self._template = f.read()

    def render(self, company_name: str) -> str:
        """Remplace {entreprise} par le nom de l'entreprise."""
        if not self._template:
            raise RuntimeError("Template non chargé. Appelez load() d'abord.")
        return self._template.replace(self.PLACEHOLDER, company_name)

    @staticmethod
    def render_subject(subject_template: str, company_name: str) -> str:
        """Remplace {entreprise} dans l'objet du mail."""
        return subject_template.replace("{entreprise}", company_name)
