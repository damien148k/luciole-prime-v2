<div align="center">

<img src="https://lucioleprime.com/assets/favicon-512.png" alt="Logo Luciole" width="120"/>

# Luciole

**L'IA générative souveraine, installée chez vous.**

[![Licence: AGPL v3](https://img.shields.io/badge/Licence-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Made in France](https://img.shields.io/badge/Made%20in-France-002654?labelColor=ED2939)](https://lucioleprime.com)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)

[Site officiel](https://lucioleprime.com) · [Démo](https://lucioleprime.com/#demo) · [Documentation](#documentation) · [Contact](mailto:contact@148kprod.com)

</div>

---

## Qu'est-ce que Luciole ?

**Luciole est une IA générative souveraine installée sur vos propres serveurs.** Elle exploite vos documents internes (PDF, Word, Excel, intranet, SharePoint) grâce à un système RAG (*Retrieval-Augmented Generation*), sans jamais exposer vos données à des services tiers.

Conçue pour les entreprises françaises et européennes qui souhaitent **garder la maîtrise de leurs données**, Luciole tourne 100 % on-premise : aucune donnée ne quitte votre infrastructure, aucun abonnement par utilisateur, aucune dépendance à un service externe.

### Pour qui ?

- 🏢 **PME & ETI** qui veulent exploiter leur capital documentaire interne
- 🏛️ **Administrations & collectivités** soumises au RGPD strict
- ⚖️ **Cabinets d'avocats, experts-comptables, conseils** manipulant des données confidentielles
- 🏥 **Secteur santé** (RGPD + secret médical)
- 🛡️ **Industries sensibles** (défense, énergie, banque)

### Caractéristiques clés

| Caractéristique | Luciole |
|---|---|
| Données qui restent sur vos serveurs | ✅ |
| Conformité RGPD native | ✅ |
| Sans abonnement par utilisateur | ✅ |
| Fonctionnement hors-ligne possible | ✅ |
| Code open-source auditable | ✅ |
| Support technique en France | ✅ |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Interface utilisateur                     │
│              (Web UI · API REST · Intégrations)              │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    API FastAPI (Python)                      │
│         Orchestration · Auth · Logs · Rate limiting          │
└──────┬──────────────────┬──────────────────┬────────────────┘
       │                  │                  │
┌──────▼──────┐    ┌──────▼──────┐    ┌──────▼──────────┐
│   E5        │    │  OpenSearch │    │  LLM local      │
│ Embeddings  │───▶│  Vector DB  │───▶│  (LM Studio /   │
│ multilingue │    │  + BM25     │    │   Ollama)       │
└─────────────┘    └─────────────┘    └─────────────────┘
       ▲
       │
┌──────┴──────────────────────────────────────────────┐
│              Pipeline d'ingestion                    │
│   PDF · Word · Excel · PPT · MD · HTML · Texte      │
└─────────────────────────────────────────────────────┘
```

### Stack technique

- **Backend** : Python 3.11+ / FastAPI / Pydantic
- **Embeddings** : `multilingual-e5-large` (Hugging Face)
- **Vector DB** : OpenSearch (recherche hybride dense + BM25)
- **LLM** : LM Studio ou Ollama (Mistral, Llama 3, Qwen, ou modèles fine-tunés)
- **Ingestion** : pypdf, python-docx, openpyxl, BeautifulSoup, OCR (Tesseract)
- **Déploiement** : Docker Compose (single-node) ou Kubernetes (cluster)
- **Frontend** : interface web légère HTML/JS (modulaire)

---

## Démarrage rapide

### Prérequis

- Docker & Docker Compose v2+
- 16 Go RAM minimum (32 Go recommandés pour les modèles 7B+)
- 50 Go d'espace disque
- GPU NVIDIA optionnel (fortement recommandé pour les performances)

### Installation en ligne (machine connectée à internet)

**Windows :**
```powershell
git clone https://github.com/damien148k/luciole-prime.git
cd luciole-prime
.\INSTALL.ps1 -InstanceName mon-projet
```

**Linux / macOS :**
```bash
git clone https://github.com/damien148k/luciole-prime.git
cd luciole-prime
chmod +x install.sh
./install.sh mon-projet
```

Le script télécharge les images Docker, les modèles LLM (Qwen 2.5) et embeddings (BGE-M3), génère un mot de passe admin aléatoire, et démarre les services.

### Installation hors-ligne (air-gap)

Pour les environnements déconnectés d'internet (administrations, OIV, sites isolés) :

1. Sur une machine connectée : exécutez `PREPARE_OFFLINE.ps1` pour créer un package autonome
2. Transférez le package via clé USB / réseau interne
3. Sur la machine cible : exécutez `INSTALL_OFFLINE.ps1` (ou `install_offline.sh`)

Voir [GUIDE_INSTALLATION.md](GUIDE_INSTALLATION.md) pour la procédure détaillée.

### Accès aux interfaces

Après installation, les interfaces sont accessibles sur :
- **Chat** : `http://localhost:8501` — poser des questions à vos documents
- **Admin** : `http://localhost:8080` — ingestion et configuration
- **API** : `http://localhost:8000` — endpoint REST

Les identifiants admin sont sauvegardés dans `INSTANCE_CREDENTIALS.txt` à la racine de votre instance (à supprimer après lecture).

---

## Documentation

- 📘 [Guide d'installation complet](GUIDE_INSTALLATION.md) — installation pas-à-pas, online et offline
- ✅ [Vérifications post-déploiement](VERIFICATION_POST_DEPLOIEMENT.md) — checklist de validation après install
- 🔧 [Configuration de référence](config/) — fichiers `settings.yaml`, `prompts.yaml`, `synonyms.txt`
- 🐳 [Docker Compose](docker-compose.yml) — orchestration des services
- 🔐 [Politique de sécurité](SECURITY.md) — comment signaler une vulnérabilité

---

## Cas d'usage

### 📚 Base de connaissances interne
Vos employés interrogent en langage naturel tous les documents de l'entreprise — procédures, contrats, comptes-rendus, base de connaissance technique.

### ⚖️ Recherche juridique & contractuelle
Cabinets d'avocats, services juridiques : retrouvez instantanément la clause précise dans des milliers de contrats.

### 🏥 Dossiers médicaux & protocoles
Praticiens et chercheurs : interrogez vos protocoles, études et dossiers en restant 100 % sur site.

### 🏛️ Documentation publique
Collectivités : un assistant pour vos agents qui connaît vos délibérations, arrêtés, et règlements.

### 🏗️ Documentation technique
Industriels : interrogation des manuels machines, fiches sécurité, documentation produit.

---

## Pourquoi "souverain" ?

Avec Luciole, vous gardez le contrôle :

- **Vos données restent dans votre infrastructure.** Aucun appel à un service externe pour le traitement.
- **Hébergement local ou en France.** Le code et les données sont sur les serveurs de votre choix.
- **Auditable de bout en bout.** Le code est ouvert, vous pouvez le vérifier.
- **Pas de "vendor lock-in".** Vous restez maître de votre infrastructure et de vos modèles.
- **Conforme RGPD** par conception, sans transfert international.

---

## Feuille de route

- [x] Pipeline RAG complet (ingestion → embeddings → recherche → génération)
- [x] Interface web de chat
- [x] API REST documentée
- [x] Ingestion PDF/Word/Excel/Markdown
- [ ] Connecteurs SharePoint et Confluence
- [ ] Mode multi-tenant
- [ ] Fine-tuning assisté sur corpus client
- [ ] Module NMT+LLM pour traduction souveraine
- [ ] Interface d'administration avancée

---

## Contribution

Les contributions sont bienvenues. Voici comment participer :

1. Fork le projet
2. Créez votre branche (`git checkout -b feature/ma-fonctionnalite`)
3. Committez (`git commit -m 'Ajout de ma fonctionnalité'`)
4. Push (`git push origin feature/ma-fonctionnalite`)
5. Ouvrez une Pull Request

Avant de soumettre, lisez [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Licence

Luciole est distribuée sous licence **GNU Affero General Public License v3.0 (AGPL-3.0)**.

En clair :
- ✅ **Vous pouvez** utiliser, modifier, redistribuer Luciole librement
- ✅ **Vous pouvez** l'utiliser en interne dans votre entreprise
- ⚠️ **Si vous proposez Luciole comme service à des tiers** (SaaS, cloud, hébergement), vous devez publier votre code modifié sous la même licence

Voir [LICENSE](LICENSE) pour le texte intégral.

Pour un usage commercial qui ne peut pas se conformer à l'AGPL (licence propriétaire, intégration dans produit fermé), [contactez 148K](mailto:contact@148kprod.com) pour discuter d'une licence commerciale.

---

## Support & services professionnels

Luciole est développée par **[148K](https://lucioleprime.com)**, SASU basée à Vélizy-Villacoublay (France).

Nous proposons des **services professionnels** autour de Luciole :

- 🚀 **Installation clé-en-main** sur votre infrastructure
- 🎓 **Formation** de vos équipes techniques et métier
- 🔧 **Support et maintenance** sous SLA
- 🎯 **Fine-tuning** sur votre corpus métier
- 🏗️ **Intégrations sur mesure** (SSO, SI métier, connecteurs spécifiques)

📧 **contact@148kprod.com** · 🌐 **[lucioleprime.com](https://lucioleprime.com)**

---

<div align="center">

**🇫🇷 Fait en France, avec passion, pour la souveraineté numérique.**

⭐ Si Luciole vous est utile, mettez une étoile au repo — ça aide énormément.

</div>
