#Requires -Version 5.1
# ============================================================================
# INSTALLATION 100% OFFLINE -- Luciole V3
# ============================================================================
# Prerequis : Docker Desktop installe, package prepare par PREPARE_OFFLINE.ps1.
# Ce script demande le nom du projet, cree C:\RAG\luciole-{nom}\ et installe.
# ============================================================================

param(
    [string]$InstanceName = "",
    [ValidateSet("gpu", "cpu")]
    [string]$Profile = "gpu",
    [string]$PackagePath = "",
    [string]$ImagesPath = ""
)

$ErrorActionPreference = "Stop"
$BaseInstallPath = "C:\RAG"

# ============================================================================
# FONCTIONS
# ============================================================================

function Write-Step {
    param([string]$Step, [string]$Msg)
    Write-Host ""
    Write-Host "[$Step] $Msg" -ForegroundColor Cyan
    Write-Host ("-" * 60)
}

function Write-OK {
    param([string]$Msg)
    Write-Host "  [OK] $Msg" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Msg)
    Write-Host "  [!] $Msg" -ForegroundColor Yellow
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
# DETECTER LE PACKAGE
# ============================================================================

if (-not $PackagePath) {
    $scriptDir = $PSScriptRoot
    if (Test-Path "$scriptDir\MANIFEST.json") {
        $PackagePath = $scriptDir
    } elseif (Test-Path "$scriptDir\offline_package\MANIFEST.json") {
        $PackagePath = "$scriptDir\offline_package"
    } else {
        throw "Package offline introuvable. Specifiez -PackagePath ou placez ce script dans le dossier du package."
    }
}
$PackagePath = (Resolve-Path $PackagePath).Path

if (-not (Test-Path "$PackagePath\MANIFEST.json")) {
    throw "MANIFEST.json introuvable dans $PackagePath"
}
$manifest = Get-Content "$PackagePath\MANIFEST.json" -Raw | ConvertFrom-Json

# Resoudre le chemin des images Docker
if (-not $ImagesPath) {
    # Chercher d'abord dans le package courant
    if (Test-Path "$PackagePath\docker_images") {
        $ImagesPath = "$PackagePath\docker_images"
    }
    # Fallback : offline_package frere sur le Bureau
    elseif (Test-Path "$PSScriptRoot\..\offline_package\docker_images") {
        $ImagesPath = (Resolve-Path "$PSScriptRoot\..\offline_package\docker_images").Path
        Write-Host "  [INFO] Images Docker trouvees dans : $ImagesPath" -ForegroundColor DarkYellow
    }
    # Fallback : offline_package sur le meme Bureau
    elseif (Test-Path "$env:USERPROFILE\Desktop\offline_package\docker_images") {
        $ImagesPath = "$env:USERPROFILE\Desktop\offline_package\docker_images"
        Write-Host "  [INFO] Images Docker trouvees dans : $ImagesPath" -ForegroundColor DarkYellow
    }
    else {
        $ImagesPath = "$PackagePath\docker_images"
    }
} else {
    $ImagesPath = (Resolve-Path $ImagesPath).Path
}

# ============================================================================
# DEBUT
# ============================================================================

Clear-Host
Write-Host ""
Write-Host "================================================================" -ForegroundColor Magenta
Write-Host "  LUCIOLE V3 -- Installation OFFLINE" -ForegroundColor Magenta
Write-Host "================================================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "Ce script cree une instance Luciole dediee a votre projet."
Write-Host "Les fichiers seront installes dans C:\RAG\luciole-{nom}\"
Write-Host ""
Write-Host "  Package     : $PackagePath"
Write-Host "  Images      : $ImagesPath"
Write-Host "  Prepare le  : $($manifest.created)"
Write-Host "  LLM         : $($manifest.llm_model)"
Write-Host "  Embedding   : $($manifest.embedding_model)"
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
    Write-Host ""
    Write-Host "  Pour installer Docker Desktop hors-ligne :" -ForegroundColor Yellow
    Write-Host "    1. Telechargez l'installeur Docker Desktop sur une machine avec internet"
    Write-Host "    2. Copiez-le sur cette machine via USB"
    Write-Host "    3. Executez, redemarrez, puis relancez ce script"
    Write-Host ""
    Read-Host "Appuyez sur Entree pour quitter"; exit 1
}

# ============================================================================
# ETAPE 1 : Nom du projet
# ============================================================================

Write-Step "1/8" "Configuration du projet..."

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

Write-Step "3/8" "Chargement des images Docker (depuis .tar)..."

