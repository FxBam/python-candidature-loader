# Python CV Mail Sender

## Description

Outil Python d'envoi automatique de candidatures par email. Le programme lit un fichier Excel contenant les entreprises ciblées, **recherche automatiquement les emails de contact manquants** via le web, génère des emails personnalisés, puis les envoie via Gmail SMTP.

Après chaque envoi, le fichier Excel est mis à jour immédiatement pour éviter les doublons et suivre l'avancement.

## Fonctionnalités

- **Recherche automatique d'emails** — DuckDuckGo + Bing en parallèle (asyncio/aiohttp), scraping des pages contact/carrières, détection d'emails obfusqués
- **Scoring intelligent** — algorithme de scoring pour sélectionner le meilleur email (RH > responsable IT > contact générique)
- **Correction IA** — validation et correction des emails trouvés via OpenAI ChatGPT
- **Envoi SMTP Gmail** — avec retry/backoff exponentiel et délais aléatoires anti-spam
- **Mise à jour Excel en temps réel** — sauvegarde après chaque email trouvé et après chaque envoi
- **Cache disque** — évite de re-télécharger les pages web déjà scrapées (TTL 24h)
- **Mode dry run** — simulation complète sans envoi réel
- **Nettoyage automatique** — suppression du cache et des artefacts Python en fin d'exécution

## Structure du projet

```
project/
├── main.py                # Point d'entrée — Phase 1: recherche emails, Phase 2: envoi + cleanup
├── excel_handler.py       # Gestion du fichier Excel (lecture, filtrage, sauvegarde)
├── email_finder.py        # Recherche d'emails async multi-moteur + scraping parallèle
├── email_corrector.py     # Correction d'emails via OpenAI ChatGPT
├── email_scorer.py        # Algorithme de scoring pour la sélection d'emails
├── email_sender.py        # Connexion et envoi SMTP
├── template_renderer.py   # Chargement et rendu du template de mail
├── page_cache.py          # Cache disque pour les pages scrapées
├── config.py              # Identifiants Gmail + paramètres (NON commité)
├── message.txt            # Template du corps de mail ({entreprise})
├── prompt.txt             # Template prompt ChatGPT pour la correction d'emails
├── send.log               # Fichier de log (auto-généré)
└── README.md
```

## Format du fichier Excel

Le fichier `Liste entreprises stage.xlsx` doit contenir les colonnes suivantes :

| Entreprise | Lieu | Intitulé de poste | Contact | Nom de la personne | Score | Date de contact |
|---|---|---|---|---|---|---|
| Google | Paris | Développeur | | | | |
| Microsoft | Lyon | Ingénieur | hr@microsoft.com | | | |

- **Entreprise** — nom de l'entreprise (utilisé pour la personnalisation et la recherche web)
- **Lieu** — localisation (contexte pour la recherche web)
- **Intitulé de poste** — poste visé (contexte pour la recherche web)
- **Contact** — email du destinataire (rempli automatiquement si vide)
- **Nom de la personne** — nom du contact trouvé (rempli automatiquement)
- **Score** — score de fiabilité de l'email trouvé
- **Date de contact** — rempli automatiquement après l'envoi

Une ligne est "en attente" si `Date de contact` est vide.

## Installation

Tuto en bas

## Configuration

### Gmail SMTP

1. Activer la validation en 2 étapes sur votre compte Google
2. Créer un mot de passe d'application
3. Renseigner les identifiants dans `config.py`

### config.py

Créer un fichier `config.py` avec :

```python
# SMTP
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "votre.email@gmail.com"
SENDER_APP_PASSWORD = "xxxx xxxx xxxx xxxx"

# Email
EMAIL_SUBJECT = "Candidature spontanée — {entreprise}"
MIN_DELAY = 30      # délai min entre envois (secondes)
MAX_DELAY = 90      # délai max entre envois (secondes)
MAX_RETRIES = 3
BACKOFF_FACTOR = 2

# Recherche web
AUTO_FIND_EMAILS = True
SEARCH_MIN_SCORE = 5
SEARCH_CONCURRENT_REQUESTS = 10

# OpenAI (correction d'emails)
ENABLE_EMAIL_CORRECTION = True
OPENAI_API_KEY = "sk-..."
OPENAI_MODEL = "gpt-4o-mini"

# Modes
DRY_RUN = False
LOG_LEVEL = "INFO"
LOG_FILE = "send.log"
```

## Fonctionnement

