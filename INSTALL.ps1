#Requires -Version 5.1
# ============================================================================
# INSTALLATION LUCIOLE V3 -- Mode Multi-Instance
# ============================================================================
# Cree une instance Luciole isolee pour un projet/metier.
# Chaque instance dispose de ses propres containers, ports et donnees.
#
# Prerequis:
# - Docker Desktop installe et demarre
# - Package Luciole V3 (ce dossier)
# ============================================================================

param(
    [string]$InstanceName = "",
    [ValidateSet("gpu", "cpu")]
    [string]$Profile = "gpu"
)

$ErrorActionPreference = "Stop"
$PackageDir = $PSScriptRoot
$BaseInstallPath = "C:\RAG"

# Ports par defaut
$DefaultPorts = @{
    API       = 8000
    ADMIN     = 8080
    CHAT      = 8501
    FEEDBACK  = 8503
    QDRANT    = 6333
    OPENSEARCH = 9200
    OLLAMA    = 11434
    MAIL_SMTP = 25
    MAIL_IMAP = 143
    MAIL_ADMIN_WEB = 8025
}

# ============================================================================
# FONCTIONS
# ============================================================================

function Write-Step {
    param([string]$Step, [string]$Message)
    Write-Host ""
    Write-Host "[$Step] $Message" -ForegroundColor Cyan
    Write-Host ("-" * 60)
}

function Write-OK {
    param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  [!] $Message" -ForegroundColor Yellow
}

function Test-InstanceName {
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) { return $false }
    return $Name -match '^[a-z0-9][a-z0-9-]*$'
}

$script:AllocatedPorts = @()

function Get-NextAvailablePort {
    param([int]$PreferredPort)

    $usedPorts = @()
    try {
        docker ps --format "{{.Ports}}" 2>$null | ForEach-Object {
            $portMatches = [regex]::Matches($_, '(?:0\.0\.0\.0|:::):(\d+)')
            foreach ($m in $portMatches) { $usedPorts += [int]$m.Groups[1].Value }
        }
    } catch {}
    try {
        netstat -an | Select-String "LISTENING" | ForEach-Object {
            if ($_.Line -match ':(\d+)\s') { $usedPorts += [int]$matches[1] }
        }
    } catch {}
    $usedPorts = ($usedPorts + $script:AllocatedPorts) | Select-Object -Unique

    $port = $PreferredPort
    for ($i = 0; $i -lt 100; $i++) {
        if ($usedPorts -notcontains $port) {
            $script:AllocatedPorts += $port
            return $port
        }
        $port++
    }
    throw "Aucun port disponible depuis $PreferredPort"
}

# ============================================================================
# DEBUT
# ============================================================================

Clear-Host
Write-Host ""
Write-Host "================================================================" -ForegroundColor Magenta
Write-Host "  LUCIOLE V3 -- Installation" -ForegroundColor Magenta
Write-Host "================================================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "Ce script cree une instance Luciole dediee a votre projet."
Write-Host "Les fichiers seront installes dans C:\RAG\luciole-{nom}\"
Write-Host ""

# ============================================================================
# ETAPE 0 : Verification Docker
# ============================================================================

Write-Step "0/8" "Verification de Docker..."
try {
    $dockerVersion = docker --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Docker n'est pas installe" }
    Write-OK "Docker detecte: $dockerVersion"
    docker ps 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker Desktop n'est pas demarre" }
    Write-OK "Docker Desktop est actif"
} catch {
    Write-Host "  [ERREUR] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""; Read-Host "Appuyez sur Entree pour quitter"; exit 1
}

# ============================================================================
# ETAPE 1 : Nom de l'instance
# ============================================================================

Write-Step "1/8" "Configuration de l'instance..."

