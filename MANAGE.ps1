#Requires -Version 5.1
<#
.SYNOPSIS
  Gestion des instances Luciole v3 (Windows). Repertoire de base = repertoire du script.
.PARAMETER Action
  Action a executer.
.PARAMETER Instance
  Nom d'instance (sinon INSTANCE_NAME lu depuis .env du projet).
.PARAMETER Service
  Nom de service compose pour les logs (optionnel).
.PARAMETER Force
  Supprime sans confirmation (remove).
.PARAMETER TargetProfile
  Pour switch-profile uniquement : cpu | balanced | gpu-high | expert
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "list", "start", "stop", "restart", "logs", "status", "remove",
        "backup", "urls", "health", "metrics", "profiles", "switch-profile"
    )]
    [string]$Action,

    [string]$Instance,
    [string]$Service,
    [switch]$Force,
    [string]$TargetProfile
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

function Get-DotEnvPath {
    return (Join-Path $ProjectRoot ".env")
}

function Read-InstanceNameFromEnv {
    $envPath = Get-DotEnvPath
    if (-not (Test-Path -LiteralPath $envPath)) { return $null }
    Get-Content -LiteralPath $envPath | ForEach-Object {
        if ($_ -match '^\s*INSTANCE_NAME=(.*)$') { return $matches[1].Trim() }
    } | Select-Object -First 1
}

function Resolve-InstanceName {
    if ($Instance) { return $Instance }
    $n = Read-InstanceNameFromEnv
    if (-not $n) {
        throw "INSTANCE_NAME introuvable : definissez -Instance ou creez .env (ex. via INSTALL.ps1)."
    }
    return $n
}

function Get-DotEnvValue {
    param([string]$Key)
    $envPath = Get-DotEnvPath
    if (-not (Test-Path -LiteralPath $envPath)) { return $null }
    Get-Content -LiteralPath $envPath | ForEach-Object {
        if ($_ -match "^\s*$Key=(.*)$") { return $matches[1].Trim() }
    } | Select-Object -First 1
}

function Invoke-Compose {
    param([string[]]$ComposeArgs)
    $profile = Get-DotEnvValue -Key "COMPOSE_PROFILES"
    if (-not $profile) { $profile = "gpu" }
    $all = @("--profile", $profile) + $ComposeArgs
    & docker compose @all
}

$instanceName = $null
if ($Action -ne "list" -and $Action -ne "profiles") {
    $instanceName = Resolve-InstanceName
}

