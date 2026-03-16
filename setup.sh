#!/usr/bin/env bash
# Setup script — crée l'environnement virtuel et installe les dépendances.
# Usage: bash setup.sh

set -e

VENV_DIR="venv"

echo "=== Python CV Mail Sender — Setup ==="
echo ""

# Vérifier Python
if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
    echo "Erreur : Python n'est pas installé."
    exit 1
fi

PYTHON=$(command -v python3 || command -v python)
echo "Python trouvé : $PYTHON ($($PYTHON --version 2>&1))"
echo ""

# Créer l'environnement virtuel
if [ ! -d "$VENV_DIR" ]; then
    echo "Création de l'environnement virtuel ($VENV_DIR/)..."
    $PYTHON -m venv "$VENV_DIR"
else
    echo "Environnement virtuel déjà existant ($VENV_DIR/)."
fi

# Activer le venv
if [ -f "$VENV_DIR/Scripts/activate" ]; then
    # Windows (Git Bash / MSYS2)
    source "$VENV_DIR/Scripts/activate"
else
    # Linux / macOS
    source "$VENV_DIR/bin/activate"
fi

echo "Environnement virtuel activé."
echo ""

# Mettre à jour pip
pip install --upgrade pip --quiet

# Installer les dépendances
echo "Installation des dépendances..."
pip install pandas openpyxl ddgs requests beautifulsoup4 aiohttp openai

echo ""
echo "=== Setup terminé ==="
echo ""
echo "Pour activer l'environnement virtuel :"
echo "  source $VENV_DIR/Scripts/activate   (Windows / Git Bash)"
echo "  source $VENV_DIR/bin/activate        (Linux / macOS)"
echo ""
echo "Copier config.example.py vers config.py et remplir les credentials :"
echo "  cp config.example.py config.py"
echo ""
echo "Lancer le programme :"
echo "  python main.py"