if ([string]::IsNullOrWhiteSpace($InstanceName)) {
    do {
        Write-Host ""
        $InstanceName = Read-Host "  Nom du projet/metier (ex: chavenay, juridique, rh)"
        $InstanceName = $InstanceName.ToLower().Trim()
        if (-not (Test-InstanceName $InstanceName)) {
            Write-Warn "Nom invalide. Utilisez: lettres minuscules, chiffres, tirets"
            Write-Host "  Exemples: chavenay, juridique, rh, finance-2024" -ForegroundColor Gray
            $InstanceName = ""
        }
    } while ([string]::IsNullOrWhiteSpace($InstanceName))
}

$InstancePath = "$BaseInstallPath\luciole-$InstanceName"

Write-OK "Instance : $InstanceName"
Write-Host "  Repertoire : $InstancePath" -ForegroundColor Gray

# Verifier si existe deja
if (Test-Path $InstancePath) {
    Write-Host ""
    Write-Warn "L'instance '$InstanceName' existe deja dans $InstancePath"
    $confirm = Read-Host "  Voulez-vous la REMPLACER ? (oui/non)"
    if ($confirm -ne "oui") {
        Write-Host "  Installation annulee." -ForegroundColor Yellow
        Read-Host "Appuyez sur Entree pour quitter"; exit 0
    }
    # Arreter les containers existants
    Write-Host "  Arret des containers existants..."
    $ErrorActionPreference = "Continue"
    Push-Location $InstancePath
    docker compose --profile $Profile down 2>&1 | Out-Null
    Pop-Location
    $ErrorActionPreference = "Stop"
}

# ============================================================================
# ETAPE 2 : Detection des ports
# ============================================================================

Write-Step "2/8" "Detection des ports disponibles..."

$script:AllocatedPorts = @()
$Ports = @{}

foreach ($name in @("API", "ADMIN", "CHAT", "FEEDBACK", "QDRANT", "OPENSEARCH", "OLLAMA", "MAIL_SMTP", "MAIL_IMAP", "MAIL_ADMIN_WEB")) {
    $preferred = $DefaultPorts[$name]
    $allocated = Get-NextAvailablePort -PreferredPort $preferred
    $Ports[$name] = $allocated
    if ($allocated -eq $preferred) {
        Write-Host "  $($name.PadRight(12)) : $allocated" -ForegroundColor Gray
    } else {
        Write-Host "  $($name.PadRight(12)) : $allocated (prefere $preferred occupe)" -ForegroundColor Yellow
    }
}

Write-OK "Ports alloues"

# ============================================================================
# ETAPE 3 : Chargement des images Docker
# ============================================================================

Write-Step "3/8" "Chargement des images Docker..."

$dockerImagesDir = Join-Path $PackageDir "docker_images"
if (Test-Path $dockerImagesDir) {
    $tarFiles = Get-ChildItem -Path $dockerImagesDir -Filter "*.tar"
    foreach ($tar in $tarFiles) {
        $sizeMB = [int](($tar.Length / 1MB) + 0.5)
        Write-Host "  Chargement $($tar.Name) ($sizeMB Mo)..."
        docker load -i $tar.FullName
        Write-OK $tar.Name
    }
} else {
    Write-Host "  Pas de dossier docker_images/ (les images doivent etre deja chargees)" -ForegroundColor Gray
}

# Verifier image luciole et adapter automatiquement si necessaire
$lucioleImage = if ($Profile -eq "cpu") { "luciole-cpu:latest" } else { "luciole-gpu:latest" }
$allImages = docker images --format "{{.Repository}}:{{.Tag}}" 2>$null
$imageExists = $allImages | Where-Object { $_ -eq $lucioleImage }
if (-not $imageExists) {
    if ($Profile -eq "cpu" -and ($allImages -contains "luciole-gpu:latest")) {
        docker tag luciole-gpu:latest luciole-cpu:latest
        Write-Warn "luciole-cpu:latest absent -- luciole-gpu tague comme luciole-cpu (tournera en mode CPU)"
    } elseif ($Profile -eq "gpu" -and ($allImages -contains "luciole-cpu:latest")) {
        docker tag luciole-cpu:latest luciole-gpu:latest
        Write-Warn "luciole-gpu:latest absent -- luciole-cpu tague comme luciole-gpu"
    } else {
        Write-Host "  Build de $lucioleImage..."
        $dockerfile = if ($Profile -eq "cpu") { "Dockerfile.cpu" } else { "Dockerfile.gpu" }
        docker build -f (Join-Path $PackageDir $dockerfile) -t $lucioleImage (Join-Path $PackageDir "rag-system")
    }
}
Write-OK "Image $lucioleImage disponible"

