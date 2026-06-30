#!/bin/bash
# ============================================================================
# INSTALLATION LUCIOLE V3 -- Mode Multi-Instance (Linux)
# ============================================================================
# Cree une instance Luciole isolee pour un projet/metier.
# Structure : /opt/rag/luciole-{nom}/
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_INSTALL_PATH="/opt/rag"
INSTANCE_NAME="${1:-}"
PROFILE="${2:-gpu}"

# Ports par defaut
declare -A DEFAULT_PORTS=(
    [API]=8000
    [ADMIN]=8080
    [CHAT]=8501
    [FEEDBACK]=8503
    [QDRANT]=6333
    [OPENSEARCH]=9200
    [OLLAMA]=11434
    [MAIL_SMTP]=25
    [MAIL_IMAP]=143
    [MAIL_ADMIN_WEB]=8025
)

# ============================================================================
# Fonctions
# ============================================================================

step() { echo ""; echo "[$1] $2"; echo "------------------------------------------------------------"; }
ok()   { echo "  [OK] $1"; }
warn() { echo "  [!] $1"; }

validate_name() {
    [[ "$1" =~ ^[a-z0-9][a-z0-9-]*$ ]]
}

is_port_free() {
    ! ss -tlnH 2>/dev/null | grep -q ":$1 " && \
    ! docker ps --format '{{.Ports}}' 2>/dev/null | grep -q "0.0.0.0:$1->"
}

get_free_port() {
    local port=$1
    for _ in $(seq 1 100); do
        if is_port_free "$port"; then
            echo "$port"
            return
        fi
        port=$((port + 1))
    done
    echo "ERREUR: aucun port disponible depuis $1" >&2; exit 1
}

# ============================================================================
# Debut
# ============================================================================

echo ""
echo "================================================================"
echo "  LUCIOLE V3 -- Installation"
echo "================================================================"
echo ""
echo "Ce script cree une instance Luciole dediee a votre projet."
echo "Les fichiers seront installes dans $BASE_INSTALL_PATH/luciole-{nom}/"
echo ""

# Verification Docker
step "0/8" "Verification de Docker..."
if ! command -v docker &>/dev/null; then
    echo "  [ERREUR] Docker n'est pas installe." >&2
    exit 1
fi
docker ps &>/dev/null || { echo "  [ERREUR] Docker n'est pas demarre." >&2; exit 1; }
ok "Docker detecte: $(docker --version)"

# Nom de l'instance
step "1/8" "Configuration du projet..."

if [ -z "$INSTANCE_NAME" ]; then
    while true; do
        echo ""
        read -rp "  Nom du projet/metier (ex: chavenay, juridique, rh) : " INSTANCE_NAME
        INSTANCE_NAME=$(echo "$INSTANCE_NAME" | tr '[:upper:]' '[:lower:]' | xargs)
        if validate_name "$INSTANCE_NAME"; then
            break
        fi
        warn "Nom invalide. Utilisez: lettres minuscules, chiffres, tirets"
        echo "  Exemples: chavenay, juridique, rh, finance-2024"
        INSTANCE_NAME=""
    done
fi

INSTANCE_PATH="$BASE_INSTALL_PATH/luciole-$INSTANCE_NAME"

ok "Instance : $INSTANCE_NAME"
echo "  Repertoire : $INSTANCE_PATH"

# Verifier si existe deja
if [ -d "$INSTANCE_PATH" ]; then
    echo ""
    warn "L'instance '$INSTANCE_NAME' existe deja dans $INSTANCE_PATH"
    read -rp "  Voulez-vous la REMPLACER ? (oui/non) : " confirm
    if [ "$confirm" != "oui" ]; then
        echo "  Installation annulee."
        exit 0
    fi
    cd "$INSTANCE_PATH"
    docker compose --profile "$PROFILE" down 2>/dev/null || true
    cd "$SCRIPT_DIR"
fi

# Detection des ports
step "2/8" "Detection des ports disponibles..."