```
Liste entreprises stage.xlsx
  → Phase 1 : Recherche des emails manquants
       EmailFinder (DDG + Bing async → scraping parallèle → cache → scoring → correction IA)
       → Remplit les colonnes Contact, Nom, Score → sauvegarde après chaque trouvaille
  → Phase 2 : Envoi des candidatures
       Filtre les lignes prêtes (email présent, pas encore envoyé)
       → TemplateRenderer.render({entreprise})
       → EmailSender.send() avec retry/backoff
       → ExcelHandler.mark_sent() → sauvegarde après chaque envoi
  → Nettoyage automatique (cache web + __pycache__)
```

## Améliorations possibles

- Ajout automatique du CV en pièce jointe
- Arguments CLI (`--dry-run`, `--search-only`, `--send-only`)
- Interface graphique
- Support multi-comptes email
- Journalisation structurée (JSON)
- Intégration API Hunter.io / Snov.io

## Avertissement

> Ce projet doit être utilisé de manière responsable.
> L'envoi massif d'emails peut être considéré comme du spam.
> Respectez les bonnes pratiques et limitez le nombre d'envois.

## Licence

Projet open-source libre d'utilisation.




# Installation et Setup

## Prérequis

- Python 3.9 ou supérieur
- bash (inclus sur macOS / Linux, disponible sur Windows via Git Bash, WSL, ou MSYS2)

## Dépendances

Le script `setup.sh` installe automatiquement :

- `pandas` — manipulation Excel
- `openpyxl` — support format .xlsx
- `aiohttp` — requêtes HTTP asynchrones
- `beautifulsoup4` — parsing HTML
- `ddgs` — API DuckDuckGo
- `requests` — requêtes HTTP synchrones
- `openai` — API OpenAI ChatGPT

**+ toutes les stdlib** : `smtplib`, `email`, `logging`, `asyncio`, `random`, `time`, etc.

## Installation

### Sur macOS / Linux

```bash
# Rendre le script exécutable
chmod +x setup.sh

# Lancer le setup
./setup.sh
```

### Sur Windows

#### Option 1 : Git Bash / MSYS2 (recommandé)

```bash
chmod +x setup.sh
./setup.sh
```

#### Option 2 : WSL (Windows Subsystem for Linux)

```bash
chmod +x setup.sh
./setup.sh
```

#### Option 3 : PowerShell ou CMD (sans bash)

```powershell
# Si bash n'est pas disponible, installer manuellemen :
python -m venv venv
venv\Scripts\activate
pip install pandas openpyxl ddgs requests beautifulsoup4 aiohttp openai
```

## Configuration

1. **Copier le template** :
   ```bash
   cp config.example.py config.py
   ```

2. **Configurer Gmail SMTP** :
   - Activer la validation en 2 étapes sur votre compte Gmail
   - Créer un mot de passe d'application (pas le mot de passe du compte)
   - Remplir `SENDER_EMAIL` et `SENDER_APP_PASSWORD` dans `config.py`

3. **Configurer OpenAI (optionnel)** :
   - Créer une clé API sur https://platform.openai.com/api-keys
   - Remplir `OPENAI_API_KEY` dans `config.py`

4. **Préparer le fichier Excel** :
   - Créer `Liste entreprises stage.xlsx` avec les colonnes :
     - `Entreprise`, `Lieu`, `Intitulé de poste`, `Contact`, `Nom de la personne`, `Score`, `Date de contact`

## Lancement

```bash
# Activer le venv
source venv/bin/activate            # macOS / Linux
source venv/Scripts/activate        # Windows Git Bash
venv\Scripts\activate.bat           # Windows CMD/PowerShell

# Lancer le programme
python main.py
```

## Désinstallation

```bash
# Supprimer l'environnement virtuel
rm -rf venv                # macOS / Linux
rmdir /s venv              # Windows CMD
```

## Troubleshooting

### bash n'est pas disponible

- Installez Git Bash (https://git-scm.com/download/win)
- OU installez WSL (https://docs.microsoft.com/en-us/windows/wsl/install)
- OU lancez l'installation manuelle (voir section Installation, PowerShell/CMD)

### Python n'est pas trouvé

```bash
python --version
python3 --version
```

Si aucune commande ne marche, installez Python depuis https://www.python.org/

### Erreur lors de l'installation des dépendances

```bash
# Essayer de mettre à jour pip
pip install --upgrade pip

# Puis réinstaller
source venv/bin/activate  # réactiver si nécessaire
pip install pandas openpyxl ddgs requests beautifulsoup4 aiohttp openai
```