Write-Host "  Recherche des images dans : $ImagesPath" -ForegroundColor Gray
$tarFiles = Get-ChildItem -Path $ImagesPath -Filter "*.tar" -ErrorAction SilentlyContinue
if (-not $tarFiles -or $tarFiles.Count -eq 0) {
    throw "Aucun fichier .tar dans $ImagesPath`n  Conseil : relancez avec -ImagesPath `"C:\chemin\vers\docker_images`""
}

# Map des noms de fichiers tar vers les noms d'images Docker attendus
$tarToImage = @{
    "luciole-gpu.tar" = "luciole-gpu"
    "ollama.tar"      = "ollama/ollama"
    "opensearch.tar"  = "opensearchproject/opensearch"
    "qdrant.tar"      = "qdrant/qdrant"
    "greenmail.tar"   = "greenmail/standalone"
}

$existingImages = docker images --format "{{.Repository}}" 2>$null

foreach ($tar in $tarFiles) {
    $sizeMB = [int](($tar.Length / 1MB) + 0.5)
    $imageName = $tarToImage[$tar.Name]

    if ($imageName -and ($existingImages -contains $imageName)) {
        Write-OK "$($tar.Name) -- image '$imageName' deja presente, chargement ignore"
        continue
    }

    Write-Host "  Chargement $($tar.Name) ($sizeMB Mo)..."
    docker load -i $tar.FullName
    if ($LASTEXITCODE -eq 0) {
        Write-OK $tar.Name
    } else {
        Write-Warn "Erreur chargement $($tar.Name) (code $LASTEXITCODE)"
    }
}

# Adapter automatiquement l'image selon le profil si l'image exacte est absente
$expectedImage = "luciole-${Profile}:latest"
$allImages = docker images --format "{{.Repository}}:{{.Tag}}" 2>$null
if (-not ($allImages -contains $expectedImage)) {
    if ($Profile -eq "cpu" -and ($allImages -contains "luciole-gpu:latest")) {
        docker tag luciole-gpu:latest luciole-cpu:latest
        Write-Warn "luciole-cpu:latest absent -- luciole-gpu tague comme luciole-cpu (tournera en mode CPU)"
    } elseif ($Profile -eq "gpu" -and ($allImages -contains "luciole-cpu:latest")) {
        docker tag luciole-cpu:latest luciole-gpu:latest
        Write-Warn "luciole-gpu:latest absent -- luciole-cpu tague comme luciole-gpu"
    } else {
        Write-Host "  [ERREUR] Aucune image luciole-gpu ou luciole-cpu trouvee dans les .tar" -ForegroundColor Red
        exit 1
    }
}
Write-OK "Image $expectedImage disponible"
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
    "$InstancePath\mail-server\config",
    "$InstancePath\mail-server\init"
)

foreach ($dir in $directories) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}
Write-OK "Repertoires crees"

# Copier la configuration
# Priorite a config\* (version courante). Fallback sur config\config\* pour anciens packages.
Write-Host "  Copie de la configuration..."
if (Test-Path "$PackagePath\config\settings.yaml") {
    Copy-Item -Path "$PackagePath\config\*" -Destination "$InstancePath\config" -Recurse -Force
    Write-OK "Configuration copiee depuis config\*"
} elseif (Test-Path "$PackagePath\config\config\settings.yaml") {
    Copy-Item -Path "$PackagePath\config\config\*" -Destination "$InstancePath\config" -Recurse -Force
    Write-Warn "Configuration copiee depuis config\config\* (package legacy)"
} else {
    throw "Configuration introuvable: ni config\settings.yaml ni config\config\settings.yaml dans le package."
}

# Copier docker-compose.yml
Copy-Item -Path "$PackagePath\docker-compose.yml" -Destination "$InstancePath\docker-compose.yml" -Force
Write-OK "docker-compose.yml copie"

# Copier MANAGE.ps1
if (Test-Path "$PackagePath\MANAGE.ps1") {
    Copy-Item -Path "$PackagePath\MANAGE.ps1" -Destination "$InstancePath\MANAGE.ps1" -Force
}

# Copier src_overrides (fichiers Python modifies montes en bind mount)
if (Test-Path "$PackagePath\src_overrides") {
    Write-Host "  Copie des src_overrides..."
    cmd /c "xcopy `"$PackagePath\src_overrides`" `"$InstancePath\src_overrides`" /E /H /Y /Q"
    Write-OK "src_overrides copies"
} else {
    Write-Host "  [SKIP] Pas de src_overrides dans le package" -ForegroundColor DarkYellow
}