# ============================================================================
# ETAPE 4 : Creation de la structure
# ============================================================================

Write-Step "4/8" "Creation de la structure pour '$InstanceName'..."

$directories = @(
    $InstancePath,
    "$InstancePath\data",
    "$InstancePath\data\uploads",
    "$InstancePath\data\processed",
    "$InstancePath\backups",
    "$InstancePath\config",
    "$InstancePath\feedbacks",
    "$InstancePath\evaluation\datasets",
    "$InstancePath\models\huggingface",
    "$InstancePath\models\ollama",
    "$InstancePath\src_overrides\agent",
    "$InstancePath\src_overrides\api",
    "$InstancePath\src_overrides\mail",
    "$InstancePath\src_overrides\ingestion",
    "$InstancePath\src_overrides\watcher",
    "$InstancePath\mail-server\config",
    "$InstancePath\mail-server\init"
)

foreach ($dir in $directories) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}
Write-OK "Repertoires crees"

# Copier la configuration
Write-Host "  Copie de la configuration..."
Copy-Item -Path "$PackageDir\config\*" -Destination "$InstancePath\config" -Recurse -Force
Write-OK "Configuration copiee"

# Copier les modeles si disponibles
if (Test-Path "$PackageDir\models\huggingface") {
    $hfContent = Get-ChildItem -Path "$PackageDir\models\huggingface" -ErrorAction SilentlyContinue
    if ($hfContent -and $hfContent.Count -gt 0) {
        Write-Host "  Copie des modeles HuggingFace (peut prendre quelques minutes)..."
        Copy-Item -Path "$PackageDir\models\huggingface\*" -Destination "$InstancePath\models\huggingface" -Recurse -Force
        Write-OK "Modeles HuggingFace copies"
    }
}
if (Test-Path "$PackageDir\models\ollama") {
    $ollamaContent = Get-ChildItem -Path "$PackageDir\models\ollama" -ErrorAction SilentlyContinue
    if ($ollamaContent -and $ollamaContent.Count -gt 0) {
        Write-Host "  Copie des modeles Ollama..."
        Copy-Item -Path "$PackageDir\models\ollama\*" -Destination "$InstancePath\models\ollama" -Recurse -Force
        Write-OK "Modeles Ollama copies"
    }
}

# Copier src_overrides (fichiers Python modifies montes en bind mount Docker)
# Necessaire pour agent, api, ingestion, watcher et mail
if (Test-Path "$PackageDir\src_overrides") {
    Write-Host "  Copie des src_overrides..."
    cmd /c "xcopy `"$PackageDir\src_overrides`" `"$InstancePath\src_overrides`" /E /H /Y /Q" | Out-Null
    Write-OK "src_overrides copies"
} else {
    Write-Warn "Pas de src_overrides dans le package source"
}

# Copier le module mail depuis rag-system/src/mail vers src_overrides/mail
# (docker-compose monte src_overrides/mail mais le code source est dans rag-system/)
if (Test-Path "$PackageDir\rag-system\src\mail") {
    Write-Host "  Copie du module mail..."
    cmd /c "xcopy `"$PackageDir\rag-system\src\mail`" `"$InstancePath\src_overrides\mail`" /E /H /Y /Q" | Out-Null
    Write-OK "Module mail copie dans src_overrides"
} else {
    Write-Warn "Module mail introuvable dans rag-system/src/mail"
}

# ============================================================================
# ETAPE 5 : Generation du docker-compose et .env
# ============================================================================

Write-Step "5/8" "Generation de la configuration Docker..."

