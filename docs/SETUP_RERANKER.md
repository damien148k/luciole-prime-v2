# Setup du reranker BAAI/bge-reranker-v2-m3

Le reranker améliore significativement la pertinence des résultats RAG en re-scorant les passages renvoyés par la fusion BM25/dense. Sans lui, les passages les plus pertinents peuvent être noyés dans des résultats moins utiles.

## Problème courant : reranker non chargé

Symptôme dans les logs `luciole-agent-<instance>` :

```
Reranker model not found locally, will attempt online download
HF_HUB_OFFLINE=1 → download impossible
```

Cause : le modèle `BAAI/bge-reranker-v2-m3` (~2,2 GB) n'est pas présent dans le volume `models/huggingface/` monté côté hôte.

## Solution : pré-télécharger le modèle côté hôte

### Pré-requis

- Container `luciole-agent-<instance>` qui tourne (l'agent doit avoir bge-m3 déjà chargé)
- ~3 GB libres sur le disque hôte
- Python + `huggingface_hub` installable dans le container (déjà présent)

### Étape 1 — Vérifier que le volume HF est bien monté

Dans `docker-compose.yml`, les services `agent`, `admin-ui` et `watcher` doivent monter :

```yaml
volumes:
  - ./models/huggingface:/app/models/huggingface
```

Si le dossier `./models/huggingface/` est vide sur l'hôte mais que bge-m3 fonctionne dans le container, cela signifie que le cache est piégé **dans la couche du container** (pas sur le volume). Il faut d'abord récupérer ce cache :

```bash
cd ~/luciole-prime-<instance>

# Snapshot du cache HF du container vers le volume hôte
sudo docker exec luciole-agent-<instance> tar czf /tmp/hf_cache.tar.gz -C /app/models huggingface
sudo docker cp luciole-agent-<instance>:/tmp/hf_cache.tar.gz ./hf_cache.tar.gz
tar xzf hf_cache.tar.gz -C ./models/
rm hf_cache.tar.gz

# Vérifier
ls -la models/huggingface/
# Doit afficher : models--BAAI--bge-m3/, xet/, .locks/, CACHEDIR.TAG
```

### Étape 2 — Télécharger le reranker

Depuis l'hôte, en utilisant le container pour disposer de l'env Python complet :

```bash
sudo docker exec luciole-agent-<instance> python3 -c "
from huggingface_hub import snapshot_download
import os
os.environ['HF_HUB_OFFLINE'] = '0'  # override temporaire
path = snapshot_download(
    repo_id='BAAI/bge-reranker-v2-m3',
    cache_dir='/app/models/huggingface',
)
print(f'Downloaded to: {path}')
"
```

Le download prend ~2 à 5 minutes selon la connexion. Le modèle apparaîtra dans :
```
models/huggingface/models--BAAI--bge-reranker-v2-m3/snapshots/<hash>/
```
avec 6 fichiers : `config.json`, `model.safetensors`, `sentencepiece.bpe.model`, `special_tokens_map.json`, `tokenizer.json`, `tokenizer_config.json`.

### Étape 3 — Redémarrer l'agent

```bash
sudo docker restart luciole-agent-<instance>
sleep 20
sudo docker logs luciole-agent-<instance> --tail 30 | grep -iE "reranker"
```

Le log attendu :
```
Loading reranker model: BAAI/bge-reranker-v2-m3 on cpu (batch_size=8)
Modèle reranker trouvé dans le cache local: /app/models/huggingface/models--BAAI--bge-reranker-v2-m3/snapshots/<hash>
Chargement du reranker en mode offline depuis: ...
Reranker model loaded
```

## Validation

Pose une question dans l'UI chat (ou via mail) et vérifie les logs :

```bash
sudo docker logs luciole-agent-<instance> --tail 100 | grep -iE "rerank"
```

Tu dois voir :
```
Reranking 30 results
Reranked 30 results, returning top 15
```

## Pièges courants

### Le réseau Docker se perd après `up --no-deps`

Si tu fais `docker compose up --no-deps agent` sans avoir `COMPOSE_PROJECT_NAME` figé dans `.env`, docker compose peut créer un nouveau réseau et isoler les containers recréés des anciens.

**Toujours figer le projet name** dans `.env` :
```
INSTANCE_NAME=<nom>
COMPOSE_PROJECT_NAME=luciole-<nom>
```

### Modèle LLM dans settings.yaml ≠ modèle installé dans ollama

L'agent lit `config/settings.yaml` à chaque démarrage. Si `llm.model` ne correspond à aucun modèle pullé dans ollama, l'agent retourne **404** sur `/v1/chat/completions`.

Vérifier :
```bash
sudo docker exec luciole-ollama-<instance> ollama list
sudo docker exec luciole-agent-<instance> grep -A 3 "^llm:" /app/config/settings.yaml
```

Les deux modèles doivent matcher. Si tu changes le modèle, fais aussi un `ollama pull` correspondant.

## Configuration reranker dans settings.yaml

```yaml
reranker:
  model: BAAI/bge-reranker-v2-m3
  device: cpu              # ou cuda si GPU disponible
  batch_size: 8            # 8 sur CPU, 32+ sur GPU

retrieval:
  fusion_top_k: 30         # nombre de candidats avant rerank
  rerank_top_n: 15         # nombre de passages renvoyés après rerank
```

## Voir aussi

- Code reranker : `rag-system/src/retrieval/reranker.py`
- Config principale : `config/settings.yaml.example`
