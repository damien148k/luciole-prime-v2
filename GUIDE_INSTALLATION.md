# Luciole v4 â€” Guide d'installation complet pour debutants

> **Version** : 4.0 | **Auteur** : 148K | **Derniere MAJ** : Mai 2026

---

## Table des matieres

1. [Vue d'ensemble](#1-vue-densemble)
2. [Pre-requis materiel](#2-pre-requis-materiel)
3. [Phase A â€” Preparation sur machine connectee](#3-phase-a--preparation-sur-machine-connectee)
4. [Phase B â€” Installation sur machine cible (offline)](#4-phase-b--installation-sur-machine-cible-offline)
5. [Premier lancement et configuration](#5-premier-lancement-et-configuration)
6. [Utilisation au quotidien](#6-utilisation-au-quotidien)
7. [Gestion et maintenance](#7-gestion-et-maintenance)
8. [Depannage](#8-depannage)
9. [Architecture technique](#9-architecture-technique)

---

## 1. Vue d'ensemble

Luciole est un systeme de **RAG** (Retrieval Augmented Generation) qui permet de poser des questions en langage naturel sur vos documents. Il fonctionne **100% en local**, sans aucun appel a internet apres installation.

### Principe en 2 phases

```
  MACHINE AVEC INTERNET              CLE USB / RESEAU              MACHINE CIBLE (OFFLINE)
 â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”            â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”            â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
 â”‚  PREPARE_OFFLINE.ps1 â”‚ â”€â”€â”€â”€â”€â”€â”€â”€> â”‚  Package     â”‚ â”€â”€â”€â”€â”€â”€â”€â”€> â”‚   INSTALL_OFFLINE.ps1    â”‚
 â”‚  Telecharge tout :   â”‚            â”‚  ~20-40 Go   â”‚            â”‚   Demande le nom projet  â”‚
 â”‚  - Images Docker     â”‚            â”‚              â”‚            â”‚   Cree C:\RAG\luciole-X  â”‚
 â”‚  - Modeles IA        â”‚            â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜            â”‚   Demarre les services   â”‚
 â”‚  - Librairies Python â”‚                                        â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
 â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### Fonctionnement multi-instance

Chaque installation cree une instance isolee dans `C:\RAG\` (Windows) ou `/opt/rag/` (Linux) :

```
C:\RAG\
â”œâ”€â”€ luciole-chavenay\         â† Instance "chavenay"
â”‚   â”œâ”€â”€ data\                 â† Deposez vos documents ici
â”‚   â”‚   â”œâ”€â”€ uploads\
â”‚   â”‚   â””â”€â”€ processed\
â”‚   â”œâ”€â”€ config\               â† Configuration (prompts, settings, auth)
â”‚   â”œâ”€â”€ feedbacks\            â† Feedbacks utilisateurs + RAGAS
â”‚   â”œâ”€â”€ backups\              â† Sauvegardes
â”‚   â”œâ”€â”€ models\               â† Modeles IA pre-telecharges
â”‚   â”œâ”€â”€ docker-compose.yml
â”‚   â”œâ”€â”€ .env                  â† Ports, instance name
â”‚   â””â”€â”€ MANAGE.ps1            â† Script de gestion
â”‚
â”œâ”€â”€ luciole-juridique\        â† Instance "juridique" (independante)
â”‚   â”œâ”€â”€ data\
â”‚   â”œâ”€â”€ config\
â”‚   â””â”€â”€ ...
```

Chaque instance a ses propres containers, ports, index et donnees. Aucun partage entre instances.

### Ce que contient le package offline

| Composant | Taille approx. | Description |
|-----------|---------------|-------------|
| Image Docker Ollama | ~1.5 Go | Serveur LLM local |
| Image Docker Qdrant | ~200 Mo | Base vectorielle (embeddings) |
| Image Docker OpenSearch | ~800 Mo | Moteur de recherche textuel (BM25) |
| Image Docker Luciole | ~4 Go | Application principale (API + UI) |
| Modele LLM (Qwen2.5 14B) | ~9 Go | Intelligence artificielle generative |
| Modele Embedding (BGE-M3) | ~1.3 Go | Transforme le texte en vecteurs |
| Modele Reranker | ~600 Mo | Re-classe les resultats de recherche |
| Modeles EasyOCR | ~100 Mo | Lecture d'images (OCR) |
| Packages Python (wheels) | ~2 Go | Librairies du systeme |
| **TOTAL** | **~20-40 Go** | Selon profil GPU/CPU |

---

## 2. Pre-requis materiel

### Machine de preparation (avec internet)

- **OS** : Windows 10/11 ou Linux (Ubuntu 22.04+)
- **Docker Desktop** installe et fonctionnel
- **Python 3.11+** installe
- **Espace disque** : 50 Go libres (temporaire pendant le telechargement)
- **Connexion internet** : debit correct (plusieurs Go a telecharger)

### Machine cible (offline)

| Composant | Minimum (CPU) | Recommande (GPU) |
|-----------|--------------|-------------------|
| **OS** | Windows Server 2019+ / Ubuntu 22.04+ | idem |
| **RAM** | 16 Go | 32 Go |
| **CPU** | 8 coeurs | 8+ coeurs |
| **GPU** | aucun | NVIDIA RTX 3070+ (8 Go VRAM) |
| **Disque** | 100 Go SSD | 200 Go NVMe SSD |
| **Docker** | Docker Desktop / Engine | idem |

> **Important** : Docker Desktop doit etre installe AVANT de lancer l'installation offline.
> Si la machine cible n'a pas Docker, voir la section [Installer Docker hors-ligne](#installer-docker-hors-ligne).

---

## 3. Phase A â€” Preparation sur machine connectee

### Etape 1 : Copier le projet Luciole_V3

Copiez le dossier `Luciole_V3` sur la machine connectee (ou clonez le depot).

### Etape 2 : Ouvrir un terminal

**Windows** : clic droit sur le dossier `Luciole_V3` > "Ouvrir dans le terminal"
**Linux** : `cd /chemin/vers/Luciole_V3`

### Etape 3 : Lancer la preparation

**Windows (PowerShell)** :
```powershell
# Profil GPU (machine cible avec carte NVIDIA) :
.\PREPARE_OFFLINE.ps1 -Profile gpu

# Profil CPU (machine cible sans GPU) :
.\PREPARE_OFFLINE.ps1 -Profile cpu

# Avec chemin de sortie personnalise (ex: cle USB) :
.\PREPARE_OFFLINE.ps1 -Profile gpu -OutputDir "E:\luciole_package"
```

**Linux (Bash)** :
```bash
chmod +x prepare_offline.sh
# GPU :
./prepare_offline.sh gpu
# CPU :
./prepare_offline.sh cpu
# Avec chemin personnalise :
./prepare_offline.sh gpu /media/usb/luciole_package
```

### Ce que fait le script

Le script effectue automatiquement ces etapes (duree : 30 min a 2h selon connexion) :

1. **Cree la structure** du package de sortie
2. **Telecharge 4 images Docker** et les exporte en fichiers `.tar`
3. **Build l'image Luciole** (compile l'application)
4. **Telecharge le modele LLM** Qwen2.5 via Ollama (9 Go pour GPU, 5 Go pour CPU)
5. **Telecharge les modeles HuggingFace** (embedding BGE-M3 + reranker)
6. **Telecharge les packages Python** sous forme de wheels
7. **Genere un manifeste** MANIFEST.json recapitulatif

### Etape 4 : Verifier le package

A la fin, le script affiche un resume. Verifiez que :
- La taille totale est coherente (~20-40 Go)
- Aucune erreur critique n'est apparue
- Le fichier `MANIFEST.json` existe dans le dossier de sortie

### Etape 5 : Transferer le package

Copiez le dossier `offline_package` (ou le chemin personnalise) sur :
- Une **cle USB** (USB 3.0 recommande, le transfert de 20+ Go prend du temps en USB 2.0)
- Un **disque dur externe**
- Un **partage reseau** accessible depuis la machine cible

---

## 4. Phase B â€” Installation sur machine cible (offline)

### Etape 1 : Copier le package

Copiez le dossier du package sur le disque local de la machine cible.
Exemple : `C:\Luciole_Package` (Windows) ou `/opt/luciole_package` (Linux).

> **Conseil** : Evitez les chemins avec des espaces ou des caracteres speciaux.

### Etape 2 : Verifier Docker

Ouvrez un terminal et tapez :
```
docker --version
```
Si Docker n'est pas installe, voir [Installer Docker hors-ligne](#installer-docker-hors-ligne).

### Etape 3 : Lancer l'installation

**Windows (PowerShell)** :
```powershell
cd C:\Luciole_Package
.\INSTALL_OFFLINE.ps1
```

Le script vous demande :
1. **Le nom du projet** (ex: `chavenay`, `juridique`, `rh`)
2. Il cree automatiquement `C:\RAG\luciole-chavenay\` avec toute la structure
3. Il detecte les ports disponibles et evite les conflits
4. Il demarre tous les services

Options avancees (sans prompt interactif) :
```powershell
.\INSTALL_OFFLINE.ps1 -InstanceName "chavenay" -Profile gpu
.\INSTALL_OFFLINE.ps1 -InstanceName "test-01" -Profile cpu -PackagePath "D:\package"
```

**Linux (Bash)** :
```bash
cd /opt/luciole_package
chmod +x install_offline.sh
./install_offline.sh
```

Options avancees :
```bash
./install_offline.sh chavenay gpu /chemin/vers/package
```

### Ce que fait le script

1. **Demande le nom du projet** (sauf si passe en parametre)
2. **Cree `C:\RAG\luciole-{nom}\`** avec la structure complete :
   - `data/uploads/` et `data/processed/` pour les documents
   - `config/` avec settings, prompts, auth
   - `feedbacks/` pour les retours utilisateurs
   - `backups/` pour les sauvegardes
   - `models/` pour les modeles IA
3. **Detecte les ports disponibles** (pas de conflit si plusieurs instances)
4. **Charge les images Docker** depuis les fichiers `.tar` (~5-10 min)
5. **Copie les modeles** IA pre-telecharges
6. **Genere `.env` et `auth.yaml`** avec mot de passe par defaut
7. **Demarre tous les services** (Ollama, Qdrant, OpenSearch, Luciole)

### Etape 4 : Verifier l'installation

A la fin du script, les URLs et identifiants sont affiches :

| Service | URL par defaut | Description |
|---------|---------------|-------------|
| **Chat** | http://localhost:8501 | Interface de conversation (avec feedback pour les key users) |
| **Admin / Ingestion** | http://localhost:8080 | Ingestion de documents + RAGAS |
| **Config** | http://localhost:8503 | Configuration : prompt, synonymes, modeles Ollama, dashboard feedbacks |
| **API** | http://localhost:8000 | API REST directe |

> Les ports peuvent varier si d'autres instances occupent deja les ports par defaut.
> Consultez le fichier `C:\RAG\luciole-{nom}\.env` pour les ports reels.

Identifiants Admin par defaut :
- **Utilisateur** : `admin`
- **Mot de passe** : (genere aleatoirement, voir `INSTANCE_CREDENTIALS.txt` a la racine de votre instance)

---

## 5. Premier lancement et configuration

### 5.1 Se connecter a l'Admin

1. Ouvrez `http://localhost:8080`
2. Entrez les identifiants :
   - **Utilisateur** : `admin`
   - **Mot de passe** : (genere aleatoirement, voir `INSTANCE_CREDENTIALS.txt` a la racine de votre instance)

> Pour changer le mot de passe, generez un nouveau hash bcrypt :
> ```python
> import bcrypt
> print(bcrypt.hashpw(b"nouveau_mot_de_passe", bcrypt.gensalt()).decode())
> ```
> Puis remplacez la valeur dans `C:\RAG\luciole-{nom}\config\auth.yaml` > `credentials.usernames.admin.password`.

### 5.2 Ingerer vos premiers documents

1. **Deposez vos documents** dans `C:\RAG\luciole-{nom}\data\`
   - Formats supportes : PDF, DOCX, PPTX, XLSX, MSG, EML, TXT, images (JPG, PNG...)
   - Vous pouvez creer des sous-dossiers pour organiser vos fichiers
2. Ouvrez l'Admin UI (`http://localhost:8080`)
3. Onglet **Ingestion** > le chemin `/app/data` correspond a votre dossier `data/`
4. **Selectionnez** les fichiers ou dossiers a indexer
5. **Cliquez sur "Lancer l'ingestion"**
6. Les logs s'affichent en temps reel (parsing, chunking, embedding, indexation)

> **Premiere ingestion** : comptez ~1 min/document en GPU, ~5 min/document en CPU.
> Les ingestions suivantes sautent les fichiers deja indexes (suivi MD5).

### 5.3 Poser votre premiere question

1. Ouvrez le Chat (`http://localhost:8501`)
2. Tapez une question en langage naturel, par exemple :
   - "Quels sont les criteres d'evaluation du projet X ?"
   - "Resume le contenu du document Y"
   - "Quelle est la procedure pour Z ?"
3. Luciole va :
   - **Reformuler** votre question (query rewriting)
   - **Rechercher** dans vos documents (BM25 + vecteurs + fusion)
   - **Re-classer** les resultats (reranking)
   - **Generer** une reponse avec les sources citees

### 5.4 Personnaliser le prompt systeme

Le Config UI (`http://localhost:8503`) permet de saisir un **prompt personnalise** qui oriente le comportement de l'IA.
Exemple : "Tu es un assistant specialise en urbanisme de la ville de Chavenay."

Ce prompt est stocke dans `config/prompts.yaml` sous `system_prompt`.

---

## 6. Utilisation au quotidien

### Interface Chat (port 8501)

- **Conversation naturelle** : posez vos questions comme a un collegue
- **Historique** : les echanges precedents servent de contexte
- **Sources** : chaque reponse cite les documents utilises
- **Recherche approfondie** : cochez "Deep Search" pour une double recherche
- **Feedback** : les key users (definis dans `config/settings.yaml > feedback > key_users`) voient des boutons pouce haut/bas sous chaque reponse pour evaluer et corriger les reponses

### Interface Admin / Ingestion (port 8080)

- **Onglet Ingestion** : ajoutez de nouveaux documents, suivez les logs, gerez les index
- **Onglet RAGAS** : evaluez la qualite du RAG avec diagnostic et recommandations (analyse des feedbacks, simulation)

### Interface Config (port 8503)

- **System Prompt** : personnalisez le comportement de l'IA
- **Synonymes** : ajoutez des synonymes metier pour ameliorer la recherche
- **Modeles Ollama** : installez, activez, supprimez des modeles LLM
- **Parametres** : modifiez settings.yaml, query_rewriter.py
- **Dashboard feedbacks** : consultez et exportez les feedbacks des key users

### API REST (port 8000)

Pour integration avec d'autres systemes :
```bash
# Poser une question
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Votre question ici", "index_name": "documents"}'

# Verifier la sante
curl http://localhost:8000/health
```

---

## 7. Gestion et maintenance

### Commandes de gestion

Depuis le repertoire de l'instance (ex: `C:\RAG\luciole-chavenay\`) :

**Windows** :
```powershell
.\MANAGE.ps1 -Action status      # Etat des containers
.\MANAGE.ps1 -Action start       # Demarrer
.\MANAGE.ps1 -Action stop        # Arreter
.\MANAGE.ps1 -Action restart     # Redemarrer
.\MANAGE.ps1 -Action logs        # Voir les logs (tous les services)
.\MANAGE.ps1 -Action logs -Service admin-ui   # Logs d'un seul service
.\MANAGE.ps1 -Action health      # Verification de sante
.\MANAGE.ps1 -Action backup      # Sauvegarde
.\MANAGE.ps1 -Action metrics     # Scores RAGAS
.\MANAGE.ps1 -Action urls        # Afficher les URLs
.\MANAGE.ps1 -Action profiles    # Profils modeles disponibles
.\MANAGE.ps1 -Action list        # Lister toutes les instances
```

**Linux** :
```bash
./manage.sh -Action status
./manage.sh -Action start
./manage.sh -Action stop
./manage.sh -Action restart
./manage.sh -Action logs
./manage.sh -Action health
./manage.sh -Action backup
./manage.sh -Action metrics
./manage.sh -Action urls
./manage.sh -Action profiles
./manage.sh -Action list
```

### Deposer des documents

Le chemin de depot des documents est :

| Windows | Linux |
|---------|-------|
| `C:\RAG\luciole-{nom}\data\` | `/opt/rag/luciole-{nom}/data/` |

Deposez vos fichiers la, puis lancez l'ingestion via l'Admin UI.
Seuls les fichiers nouveaux/modifies seront traites (detection MD5).

### Sauvegardes

Les donnees a sauvegarder sont :
- `config/` -- configuration (settings, prompts, auth)
- `feedbacks/` -- feedbacks utilisateurs et scores RAGAS
- `data/` -- documents sources
- Volumes Docker : Qdrant (index vectoriel) et OpenSearch (index textuel)

La commande `backup` cree une archive automatique dans le dossier `backups/`.

### Changer de modele LLM

1. Editez `config/settings.yaml` (ou utilisez le Config UI sur le port 8503) :
   ```yaml
   llm:
     model: "qwen2.5:7b-instruct-q4_K_M"  # ou autre modele
   ```
2. Si le modele n'est pas deja dans Ollama :
   ```bash
   # Si internet est disponible :
   docker exec luciole-ollama-{nom} ollama pull nom-du-modele
   ```
3. Redemarrez : `.\MANAGE.ps1 -Action restart`

### Installer plusieurs instances

Lancez simplement `INSTALL_OFFLINE.ps1` (ou `install_offline.sh`) a nouveau avec un nom different :

```powershell
.\INSTALL_OFFLINE.ps1 -InstanceName "juridique"
.\INSTALL_OFFLINE.ps1 -InstanceName "rh"
```

Chaque instance sera creee dans un sous-dossier separe avec ses propres ports auto-detectes.

---

## 4-bis. Phase B-Linux -- Installation sur Debian/Ubuntu

### Etape 1 : Preparer la machine cible

Connectez-vous en root (ou avec sudo) puis installez les dependances minimales :

```bash
apt-get update && apt-get install -y curl wget ca-certificates gnupg sudo
```

### Etape 2 : Creer l'utilisateur dedie

```bash
# Creer l'utilisateur luciole
useradd -m -s /bin/bash luciole
passwd luciole        # choisir un mot de passe

# L'ajouter au groupe sudo
/usr/sbin/usermod -aG sudo luciole
```

### Etape 3 : Installer Docker Engine

```bash
# Script officiel Docker (recommande sur Debian/Ubuntu)
curl -fsSL https://get.docker.com | sh

# Ajouter luciole au groupe docker (evite sudo a chaque commande docker)
/usr/sbin/usermod -aG docker luciole

# Verifier
docker --version
docker compose version
```

> Note : si `usermod` n'est pas trouve, utilisez `/usr/sbin/usermod`.
> Note : `sudo` n'est pas installe par defaut sur Debian minimale -- installez-le d'abord.

### Etape 4 : Transferer le package offline

Depuis Windows avec WinSCP, copiez le dossier `offline_package` vers la VM.
Connexion SFTP : IP de la VM, utilisateur `luciole`, destination `/home/luciole/`.

Ou depuis PowerShell Windows :
```powershell
scp -r "C:\Users\...\offline_package" luciole@IP_VM:/home/luciole/
```

### Etape 5 : Rendre les scripts executables

```bash
chmod +x /home/luciole/offline_package/install_offline.sh
chmod +x /home/luciole/offline_package/install.sh
chmod +x /home/luciole/offline_package/manage.sh
```

### Etape 6 : Lancer l'installation

```bash
cd /home/luciole/offline_package

# Profil CPU (Hyper-V, VM sans GPU) :
bash install_offline.sh nom-du-projet cpu

# Profil GPU (machine avec NVIDIA + Container Toolkit) :
bash install_offline.sh nom-du-projet gpu
```

> Note GPU/CPU : si vous n'avez que `luciole-gpu.tar`, passez `cpu` quand meme --
> le script tague automatiquement l'image gpu en cpu.

### Etape 7 : Verifier l'installation

```bash
# Voir les containers
cd /opt/rag/luciole-{nom}
docker compose --profile cpu ps

# Lire les identifiants admin
cat /opt/rag/luciole-{nom}/INSTANCE_CREDENTIALS.txt

# Tester les services
curl http://localhost:8000/api/health
curl http://localhost:8503/health
```

Acces depuis le reseau local : `http://IP_VM:8501` (Chat), `http://IP_VM:8080` (Admin).

### Erreurs frequentes Linux et solutions

| Probleme | Cause | Solution |
|---|---|---|
| `sudo: commande introuvable` | Debian minimale sans sudo | `apt-get install -y sudo` |
| `usermod: commande introuvable` | PATH incomplet | Utiliser `/usr/sbin/usermod` |
| `Error: nvidia device driver` | VM sans GPU / Hyper-V | Utiliser le profil `cpu` |
| Erreur montage `parsers.py` | Faux dossiers Docker | Corrige automatiquement depuis v4.1 |
| Hash bcrypt echec | Python3 absent sur l'hote | Le script utilise Docker auto |
| `MAIL_ENC_KEY: variable sans liaison` | Python3 absent | Fallback bash integre depuis v4.1 |

---

### "Docker n'est pas installe"

Voir [Installer Docker hors-ligne](#installer-docker-hors-ligne).

### Un service ne demarre pas

```powershell
# Depuis C:\RAG\luciole-{nom}\
.\MANAGE.ps1 -Action logs

# Ou pour un service specifique :
docker logs luciole-ollama-{nom}
docker logs luciole-agent-{nom}
docker logs luciole-admin-{nom}
docker logs luciole-qdrant-{nom}
docker logs luciole-opensearch-{nom}
```

### "CUDA out of memory"

Le GPU n'a pas assez de VRAM. Solutions :
1. Passer au modele LLM plus leger (`qwen2.5:7b` au lieu de `14b`)
2. Reduire `batch_size` dans `settings.yaml`
3. Passer en profil CPU

### L'ingestion est lente

- **GPU** : verifiez que CUDA est bien utilise (`device: "auto"` dans settings.yaml)
- **CPU** : c'est normal, comptez ~5 min/document
- **PDF scannes** : l'OCR est plus lent, c'est attendu

### Les reponses ne sont pas pertinentes

1. Verifiez que les documents sont bien indexes (Admin UI > onglet Ingestion)
2. Essayez la "Deep Search" dans le Chat
3. Ajoutez des synonymes dans `config/synonyms.txt`
4. Personnalisez le prompt systeme (Config UI > System Prompt)
5. Consultez les metriques RAGAS (Admin UI > onglet RAGAS)

### Reinitialiser completement

```powershell
# Depuis C:\RAG\luciole-{nom}\
.\MANAGE.ps1 -Action remove -Force
# Relancer l'installation depuis le package
cd C:\Luciole_Package
.\INSTALL_OFFLINE.ps1
```

> **Attention** : `remove` supprime les volumes (index Qdrant + OpenSearch). Les documents sources dans `data/` ne sont pas affectes.

### Conflit de ports

Si un port est deja utilise, le script detecte automatiquement le prochain port libre. Les ports reels sont dans `.env`.

---

## 9. Architecture technique

### Schema des services

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  C:\RAG\luciole-{nom}\                                               â”‚
â”‚                                                                     â”‚
â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”                       â”‚
â”‚  â”‚  Ollama   â”‚  â”‚  Qdrant  â”‚  â”‚  OpenSearch  â”‚  (Services de donnees)â”‚
â”‚  â”‚  (LLM)   â”‚  â”‚ (vecteur)â”‚  â”‚   (BM25)     â”‚                       â”‚
â”‚  â”‚ :11434   â”‚  â”‚  :6333   â”‚  â”‚   :9200      â”‚                       â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜                       â”‚
â”‚       â–²              â–²              â–²                                â”‚
â”‚       â”‚              â”‚              â”‚                                â”‚
â”‚  â”Œâ”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”              â”‚
â”‚  â”‚              Agent API (:8000)                      â”‚              â”‚
â”‚  â”‚     RAG pipeline : query â†’ search â†’ rerank â†’ LLM  â”‚              â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜              â”‚
â”‚       â–²              â–²              â–²                                â”‚
â”‚  â”Œâ”€â”€â”€â”€â”´â”€â”€â”€â”€â”  â”Œâ”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”  â”Œâ”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”                        â”‚
â”‚  â”‚ Chat UI â”‚  â”‚ Admin UI  â”‚  â”‚ Config UI  â”‚                        â”‚
â”‚  â”‚ :8501   â”‚  â”‚ :8080     â”‚  â”‚ :8503      â”‚                        â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜                        â”‚
â”‚                                                                     â”‚
â”‚  docker-compose.yml + .env (ports configurables)                    â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### Pipeline de traitement

```
Document (PDF, DOCX, ...) 
    â”‚
    â–¼
[Parsing] â†’ Extraction du texte (+ OCR si image/scan)
    â”‚
    â–¼
[Chunking] â†’ Decoupage intelligent (adapte au format)
    â”‚
    â–¼
[Embedding] â†’ Vectorisation (BGE-M3, 1024 dimensions)
    â”‚
    â”œâ”€â”€> [Qdrant]     Stockage des vecteurs
    â””â”€â”€> [OpenSearch]  Indexation textuelle (BM25)

Question utilisateur
    â”‚
    â–¼
[Query Rewriting] â†’ Reformulation, synonymes, detection de type
    â”‚
    â”œâ”€â”€> [BM25 Search]   Recherche textuelle (OpenSearch)
    â””â”€â”€> [Dense Search]  Recherche vectorielle (Qdrant)
           â”‚
           â–¼
    [Hybrid Fusion] â†’ Fusion RRF des resultats
           â”‚
           â–¼
    [Reranking] â†’ Re-classement par pertinence (cross-encoder)
           â”‚
           â–¼
    [LLM Generation] â†’ Generation de la reponse (Qwen2.5 via Ollama)
           â”‚
           â–¼
    Reponse + Sources citees
```

### Fichiers de configuration

| Fichier | Role |
|---------|------|
| `config/settings.yaml` | Configuration principale (modeles, retrieval, chunking) |
| `config/prompts.yaml` | Prompts du LLM (systeme, RAG, no-results) |
| `config/synonyms.txt` | Synonymes metier (1 par ligne : terme1,terme2,terme3) |
| `config/auth.yaml` | Identifiants admin (bcrypt) |
| `.env` | Variables d'environnement Docker (ports, instance) |

### Structure d'une instance

```
C:\RAG\luciole-{nom}\
â”œâ”€â”€ data\                    â† Deposez vos documents ici
â”‚   â”œâ”€â”€ uploads\             â† Fichiers en attente
â”‚   â””â”€â”€ processed\           â† Fichiers traites
â”œâ”€â”€ config\
â”‚   â”œâ”€â”€ settings.yaml        â† Configuration principale
â”‚   â”œâ”€â”€ prompts.yaml         â† Prompts personnalises
â”‚   â”œâ”€â”€ synonyms.txt         â† Synonymes metier
â”‚   â””â”€â”€ auth.yaml            â† Identifiants admin
â”œâ”€â”€ feedbacks\
â”‚   â”œâ”€â”€ feedbacks.db         â† Base feedbacks utilisateurs
â”‚   â””â”€â”€ ragas.db             â† Base scores RAGAS
â”œâ”€â”€ evaluation\datasets\     â† Datasets RAGAS (optionnel)
â”œâ”€â”€ backups\                 â† Sauvegardes automatiques
â”œâ”€â”€ models\
â”‚   â”œâ”€â”€ huggingface\         â† Embedding + Reranker + OCR
â”‚   â””â”€â”€ ollama\              â† Modele LLM
â”œâ”€â”€ docker-compose.yml       â† Definition des services
â”œâ”€â”€ .env                     â† Ports et variables
â””â”€â”€ MANAGE.ps1               â† Script de gestion
```

---

## Installer Docker hors-ligne

### Windows

1. Sur une machine avec internet, telechargez l'installeur :
   - https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe
2. Copiez `Docker Desktop Installer.exe` sur la machine cible via USB
3. Executez l'installeur (acceptez les options par defaut)
4. **Redemarrez** la machine
5. Lancez Docker Desktop depuis le menu Demarrer
6. Attendez que Docker soit "Running" (icone verte dans la barre des taches)
7. Verifiez : `docker --version` dans PowerShell

### Linux (Ubuntu/Debian)

1. Sur une machine avec internet :
   ```bash
   # Telecharger les packages .deb
   apt-get download docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   ```
2. Copiez les fichiers `.deb` sur la machine cible
3. Sur la machine cible :
   ```bash
   sudo dpkg -i containerd.io_*.deb docker-ce-cli_*.deb docker-ce_*.deb \
       docker-buildx-plugin_*.deb docker-compose-plugin_*.deb
   sudo systemctl start docker
   sudo systemctl enable docker
   sudo usermod -aG docker $USER  # Pour eviter 'sudo' a chaque commande
   ```
4. Deconnectez-vous et reconnectez-vous
5. Verifiez : `docker --version`

### Linux (avec archive statique)

Alternative plus simple :
```bash
# Sur machine avec internet :
curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-24.0.7.tgz -o docker.tgz

# Sur machine cible :
tar xzf docker.tgz
sudo cp docker/* /usr/bin/
sudo dockerd &
```

### Drivers NVIDIA (pour profil GPU)

Si la machine cible a un GPU NVIDIA mais pas de drivers :

1. Sur une machine avec internet, telechargez le driver :
   - https://www.nvidia.com/Download/index.aspx (selectionnez votre GPU)
2. Copiez l'installeur sur la machine cible
3. Installez le driver
4. Installez le NVIDIA Container Toolkit :
   - https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html
5. Redemarrez Docker

---

## 10. Configuration du module mail

Le module mail permet à Luciole de recevoir des emails, générer des réponses RAG et les envoyer. Il se configure dans l'interface Config (port 8503) > onglet **📧 Mail**.

### 10.1 Serveur mail local de test (Greenmail)

Pour les tests en LAN fermé, utilisez le serveur Greenmail intégré (container `luciole-mail`).
Cliquez **🏠 Preset luciole-mail local** pour remplir automatiquement.

| Champ | Valeur |
|---|---|
| IMAP Hôte | `luciole-mail` |
| IMAP Port | `3143` |
| SSL/TLS | Non |
| SMTP Hôte | `luciole-mail` |
| SMTP Port | `3025` |
| TLS | Non |
| Utilisateur | `luciole@local.lan` |
| Mot de passe | `luciole2024` |

Depuis un client de messagerie externe (Thunderbird, Outlook) sur le même réseau :
remplacez `luciole-mail` par l'IP du serveur (ex: `192.168.1.100`), ports `143` et `25`.

### 10.2 Microsoft Exchange (on-premise)

| Champ | Valeur |
|---|---|
| IMAP Hôte | `mail.entreprise.fr` ou IP du serveur Exchange |
| IMAP Port | `993` (SSL) ou `143` (STARTTLS) |
| SSL/TLS | Oui |
| SMTP Hôte | `mail.entreprise.fr` |
| SMTP Port | `587` (STARTTLS recommandé) ou `465` (SSL) |
| TLS | Oui |
| Utilisateur | `luciole@entreprise.fr` |
| Mot de passe | Mot de passe du compte de service Exchange |

> Créer un compte de service dédié dans l'Active Directory (ex: `svc-luciole@entreprise.fr`).
> Activer IMAP et SMTP sur ce compte dans Exchange Admin Center.

### 10.3 Microsoft 365 / Office 365

| Champ | Valeur |
|---|---|
| IMAP Hôte | `outlook.office365.com` |
| IMAP Port | `993` |
| SSL/TLS | Oui |
| SMTP Hôte | `smtp.office365.com` |
| SMTP Port | `587` |
| TLS | Oui (STARTTLS) |
| Utilisateur | `luciole@entreprise.fr` |
| Mot de passe | Mot de passe du compte ou mot de passe d'application |

> **Important** : si l'authentification moderne (MFA/OAuth) est activée sur le tenant,
> créez un **mot de passe d'application** dans le portail Microsoft ou désactivez le MFA
> pour ce compte de service. IMAP/SMTP avec authentification basique doit être autorisé
> dans les paramètres Exchange Online (`Enable-MailboxSMTPClientAuthentication`).

### 10.4 Google Workspace (Gmail professionnel)

| Champ | Valeur |
|---|---|
| IMAP Hôte | `imap.gmail.com` |
| IMAP Port | `993` |
| SSL/TLS | Oui |
| SMTP Hôte | `smtp.gmail.com` |
| SMTP Port | `465` (SSL) ou `587` (STARTTLS) |
| TLS | Oui |
| Utilisateur | `luciole@entreprise.fr` |
| Mot de passe | Mot de passe d'application Google (pas le mot de passe du compte) |

> Activer IMAP dans les paramètres Gmail du compte.
> Générer un **mot de passe d'application** dans les paramètres de sécurité Google
> (nécessite la validation en deux étapes activée sur le compte).

### 10.5 Serveur SMTP/IMAP standard (Zimbra, Postfix+Dovecot, etc.)

| Champ | Valeur type |
|---|---|
| IMAP Hôte | `mail.entreprise.fr` |
| IMAP Port | `993` (IMAPS) ou `143` (IMAP+STARTTLS) |
| SSL/TLS | Oui si port 993, Non si port 143 |
| SMTP Hôte | `smtp.entreprise.fr` |
| SMTP Port | `465` (SMTPS) ou `587` (SMTP+STARTTLS) |
| TLS | Oui si port 465, Non si port 587 |
| Utilisateur | Adresse email complète du compte |
| Mot de passe | Mot de passe du compte |

### 10.6 Bonnes pratiques pour la mise en production

1. **Compte dédié** : créez un compte `luciole@entreprise.fr` (ou alias) spécifique, pas un compte utilisateur existant.
2. **Mot de passe fort** : généré, pas partagé avec d'autres systèmes.
3. **Droits minimaux** : accès IMAP/SMTP uniquement, pas d'accès admin.
4. **Boîte dédiée** : une seule boîte pour Luciole, bien identifiable pour les utilisateurs.
5. **Test avant activation** : utilisez les boutons "Tester IMAP" et "Tester SMTP" avant d'activer l'auto-réponse.
6. **Auto-réponse** : laissez désactivée au départ, validez les premiers brouillons manuellement.

---