# Copier docker-compose.yml
Copy-Item -Path "$PackageDir\docker-compose.yml" -Destination "$InstancePath\docker-compose.yml" -Force

# Copier mail-server
if (Test-Path "$PackageDir\mail-server") {
    cmd /c "xcopy `"$PackageDir\mail-server`" `"$InstancePath\mail-server`" /E /H /Y /Q" | Out-Null
    Write-OK "mail-server copie"
}

# Generer .env
$chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
$secret = -join ((1..32) | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })

# Generer la cle de chiffrement mail (Fernet) via Python
$mailEncKey = ""
try {
    $mailEncKey = python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>$null
} catch {}
if (-not $mailEncKey) {
    # Fallback CLM-compatible : base64url 44 chars (Fernet-compatible, pas d'appel .NET)
    $b64chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    $mailEncKey = (-join ((1..43) | ForEach-Object { $b64chars[(Get-Random -Maximum 64)] })) + "="
}

$envContent = @"
# Luciole V3 -- Instance: $InstanceName
# Genere le: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
# Profil: $Profile

INSTANCE_NAME=$InstanceName
COMPOSE_PROFILES=$Profile

# Ports reseau
API_PORT=$($Ports['API'])
ADMIN_PORT=$($Ports['ADMIN'])
CHAT_PORT=$($Ports['CHAT'])
FEEDBACK_PORT=$($Ports['FEEDBACK'])
QDRANT_PORT=$($Ports['QDRANT'])
OPENSEARCH_PORT=$($Ports['OPENSEARCH'])
OLLAMA_PORT=$($Ports['OLLAMA'])

# Ports module mail
MAIL_SMTP_PORT=$($Ports['MAIL_SMTP'])
MAIL_IMAP_PORT=$($Ports['MAIL_IMAP'])
MAIL_ADMIN_PORT=$($Ports['MAIL_ADMIN_WEB'])

# Services Docker internes
OLLAMA_URL=http://ollama:11434
QDRANT_URL=http://qdrant:6333
OPENSEARCH_URL=http://opensearch:9200

# Module mail
MAIL_DB_PATH=/app/feedbacks/mail.db
MAIL_ATTACHMENTS_PATH=/app/feedbacks/mail_attachments
MAIL_ENCRYPTION_KEY=$mailEncKey

# Offline
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
CUDA_VISIBLE_DEVICES=0

# Auth
AUTH_SECRET=$secret
"@

Set-Content -Path "$InstancePath\.env" -Value $envContent -Encoding UTF8
Write-OK "docker-compose.yml et .env generes"

# ============================================================================
# ETAPE 6 : Generer auth.yaml avec vrai mot de passe
# ============================================================================

Write-Step "6/8" "Configuration de l'authentification..."

# Generation d'un mot de passe aleatoire de 16 caracteres (CLM-compatible, pas de [System.Web])
$pwChars = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#%^&*-_"
$defaultPassword = -join ((1..16) | ForEach-Object { $pwChars[(Get-Random -Maximum $pwChars.Length)] })
$defaultPassword = $defaultPassword -replace '[`"$\\<>|&]', 'a'

Write-Host "  Generation d'un mot de passe Admin aleatoire..." -ForegroundColor White

# Hash bcrypt : essai 1 - Python local
$bcryptHash = ""
try {
    $bcryptHash = python -c "import bcrypt; print(bcrypt.hashpw(b'$defaultPassword', bcrypt.gensalt()).decode())" 2>$null
} catch {}

# Hash bcrypt : essai 2 - Container Docker (si Python/bcrypt absent sur l'hote)
if (-not ($bcryptHash -and $bcryptHash.StartsWith('$2b$'))) {
    Write-Host "  Python/bcrypt absent sur l'hote -- utilisation du container Docker..." -ForegroundColor Yellow
    $lucioleImage = if ($Profile -eq "cpu") { "luciole-cpu:latest" } else { "luciole-gpu:latest" }
    $ErrorActionPreference = "Continue"
    $bcryptHash = docker run --rm $lucioleImage python -c "import bcrypt; print(bcrypt.hashpw(b'$defaultPassword', bcrypt.gensalt()).decode())" 2>$null
    $ErrorActionPreference = "Stop"
}

