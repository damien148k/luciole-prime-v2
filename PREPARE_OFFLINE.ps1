#Requires -Version 5.1
<#
.SYNOPSIS
  Prepare le package offline Luciole v3 sur une machine CONNECTEE a internet.
  Telecharge toutes les dependances, modeles et images Docker.
  Le dossier resultant peut etre copie sur une cle USB pour installation offline.

.PARAMETER Profile
  gpu (defaut) ou cpu -- determine les modeles et images a telecharger.

.PARAMETER OutputDir
  Dossier de sortie du package (defaut: ./offline_package).

.PARAMETER LlmModel
  Modele Ollama a pre-telecharger (defaut: auto selon profil).

.PARAMETER EmbeddingModel
  Modele HuggingFace d'embedding (defaut: BAAI/bge-m3).

.PARAMETER RerankerModel
  Modele HuggingFace de reranking (defaut: BAAI/bge-reranker-v2-m3).

.EXAMPLE
  .\PREPARE_OFFLINE.ps1 -Profile gpu
  .\PREPARE_OFFLINE.ps1 -Profile cpu -OutputDir "D:\luciole_usb"
#>
param(
    [ValidateSet("gpu", "cpu")]
    [string]$Profile = "gpu",

    [string]$OutputDir = ".\offline_package",

    [string]$LlmModel = "",

    [string]$EmbeddingModel = "BAAI/bge-m3",
    [string]$RerankerModel  = "BAAI/bge-reranker-v2-m3"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

# ============================================================================
# Helpers
# ============================================================================

function Write-Step {
    param([string]$Msg)
    Write-Host ""
    Write-Host ">> $Msg" -ForegroundColor Cyan
    Write-Host ("-" * 60)
}

function Assert-Command {
    param([string]$Cmd, [string]$Hint)
    if (-not (Get-Command $Cmd -ErrorAction SilentlyContinue)) {
        throw "Commande introuvable : $Cmd. $Hint"
    }
}

# ============================================================================
# Verifications
# ============================================================================

Write-Host ""
Write-Host "================================================================" -ForegroundColor Yellow
Write-Host "  Luciole v3 -- Preparation du package offline ($Profile)" -ForegroundColor Yellow
Write-Host "================================================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "Ce script doit etre execute sur une machine AVEC acces internet."
Write-Host "Il va telecharger toutes les dependances necessaires."
Write-Host ""

Assert-Command "docker" "Installez Docker Desktop : https://docs.docker.com/desktop/install/windows-install/"
Assert-Command "python" "Installez Python 3.11+ : https://www.python.org/downloads/"

# Auto LLM model
if (-not $LlmModel) {
    $LlmModel = if ($Profile -eq "cpu") {
        "qwen2.5:7b-instruct-q4_K_M"
    } else {
        "qwen2.5:14b-instruct-q4_K_M"
    }
}

Write-Host "Configuration :"
Write-Host "  Profil       : $Profile"
Write-Host "  LLM          : $LlmModel"
Write-Host "  Embedding    : $EmbeddingModel"
Write-Host "  Reranker     : $RerankerModel"
Write-Host "  Sortie       : $OutputDir"
Write-Host ""

# ============================================================================
# 1. Creer la structure de sortie
# ============================================================================

Write-Step "1/7 -- Creation de la structure de dossiers"

$dirs = @(
    "$OutputDir",
    "$OutputDir\docker_images",
    "$OutputDir\models\huggingface",
    "$OutputDir\models\ollama",
    "$OutputDir\rag-system",
    "$OutputDir\config",
    "$OutputDir\evaluation\datasets",
    "$OutputDir\feedbacks"
)
foreach ($d in $dirs) {
    New-Item -ItemType Directory -Path $d -Force | Out-Null
}

# Copier tout le projet source
Write-Host "Copie du code source..."
$itemsToCopy = @(
    "config",
    "rag-system\src",
    "rag-system\setup",
    "rag-system\pics",
    "rag-system\easyocr_models",
    "docker-compose.yml",
    "Dockerfile.gpu",
    "Dockerfile.cpu",
    "Dockerfile.gpu.offline",
    "Dockerfile.cpu.offline",
    ".env.template",
    "src_overrides",
    "mail-server",
    "INSTALL.ps1",
    "install.sh",
    "MANAGE.ps1",
    "manage.sh",
    "INSTALL_OFFLINE.ps1",
    "install_offline.sh"
)
foreach ($item in $itemsToCopy) {
    $src = Join-Path $ProjectRoot $item
    $dst = Join-Path $OutputDir $item
    if (Test-Path $src) {
        if ((Get-Item $src).PSIsContainer) {
            New-Item -ItemType Directory -Path $dst -Force | Out-Null
            Copy-Item -Path "$src\*" -Destination $dst -Recurse -Force
        } else {
            $parentDir = Split-Path $dst -Parent
            New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
            Copy-Item -Path $src -Destination $dst -Force
        }
        Write-Host "  [OK] $item"
    } else {
        Write-Host "  [SKIP] $item (introuvable)" -ForegroundColor DarkYellow
    }
}

Write-Host "Structure creee." -ForegroundColor Green

# ============================================================================
# 2. Telecharger et exporter les images Docker
# ============================================================================

Write-Step "2/7 -- Telechargement des images Docker"

$dockerImages = @(
    @{ Name = "ollama/ollama:latest";                     File = "ollama.tar" },
    @{ Name = "qdrant/qdrant:v1.7.4";                    File = "qdrant.tar" },
    @{ Name = "opensearchproject/opensearch:2.11.0";      File = "opensearch.tar" },
    @{ Name = "greenmail/standalone:latest";              File = "greenmail.tar" }
)

foreach ($img in $dockerImages) {
    $tarPath = Join-Path "$OutputDir\docker_images" $img.File
    if (Test-Path $tarPath) {
        Write-Host "  [CACHE] $($img.Name) -- deja exporte"
        continue
    }
    Write-Host "  Pulling $($img.Name)..."
    docker pull $img.Name
    Write-Host "  Export vers $($img.File)..."
    docker save -o $tarPath $img.Name
    Write-Host "  [OK] $($img.File)" -ForegroundColor Green
}

# ============================================================================
# 3. Build et exporter l'image Luciole custom
# ============================================================================

Write-Step "3/7 -- Build de l'image Luciole ($Profile)"

$dockerfileName = if ($Profile -eq "cpu") { "Dockerfile.cpu" } else { "Dockerfile.gpu" }
$imageName = "luciole-${Profile}:latest"
$tarFile = "luciole-${Profile}.tar"
$tarPath = Join-Path "$OutputDir\docker_images" $tarFile

Write-Host "  Build $imageName depuis $dockerfileName..."
docker build -f $dockerfileName -t $imageName .\rag-system\
Write-Host "  Export vers $tarFile..."
docker save -o $tarPath $imageName
Write-Host "  [OK] $tarFile" -ForegroundColor Green

# ============================================================================
# 4. Telecharger le modele Ollama
# ============================================================================

Write-Step "4/7 -- Telechargement du modele Ollama : $LlmModel"

$ollamaModelDir = Join-Path $OutputDir "models\ollama"

# Demarrer un container Ollama temporaire pour pull le modele
$containerName = "luciole-prepare-ollama"

# Nettoyer si existe (ignorer erreur si absent)
$ErrorActionPreference = "Continue"
docker rm -f $containerName 2>&1 | Out-Null
$ErrorActionPreference = "Stop"

Write-Host "  Demarrage container Ollama temporaire..."
docker run -d --name $containerName -v "${ollamaModelDir}:/root/.ollama" ollama/ollama:latest
Start-Sleep -Seconds 10

Write-Host "  Telechargement de $LlmModel (peut prendre plusieurs minutes)..."
docker exec $containerName ollama pull $LlmModel

# Si profil GPU, telecharger aussi le modele RAGAS (plus leger)
$ragasModel = "qwen2.5:7b"
if ($LlmModel -ne $ragasModel) {
    Write-Host "  Telechargement du modele RAGAS : $ragasModel..."
    docker exec $containerName ollama pull $ragasModel
}

Write-Host "  Nettoyage container temporaire..."
$ErrorActionPreference = "Continue"
docker rm -f $containerName 2>&1 | Out-Null
$ErrorActionPreference = "Stop"

Write-Host "  [OK] Modele(s) Ollama telecharge(s)" -ForegroundColor Green

# ============================================================================
# 5. Telecharger les modeles HuggingFace (embedding + reranker)
# ============================================================================

Write-Step "5/7 -- Telechargement des modeles HuggingFace"

$hfCacheDir = Join-Path $OutputDir "models\huggingface"

# Strategie : telecharger dans un container Docker pour eviter les symlinks
# HuggingFace utilise des symlinks (blobs -> snapshots) incompatibles avec Windows.
# On telecharge dans un container Linux, on resout les symlinks avec cp -rL,
# puis on copie les fichiers reels vers Windows.

$dlContainerName = "luciole-hf-download"
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"

docker rm -f $dlContainerName 2>&1 | Out-Null

Write-Host "  Demarrage d'un container pour telecharger les modeles..."
docker run -d --name $dlContainerName `
    -v "${hfCacheDir}:/output" `
    python:3.11-slim `
    sleep 3600 2>&1 | ForEach-Object { Write-Host "  $_" }

Write-Host "  Installation de sentence-transformers dans le container..."
docker exec $dlContainerName pip install --root-user-action=ignore sentence-transformers 2>&1 | ForEach-Object {
    if ($_ -match "Successfully installed|Requirement already") { Write-Host "  $_" -ForegroundColor Green }
}

# Creer le script Python en fichier temporaire (evite les problemes heredoc/CRLF)
$pyTmpFile = Join-Path $env:TEMP "luciole_dl_models.py"
$pyContent = @(
    "import os"
    "os.environ['HF_HOME'] = '/tmp/hf_cache'"
    "os.environ['SENTENCE_TRANSFORMERS_HOME'] = '/tmp/hf_cache'"
    ""
    "print('[1/2] Telechargement embedding : $EmbeddingModel')"
    "from sentence_transformers import SentenceTransformer"
    "model_emb = SentenceTransformer('$EmbeddingModel', cache_folder='/tmp/hf_cache')"
    "test = model_emb.encode(['test de verification'])"
    "print(f'  OK -- dimension : {len(test[0])}')"
    ""
    "print('[2/2] Telechargement reranker : $RerankerModel')"
    "from sentence_transformers import CrossEncoder"
    "model_rr = CrossEncoder('$RerankerModel')"
    "score = model_rr.predict([('query test', 'document test')])"
    "print(f'  OK -- score test : {score}')"
    ""
    "print('Tous les modeles HuggingFace sont telecharges.')"
) -join "`n"
[System.IO.File]::WriteAllText($pyTmpFile, $pyContent, [System.Text.UTF8Encoding]::new($false))

docker cp $pyTmpFile "${dlContainerName}:/tmp/dl_models.py" 2>&1 | Out-Null
Remove-Item $pyTmpFile -ErrorAction SilentlyContinue

Write-Host "  Telechargement des modeles (cela peut prendre plusieurs minutes)..."
docker exec $dlContainerName python /tmp/dl_models.py 2>&1 | ForEach-Object { Write-Host "  $_" }

# Resolution des symlinks : une seule ligne bash pour eviter les \r Windows
Write-Host "  Resolution des symlinks HuggingFace (cp -rL)..."
docker exec $dlContainerName bash -c "cp -rL /tmp/hf_cache /tmp/hf_resolved && rm -rf /tmp/hf_resolved/models--*/blobs /tmp/hf_resolved/models--*/.no_exist /tmp/hf_resolved/.locks /tmp/hf_resolved/xet && cp -r /tmp/hf_resolved/* /output/" 2>&1 | ForEach-Object { Write-Host "  $_" }

Write-Host "  Nettoyage du container temporaire..."
docker rm -f $dlContainerName 2>&1 | Out-Null

$ErrorActionPreference = $prevEAP

Write-Host "  [OK] Modeles HuggingFace telecharges (symlinks resolus)" -ForegroundColor Green

# ============================================================================
# 6. Telecharger les packages pip (roues offline)
# ============================================================================

Write-Step "6/7 -- Telechargement des packages pip (wheels offline)"

$pipDir = Join-Path $OutputDir "pip_packages"
New-Item -ItemType Directory -Path $pipDir -Force | Out-Null

$reqFile = if ($Profile -eq "cpu") {
    "rag-system\setup\requirements-linux-cpu.txt"
} else {
    "rag-system\setup\requirements-linux-gpu.txt"
}

Write-Host "  Telechargement des wheels depuis $reqFile..."
Write-Host "  (plateforme cible : linux, python 3.11)"
$ErrorActionPreference = "Continue"
pip download -r $reqFile -d $pipDir --platform manylinux2014_x86_64 --python-version 3.11 --only-binary=:all: 2>&1 | ForEach-Object { if ($_ -notmatch "^(WARNING|ERROR)") { Write-Host "  $_" } }

# Torch separement (gros fichiers)
if ($Profile -eq "cpu") {
    Write-Host "  Telechargement PyTorch CPU..."
    pip download torch torchvision torchaudio -d $pipDir --index-url https://download.pytorch.org/whl/cpu --platform manylinux2014_x86_64 --python-version 3.11 --only-binary=:all: 2>&1 | Out-Null
} else {
    Write-Host "  Telechargement PyTorch CUDA 12.1..."
    pip download torch torchvision torchaudio -d $pipDir --index-url https://download.pytorch.org/whl/cu121 --platform manylinux2014_x86_64 --python-version 3.11 --only-binary=:all: 2>&1 | Out-Null
}
$ErrorActionPreference = "Stop"

$wheelCount = (Get-ChildItem -Path $pipDir -Filter "*.whl" | Measure-Object).Count
Write-Host "  [OK] $wheelCount wheels telecharges" -ForegroundColor Green

# Copier le requirements dans pip_packages (necessaire pour Dockerfile.*.offline)
Copy-Item -Path $reqFile -Destination $pipDir -Force
Write-Host "  [OK] $reqFile copie dans pip_packages" -ForegroundColor Green

# Telecharger les wheels du module mail (cryptography + cffi) pour installation offline
# dans le container feedback sans acces internet
Write-Host "  Telechargement wheels module mail (cryptography, cffi)..."
$ErrorActionPreference = "Continue"
pip download cryptography==42.0.8 cffi==1.16.0 `
    --dest $pipDir `
    --platform manylinux2014_x86_64 `
    --python-version 311 `
    --only-binary=:all: `
    --no-deps `
    --quiet 2>&1 | Out-Null
$ErrorActionPreference = "Stop"
$mailWheels = (Get-ChildItem -Path $pipDir -Filter "cryptography*.whl" | Measure-Object).Count
if ($mailWheels -gt 0) {
    Write-Host "  [OK] Wheels cryptography/cffi telecharges pour le module mail" -ForegroundColor Green
} else {
    Write-Host "  [!] Wheels cryptography/cffi non disponibles (sera installe via pip au demarrage)" -ForegroundColor Yellow
}

# ============================================================================
# 7. Generer le manifeste et resume
# ============================================================================

Write-Step "7/7 -- Generation du manifeste"

$tars = Get-ChildItem -Path "$OutputDir\docker_images" -Filter "*.tar"
$totalSizeGB = [int]((($tars | Measure-Object -Property Length -Sum).Sum / 1GB) * 100 + 0.5) / 100

$manifest = @{
    version         = "3.0"
    profile         = $Profile
    created         = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    llm_model       = $LlmModel
    embedding_model = $EmbeddingModel
    reranker_model  = $RerankerModel
    docker_images   = ($tars | ForEach-Object { @{ name = $_.Name; size_mb = [int](($_.Length / 1MB) * 10 + 0.5) / 10 } })
    pip_wheels      = $wheelCount
}
$manifest | ConvertTo-Json -Depth 3 | Set-Content -Path "$OutputDir\MANIFEST.json" -Encoding UTF8

# Taille totale du package
$totalSize = 0
Get-ChildItem -Path $OutputDir -Recurse -File | ForEach-Object { $totalSize += $_.Length }
$totalGB = [int](($totalSize / 1GB) * 100 + 0.5) / 100

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  Package offline pret !" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Dossier   : $((Resolve-Path $OutputDir).Path)"
Write-Host "  Taille    : $totalGB Go"
Write-Host "  Profil    : $Profile"
Write-Host "  LLM       : $LlmModel"
Write-Host "  Embedding : $EmbeddingModel"
Write-Host "  Reranker  : $RerankerModel"
Write-Host ""
Write-Host "Prochaine etape :" -ForegroundColor Yellow
Write-Host "  1. Copiez le dossier '$OutputDir' sur une cle USB ou partage reseau"
Write-Host "  2. Sur la machine cible (offline), executez :"
Write-Host "       .\INSTALL_OFFLINE.ps1 -Profile $Profile" -ForegroundColor White
Write-Host ""
