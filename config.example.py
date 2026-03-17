# Gmail SMTP Configuration
# /!\ Ne jamais commiter config.py — il contient des données sensibles
# Copier ce fichier vers config.py et remplir les valeurs.

SENDER_EMAIL = "votre.email@gmail.com"
SENDER_APP_PASSWORD = "xxxx xxxx xxxx xxxx"  # Mot de passe d'application Gmail (pas le mot de passe du compte)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

EMAIL_SUBJECT = "Demande de stage informatique — {entreprise}"

# --- Paramètres d'envoi ---
# Délai aléatoire entre envois (secondes)
MIN_DELAY = 5
MAX_DELAY = 12

# Retry / backoff
MAX_RETRIES = 3
BACKOFF_FACTOR = 2  # base pour l'exponential backoff

# Logging
LOG_FILE = "send.log"
LOG_LEVEL = "INFO"

# Mode test: si True, les emails ne seront pas réellement envoyés
DRY_RUN = True

# --- Recherche d'emails ---
# Activer la recherche automatique d'emails pour les lignes sans contact
AUTO_FIND_EMAILS = True

# Nombre de résultats DDG par requête
SEARCH_MAX_RESULTS = 10

# Nombre max de pages web à scraper par entreprise
SEARCH_MAX_PAGES = 20

# Timeout HTTP pour le scraping (secondes)
SEARCH_TIMEOUT = 5

# Délai entre requêtes web (secondes)
SEARCH_DELAY = 0.2

# Score minimum pour accepter un email trouvé automatiquement
SEARCH_MIN_SCORE = 6

# Requêtes parallèles max (aiohttp)
SEARCH_CONCURRENT_REQUESTS = 10

# Nombre de tentatives par page avant abandon
SEARCH_RETRY = 2

# Durée de vie du cache des pages scrapées (secondes) — 24h par défaut
SEARCH_CACHE_TTL = 86400

# --- Nettoyage en fin d'exécution ---
# Supprime le dossier `cache/` (pages scrapées) à la fin de la phase 1 et en fin de programme.
CLEANUP_CACHE = True

# Supprime les `__pycache__/` et fichiers `.pyc` à la fin du programme.
CLEANUP_PYCACHE = True

# --- Correction d'email par ChatGPT ---
# Activer la correction automatique des emails trouvés via l'API OpenAI
ENABLE_EMAIL_CORRECTION = True

# Clé API OpenAI (https://platform.openai.com/api-keys)
OPENAI_API_KEY = "sk-..."

# Modèle OpenAI à utiliser
OPENAI_MODEL = "gpt-4o-mini"

# Fichier contenant le prompt de correction
CORRECTION_PROMPT_FILE = "prompt.txt"

# --- Formatage du texte (salutation + préposition entreprise) ---
# Utilise OpenAI pour :
# - Déterminer "Madame," ou "Monsieur," selon le prénom du contact
# - Accorder "de/d'/de l'" devant le nom d'entreprise
ENABLE_TEXT_FORMATTING = True

# --- Pièces jointes ---
# Chemins vers les fichiers à joindre à chaque email (laisser vide pour ne pas joindre)
CV_PATH = r""           # ex: r"C:/Users/moi/Documents/cv.pdf"
LM_PATH = r""           # ex: r"C:/Users/moi/Documents/lettre_de_motivation.pdf"