# Copier mail-server (configuration Greenmail + scripts d'initialisation)
if (Test-Path "$PackagePath\mail-server") {
    Write-Host "  Copie de mail-server..."
    cmd /c "xcopy `"$PackagePath\mail-server`" `"$InstancePath\mail-server`" /E /H /Y /Q"
    Write-OK "mail-server copie"
}

# Copier les modeles HuggingFace (xcopy car docker cp cree des fichiers invisibles pour PowerShell)
# Fallback vers offline_package si les modeles sont absents du PackagePath courant
$hfSrc = ""
if (Test-Path "$PackagePath\models\huggingface") {
    $hfCheck = cmd /c "dir /b `"$PackagePath\models\huggingface`" 2>nul" | Where-Object { $_ }
    if ($hfCheck) { $hfSrc = "$PackagePath\models\huggingface" }
}
if (-not $hfSrc) {
    $candidates = @(
        "$PSScriptRoot\..\offline_package\models\huggingface",
        "$env:USERPROFILE\Desktop\offline_package\models\huggingface"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $resolved = (Resolve-Path $c).Path
            $check = cmd /c "dir /b `"$resolved`" 2>nul" | Where-Object { $_ }
            if ($check) { $hfSrc = $resolved; break }
        }
    }
}
if ($hfSrc) {
    Write-Host "  Copie des modeles HuggingFace depuis : $hfSrc" -ForegroundColor Gray
    Write-Host "  (peut prendre quelques minutes selon la taille)..."
    cmd /c "xcopy `"$hfSrc`" `"$InstancePath\models\huggingface`" /E /H /Y /Q"
    Write-OK "Modeles HuggingFace copies"
} else {
    Write-Warn "Modeles HuggingFace introuvables - a copier manuellement dans $InstancePath\models\huggingface"
}

# Copier les modeles Ollama (xcopy pour compatibilite fichiers docker cp)
# Fallback vers offline_package si absents du PackagePath courant
$ollamaSrc = ""
if (Test-Path "$PackagePath\models\ollama") {
    $ollamaCheck = cmd /c "dir /b `"$PackagePath\models\ollama`" 2>nul" | Where-Object { $_ }
    if ($ollamaCheck) { $ollamaSrc = "$PackagePath\models\ollama" }
}
if (-not $ollamaSrc) {
    $candidates = @(
        "$PSScriptRoot\..\offline_package\models\ollama",
        "$env:USERPROFILE\Desktop\offline_package\models\ollama"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $resolved = (Resolve-Path $c).Path
            $check = cmd /c "dir /b `"$resolved`" 2>nul" | Where-Object { $_ }
            if ($check) { $ollamaSrc = $resolved; break }
        }
    }
}
if ($ollamaSrc) {
    Write-Host "  Copie des modeles Ollama depuis : $ollamaSrc" -ForegroundColor Gray
    cmd /c "xcopy `"$ollamaSrc`" `"$InstancePath\models\ollama`" /E /H /Y /Q"
    Write-OK "Modeles Ollama copies"
} else {
    Write-Warn "Modeles Ollama introuvables - a telecharger apres demarrage via : docker exec luciole-ollama-$InstanceName ollama pull qwen2.5:7b"
}

# ============================================================================
# ETAPE 5 : Generation .env
# ============================================================================

Write-Step "5/8" "Generation de la configuration..."

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
# Luciole V3 -- Instance: $InstanceName (OFFLINE)
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
Write-OK "Fichier .env genere"

# ============================================================================
# ETAPE 6 : Authentification
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

# Hash bcrypt : essai 2 - Container Docker (luciole-gpu est deja charge en offline)
if (-not ($bcryptHash -and $bcryptHash.StartsWith('$2b$'))) {
    Write-Host "  Python/bcrypt absent sur l'hote -- utilisation du container Docker..." -ForegroundColor Yellow
    $ErrorActionPreference = "Continue"
    $bcryptHash = docker run --rm luciole-gpu:latest python -c "import bcrypt; print(bcrypt.hashpw(b'$defaultPassword', bcrypt.gensalt()).decode())" 2>$null
    $ErrorActionPreference = "Stop"
}

