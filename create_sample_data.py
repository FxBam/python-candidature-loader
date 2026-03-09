"""
Script utilitaire — Génère un fichier companies.xlsx d'exemple.
Lancer une seule fois pour initialiser le fichier de données.

Usage :
    pip install pandas openpyxl
    python create_sample_data.py
"""

import pandas as pd

data = {
    "company_name": ["Google", "Microsoft", "Amazon"],
    "email": ["contact@google.com", "hr@microsoft.com", "jobs@amazon.com"],
    "sent": ["FALSE", "FALSE", "FALSE"],
    "date_sent": ["", "", ""],
}

df = pd.DataFrame(data)
df.to_excel("companies.xlsx", index=False)
print("companies.xlsx créé avec succès.")
