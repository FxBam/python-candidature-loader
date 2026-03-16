# Python CV Mail Sender

## Description

Ce projet Python permet d'envoyer automatiquement des candidatures par email à partir d'un fichier Excel.

Le programme lit un fichier Excel contenant les informations des entreprises (nom, email, statut d'envoi, etc.), génère un email personnalisé à partir d'un texte modèle, puis envoie la candidature par email.

Après l'envoi, le fichier Excel est automatiquement mis à jour afin d'éviter les doublons et de suivre les candidatures envoyées.

Ce projet est utile pour automatiser l'envoi de candidatures spontanées ou de réponses à des offres d'emploi.

---

## Fonctionnalités

- Lecture d'un fichier Excel contenant les entreprises
- Génération automatique d'emails personnalisés
- Envoi d'emails via Gmail SMTP
- Mise à jour automatique du fichier Excel
- Suivi des candidatures envoyées
- Personnalisation du message avec le nom de l'entreprise

---

## Structure du projet

```
project/
│
├── main.py
├── companies.xlsx
├── template.txt
├── config.py
└── README.md
```

---

## Format du fichier Excel

Le fichier `companies.xlsx` doit contenir les colonnes suivantes :

| company_name | email | sent | date_sent |
|---|---|---|---|
| Google | contact@google.com | FALSE | |
| Microsoft | hr@microsoft.com | FALSE | |

### Description des colonnes

- **company_name** : nom de l'entreprise
- **email** : email du recruteur ou du contact
- **sent** : indique si la candidature a déjà été envoyée
- **date_sent** : date d'envoi du mail

Le programme envoie uniquement les mails où `sent = FALSE`.

---

## Modèle de mail

Le fichier `template.txt` contient le texte du mail avec des variables.

Exemple :

```
Bonjour,

Je vous contacte afin de vous proposer ma candidature pour un poste au sein de {company_name}.

Je vous remercie pour votre attention.

Cordialement,
Votre Nom
```

Le programme remplacera automatiquement `{company_name}` par le nom de l'entreprise.

---

## Installation

Installer les dépendances :

```bash
pip install pandas openpyxl
```

---

## Configuration Gmail

Le projet utilise le serveur SMTP de Gmail.

Dans votre compte Google :

1. Activer la validation en 2 étapes
2. Créer un mot de passe d'application
3. Utiliser ce mot de passe dans le script Python

SMTP utilisé :

```
smtp.gmail.com
port 587
```

---

## Fonctionnement

1. Le programme charge le fichier Excel
2. Il lit chaque entreprise
3. Il génère un email personnalisé
4. Il envoie l'email
5. Il met à jour le fichier Excel (`sent = TRUE`)
6. Il enregistre la date d'envoi

### Exemple de workflow

```
Excel → Lecture des entreprises
      → Génération du mail
      → Envoi SMTP
      → Mise à jour Excel
```

---

## Améliorations possibles

- Ajout automatique du CV en pièce jointe
- Gestion d'un délai entre les emails
- Génération automatique de lettres de motivation
- Interface graphique
- Envoi multi-comptes email
- Journalisation des envois

---

## Avertissement

> Ce projet doit être utilisé de manière responsable.
>
> L'envoi massif d'emails peut être considéré comme du spam.
> Respectez les bonnes pratiques et limitez le nombre d'envois.

---

## Licence

Projet open-source libre d'utilisation.