if (-not ($bcryptHash -and $bcryptHash.StartsWith('$2b$'))) {
    Write-Host ""
    Write-Host "ERREUR : Impossible de generer le hash bcrypt." -ForegroundColor Red
    Write-Host "  - Verifiez que Docker tourne et que l'image Luciole est disponible" -ForegroundColor Yellow
    Write-Host "  - Ou installez Python + bcrypt : pip install bcrypt" -ForegroundColor Yellow
    exit 1
}

$authYaml = @"
credentials:
  usernames:
    admin:
      email: admin@$InstanceName.local
      name: Administrateur
      password: "$bcryptHash"
roles:
  admin: [admin_ui, feedback_ui, ragas]
cookie:
  name: luciole_admin
  key: $secret
  expiry_days: 1
"@
Set-Content -Path "$InstancePath\config\auth.yaml" -Value $authYaml -Encoding UTF8
Write-OK "auth.yaml genere"

# ============================================================================
# ETAPE 7 : Demarrage Ollama + modele LLM
# ============================================================================

Write-Step "7/8" "Demarrage des services..."

Set-Location $InstancePath

$ollamaService = if ($Profile -eq "cpu") { "ollama-cpu" } else { "ollama" }

Write-Host "  Demarrage Ollama..."
docker compose --profile $Profile up -d $ollamaService qdrant opensearch

Write-Host "  Attente Ollama (15 s)..."
Start-Sleep -Seconds 15

# Detecter le modele LLM
$model = if ($Profile -eq "cpu") { "qwen2.5:7b-instruct-q4_K_M" } else { "qwen2.5:14b-instruct-q4_K_M" }
$ollamaContainer = "luciole-ollama-$InstanceName"

$ErrorActionPreference = "Continue"
$ollamaList = docker exec $ollamaContainer ollama list 2>&1
$ErrorActionPreference = "Stop"

$modelBase = $model.Split(":")[0]
if ($ollamaList -match $modelBase) {
    Write-OK "Modele $model deja present (offline)"
} else {
    Write-Host "  Telechargement du modele $model (internet requis)..." -ForegroundColor Yellow
    docker exec $ollamaContainer ollama pull $model
}

# Modele d'embedding RAGAS (answer_relevancy) -- 274 Mo
$ErrorActionPreference = "Continue"
$ollamaList2 = docker exec $ollamaContainer ollama list 2>&1
$ErrorActionPreference = "Stop"
$ragasEmbed = "nomic-embed-text"
if ($ollamaList2 -match "nomic-embed-text") {
    Write-OK "Modele embedding RAGAS $ragasEmbed deja present"
} else {
    Write-Host "  Telechargement du modele embedding RAGAS : $ragasEmbed..." -ForegroundColor Yellow
    docker exec $ollamaContainer ollama pull $ragasEmbed
}

# Creation du modele RAGAS avec contexte elargi (base sur le LLM principal)
if ($ollamaList2 -match "qwen2.5-14b-ragas") {
    Write-OK "Modele RAGAS qwen2.5-14b-ragas deja present"
} else {
    Write-Host "  Creation du modele qwen2.5-14b-ragas (Modelfile num_ctx=16384)..." -ForegroundColor Yellow
    docker exec $ollamaContainer sh -c "echo 'FROM qwen2.5:14b-instruct-q4_K_M' > /tmp/Modelfile && echo 'PARAMETER num_ctx 16384' >> /tmp/Modelfile && echo 'PARAMETER temperature 0' >> /tmp/Modelfile && ollama create qwen2.5-14b-ragas -f /tmp/Modelfile"
}

# ============================================================================
# ETAPE 8 : Demarrage de tous les services
# ============================================================================

Write-Step "8/8" "Demarrage complet..."

docker compose --profile $Profile up -d

