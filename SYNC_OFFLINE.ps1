#Requires -Version 5.1
<#
.SYNOPSIS
  Synchronise le code source de luciole-prime vers le dossier offline_package.
  NE touche PAS aux binaires lourds (docker_images/, models/) — uniquement le code.

  A lancer apres chaque modification validee dans luciole-prime.

.PARAMETER OfflineDir
  Chemin vers le dossier offline_package (defaut: ..\offline_package ou Bureau\offline_package).

.EXAMPLE
  .\SYNC_OFFLINE.ps1
  .\SYNC_OFFLINE.ps1 -OfflineDir "D:\deploy\offline_package"
#>
param(
    [string]$OfflineDir = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

# ============================================================================
# Detecter le dossier offline_package
# ============================================================================

if (-not $OfflineDir) {
    $candidates = @(
        (Join-Path (Split-Path $ProjectRoot) "offline_package"),
        (Join-Path "$env:USERPROFILE\Desktop" "offline_package")
    )
    foreach ($c in $candidates) {
        if (Test-Path "$c\MANIFEST.json") {
            $OfflineDir = $c
            break
        }
    }
    if (-not $OfflineDir) {
        throw "offline_package introuvable. Specifiez -OfflineDir ou placez-le sur le Bureau."
    }
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  Synchronisation luciole-prime -> offline_package" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Source  : $ProjectRoot"
Write-Host "  Cible   : $OfflineDir"
Write-Host ""

# ============================================================================
# Fichiers et dossiers a synchroniser (CODE uniquement, pas les binaires)
# ============================================================================

$itemsToSync = @(
    "config",
    "rag-system\src",
    "rag-system\setup",
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
    "install_offline.sh",
    "PREPARE_OFFLINE.ps1",
    "GUIDE_INSTALLATION.md",
    "VERIFICATION_POST_DEPLOIEMENT.md",
    "pipeline.py",
    "evaluation"
)

# ── Sync additionnel : src_overrides/mail toujours aligné sur rag-system/src/mail ──
# src_overrides/mail n'existe pas dans le dépôt Git (c'est un dossier de déploiement)
# donc il faut le peupler explicitement depuis rag-system/src/mail qui est la source de vérité
$mailSrc = Join-Path $ProjectRoot "rag-system\src\mail"
$mailDst = Join-Path $OfflineDir "src_overrides\mail"
if (Test-Path $mailSrc) {
    New-Item -ItemType Directory -Path $mailDst -Force | Out-Null
    cmd /c "xcopy `"$mailSrc`" `"$mailDst`" /E /H /Y /Q" | Out-Null
    Write-Host "  [OK] src_overrides\mail (sync depuis rag-system\src\mail)" -ForegroundColor Green
    $synced++
}

$synced = 0
$skipped = 0

foreach ($item in $itemsToSync) {
    $src = Join-Path $ProjectRoot $item
    $dst = Join-Path $OfflineDir $item

    if (-not (Test-Path $src)) {
        $skipped++
        continue
    }

    if ((Get-Item $src).PSIsContainer) {
        New-Item -ItemType Directory -Path $dst -Force | Out-Null
        cmd /c "xcopy `"$src`" `"$dst`" /E /H /Y /Q" | Out-Null
    } else {
        $parentDir = Split-Path $dst -Parent
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
        Copy-Item -Path $src -Destination $dst -Force
    }
    Write-Host "  [OK] $item" -ForegroundColor Green
    $synced++
}

# ============================================================================
# Mettre a jour le MANIFEST.json avec la date de synchro
# ============================================================================

$manifestPath = Join-Path $OfflineDir "MANIFEST.json"
if (Test-Path $manifestPath) {
    $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
    $manifest | Add-Member -NotePropertyName "last_sync" -NotePropertyValue (Get-Date -Format "yyyy-MM-dd HH:mm:ss") -Force
    $manifest | Add-Member -NotePropertyName "synced_from" -NotePropertyValue $ProjectRoot -Force
    $manifest | ConvertTo-Json -Depth 5 | Set-Content $manifestPath -Encoding UTF8
}

# ============================================================================
# Verification des binaires obligatoires pour 100% offline
# ============================================================================

Write-Host ""
Write-Host "  Verification du package offline (binaires)..." -ForegroundColor Cyan

$errors = @()
$warnings = @()

# Images Docker
$requiredTars = @("luciole-gpu.tar", "ollama.tar", "qdrant.tar", "opensearch.tar", "greenmail.tar")
foreach ($tar in $requiredTars) {
    if (-not (Test-Path "$OfflineDir\docker_images\$tar")) {
        $errors += "Image Docker MANQUANTE : docker_images\$tar"
    } else {
        Write-Host "  [OK] docker_images\$tar" -ForegroundColor Green
    }
}

# Modeles HuggingFace
$requiredHF = @("models--BAAI--bge-m3", "models--BAAI--bge-reranker-v2-m3")
foreach ($model in $requiredHF) {
    $found = (Test-Path "$OfflineDir\models\huggingface\$model") -or
             (Test-Path "$OfflineDir\models\huggingface\hub\$model")
    if (-not $found) {
        $errors += "Modele HuggingFace MANQUANT : models\huggingface\$model"
    } else {
        Write-Host "  [OK] huggingface\$model" -ForegroundColor Green
    }
}

# Modeles Ollama (au moins un blob)
$ollamaBlobs = Get-ChildItem "$OfflineDir\models\ollama" -Recurse -ErrorAction SilentlyContinue |
               Where-Object { -not $_.PSIsContainer }
if (-not $ollamaBlobs -or $ollamaBlobs.Count -eq 0) {
    $errors += "Modeles Ollama MANQUANTS : models\ollama\ est vide"
} else {
    Write-Host "  [OK] models\ollama ($($ollamaBlobs.Count) fichiers)" -ForegroundColor Green
}

# ============================================================================
# Resume
# ============================================================================

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  Synchronisation terminee" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Fichiers code synchronises : $synced"
if ($skipped -gt 0) {
    Write-Host "  Fichiers absents (skip)    : $skipped" -ForegroundColor DarkYellow
}
Write-Host ""

if ($errors.Count -gt 0) {
    Write-Host "================================================================" -ForegroundColor Red
    Write-Host "  ATTENTION : PACKAGE INCOMPLET - PAS 100% OFFLINE !" -ForegroundColor Red
    Write-Host "================================================================" -ForegroundColor Red
    foreach ($e in $errors) {
        Write-Host "  [MANQUANT] $e" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  Pour completer le package, executez sur une machine connectee :" -ForegroundColor Yellow
    Write-Host "    .\PREPARE_OFFLINE.ps1 -Profile gpu -OutputDir `"$OfflineDir`"" -ForegroundColor White
} else {
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host "  Package 100% offline VALIDE" -ForegroundColor Green
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host "  Code    : a jour"
    Write-Host "  Docker  : OK"
    Write-Host "  Modeles : OK"
}
Write-Host ""
