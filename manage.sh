#!/usr/bin/env bash
# Luciole v3 — Gestion (Linux). Répertoire de base = répertoire du script.
set -euo pipefail

ACTION=""
INSTANCE=""
SERVICE=""
FORCE=0
TARGET_PROFILE=""

usage() {
  sed -n '1,40p' "$0" | grep '^#' | sed 's/^# //' | head -n 20 || true
  echo "Usage: $0 -Action <list|start|stop|restart|logs|status|remove|backup|urls|health|metrics|profiles|switch-profile> [-Instance name] [-Service name] [-Force] [-TargetProfile profil]"
  echo "  -TargetProfile : cpu | balanced | gpu-high | expert (pour switch-profile uniquement)"
  echo "  Ou: LUCIOLE_TARGET_PROFILE=balanced $0 -Action switch-profile"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -Action) ACTION="$2"; shift 2 ;;
    -Instance) INSTANCE="$2"; shift 2 ;;
    -Service) SERVICE="$2"; shift 2 ;;
    -Force) FORCE=1; shift ;;
    -TargetProfile) TARGET_PROFILE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Argument inconnu: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$ACTION" ]]; then
  echo "Erreur: -Action est obligatoire." >&2
  usage >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

read_instance_from_env() {
  if [[ -f ".env" ]]; then
    grep -E '^INSTANCE_NAME=' .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r'
  fi
}

resolve_instance() {
  if [[ -n "$INSTANCE" ]]; then
    echo "$INSTANCE"
    return
  fi
  local n
  n="$(read_instance_from_env)"
  if [[ -z "$n" ]]; then
    echo "INSTANCE_NAME introuvable : utilisez -Instance ou créez .env (install.sh)." >&2
    exit 1
  fi
  echo "$n"
}

get_env_val() {
  local key="$1"
  if [[ -f ".env" ]]; then
    grep -E "^${key}=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r'
  fi
}

compose_with_profile() {
  local profile
  profile="$(get_env_val COMPOSE_PROFILES)"
  [[ -z "$profile" ]] && profile="gpu"
  docker compose --profile "$profile" "$@"
}

INSTANCE_NAME=""
if [[ "$ACTION" != "list" && "$ACTION" != "profiles" ]]; then
  INSTANCE_NAME="$(resolve_instance)"
fi

case "$ACTION" in
  list)
    echo "Instances Luciole (conteneurs luciole-agent-*) :"
    mapfile -t lines < <(docker ps -a --format '{{.Names}}' 2>/dev/null | { grep -E '^luciole-agent-' || true; } | sed 's/^luciole-agent-//' | sort -u)
    if [[ ${#lines[@]} -eq 0 ]] || [[ -z "${lines[0]:-}" ]]; then
      echo "  (aucune instance détectée)"
    else
      for line in "${lines[@]}"; do
        [[ -n "$line" ]] && echo "  - $line"
      done
    fi
    ;;
  start)
    compose_with_profile start
    ;;
  stop)
    compose_with_profile stop
    ;;
  restart)
    compose_with_profile restart
    ;;
  logs)
    if [[ -n "$SERVICE" ]]; then
      compose_with_profile logs -f --tail 200 "$SERVICE"
    else
      compose_with_profile logs -f --tail 200
    fi
    ;;
  status)
    echo "=== docker compose ps ==="
    compose_with_profile ps -a
    echo ""
    echo "=== Conteneurs liés à l'instance ${INSTANCE_NAME} ==="
    docker ps -a --filter "name=luciole-${INSTANCE_NAME}" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    ;;
  remove)
    if [[ "$FORCE" -ne 1 ]]; then
      read -r -p "Supprimer l'instance (docker compose down -v) ? [o/N] " c || true
      if [[ ! "$c" =~ ^[oOyY]$ ]]; then
        echo "Annulé."
        exit 0
      fi
    fi
    profile="$(get_env_val COMPOSE_PROFILES)"
    [[ -z "$profile" ]] && profile="gpu"
    docker compose --profile "$profile" down -v
    ;;
  backup)
    ts="$(date +%Y%m%d-%H%M%S)"
    dest="$PROJECT_ROOT/backups/backup-$ts"
    mkdir -p "$dest"
    for d in data feedbacks config evaluation; do
      if [[ -d "$PROJECT_ROOT/$d" ]]; then
        cp -a "$PROJECT_ROOT/$d" "$dest/"
      fi
    done
    echo "Sauvegarde créée : $dest"
    ;;
  urls)
    api="$(get_env_val API_PORT)"; [[ -z "$api" ]] && api="8000"
    chat="$(get_env_val CHAT_PORT)"; [[ -z "$chat" ]] && chat="8501"
    admin="$(get_env_val ADMIN_PORT)"; [[ -z "$admin" ]] && admin="8080"
    feedback="$(get_env_val FEEDBACK_PORT)"; [[ -z "$feedback" ]] && feedback="8503"
    ollama="$(get_env_val OLLAMA_PORT)"; [[ -z "$ollama" ]] && ollama="11434"
    echo "Instance : $INSTANCE_NAME"
    echo "  API / health : http://localhost:${api}/api/health"
    echo "  Chat         : http://localhost:${chat}"
    echo "  Admin        : http://localhost:${admin}"
    echo "  Feedback     : http://localhost:${feedback}"
    echo "  Ollama       : http://localhost:${ollama}"
    ;;
  health)
    api="$(get_env_val API_PORT)"; [[ -z "$api" ]] && api="8000"
    echo "=== Conteneurs (luciole-*-${INSTANCE_NAME}) ==="
    docker ps -a --filter "name=luciole-${INSTANCE_NAME}" --format "table {{.Names}}\t{{.Status}}"
    echo ""
    echo "=== GET http://localhost:${api}/api/health ==="
    if command -v curl >/dev/null 2>&1; then
      curl -sS -m 15 "http://127.0.0.1:${api}/api/health" || echo "Échec curl" >&2
    else
      echo "curl non installé" >&2
    fi
    echo ""
    ;;
  metrics)
    db="$PROJECT_ROOT/feedbacks/ragas.db"
    if [[ ! -f "$db" ]]; then
      echo "Aucune base RAGAS ($db). Lancez des évaluations depuis l'UI Feedback." >&2
      exit 0
    fi
    if ! command -v sqlite3 >/dev/null 2>&1; then
      echo "sqlite3 introuvable dans le PATH." >&2
      exit 0
    fi
    echo "=== RAGAS — 30 derniers jours (toutes collections) ==="
    sqlite3 "$db" "
