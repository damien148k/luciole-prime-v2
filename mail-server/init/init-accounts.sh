#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# init-accounts.sh — Création des comptes de test Luciole Mail
#
# À exécuter UNE FOIS après le premier démarrage de luciole-mail,
# une fois le container marqué "healthy".
#
# Usage (depuis l'hôte) :
#   docker exec luciole-mail-<INSTANCE_NAME> /bin/sh /init/init-accounts.sh
#
# Ou depuis le dossier du projet :
#   docker exec luciole-mail-watcher /bin/sh /init/init-accounts.sh
# ─────────────────────────────────────────────────────────────────────────────

set -e

ADMIN_USER="admin"
ADMIN_PASS="admin_luciole_2024"
API="http://localhost:8080/api"

echo "=== Initialisation des comptes Luciole Mail ==="

# Attendre que l'API soit disponible
echo "Attente de l'API d'administration..."
for i in $(seq 1 20); do
    if wget -q -O- --user "$ADMIN_USER" --password "$ADMIN_PASS" \
       "$API/principal" > /dev/null 2>&1; then
        echo "API disponible."
        break
    fi
    echo "  Tentative $i/20 — attente 3s..."
    sleep 3
done

create_account() {
    local name="$1"
    local password="$2"
    local description="$3"

    echo "Création du compte : $name"
    wget -q -O- \
        --user "$ADMIN_USER" --password "$ADMIN_PASS" \
        --method POST \
        --header "Content-Type: application/json" \
        --body-data "{
            \"type\": \"individual\",
            \"name\": \"$name\",
            \"secrets\": [\"$password\"],
            \"description\": \"$description\",
            \"emails\": [\"$name\"]
        }" \
        "$API/principal" > /dev/null 2>&1 && echo "  ✅ $name créé" || echo "  ⚠️  $name existe peut-être déjà"
}

# Créer les comptes de test
create_account "luciole@local.lan"  "luciole2024"  "Boîte Luciole - lue par le module mail"
create_account "testeur@local.lan"  "testeur2024"  "Boîte testeur - client Thunderbird"
create_account "demo@local.lan"     "demo2024"     "Boîte démo - usage optionnel"

echo ""
echo "=== Comptes créés ==="
echo ""
echo "  luciole@local.lan  /  luciole2024  (boîte principale de Luciole)"
echo "  testeur@local.lan  /  testeur2024  (boîte du testeur humain)"
echo "  demo@local.lan     /  demo2024     (optionnel, pour démos)"
echo ""
echo "Paramètres à saisir dans l'UI Luciole (/config → onglet Mail) :"
echo "  IMAP host = mail     IMAP port = 143   SSL/TLS = Non"
echo "  Utilisateur = luciole@local.lan   Mot de passe = luciole2024"
echo "  SMTP host = mail     SMTP port = 25    TLS = Non"
echo ""
echo "Interface admin : http://localhost:8025"
echo "  Login : admin / admin_luciole_2024"