if (-not ($bcryptHash -and $bcryptHash.StartsWith('$2b$'))) {
    Write-Host ""
    Write-Host "ERREUR : Impossible de generer le hash bcrypt." -ForegroundColor Red
    Write-Host "  - Verifiez que l'image Docker luciole-gpu:latest est bien chargee (etape 3)" -ForegroundColor Yellow
    Write-Host "  - Ou installez Python + bcrypt sur l'hote : pip install bcrypt" -ForegroundColor Yellow
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
$ollamaContainer = "luciole-ollama-$InstanceName"

Write-Host "  Demarrage Ollama + Qdrant + OpenSearch..."
docker compose --profile $Profile up -d $ollamaService qdrant opensearch

Write-Host "  Attente Ollama (20 s)..."
Start-Sleep -Seconds 20

# Detecter le modele LLM
$model = if ($Profile -eq "cpu") { "qwen2.5:7b-instruct-q4_K_M" } else { "qwen2.5:14b-instruct-q4_K_M" }
$modelBase = $model.Split(":")[0]

$ErrorActionPreference = "Continue"
$ollamaList = docker exec $ollamaContainer ollama list 2>&1
$ErrorActionPreference = "Stop"

if ($ollamaList -match $modelBase) {
    Write-OK "Modele $model deja present (offline)"
} else {
    Write-Warn "Modele $model non detecte."
    Write-Host "  Les modeles pre-telecharges sont dans le volume Ollama." -ForegroundColor Gray
    Write-Host "  Si besoin, vous pourrez le telecharger manuellement plus tard." -ForegroundColor Gray
}

# Verifier modele d'embedding RAGAS
if ($ollamaList -match "nomic-embed-text") {
    Write-OK "Modele embedding RAGAS nomic-embed-text deja present"
} else {
    Write-Warn "Modele embedding RAGAS nomic-embed-text non detecte (answer_relevancy sera indisponible)."
}

# Creation du modele RAGAS avec contexte elargi (base sur le LLM principal)
if ($ollamaList -match "qwen2.5-14b-ragas") {
    Write-OK "Modele RAGAS qwen2.5-14b-ragas deja present"
} else {
    Write-Host "  Creation du modele qwen2.5-14b-ragas (Modelfile num_ctx=16384)..." -ForegroundColor Yellow
    docker exec $ollamaContainer sh -c "echo 'FROM qwen2.5:14b-instruct-q4_K_M' > /tmp/Modelfile && echo 'PARAMETER num_ctx 16384' >> /tmp/Modelfile && echo 'PARAMETER temperature 0' >> /tmp/Modelfile && ollama create qwen2.5-14b-ragas -f /tmp/Modelfile"
    if ($LASTEXITCODE -eq 0) {
        Write-OK "Modele qwen2.5-14b-ragas cree avec succes"
    } else {
        Write-Warn "Impossible de creer qwen2.5-14b-ragas (RAGAS evaluations degradees)."
    }
}

# ============================================================================
# ETAPE 8 : Demarrage complet
# ============================================================================

Write-Step "8/8" "Demarrage complet de tous les services..."

docker compose --profile $Profile up -d

Write-Host "  Attente stabilisation (30 s)..."
Start-Sleep -Seconds 30

# Installation de cryptography dans le container feedback (necessaire pour le module mail)
Write-Host ""
Write-Host "  Installation de la dependance mail (cryptography)..." -ForegroundColor Gray
$feedbackContainer = "luciole-feedback-$InstanceName"
$pipWheel = Get-ChildItem "$PackagePath\pip_packages" -Filter "cryptography*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
$cffiWheel = Get-ChildItem "$PackagePath\pip_packages" -Filter "cffi*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pipWheel -and $cffiWheel) {
    docker cp $pipWheel.FullName "${feedbackContainer}:/tmp/cryptography.whl" 2>$null | Out-Null
    docker cp $cffiWheel.FullName "${feedbackContainer}:/tmp/cffi.whl" 2>$null | Out-Null
    $ErrorActionPreference = "Continue"
    docker exec $feedbackContainer pip install /tmp/cffi.whl /tmp/cryptography.whl --quiet --no-warn-script-location 2>&1 | Out-Null
    $ErrorActionPreference = "Stop"
    Write-OK "cryptography installe dans le container feedback"
} else {
    Write-Warn "Wheels cryptography/cffi introuvables dans $PackagePath\pip_packages -- module mail sans chiffrement"
}

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
Write-Host "    1. Ouvrez http://localhost:$($Ports['FEEDBACK'])/config -> onglet Mail" -ForegroundColor Gray
Write-Host "    2. Cliquez 'Preset luciole-mail local' puis Sauvegarder" -ForegroundColor Gray
Write-Host "    3. Initialisez les comptes mail :" -ForegroundColor Gray
Write-Host "       docker exec luciole-mail-$InstanceName /bin/sh /init/init-accounts.sh" -ForegroundColor Gray
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

Set-Location $PackagePath