Write-Host "  Attente stabilisation (20 s)..."
Start-Sleep -Seconds 20

# ============================================================================
# RESUME
# ============================================================================

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  INSTALLATION TERMINEE : $($InstanceName.ToUpper())" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Repertoire : $InstancePath" -ForegroundColor White
Write-Host ""
Write-Host "  Services :" -ForegroundColor White
Write-Host "    Chat       : http://localhost:$($Ports['CHAT'])" -ForegroundColor Cyan
Write-Host "    Admin      : http://localhost:$($Ports['ADMIN'])" -ForegroundColor Cyan
Write-Host "    Feedback   : http://localhost:$($Ports['FEEDBACK'])" -ForegroundColor Cyan
Write-Host "    API        : http://localhost:$($Ports['API'])" -ForegroundColor Gray
Write-Host "    Ollama     : http://localhost:$($Ports['OLLAMA'])" -ForegroundColor Gray
Write-Host "    Mail admin : http://localhost:$($Ports['MAIL_ADMIN_WEB']) (SMTP:$($Ports['MAIL_SMTP']) IMAP:$($Ports['MAIL_IMAP']))" -ForegroundColor Gray
Write-Host ""
Write-Host "  Module mail :" -ForegroundColor White
Write-Host "    1. http://localhost:$($Ports['FEEDBACK'])/config -> onglet Mail -> Preset luciole-mail local" -ForegroundColor Gray
Write-Host "    2. Comptes mail : docker exec luciole-mail-$InstanceName /bin/sh /init/init-accounts.sh" -ForegroundColor Gray
Write-Host ""
# Sauvegarde des credentials dans un fichier dedie (a supprimer apres lecture)
$credFile = Join-Path $InstancePath "INSTANCE_CREDENTIALS.txt"
$credContent = @"
================================================================
  Identifiants Luciole - Instance : $InstanceName
  Genere le : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
================================================================

  Utilisateur  : admin
  Mot de passe : $defaultPassword

  Admin UI     : http://localhost:$($Ports['ADMIN'])
  Chat UI      : http://localhost:$($Ports['CHAT'])

  /!\  IMPORTANT :
  - Notez ce mot de passe maintenant
  - SUPPRIMEZ ce fichier apres lecture
  - Pour le changer : Admin UI > Profil > Mot de passe
================================================================
"@
Set-Content -Path $credFile -Value $credContent -Encoding UTF8

Write-Host "  Identifiants Admin :" -ForegroundColor White
Write-Host "  +-----------------------------------------------+" -ForegroundColor Yellow
Write-Host ("  | Utilisateur  : admin{0,-29}|" -f " ") -ForegroundColor Yellow
Write-Host ("  | Mot de passe : {0,-32}|" -f $defaultPassword) -ForegroundColor Yellow
Write-Host "  +-----------------------------------------------+" -ForegroundColor Yellow
Write-Host ""
Write-Host "  ATTENTION : Notez ce mot de passe MAINTENANT." -ForegroundColor Red
Write-Host "  Il est aussi sauvegarde dans : INSTANCE_CREDENTIALS.txt" -ForegroundColor Yellow
Write-Host "  (a supprimer apres lecture)" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Pour ingerer des documents :" -ForegroundColor Yellow
Write-Host "    1. Deposez vos fichiers dans : $InstancePath\data\" -ForegroundColor White
Write-Host "    2. Ouvrez l'Admin UI : http://localhost:$($Ports['ADMIN'])"
Write-Host "    3. Onglet Ingestion > chemin : /app/data"
Write-Host ""
Write-Host "  Gestion : cd $InstancePath" -ForegroundColor Gray
Write-Host "            .\MANAGE.ps1 -Action status" -ForegroundColor Gray
Write-Host ""

# Copier MANAGE.ps1 dans l'instance
if (Test-Path "$PackageDir\MANAGE.ps1") {
    Copy-Item -Path "$PackageDir\MANAGE.ps1" -Destination "$InstancePath\MANAGE.ps1" -Force
}

Set-Location $PackageDir
