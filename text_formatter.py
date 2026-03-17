"""Formatage du texte local — salutation selon le prénom et accord du nom d'entreprise."""

import logging
from functools import lru_cache

logger = logging.getLogger("candidature")


@lru_cache(maxsize=256)
def get_salutation(contact_name: str) -> str:
    """Retourne la salutation appropriée en demandant à l'utilisateur dans le terminal.
    
    - Si contact_name est vide → "Madame, Monsieur,"
    - S'il y a un nom, demande H/F pour renvoyer "Monsieur," ou "Madame,".
    """
    name = contact_name.strip() if contact_name else ""
    if not name:
        return "Madame, Monsieur,"

    while True:
        choice = input(f"\n[?] Le contact '{name}' a-t-il un genre Masculin ou Féminin ? [H/F] : ").strip().upper()
        if choice == 'H':
            return "Monsieur,"
        elif choice == 'F':
            return "Madame,"
        print("Choix invalide. Veuillez répondre par 'H' (Homme) ou 'F' (Femme).")


@lru_cache(maxsize=256)
def format_company_with_preposition(company_name: str) -> str:
    """Retourne le nom d'entreprise avec la bonne préposition française de manière locale.

    Exemples :
    - "Google" → "de Google"
    - "Apple" → "d'Apple"
    - "L'Oréal" → "de L'Oréal"
    - "Orange" → "d'Orange"
    - "La Poste" → "de La Poste"
    - "Le Monde" → "du Monde"
    - "Les Échos" → "des Échos"
    """
    name = company_name.strip() if company_name else ""
    if not name:
        return "de l'entreprise"

    lower_name = name.lower()

    # Mots commençant par L' ou L’ (ex: L'Oréal) -> on remplace le L' par de l'
    if lower_name.startswith("l'") or lower_name.startswith("l’"):
        return f"de l'{name[2:]}"

    # Articles définis contractés (ex: Les Échos -> des Échos, Le Monde -> du Monde)
    if lower_name.startswith("les "):
        return f"des {name[4:]}"
    if lower_name.startswith("le "):
        return f"du {name[3:]}"
    if lower_name.startswith("la "):
        return f"de {name}"

    # Liste d'exceptions strictes de H aspiré (pas d'élision)
    exceptions_h_aspire = {
        "hp", "huawei", "havas", "holland", "honda", "hyundai", "haier", "hasbro", 
        "h&m", "hachette", "heineken", "hilton", "hertz", "haribo", "halliburton",
        "hugo boss", "häagen-dazs", "harvey", "hasbro", "hachette"
    }
    for exc in exceptions_h_aspire:
        if lower_name == exc or lower_name.startswith(exc + " ") or lower_name.startswith(exc + "-"):
            return f"de {name}"

    # Liste d'exceptions pour Y consonne (ex: Yahoo -> de Yahoo, pas d'Yahoo)
    exceptions_y_consonne = {
        "yahoo", "yamaha", "yoplait", "youtube", "yves rocher", "yakult", "y Combinator"
    }
    for exc in exceptions_y_consonne:
        if lower_name == exc or lower_name.startswith(exc + " ") or lower_name.startswith(exc + "-"):
            return f"de {name}"

    # Entités, institutions ou acronymes prenant "de l'" au lieu de "d'"
    exceptions_de_l = {
        "urssaf", "agence", "association", "institut", "université", "universite", 
        "office", "hôpital", "hopital", "école", "ecole", "union", "organisation", 
        "académie", "academie", "assemblée", "assemblee", "amicale", "administration", 
        "inspection", "entreprise", "onu", "oms", "ap-hp", "inserm", "insee", "inra", 
        "inrae", "ofb", "ademe"
    }
    for exc in exceptions_de_l:
        if lower_name == exc or lower_name.startswith(exc + " ") or lower_name.startswith(exc + "-"):
            return f"de l'{name}"

    # Acronymes ou entités prenant "de la " au lieu de "de "
    exceptions_de_la = {
        "caf", "cpam", "sncf", "ratp", "carsat", "msa", "mdph", "cram", "ddass", "pmi", "cpme"
    }
    for exc in exceptions_de_la:
        if lower_name == exc or lower_name.startswith(exc + " ") or lower_name.startswith(exc + "-"):
            return f"de la {name}"

    # Règle générale d'élision pour les voyelles et le H muet
    if lower_name[0] in "aeiouyàâäéèêëïîôöùûü" or lower_name.startswith("h"):
        return f"d'{name}"

    return f"de {name}"