SELECT 
  IFNULL(index_name, '(null)') AS index_name,
  ROUND(AVG(faithfulness), 3) AS avg_faithfulness,
  ROUND(AVG(answer_relevancy), 3) AS avg_answer_relevancy,
  ROUND(AVG(context_recall), 3) AS avg_context_recall,
  COUNT(*) AS n
FROM ragas_scores
WHERE timestamp >= datetime('now', '-30 days')
GROUP BY index_name
ORDER BY index_name;
"
    echo ""
    echo "=== Dernières entrées (10) ==="
    sqlite3 -header -column "$db" "SELECT timestamp, index_name, faithfulness, answer_relevancy, context_recall FROM ragas_scores ORDER BY id DESC LIMIT 10;"
    ;;
  profiles)
    echo "Profils modèles disponibles (switch-profile) :"
    echo "  cpu        — Embedding/rerank légers + qwen2.5 7B (machine CPU / petit GPU)"
    echo "  balanced   — bge-m3 + bge-reranker-v2-m3 + qwen2.5 14B (défaut GPU)"
    echo "  gpu-high   — Même embedding/rerank, LLM 14B optimisé charge"
    echo "  expert     — bge-m3 + reranker + qwen2.5 32B (VRAM élevée)"
    echo ""
    echo "Profils Docker Compose (.env COMPOSE_PROFILES) : gpu | cpu"
    ;;
  switch-profile)
    tp="${TARGET_PROFILE:-}"
    [[ -z "$tp" ]] && tp="${LUCIOLE_TARGET_PROFILE:-}"
    if [[ -z "$tp" ]]; then
      echo "Usage: $0 -Action switch-profile -TargetProfile <cpu|balanced|gpu-high|expert>" >&2
      echo "Ou: LUCIOLE_TARGET_PROFILE=balanced $0 -Action switch-profile" >&2
      exit 1
    fi
    case "$tp" in
      cpu|balanced|gpu-high|expert) ;;
      *)
        echo "Profil inconnu: $tp" >&2
        exit 1
        ;;
    esac
    settings="$PROJECT_ROOT/config/settings.yaml"
    if [[ ! -f "$settings" ]]; then
      echo "Fichier introuvable: config/settings.yaml" >&2
      exit 1
    fi
    emb="BAAI/bge-m3"
    rer="BAAI/bge-reranker-v2-m3"
    llm="qwen2.5:14b-instruct-q4_K_M"
    bs="32"
    case "$tp" in
      cpu)
        llm="qwen2.5:7b-instruct-q4_K_M"
        bs="8"
        ;;
      balanced|gpu-high)
        llm="qwen2.5:14b-instruct-q4_K_M"
        bs="32"
        ;;
      expert)
        llm="qwen2.5:32b-instruct-q4_K_M"
        bs="32"
        ;;
    esac
    if command -v python3 >/dev/null 2>&1; then
      python3 - "$settings" "$emb" "$rer" "$llm" "$bs" <<'PY'
import re, sys
path, emb, rer, llm, bs = sys.argv[1:6]
with open(path, encoding="utf-8") as f:
    c = f.read()
c = re.sub(r'(embedding:\s*\r?\n  model: ")[^"]+(")', r"\1" + emb + r"\2", c, count=1)
c = re.sub(
    r"(embedding:\s*\r?\n  model:[^\r\n]+\r?\n  device:[^\r\n]+\r?\n  batch_size: )\d+",
    r"\1" + bs,
    c,
    count=1,
)
c = re.sub(r'(reranker:\s*\r?\n  model: ")[^"]+(")', r"\1" + rer + r"\2", c, count=1)
c = re.sub(
    r'(llm:\s*\r?\n  provider:[^\r\n]+\r?\n  model: ")[^"]+(")',
    r"\1" + llm + r"\2",
    c,
    count=1,
)
with open(path, "w", encoding="utf-8") as f:
    f.write(c)
PY
    else
      echo "python3 requis pour switch-profile (édition de config/settings.yaml)." >&2
      exit 1
    fi
    echo "Profil appliqué : $tp (config/settings.yaml)"
    echo "Redémarrez les services : ./manage.sh -Action restart -Instance $INSTANCE_NAME"
    if [[ "$tp" == "cpu" ]]; then
      echo "Pour exécution CPU-only : définissez COMPOSE_PROFILES=cpu dans .env puis relancez les conteneurs Ollama." >&2
    fi
    ;;
  *)
    echo "Action inconnue: $ACTION" >&2
    exit 1
    ;;
esac