declare -A PORTS
for name in API ADMIN CHAT FEEDBACK QDRANT OPENSEARCH OLLAMA MAIL_SMTP MAIL_IMAP MAIL_ADMIN_WEB; do
    preferred=${DEFAULT_PORTS[$name]}
    allocated=$(get_free_port "$preferred")
    PORTS[$name]=$allocated
    if [ "$allocated" -eq "$preferred" ]; then
        printf "  %-12s : %s\n" "$name" "$allocated"
    else
        printf "  %-12s : %s (prefere %s occupe)\n" "$name" "$allocated" "$preferred"
    fi
done
ok "Ports alloues"

# Chargement images Docker (si .tar disponibles)
step "3/8" "Chargement des images Docker..."

if [ -d "$SCRIPT_DIR/docker_images" ]; then
    tar_count=$(find "$SCRIPT_DIR/docker_images" -name "*.tar" 2>/dev/null | wc -l)
    if [ "$tar_count" -gt 0 ]; then
        for tar_file in "$SCRIPT_DIR"/docker_images/*.tar; do
            name=$(basename "$tar_file")
            size=$(du -m "$tar_file" | cut -f1)
            echo "  Chargement $name (${size} Mo)..."
            docker load -i "$tar_file"
            ok "$name"
        done
    fi
fi

# Verifier image luciole et adapter automatiquement si necessaire
LUCIOLE_IMAGE="luciole-gpu:latest"
[ "$PROFILE" = "cpu" ] && LUCIOLE_IMAGE="luciole-cpu:latest"
if ! docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "^${LUCIOLE_IMAGE}$"; then
    if [ "$PROFILE" = "cpu" ] && docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "^luciole-gpu:latest$"; then
        docker tag luciole-gpu:latest luciole-cpu:latest
        warn "luciole-cpu:latest absent -- luciole-gpu tague comme luciole-cpu"
    elif [ "$PROFILE" = "gpu" ] && docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "^luciole-cpu:latest$"; then
        docker tag luciole-cpu:latest luciole-gpu:latest
        warn "luciole-gpu:latest absent -- luciole-cpu tague comme luciole-gpu"
    else
        echo "  Build de $LUCIOLE_IMAGE..."
        dockerfile="Dockerfile.gpu"
        [ "$PROFILE" = "cpu" ] && dockerfile="Dockerfile.cpu"
        docker build -f "$SCRIPT_DIR/$dockerfile" -t "$LUCIOLE_IMAGE" "$SCRIPT_DIR/rag-system"
    fi
fi
ok "Image $LUCIOLE_IMAGE disponible"

# Creation de la structure
step "4/8" "Creation de la structure pour '$INSTANCE_NAME'..."

# Detecter si sudo est necessaire (root n'en a pas besoin)
SUDO=""
[ "$(id -u)" != "0" ] && SUDO="sudo"

$SUDO mkdir -p \
    "$INSTANCE_PATH/data/$INSTANCE_NAME" \
    "$INSTANCE_PATH/data/uploads" \
    "$INSTANCE_PATH/data/processed" \
    "$INSTANCE_PATH/backups" \
    "$INSTANCE_PATH/config" \
    "$INSTANCE_PATH/feedbacks" \
    "$INSTANCE_PATH/evaluation/datasets" \
    "$INSTANCE_PATH/models/huggingface/hub" \
    "$INSTANCE_PATH/models/ollama" \
    "$INSTANCE_PATH/src_overrides/agent" \
    "$INSTANCE_PATH/src_overrides/api" \
    "$INSTANCE_PATH/src_overrides/ingestion" \
    "$INSTANCE_PATH/src_overrides/mail" \
    "$INSTANCE_PATH/src_overrides/watcher" \
    "$INSTANCE_PATH/mail-server/config" \
    "$INSTANCE_PATH/mail-server/init"

$SUDO chown -R "$(id -u):$(id -g)" "$INSTANCE_PATH"
ok "Repertoires crees"

# Copier la configuration
echo "  Copie de la configuration..."
cp -r "$SCRIPT_DIR/config/"* "$INSTANCE_PATH/config/"

# Generer settings.yaml depuis l'example si absent (config par instance, non versionnee)
if [ ! -f "$INSTANCE_PATH/config/settings.yaml" ] && [ -f "$INSTANCE_PATH/config/settings.yaml.example" ]; then
    cp "$INSTANCE_PATH/config/settings.yaml.example" "$INSTANCE_PATH/config/settings.yaml"
    ok "settings.yaml genere depuis settings.yaml.example"
fi
ok "Configuration copiee"

# Copier docker-compose.yml
cp "$SCRIPT_DIR/docker-compose.yml" "$INSTANCE_PATH/docker-compose.yml"
ok "docker-compose.yml copie"

# Copier manage.sh
[ -f "$SCRIPT_DIR/manage.sh" ] && cp "$SCRIPT_DIR/manage.sh" "$INSTANCE_PATH/manage.sh" && chmod +x "$INSTANCE_PATH/manage.sh"

# Copier mail-server
if [ -d "$SCRIPT_DIR/mail-server" ]; then
    cp -r "$SCRIPT_DIR/mail-server/"* "$INSTANCE_PATH/mail-server/"
    ok "mail-server copie"
fi

# Copier src_overrides (nettoyer les faux dossiers Docker avant)
if [ -d "$SCRIPT_DIR/src_overrides" ]; then
    if [ -d "$INSTANCE_PATH/src_overrides" ]; then
        find "$INSTANCE_PATH/src_overrides" -type d | while read d; do
            src_file="$SCRIPT_DIR/src_overrides/${d#$INSTANCE_PATH/src_overrides/}"
            [ -f "$src_file" ] && [ -d "$d" ] && rm -rf "$d" || true
        done
    fi
    cp -r "$SCRIPT_DIR/src_overrides/"* "$INSTANCE_PATH/src_overrides/" 2>/dev/null || true
    ok "src_overrides copies"
fi

# Copier les modeles si disponibles
if [ -d "$SCRIPT_DIR/models/huggingface" ] && [ "$(ls -A "$SCRIPT_DIR/models/huggingface" 2>/dev/null)" ]; then
    echo "  Copie des modeles HuggingFace (peut prendre quelques minutes)..."
    cp -r "$SCRIPT_DIR/models/huggingface/"* "$INSTANCE_PATH/models/huggingface/"
    ok "Modeles HuggingFace copies"
fi
if [ -d "$SCRIPT_DIR/models/ollama" ] && [ "$(ls -A "$SCRIPT_DIR/models/ollama" 2>/dev/null)" ]; then
    echo "  Copie des modeles Ollama..."
    cp -r "$SCRIPT_DIR/models/ollama/"* "$INSTANCE_PATH/models/ollama/"
    ok "Modeles Ollama copies"
fi

# Generation .env
step "5/8" "Generation de la configuration..."

SECRET=$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32)

# Generer la cle de chiffrement mail (Fernet)
MAIL_ENC_KEY=""
MAIL_ENC_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null) || MAIL_ENC_KEY=""
if [ -z "$MAIL_ENC_KEY" ]; then
    MAIL_ENC_KEY=$(python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())" 2>/dev/null) || MAIL_ENC_KEY=""
fi
if [ -z "$MAIL_ENC_KEY" ]; then
    MAIL_ENC_KEY=$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n' | head -c 43)=
fi

cat > "$INSTANCE_PATH/.env" << ENVEOF
# Luciole V3 -- Instance: $INSTANCE_NAME
# Genere le: $(date '+%Y-%m-%d %H:%M:%S')
# Profil: $PROFILE

INSTANCE_NAME=$INSTANCE_NAME
COMPOSE_PROFILES=$PROFILE

# Ports reseau
API_PORT=${PORTS[API]}
ADMIN_PORT=${PORTS[ADMIN]}
CHAT_PORT=${PORTS[CHAT]}
FEEDBACK_PORT=${PORTS[FEEDBACK]}
QDRANT_PORT=${PORTS[QDRANT]}
OPENSEARCH_PORT=${PORTS[OPENSEARCH]}
OLLAMA_PORT=${PORTS[OLLAMA]}

# Ports module mail
MAIL_SMTP_PORT=${PORTS[MAIL_SMTP]}
MAIL_IMAP_PORT=${PORTS[MAIL_IMAP]}
MAIL_ADMIN_PORT=${PORTS[MAIL_ADMIN_WEB]}

# Services Docker internes
OLLAMA_URL=http://ollama:11434
QDRANT_URL=http://qdrant:6333
OPENSEARCH_URL=http://opensearch:9200

# Module mail
MAIL_DB_PATH=/app/feedbacks/mail.db
MAIL_ATTACHMENTS_PATH=/app/feedbacks/mail_attachments
MAIL_ENCRYPTION_KEY=$MAIL_ENC_KEY

# Index RAG pour le module mail (nom de l'index ingere pour ce client)
MAIL_DEFAULT_INDEX=$INSTANCE_NAME

# Reranker : device (auto|cpu|cuda)
# - GPU avec VRAM limitee (ex: 3080Ti 12 Go partagee avec bge-m3 + Qwen2.5:14b) : cpu
# - GPU avec VRAM large (H100, A100, GX10 Blackwell, RTX 5090...) : decommenter pour cuda
# Si la variable est absente, c'est settings.yaml (reranker.device) qui s'applique.
RERANKER_DEVICE=cpu
# RERANKER_DEVICE=cuda

# Offline
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
CUDA_VISIBLE_DEVICES=0

# Auth
AUTH_SECRET=$SECRET
ENVEOF

ok "Fichier .env genere"

# Authentification
step "6/8" "Configuration de l'authentification..."

# Generation d'un mot de passe aleatoire (alphanum uniquement pour eviter
# les problemes d'interpolation shell; set +o pipefail evite SIGPIPE avec head)
DEFAULT_PASSWORD=$(set +o pipefail; LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom 2>/dev/null | head -c 20; set -o pipefail) || DEFAULT_PASSWORD=""
if [ -z "$DEFAULT_PASSWORD" ]; then
    DEFAULT_PASSWORD=$(cat /proc/sys/kernel/random/uuid 2>/dev/null | tr -dc 'A-Za-z0-9' | head -c 20 || echo "LucioleAdmin2025")
fi
echo "  Generation d'un mot de passe Admin aleatoire..."

LUCIOLE_IMAGE="luciole-gpu:latest"
[ "$PROFILE" = "cpu" ] && LUCIOLE_IMAGE="luciole-cpu:latest"

# Hash bcrypt : essai 1 - Python3 local (via variable d'env pour eviter l'injection)
BCRYPT_HASH=""
BCRYPT_HASH=$(LUCIOLE_PWD="$DEFAULT_PASSWORD" python3 -c "import bcrypt,os; print(bcrypt.hashpw(os.environ['LUCIOLE_PWD'].encode(), bcrypt.gensalt()).decode())" 2>/dev/null) || BCRYPT_HASH=""

# Hash bcrypt : essai 2 - Container Docker
if [ -z "$BCRYPT_HASH" ] || [[ "$BCRYPT_HASH" != \$2b\$* ]]; then
    echo "  Python3/bcrypt absent sur l'hote -- utilisation du container Docker..."
    BCRYPT_HASH=$(docker run --rm -e LUCIOLE_PWD="$DEFAULT_PASSWORD" "$LUCIOLE_IMAGE" python3 -c "import bcrypt,os; print(bcrypt.hashpw(os.environ['LUCIOLE_PWD'].encode(), bcrypt.gensalt()).decode())" 2>/dev/null | grep '^\$2b\$') || BCRYPT_HASH=""
fi

if [ -z "$BCRYPT_HASH" ] || [[ "$BCRYPT_HASH" != \$2b\$* ]]; then
    echo ""
    echo "ERREUR : Impossible de generer le hash bcrypt."
    echo "  - Verifiez que Docker tourne et que l'image Luciole est disponible"
    echo "  - Ou installez Python3 + bcrypt : pip3 install bcrypt"
    exit 1
fi

cat > "$INSTANCE_PATH/config/auth.yaml" << AUTHEOF
credentials:
  usernames:
    admin:
      email: admin@${INSTANCE_NAME}.local
      name: Administrateur
      password: "$BCRYPT_HASH"
roles:
  admin: [admin_ui, feedback_ui, ragas]
cookie:
  name: luciole_admin
  key: $SECRET
  expiry_days: 1
AUTHEOF
ok "auth.yaml genere"

# Demarrage Ollama
step "7/8" "Demarrage des services..."

cd "$INSTANCE_PATH"

OLLAMA_SVC="ollama"
[ "$PROFILE" = "cpu" ] && OLLAMA_SVC="ollama-cpu"
OLLAMA_CONTAINER="luciole-ollama-$INSTANCE_NAME"

echo "  Demarrage Ollama + Qdrant + OpenSearch..."
docker compose --profile "$PROFILE" up -d "$OLLAMA_SVC" qdrant opensearch

echo "  Attente Ollama (15 s)..."
sleep 15

MODEL="qwen2.5:14b-instruct-q4_K_M"
[ "$PROFILE" = "cpu" ] && MODEL="qwen2.5:7b-instruct-q4_K_M"
MODEL_BASE=$(echo "$MODEL" | cut -d: -f1)

OLLAMA_LIST=$(docker exec "$OLLAMA_CONTAINER" ollama list 2>&1 || true)
if echo "$OLLAMA_LIST" | grep -q "$MODEL_BASE"; then
    ok "Modele $MODEL deja present (offline)"
else
    echo "  Modele $MODEL non detecte localement."
    echo "  Telechargement via internet..."
    docker exec "$OLLAMA_CONTAINER" ollama pull "$MODEL"
fi

# BGE-M3 : conversion safetensors (obligatoire, evite CVE-2025-32434)
echo ""
echo "  Preparation du modele BGE-M3 (conversion safetensors)..."
AGENT_CONTAINER="luciole-agent-$INSTANCE_NAME"

# Lancer l'agent temporairement pour faire la conversion dans le container
docker compose --profile "$PROFILE" up -d agent
echo "  Attente demarrage agent (15 s)..."
sleep 15

if docker exec "$AGENT_CONTAINER" test -f /app/setup_bge_model.py 2>/dev/null; then
    docker exec -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 "$AGENT_CONTAINER" python3 /app/setup_bge_model.py && ok "BGE-M3 converti en safetensors" || warn "Conversion BGE-M3 echouee -- verifiez les logs : docker compose logs agent"
else
    warn "setup_bge_model.py absent du container -- BGE-M3 non converti"
    warn "Lancez manuellement apres installation : docker exec $AGENT_CONTAINER python3 /app/setup_bge_model.py"
fi

# Reranker bge-reranker-v2-m3 : telechargement officiel (snapshot_download)
echo ""
echo "  Telechargement du reranker BGE-Reranker-v2-M3..."
RERANKER_DIR="$INSTANCE_PATH/models/huggingface/hub/models--BAAI--bge-reranker-v2-m3"
if [ -d "$RERANKER_DIR" ] && [ -n "$(ls -A "$RERANKER_DIR/snapshots" 2>/dev/null)" ]; then
    ok "Reranker deja present (cache local detecte)"
else
    docker exec \
        -e HF_HUB_OFFLINE=0 \
        -e TRANSFORMERS_OFFLINE=0 \
        -e HF_HOME=/app/models/huggingface \
        "$AGENT_CONTAINER" \
        python3 -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='BAAI/bge-reranker-v2-m3', cache_dir='/app/models/huggingface/hub')" \
        && ok "Reranker BGE-Reranker-v2-M3 telecharge" \
        || warn "Telechargement reranker echoue -- verifiez la connectivite internet"
fi

# Chown final des modeles (uid 1000 = utilisateur dans le container)
$SUDO chown -R 1000:1000 "$INSTANCE_PATH/models/huggingface" 2>/dev/null || true
ok "Permissions modeles ajustees (1000:1000)"

# Demarrage complet
step "8/8" "Demarrage complet..."

docker compose --profile "$PROFILE" up -d

echo "  Attente stabilisation (30 s)..."
sleep 30

# Resume
echo ""
echo "================================================================"
echo "  INSTALLATION TERMINEE : $(echo "$INSTANCE_NAME" | tr '[:lower:]' '[:upper:]')"
echo "================================================================"
echo ""
echo "  Repertoire : $INSTANCE_PATH"
echo ""
echo "  Services :"
echo "    Chat       : http://localhost:${PORTS[CHAT]}"
echo "    Admin      : http://localhost:${PORTS[ADMIN]}"
echo "    Feedback   : http://localhost:${PORTS[FEEDBACK]}"
echo "    API        : http://localhost:${PORTS[API]}"
echo "    Ollama     : http://localhost:${PORTS[OLLAMA]}"
echo "    Mail admin : http://localhost:${PORTS[MAIL_ADMIN_WEB]} (SMTP:${PORTS[MAIL_SMTP]} IMAP:${PORTS[MAIL_IMAP]})"
echo ""
echo "  Module mail :"
echo "    1. http://localhost:${PORTS[FEEDBACK]}/config -> onglet Mail -> Preset luciole-mail local"
echo "    2. Comptes mail : docker exec luciole-mail-$INSTANCE_NAME /bin/sh /init/init-accounts.sh"
echo ""
# Sauvegarde des credentials dans un fichier dedie
CRED_FILE="$INSTANCE_PATH/INSTANCE_CREDENTIALS.txt"
cat > "$CRED_FILE" << CREDEOF
================================================================
  Identifiants Luciole - Instance : $INSTANCE_NAME
  Genere le : $(date '+%Y-%m-%d %H:%M:%S')
================================================================

  Utilisateur  : admin
  Mot de passe : $DEFAULT_PASSWORD

  Admin UI     : http://localhost:${PORTS[ADMIN]}
  Chat UI      : http://localhost:${PORTS[CHAT]}

  /!\  IMPORTANT :
  - Notez ce mot de passe maintenant
  - SUPPRIMEZ ce fichier apres lecture
  - Pour le changer : Admin UI > Profil > Mot de passe
================================================================
CREDEOF
chmod 600 "$CRED_FILE"

echo "  Identifiants Admin :"
echo "  +-----------------------------------------------+"
echo "  | Utilisateur  : admin                          |"
printf "  | Mot de passe : %-32s|\n" "$DEFAULT_PASSWORD"
echo "  +-----------------------------------------------+"
echo ""
echo "  ATTENTION : Notez ce mot de passe MAINTENANT."
echo "  Il est aussi sauvegarde dans : INSTANCE_CREDENTIALS.txt"
echo "  (a supprimer apres lecture)"
echo ""
echo "  Pour ingerer des documents :"
echo "    1. Deposez vos fichiers dans : $INSTANCE_PATH/data/$INSTANCE_NAME/"
echo "       (regle: 1 instance = 1 metier = 1 index = $INSTANCE_NAME)"
echo "    2. Ouvrez l'Admin UI : http://localhost:${PORTS[ADMIN]}"
echo "    3. Onglet Ingestion > chemin : /app/data/$INSTANCE_NAME"
echo ""
echo "  Gestion : cd $INSTANCE_PATH && ./manage.sh status"
echo ""