switch ($Action) {
    "list" {
        Write-Host "Instances Luciole (conteneurs luciole-agent-*) :" -ForegroundColor Cyan
        $names = docker ps -a --format "{{.Names}}" 2>$null | ForEach-Object {
            if ($_ -match '^luciole-agent-(.+)$') { $matches[1] }
        } | Sort-Object -Unique
        if (-not $names) {
            Write-Host "  (aucune instance detectee)"
        } else {
            $names | ForEach-Object { Write-Host "  - $_" }
        }
    }
    "start" {
        Invoke-Compose @("start")
    }
    "stop" {
        Invoke-Compose @("stop")
    }
    "restart" {
        Invoke-Compose @("restart")
    }
    "logs" {
        if ($Service) {
            Invoke-Compose @("logs", "-f", "--tail", "200", $Service)
        } else {
            Invoke-Compose @("logs", "-f", "--tail", "200")
        }
    }
    "status" {
        Write-Host "=== docker compose ps ===" -ForegroundColor Cyan
        Invoke-Compose @("ps", "-a")
        Write-Host ""
        Write-Host "=== Conteneurs lies a l'instance $instanceName ===" -ForegroundColor Cyan
        docker ps -a --filter "name=luciole-$instanceName" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    }
    "remove" {
        if (-not $Force) {
            $c = Read-Host "Supprimer l'instance (docker compose down -v) ? [o/N]"
            if ($c -notmatch '^[oOyY]') {
                Write-Host "Annule."
                return
            }
        }
        $profile = Get-DotEnvValue -Key "COMPOSE_PROFILES"
        if (-not $profile) { $profile = "gpu" }
        docker compose --profile $profile down -v
    }
    "backup" {
        $ts = Get-Date -Format "yyyyMMdd-HHmmss"
        $dest = Join-Path $ProjectRoot "backups\backup-$ts"
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
        $dirs = @("data", "feedbacks", "config", "evaluation")
        foreach ($d in $dirs) {
            $p = Join-Path $ProjectRoot $d
            if (Test-Path -LiteralPath $p) {
                Copy-Item -Path $p -Destination (Join-Path $dest $d) -Recurse -Force
            }
        }
        Write-Host "Sauvegarde creee : $dest" -ForegroundColor Green
    }
    "urls" {
        $api = Get-DotEnvValue -Key "API_PORT"; if (-not $api) { $api = "8000" }
        $chat = Get-DotEnvValue -Key "CHAT_PORT"; if (-not $chat) { $chat = "8501" }
        $admin = Get-DotEnvValue -Key "ADMIN_PORT"; if (-not $admin) { $admin = "8080" }
        $feedback = Get-DotEnvValue -Key "FEEDBACK_PORT"; if (-not $feedback) { $feedback = "8503" }
        $ollama = Get-DotEnvValue -Key "OLLAMA_PORT"; if (-not $ollama) { $ollama = "11434" }
        Write-Host "Instance : $instanceName"
        Write-Host "  API / health : http://localhost:${api}/api/health"
        Write-Host "  Chat         : http://localhost:${chat}"
        Write-Host "  Admin        : http://localhost:${admin}"
        Write-Host "  Feedback     : http://localhost:${feedback}"
        Write-Host "  Ollama       : http://localhost:${ollama}"
    }
    "health" {
        $api = Get-DotEnvValue -Key "API_PORT"; if (-not $api) { $api = "8000" }
        Write-Host "=== Conteneurs (luciole-*-$instanceName) ===" -ForegroundColor Cyan
        docker ps -a --filter "name=luciole-$instanceName" --format "table {{.Names}}\t{{.Status}}"
        Write-Host ""
        Write-Host "=== GET http://localhost:${api}/api/health ===" -ForegroundColor Cyan
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:${api}/api/health" -UseBasicParsing -TimeoutSec 15
            Write-Host "HTTP $($r.StatusCode)"
            Write-Host $r.Content
        } catch {
            Write-Host "Echec : $_" -ForegroundColor Red
        }
    }
    "metrics" {
        $db = Join-Path $ProjectRoot "feedbacks\ragas.db"
        if (-not (Test-Path -LiteralPath $db)) {
            Write-Host "Aucune base RAGAS ($db). Lancez des evaluations depuis l'UI Feedback." -ForegroundColor Yellow
            return
        }
        $sqlite = Get-Command sqlite3 -ErrorAction SilentlyContinue
        if (-not $sqlite) {
            Write-Host "sqlite3 introuvable dans le PATH. Installez SQLite ou ajoutez sqlite3 au PATH." -ForegroundColor Yellow
            return
        }
        Write-Host "=== RAGAS - 30 derniers jours (toutes collections) ===" -ForegroundColor Cyan
        $sql = @"
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
"@
        & sqlite3 $db $sql
        Write-Host ""
        Write-Host "=== Dernieres entrees (10) ===" -ForegroundColor Cyan
        $sql2 = "SELECT timestamp, index_name, faithfulness, answer_relevancy, context_recall FROM ragas_scores ORDER BY id DESC LIMIT 10;"
        & sqlite3 -header -column $db $sql2
    }
    "profiles" {
        Write-Host "Profils modeles disponibles (switch-profile) :" -ForegroundColor Cyan
        Write-Host "  cpu        - Embedding/rerank legers + qwen2.5 7B (machine CPU / petit GPU)"
        Write-Host "  balanced   - bge-m3 + bge-reranker-v2-m3 + qwen2.5 14B (defaut GPU)"
        Write-Host "  gpu-high   - Meme embedding/rerank, LLM 14B optimise charge"
        Write-Host "  expert     - bge-m3 + reranker + qwen2.5 32B (VRAM elevee)"
        Write-Host ""
        Write-Host "Profils Docker Compose (.env COMPOSE_PROFILES) : gpu | cpu"
    }
    "switch-profile" {
        $tp = $TargetProfile
        if (-not $tp) { $tp = $env:LUCIOLE_TARGET_PROFILE }
        if (-not $tp) {
            Write-Host "Usage : -Action switch-profile -TargetProfile <cpu|balanced|gpu-high|expert>" -ForegroundColor Yellow
            Write-Host "Ou definissez la variable d'environnement LUCIOLE_TARGET_PROFILE."
            exit 1
        }
        $valid = @("cpu", "balanced", "gpu-high", "expert")
        if ($valid -notcontains $tp) {
            throw "Profil inconnu : $tp (attendu : $($valid -join ', '))"
        }
        $settingsPath = Join-Path $ProjectRoot "config\settings.yaml"
        if (-not (Test-Path -LiteralPath $settingsPath)) {
            throw "Fichier introuvable : config/settings.yaml"
        }
        $emb = "BAAI/bge-m3"
        $rer = "BAAI/bge-reranker-v2-m3"
        $llm = "qwen2.5:14b-instruct-q4_K_M"
        $bs = "32"
        switch ($tp) {
            "cpu" {
                $llm = "qwen2.5:7b-instruct-q4_K_M"
                $bs = "8"
            }
            "balanced" {
                $llm = "qwen2.5:14b-instruct-q4_K_M"
                $bs = "32"
            }
            "gpu-high" {
                $llm = "qwen2.5:14b-instruct-q4_K_M"
                $bs = "32"
            }
            "expert" {
                $llm = "qwen2.5:32b-instruct-q4_K_M"
                $bs = "32"
            }
        }
        $c = Get-Content -LiteralPath $settingsPath -Raw
        $c = $c -replace '(embedding:\r?\n  model: ")[^"]+(")', "`$1$emb`$2"
        $c = $c -replace '(embedding:\r?\n  model:[^\r\n]+\r?\n  device:[^\r\n]+\r?\n  batch_size: )\d+', "`$1$bs"
        $c = $c -replace '(reranker:\r?\n  model: ")[^"]+(")', "`$1$rer`$2"
        $c = $c -replace '(llm:\r?\n  provider:[^\r\n]+\r?\n  model: ")[^"]+(")', "`$1$llm`$2"
        Set-Content -LiteralPath $settingsPath -Value $c -Encoding UTF8
        Write-Host "Profil applique : $tp (config/settings.yaml)" -ForegroundColor Green
        Write-Host "Redemarrez les services : .\MANAGE.ps1 -Action restart -Instance $instanceName"
        if ($tp -eq "cpu") {
            Write-Host "Pour execution CPU-only : definissez COMPOSE_PROFILES=cpu dans .env puis relancez les conteneurs Ollama." -ForegroundColor Yellow
        }
    }
}
