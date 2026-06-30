"""
Agent Luciole API - Analyse Avancée de Documents
FastAPI service pour l'agent d'analyse intelligente
"""

import os
import re
import time
import json
import sqlite3
from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from loguru import logger
import yaml
import httpx

# Configuration logging
logger.add(
    "logs/agent.log",
    rotation="10 MB",
    retention="7 days",
    level="DEBUG"
)

app = FastAPI(
    title="Luciole Agent - Analyse Avancée",
    description="Agent intelligent d'analyse documentaire",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# Pydantic Models
# ============================================================================

class ScopeModel(BaseModel):
    paths: List[str] = Field(default_factory=list, description="Chemins à analyser")
    file_types: List[str] = Field(default_factory=list, description="Types de fichiers")
    date_range: Optional[Dict] = None


class OptionsModel(BaseModel):
    detail_level: str = Field(default="standard", description="quick|standard|deep")
    max_items: int = Field(default=20, description="Nombre max de résultats")
    include_sources: bool = Field(default=True)


class AnalyzeRequest(BaseModel):
    query: str = Field(..., description="Requête utilisateur")
    mode: str = Field(default="auto", description="auto|files|folder|cross|chat")
    scope: Optional[ScopeModel] = None
    options: Optional[OptionsModel] = None
    index_name: Optional[str] = Field(default=None, description="Nom de l'index à interroger")


class SummarizeFileRequest(BaseModel):
    file_path: str = Field(..., description="Chemin du fichier à résumer")
    options: Optional[OptionsModel] = None


class CrossAnalyzeRequest(BaseModel):
    query: str = Field(..., description="Question de comparaison")
    scope: ScopeModel
    options: Optional[OptionsModel] = None


class ChatMessage(BaseModel):
    role: str = Field(..., description="Role: 'user' ou 'assistant'")
    content: str = Field(..., description="Contenu du message")


class QueryRequest(BaseModel):
    query: str = Field(..., description="Question utilisateur")
    top_k: int = Field(default=20, description="Nombre de sources")
    index_name: Optional[str] = Field(default=None, description="Nom de l'index à interroger (Qdrant collection)")
    custom_prompt: Optional[str] = Field(default=None, description="Prompt personnalisé optionnel")
    enable_rewriting: bool = Field(default=True, description="Activer le query rewriting")
    deep_search: bool = Field(default=False, description="Recherche approfondie (double recherche avec/sans historique)")
    history: List[ChatMessage] = Field(default=[], description="Historique de conversation")


# ============================================================================
# Global instances (lazy loading)
# ============================================================================

_analyzers = {}            # Cache par index_name
_analyzers_ts = {}         # Timestamps de création (TTL)
_ANALYZER_TTL = int(os.environ.get("ANALYZER_CACHE_TTL", "300"))  # 5 min par défaut
_embedder = None
_llm_generator = None
_reranker = None
_query_rewriter = None
_config = None

# ============================================================================
# Index unique par instance (règle: 1 instance = 1 métier = 1 index)
# Si INSTANCE_NAME est défini dans l'environnement, l'agent ignore tout
# paramètre `index_name` reçu et force systématiquement cette valeur.
# ============================================================================
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "").strip().lower() or None
if INSTANCE_NAME:
    logger.info(f"🎯 INSTANCE_NAME défini : '{INSTANCE_NAME}' — index forcé pour toutes les requêtes")


def _resolve_index_name(requested: Optional[str]) -> str:
    """
    Résout l'index_name effectif selon la règle '1 instance = 1 index'.

    - Si INSTANCE_NAME est défini (env var), on l'utilise toujours (le paramètre
      reçu de l'UI ou du client API est ignoré).
    - Sinon, on garde le comportement historique (param reçu, ou fallback config).
    """
    if INSTANCE_NAME:
        if requested and requested != INSTANCE_NAME:
            logger.debug(
                f"index_name '{requested}' reçu mais ignoré — "
                f"force '{INSTANCE_NAME}' (INSTANCE_NAME)"
            )
        return INSTANCE_NAME
    if requested:
        return requested
    return _get_config()["qdrant"]["collection_name"]


def _get_config():
    """Charge la config une seule fois avec override par variables d'environnement"""
    global _config
    if _config is None:
        config_path = os.environ.get("CONFIG_PATH", "config/settings.yaml")
        with open(config_path, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f)
        
        # Override avec variables d'environnement pour Docker
        qdrant_url = os.environ.get("QDRANT_URL")
        if qdrant_url:
            # Parse URL: http://luciole-qdrant:6333 -> host=luciole-qdrant, port=6333
            from urllib.parse import urlparse
            parsed = urlparse(qdrant_url)
            _config["qdrant"]["host"] = parsed.hostname or "localhost"
            _config["qdrant"]["port"] = parsed.port or 6333
            logger.info(f"Qdrant config from env: {_config['qdrant']['host']}:{_config['qdrant']['port']}")
        
        opensearch_url = os.environ.get("OPENSEARCH_URL")
        if opensearch_url:
            from urllib.parse import urlparse
            parsed = urlparse(opensearch_url)
            _config["opensearch"]["host"] = parsed.hostname or "localhost"
            _config["opensearch"]["port"] = parsed.port or 9200
            logger.info(f"OpenSearch config from env: {_config['opensearch']['host']}:{_config['opensearch']['port']}")
        
        ollama_host = os.environ.get("OLLAMA_HOST")
        if ollama_host:
            _config["llm"]["base_url"] = ollama_host
            logger.info(f"Ollama config from env: {ollama_host}")
    
    return _config


def _get_embedder():
    """Charge l'embedder une seule fois"""
    global _embedder
    if _embedder is None:
        config = _get_config()
        from src.ingestion.embedder import Embedder
        _embedder = Embedder(
            model_name=config["embedding"]["model"],
            device=config["embedding"]["device"]
        )
    return _embedder


def _get_reranker():
    """Charge le reranker une seule fois"""
    global _reranker
    if _reranker is None:
        config = _get_config()
        from src.retrieval.reranker import Reranker
        try:
            _reranker = Reranker(
                model_name=config["reranker"]["model"],
                device=config["reranker"]["device"],
                top_n=config["retrieval"].get("rerank_top_n", 10)
            )
        except Exception as e:
            logger.warning(f"Reranker not available: {e}")
    return _reranker


def _get_llm_generator():
    """Charge le LLM generator une seule fois"""
    global _llm_generator
    if _llm_generator is None:
        config_path = os.environ.get("CONFIG_PATH", "config/settings.yaml")
        from src.generation.llm import LLMGenerator
        _llm_generator = LLMGenerator(config_path=config_path)
    return _llm_generator


def _get_query_rewriter():
    """Charge le query rewriter une seule fois"""
    global _query_rewriter
    if _query_rewriter is None:
        from src.retrieval.query_rewriter import QueryRewriter
        config = _get_config()
        
        # Option: activer le LLM fallback si configuré
        enable_llm = config.get("retrieval", {}).get("query_rewrite_llm_fallback", False)
        
        _query_rewriter = QueryRewriter(
            llm_client=_get_llm_generator() if enable_llm else None,
            enable_llm_fallback=enable_llm
        )
        logger.info(f"QueryRewriter initialisé (LLM fallback: {enable_llm})")
    return _query_rewriter


def get_analyzer(index_name: str = None):
    """
    Lazy loading de l'analyseur avec support multi-index
    
    Args:
        index_name: Nom de l'index/collection à utiliser (optionnel)
                   Si None, utilise l'index par défaut de la config
    """
    global _analyzers
    
    config = _get_config()
    
    # Déterminer l'index à utiliser (force INSTANCE_NAME si défini)
    index_name = _resolve_index_name(index_name)
    
    # Vérifier le cache
    if index_name in _analyzers:
        logger.debug(f"Using cached analyzer for index: {index_name}")
        # Vérifier le TTL (invalidation automatique)
        ts = _analyzers_ts.get(index_name, 0)
        if time.time() - ts < _ANALYZER_TTL:
            return _analyzers[index_name]
        else:
            logger.info(f"Cache analyzer expiré pour {index_name} (TTL={_ANALYZER_TTL}s) — reconstruction")
            del _analyzers[index_name]
            del _analyzers_ts[index_name]
    
    logger.info(f"Initializing DocumentAnalyzer for index: {index_name}...")
    
    # Imports
    from src.retrieval.bm25_search import BM25Search
    from src.retrieval.dense_search import DenseSearch
    from src.retrieval.hybrid import HybridSearch
    from src.agent.analyzer import DocumentAnalyzer
    
    # Obtenir l'embedder partagé
    embedder = _get_embedder()
    
    # OpenSearch index = version lowercase de l'index Qdrant
    opensearch_index = index_name.lower()
    
    # Dense search pour cet index
    dense_search = DenseSearch(
        host=config["qdrant"]["host"],
        port=config["qdrant"]["port"],
        collection_name=index_name,
        embedder=embedder
    )
    
    # BM25 search pour cet index
    bm25_search = BM25Search(
        host=config["opensearch"]["host"],
        port=config["opensearch"]["port"],
        index_name=opensearch_index
    )
    
    # Hybrid search - avec paramètres de retrieval du settings.yaml
    hybrid_search = HybridSearch(
        bm25_search=bm25_search,
        dense_search=dense_search,
        bm25_weight=config["retrieval"].get("bm25_weight", 0.5),
        dense_weight=config["retrieval"].get("dense_weight", 0.5),
        bm25_top_k=config["retrieval"].get("bm25_top_k", 100),
        dense_top_k=config["retrieval"].get("dense_top_k", 100)
    )
    
    # Document Analyzer — avec paramètres retrieval depuis settings.yaml
    analyzer = DocumentAnalyzer(
        hybrid_search=hybrid_search,
        llm_generator=_get_llm_generator(),
        reranker=_get_reranker(),
        cache_enabled=True,
        retrieval_config=config.get("retrieval", {})
    )
    
    # Mettre en cache
    _analyzers[index_name] = analyzer
    _analyzers_ts[index_name] = time.time()
    
    logger.info(f"DocumentAnalyzer ready for index: {index_name}")
    
    return analyzer


# ============================================================================
# Query History Logger (for RAGAS evaluation)
# ============================================================================

_QUERY_HISTORY_DB = os.environ.get("QUERY_HISTORY_DB", "/app/feedbacks/ragas.db")


def _init_query_history_db():
    Path(_QUERY_HISTORY_DB).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_QUERY_HISTORY_DB) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS query_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT,
            contexts TEXT,
            index_name TEXT,
            processing_time_ms INTEGER
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS ragas_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, index_name TEXT, question TEXT,
            faithfulness REAL, answer_relevancy REAL, context_recall REAL
        )""")


try:
    _init_query_history_db()
except Exception as e:
    logger.warning(f"Could not init query history DB: {e}")


def _log_query(question: str, answer: str, sources: list, index_name: str,
               processing_time_ms: int = 0, search_results: list = None):
    """Store query+answer+contexts for later RAGAS evaluation."""
    try:
        contexts_texts = []
        for src in (search_results or sources or []):
            if isinstance(src, dict):
                text = src.get("text", "") or src.get("text_with_context", "") or src.get("content", "") or src.get("snippet", "")
                if text:
                    contexts_texts.append(text[:2000])
        with sqlite3.connect(_QUERY_HISTORY_DB) as conn:
            conn.execute(
                "INSERT INTO query_history (timestamp, question, answer, contexts, index_name, processing_time_ms) VALUES (?,?,?,?,?,?)",
                (datetime.now().isoformat(), question, answer,
                 json.dumps(contexts_texts, ensure_ascii=False), index_name, processing_time_ms)
            )
    except Exception as e:
        logger.warning(f"Failed to log query for RAGAS: {e}")


# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/")
async def root():
    """Health check"""
    return {
        "service": "Luciole Agent",
        "status": "running",
        "version": "1.0.0"
    }


@app.post("/api/reload-config")
async def reload_config():
    """
    Recharge la configuration à chaud sans redémarrer le container.
    Réinitialise les singletons : config, LLM, query rewriter, analyzers.
    Les modèles GPU (embedder, reranker) ne sont PAS rechargés (trop coûteux).
    """
    global _config, _llm_generator, _query_rewriter, _analyzers
    
    try:
        # 1. Reset settings.yaml cache
        _config = None
        new_config = _get_config()
        logger.info("✅ settings.yaml rechargé")
        
        # 2. Reset prompts.yaml (via singleton dans config_loader)
        try:
            from src.config_loader import reset_prompts_instance
            reset_prompts_instance()
        except ImportError:
            try:
                from config_loader import reset_prompts_instance
                reset_prompts_instance()
            except ImportError:
                logger.warning("config_loader non trouvé, prompts seront rechargés via LLM generator reset")
        logger.info("✅ prompts.yaml rechargé")
        
        # 3. Reset LLM generator (va recharger les prompts au prochain appel)
        _llm_generator = None
        logger.info("✅ LLM generator réinitialisé")
        
        # 4. Reset query rewriter (reload du module Python depuis le disque + reset singleton)
        _query_rewriter = None
        import importlib
        try:
            from src.retrieval import query_rewriter as qr_module
            importlib.reload(qr_module)
            qr_module._rewriter_instance = None
        except ImportError:
            try:
                from retrieval import query_rewriter as qr_module
                importlib.reload(qr_module)
                qr_module._rewriter_instance = None
            except ImportError:
                pass
        logger.info("✅ Query rewriter rechargé depuis le disque")
        
        # 5. Mettre à jour le reranker top_n SANS recharger le modèle GPU
        if _reranker is not None:
            new_top_n = new_config.get("retrieval", {}).get("rerank_top_n", 10)
            _reranker.top_n = new_top_n
            logger.info(f"✅ Reranker top_n mis à jour: {new_top_n}")
        
        # 6. Vider le cache des analyzers (seront recréés avec la nouvelle config)
        _analyzers.clear()
        logger.info("✅ Cache analyzers vidé")
        
        logger.info("🔄 Configuration rechargée avec succès !")
        return {
            "status": "ok",
            "message": "Configuration rechargée avec succès",
            "reloaded": ["settings.yaml", "prompts.yaml", "llm_generator", "query_rewriter", "analyzers"],
            "kept": ["embedder (GPU)", "reranker model (GPU)"]
        }
        
    except Exception as e:
        logger.error(f"❌ Erreur rechargement config: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/api/health")
async def health_check():
    """Vérification de santé détaillée"""
    try:
        analyzer = get_analyzer()
        
        # Check LLM
        llm_ok = analyzer.llm_generator.health_check()
        
        return {
            "status": "healthy" if llm_ok else "degraded",
            "components": {
                "analyzer": "ok",
                "llm": "ok" if llm_ok else "unavailable",
                "cache": analyzer.get_cache_stats()
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


@app.get("/api/instance")
async def get_instance_info():
    """
    Expose l'identité de l'instance courante pour les UI.

    Retourne le nom de l'instance (depuis l'env INSTANCE_NAME), pour permettre
    aux UI d'afficher un bandeau et de masquer le sélecteur d'index.
    """
    return {
        "instance_name": INSTANCE_NAME,
        "single_index_mode": bool(INSTANCE_NAME),
    }


@app.get("/api/indexes")
async def list_indexes():
    """
    Liste les index disponibles (collections Qdrant et index OpenSearch).
    Permet à l'UI de proposer une sélection.

    Règle '1 instance = 1 index' : si INSTANCE_NAME est défini, on ne retourne
    QUE cet index (même s'il n'existe pas encore côté Qdrant/OpenSearch),
    pour masquer le sélecteur côté UI.
    """
    try:
        import httpx
        config = _get_config()

        # Mode mono-index : on retourne uniquement l'instance courante
        if INSTANCE_NAME:
            return {
                "indexes": [{
                    "name": INSTANCE_NAME,
                    "type": "qdrant",
                    "opensearch_index": INSTANCE_NAME.lower(),
                    "has_opensearch": True,
                }],
                "default": INSTANCE_NAME,
                "instance_name": INSTANCE_NAME,
                "single_index_mode": True,
            }

        indexes = []
        
        # Récupérer les collections Qdrant
        # Utiliser la variable d'environnement si disponible (pour Docker)
        try:
            qdrant_base = os.environ.get("QDRANT_URL", f"http://{config['qdrant']['host']}:{config['qdrant']['port']}")
            qdrant_url = f"{qdrant_base}/collections"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(qdrant_url)
                if response.status_code == 200:
                    data = response.json()
                    for coll in data.get("result", {}).get("collections", []):
                        coll_name = coll.get("name", "")
                        if coll_name:
                            indexes.append({
                                "name": coll_name,
                                "type": "qdrant",
                                "opensearch_index": coll_name.lower()
                            })
        except Exception as e:
            logger.warning(f"Could not fetch Qdrant collections: {e}")
        
        # Récupérer les index OpenSearch (optionnel, pour vérification)
        # Utiliser la variable d'environnement si disponible (pour Docker)
        try:
            opensearch_base = os.environ.get("OPENSEARCH_URL", f"http://{config['opensearch']['host']}:{config['opensearch']['port']}")
            os_url = f"{opensearch_base}/_cat/indices?format=json"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(os_url)
                if response.status_code == 200:
                    os_indexes = [idx.get("index") for idx in response.json() 
                                  if not idx.get("index", "").startswith(".")]
                    # Marquer les index qui ont une correspondance OpenSearch
                    for idx_info in indexes:
                        idx_info["has_opensearch"] = idx_info["opensearch_index"] in os_indexes
        except Exception as e:
            logger.warning(f"Could not fetch OpenSearch indexes: {e}")
        
        # Ne retourner le default que s'il existe dans les indexes
        default_name = config["qdrant"]["collection_name"]
        existing_names = [idx["name"] for idx in indexes]
        
        # Si le default n'existe pas, utiliser le premier index disponible ou None
        if default_name not in existing_names:
            default_name = existing_names[0] if existing_names else None
        
        return {
            "indexes": indexes,
            "default": default_name,
            "instance_name": None,
            "single_index_mode": False,
        }
    except Exception as e:
        return {
            "indexes": [],
            "default": None,
            "instance_name": INSTANCE_NAME,
            "single_index_mode": bool(INSTANCE_NAME),
            "error": str(e)
        }


@app.post("/api/analyze")
async def analyze(request: AnalyzeRequest):
    """
    Point d'entrée principal pour l'analyse de documents.
    
    Modes:
    - auto: Classification automatique de la requête
    - files: Recherche de fichiers spécifiques
    - folder: Analyse d'un dossier
    - cross: Analyse comparative
    - chat: Question générale avec RAG
    
    Args:
        request.index_name: Nom de l'index à interroger (optionnel)
    """
    try:
        # Utiliser l'index spécifié ou celui par défaut
        analyzer = get_analyzer(index_name=_resolve_index_name(request.index_name))
        
        scope = request.scope.dict() if request.scope else None
        options = request.options.dict() if request.options else None
        
        result = analyzer.analyze(
            query=request.query,
            mode=request.mode,
            scope=scope,
            options=options
        )
        
        # Ajouter l'info sur l'index utilisé
        result["index_name"] = _resolve_index_name(request.index_name)
        
        return result
        
    except Exception as e:
        logger.error(f"Analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/summarize-file")
async def summarize_file(request: SummarizeFileRequest):
    """
    Résume un fichier spécifique avec cache.
    """
    try:
        analyzer = get_analyzer()
        
        detail_level = "standard"
        if request.options:
            detail_level = request.options.detail_level
        
        result = analyzer.summarize_file(
            file_path=request.file_path,
            detail_level=detail_level
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Summarize error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cross-analyze")
async def cross_analyze(request: CrossAnalyzeRequest):
    """
    Analyse comparative entre plusieurs fichiers/dossiers.
    """
    try:
        analyzer = get_analyzer()
        
        options = request.options.dict() if request.options else None
        
        result = analyzer.analyze(
            query=request.query,
            mode="cross",
            scope=request.scope.dict(),
            options=options
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Cross-analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/classify")
async def classify_query(query: str):
    """
    Classifie une requête sans l'exécuter.
    Utile pour debug ou prévisualisation.
    """
    try:
        from src.agent.classifier import QueryClassifier
        classifier = QueryClassifier()
        result = classifier.classify(query)
        return result
        
    except Exception as e:
        logger.error(f"Classification error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Deep Search - Comparaison des résultats
# ============================================================================

def _compare_deep_search_results(result_fresh: dict, result_context: dict, query: str) -> tuple:
    """
    Compare les résultats de la double recherche et retourne le meilleur.
    
    Stratégie de sélection :
    1. Si l'un dit "pas d'info" et l'autre a une réponse → prendre celui avec réponse
    2. Si les deux ont une réponse → prendre celui avec meilleur score de confiance
    3. Si les deux disent "pas d'info" → prendre le résultat frais
    
    Args:
        result_fresh: Résultat de la recherche sans historique
        result_context: Résultat de la recherche avec historique
        query: Question originale
        
    Returns:
        Tuple (meilleur_resultat, choix_effectue)
    """
    # Patterns indiquant "pas d'information trouvée"
    no_info_patterns = [
        "pas d'information",
        "pas trouvé",
        "n'ai pas trouvé",
        "aucune information",
        "pas de données",
        "information non trouvée",
        "je ne dispose pas",
        "pas disponible dans",
        "documents ne contiennent pas"
    ]
    
    def has_no_info(response: str) -> bool:
        """Vérifie si la réponse indique qu'aucune info n'a été trouvée"""
        response_lower = response.lower()
        return any(pattern in response_lower for pattern in no_info_patterns)
    
    def get_confidence(result: dict) -> float:
        """Extrait le score de confiance du résultat"""
        return result.get("metadata", {}).get("confidence", 0.5)
    
    def get_sources_count(result: dict) -> int:
        """Compte le nombre de sources pertinentes"""
        return len(result.get("sources", []))
    
    response_fresh = result_fresh.get("response", "")
    response_context = result_context.get("response", "")
    
    fresh_no_info = has_no_info(response_fresh)
    context_no_info = has_no_info(response_context)
    
    # Cas 1 : Fresh a trouvé, Context n'a pas trouvé
    if not fresh_no_info and context_no_info:
        logger.info("🔍 Deep Search: Résultat FRAIS choisi (context n'a pas trouvé)")
        return result_fresh, "fresh_found"
    
    # Cas 2 : Context a trouvé, Fresh n'a pas trouvé
    if fresh_no_info and not context_no_info:
        logger.info("🔍 Deep Search: Résultat CONTEXTUEL choisi (fresh n'a pas trouvé)")
        return result_context, "context_found"
    
    # Cas 3 : Les deux ont trouvé → comparer confiance et sources
    if not fresh_no_info and not context_no_info:
        fresh_confidence = get_confidence(result_fresh)
        context_confidence = get_confidence(result_context)
        fresh_sources = get_sources_count(result_fresh)
        context_sources = get_sources_count(result_context)
        
        # Score combiné : confiance + bonus pour sources
        fresh_score = fresh_confidence + (fresh_sources * 0.01)
        context_score = context_confidence + (context_sources * 0.01)
        
        if fresh_score >= context_score:
            logger.info(f"🔍 Deep Search: Résultat FRAIS choisi (score {fresh_score:.2f} >= {context_score:.2f})")
            return result_fresh, "fresh_better_score"
        else:
            logger.info(f"🔍 Deep Search: Résultat CONTEXTUEL choisi (score {context_score:.2f} > {fresh_score:.2f})")
            return result_context, "context_better_score"
    
    # Cas 4 : Aucun n'a trouvé → prendre le frais (plus neutre)
    logger.info("🔍 Deep Search: Résultat FRAIS choisi (aucun n'a trouvé)")
    return result_fresh, "both_no_info"


@app.post("/api/query")
async def simple_query(request: QueryRequest):
    """
    Point d'entrée simplifié pour requête RAG standard.
    Équivalent à /api/analyze avec mode=chat.
    
    Inclut le Query Rewriting automatique pour améliorer les résultats.
    
    Args:
        request.query: Question utilisateur
        request.top_k: Nombre de sources à retourner
        request.index_name: Nom de l'index à interroger (optionnel)
        request.custom_prompt: Prompt personnalisé optionnel
        request.enable_rewriting: Activer/désactiver le query rewriting
    """
    try:
        # Convertir l'historique en format dict pour le LLM
        history_dicts = None
        if request.history and len(request.history) > 0:
            history_dicts = [{"role": msg.role, "content": msg.content} for msg in request.history]
            logger.info(f"Using conversation history: {len(history_dicts)} messages")
        
        # =====================================================================
        # Contextualisation des questions de suivi
        # Si la question semble être une question de suivi (courte, pronoms...),
        # on l'enrichit avec le contexte de la dernière question utilisateur
        # =====================================================================
        contextualized_query = request.query
        was_contextualized = False
        
        if history_dicts and len(history_dicts) >= 2:
            query_lower = request.query.lower().strip()
            
            # Détection de question de suivi (patterns typiques)
            followup_indicators = [
                # Pronoms référentiels
                query_lower.startswith(("ça", "ca", "c'", "il ", "elle ", "ils ", "elles ", "le ", "la ", "les ", "lui ")),
                # Questions courtes sans sujet clair
                len(request.query.split()) <= 8 and any(w in query_lower for w in ["aussi", "donc", "alors", "sinon", "autre", "plus", "moins", "combien", "quoi", "lequel", "lesquels", "laquelle", "lesquelles"]),
                # Questions commençant par des mots de liaison
                query_lower.startswith(("et ", "mais ", "ou ", "donc ", "car ", "parce", "sinon ", "autrement ")),
                # Questions avec "me" référant à une réponse précédente
                any(p in query_lower for p in ["donne moi", "dis moi", "montre moi", "explique moi", "liste", "détaille", "précise", "peux-tu", "peux tu", "tu peux"]),
            ]
            
            if any(followup_indicators):
                # Trouver la dernière question utilisateur dans l'historique
                last_user_query = None
                for msg in reversed(history_dicts):
                    if msg.get("role") == "user":
                        last_user_query = msg.get("content", "")
                        break
                
                if last_user_query and len(last_user_query) > 10:
                    # Enrichir la requête avec le contexte
                    contextualized_query = f"{request.query} (contexte: {last_user_query})"
                    was_contextualized = True
                    logger.info(f"Query contextualized: '{request.query}' → '{contextualized_query}'")
        
        # Query Rewriting - reformuler la requête si activé
        rewritten_query = contextualized_query
        was_rewritten = False
        query_type = None
        
        # DEBUG: Log AVANT le rewriter
        logger.info(f"DEBUG API - BEFORE rewriter: contextualized_query='{contextualized_query}' (len={len(contextualized_query)})")
        
        if request.enable_rewriting:
            query_rewriter = _get_query_rewriter()
            # rewrite() retourne: (List[str], str, bool) = (requêtes, query_type, was_modified)
            rewritten_queries, query_type, was_rewritten = query_rewriter.rewrite(contextualized_query)
            
            # DEBUG: Log APRÈS le rewriter
            logger.info(f"DEBUG API - AFTER rewriter: rewritten_queries={rewritten_queries}, type={query_type}, was_rewritten={was_rewritten}")
            
            # Requête principale pour le prompt LLM (première de la liste)
            rewritten_query = rewritten_queries[0] if rewritten_queries else contextualized_query
            
            if was_rewritten:
                logger.info(f"Query rewritten: '{contextualized_query}' → '{rewritten_query}' (type: {query_type}, {len(rewritten_queries)} variantes)")
        
        # Utiliser l'index spécifié ou celui par défaut
        analyzer = get_analyzer(index_name=_resolve_index_name(request.index_name))
        
        # DEBUG: Log de la query avant d'appeler l'analyzer
        logger.info(f"DEBUG API - Query to analyzer: '{rewritten_query}' (len={len(rewritten_query)})")
        
        # Préparer les options avec le prompt personnalisé si fourni
        options = {"max_items": request.top_k}
        if request.custom_prompt:
            options["custom_prompt"] = request.custom_prompt
            logger.info(f"Using custom prompt: {request.custom_prompt[:100]}...")
        
        # Préparer les requêtes multi-query pour la recherche
        # (toutes les variantes générées par le rewriter)
        multi_search_queries = rewritten_queries if (was_rewritten and len(rewritten_queries) > 1) else None
        
        # =====================================================================
        # DEEP SEARCH : Double recherche avec/sans historique
        # =====================================================================
        if request.deep_search and history_dicts and len(history_dicts) > 0:
            logger.info("🔍 DEEP SEARCH activé - Lancement double recherche")
            
            # Recherche 1 : SANS historique (recherche fraîche)
            result_fresh = analyzer.analyze(
                query=rewritten_query,
                mode="chat",
                options=options,
                history=None,  # Pas d'historique
                search_queries=multi_search_queries
            )
            
            # Recherche 2 : AVEC historique (recherche contextuelle)
            result_context = analyzer.analyze(
                query=rewritten_query,
                mode="chat",
                options=options,
                history=history_dicts,
                search_queries=multi_search_queries
            )
            
            # Comparer les résultats
            result, search_choice = _compare_deep_search_results(
                result_fresh, 
                result_context,
                rewritten_query
            )
            
            logger.info(f"🔍 DEEP SEARCH résultat choisi: {search_choice}")
            
            # Marquer qu'on a utilisé deep_search
            deep_search_info = {
                "enabled": True,
                "choice": search_choice,
                "fresh_confidence": result_fresh.get("metadata", {}).get("confidence", 0),
                "context_confidence": result_context.get("metadata", {}).get("confidence", 0)
            }
        else:
            # Recherche normale (simple ou multi-query)
            result = analyzer.analyze(
                query=rewritten_query,
                mode="chat",
                options=options,
                history=history_dicts,
                search_queries=multi_search_queries
            )
            deep_search_info = None
        
        search_results_raw = result.get("search_results", [])
        passages = []
        for chunk in search_results_raw[:30]:
            p = {
                "text": (chunk.get("text") or chunk.get("content") or "")[:1000],
                "file_name": chunk.get("file_name", ""),
                "score": round(chunk.get("rrf_score", chunk.get("score", 0)), 4),
            }
            meta = chunk.get("metadata", {})
            if meta.get("page"):
                p["page"] = meta["page"]
            if meta.get("section"):
                p["section"] = meta["section"]
            passages.append(p)

        response_data = {
            "response": result.get("response", ""),
            "sources": result.get("sources", []),
            "passages": passages,
            "mode": "chat",
            "index_name": _resolve_index_name(request.index_name),
            "processing_time_ms": result.get("metadata", {}).get("processing_time_ms", 0)
        }
        
        # Ajouter info sur le rewriting si appliqué
        if was_rewritten:
            response_data["query_rewriting"] = {
                "original": request.query,
                "rewritten": rewritten_query,
                "type": query_type
            }
        
        # Ajouter info sur la contextualisation si appliquée
        if was_contextualized:
            response_data["query_contextualized"] = {
                "original": request.query,
                "contextualized": contextualized_query
            }
        
        # Ajouter info sur deep_search si utilisé
        if deep_search_info:
            response_data["deep_search"] = deep_search_info
        
        # Log pour RAGAS (async-safe, fire and forget)
        _log_query(
            question=request.query,
            answer=response_data.get("response", ""),
            sources=response_data.get("sources", []),
            index_name=response_data.get("index_name", ""),
            processing_time_ms=response_data.get("processing_time_ms", 0),
            search_results=result.get("search_results", []),
        )
        
        return response_data
        
    except Exception as e:
        logger.error(f"Query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/query-history")
async def get_query_history(limit: int = 50):
    """Returns recent queries for RAGAS evaluation in Admin UI."""
    try:
        with sqlite3.connect(_QUERY_HISTORY_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, timestamp, question, answer, contexts, index_name, processing_time_ms "
                "FROM query_history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return {"queries": [dict(r) for r in rows]}
    except Exception as e:
        return {"queries": [], "error": str(e)}


@app.get("/api/cache/stats")
async def cache_stats():
    """Statistiques du cache"""
    try:
        analyzer = get_analyzer()
        return analyzer.get_cache_stats()
    except Exception as e:
        return {"error": str(e)}


@app.delete("/api/cache")
async def clear_cache(file_path: Optional[str] = None):
    """Vide le cache (tout ou pour un fichier)"""
    try:
        analyzer = get_analyzer()
        analyzer.clear_cache(file_path)
        return {"status": "cleared", "file_path": file_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Feedback Management
# ============================================================================

_FEEDBACK_DB = os.environ.get("FEEDBACK_DB_PATH", "/app/feedbacks/feedbacks.db")


def _init_feedback_db():
    db_path = Path(_FEEDBACK_DB)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            user_id TEXT,
            query TEXT NOT NULL,
            response TEXT NOT NULL,
            sources TEXT,
            index_name TEXT,
            feedback TEXT CHECK(feedback IN ('up', 'down')),
            expected_response TEXT,
            comment TEXT,
            processing_time_ms INTEGER
        )
    """)
    conn.commit()
    conn.close()


_init_feedback_db()


@app.get("/api/feedback/config")
async def get_feedback_config():
    """Retourne la configuration feedback (enabled, key_users) depuis settings.yaml."""
    config = _get_config()
    fb = config.get("feedback", {})
    return {
        "enabled": fb.get("enabled", False),
        "key_users": fb.get("key_users", []),
    }


class FeedbackSubmit(BaseModel):
    query: str
    response: str
    sources: Optional[str] = None
    index_name: Optional[str] = None
    feedback: str
    expected_response: Optional[str] = None
    comment: Optional[str] = None
    processing_time_ms: Optional[int] = None
    user_id: Optional[str] = None


@app.post("/api/feedback")
async def submit_feedback(fb: FeedbackSubmit):
    """Enregistre un feedback dans la base SQLite."""
    try:
        conn = sqlite3.connect(str(_FEEDBACK_DB))
        conn.execute(
            "INSERT INTO feedbacks (user_id, query, response, sources, index_name, feedback, expected_response, comment, processing_time_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fb.user_id, fb.query, fb.response, fb.sources, fb.index_name,
             fb.feedback, fb.expected_response, fb.comment, fb.processing_time_ms),
        )
        conn.commit()
        fid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        logger.info(f"Feedback #{fid}: {fb.feedback} by {fb.user_id}")
        return {"status": "success", "id": fid}
    except Exception as e:
        logger.error(f"Feedback error: {e}")
        return {"status": "error", "message": str(e)}


# ============================================================================
# Ollama Model Management
# ============================================================================

def _get_ollama_base_url() -> str:
    config = _get_config()
    url = os.environ.get("OLLAMA_URL") or config.get("llm", {}).get("base_url", "http://ollama:11434")
    return url.rstrip("/")


@app.get("/api/ollama/models")
async def list_ollama_models():
    """Liste les modeles installes dans Ollama avec details (famille, params, quantisation)."""
    base_url = _get_ollama_base_url()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()

            config = _get_config()
            active_model = config.get("llm", {}).get("model", "")

            models = []
            for m in data.get("models", []):
                name = m.get("name", "")
                entry = {
                    "name": name,
                    "size_mb": round(m.get("size", 0) / 1e6),
                    "modified_at": m.get("modified_at", ""),
                    "digest": m.get("digest", "")[:12],
                    "active": name == active_model,
                    "family": "",
                    "parameter_size": "",
                    "quantization": "",
                    "context_length": 0,
                }
                try:
                    show_resp = await client.post(f"{base_url}/api/show", json={"name": name})
                    if show_resp.status_code == 200:
                        show_data = show_resp.json()
                        details = show_data.get("details", {})
                        entry["family"] = details.get("family", "")
                        entry["parameter_size"] = details.get("parameter_size", "")
                        entry["quantization"] = details.get("quantization_level", "")
                        model_info = show_data.get("model_info", {})
                        for key, val in model_info.items():
                            if key.endswith(".context_length"):
                                entry["context_length"] = int(val)
                                break
                except Exception:
                    pass
                models.append(entry)

        return {"models": models, "active_model": active_model}
    except Exception as e:
        logger.error(f"Ollama list error: {e}")
        raise HTTPException(status_code=502, detail=f"Erreur communication Ollama: {e}")


@app.post("/api/ollama/pull")
async def pull_ollama_model(request: dict):
    """Telecharge un modele Ollama avec streaming de la progression."""
    model_name = request.get("model", "").strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="Nom du modele requis")

    base_url = _get_ollama_base_url()
    logger.info(f"Pulling Ollama model: {model_name}")

    async def stream_pull():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30, read=3600, write=30, pool=30)) as client:
                async with client.stream("POST", f"{base_url}/api/pull", json={"name": model_name}) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        yield f"data: {json.dumps({'error': body.decode()})}\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if line.strip():
                            try:
                                progress = json.loads(line)
                                total = progress.get("total", 0)
                                completed = progress.get("completed", 0)
                                status = progress.get("status", "")
                                pct = int(completed / total * 100) if total > 0 else 0
                                yield f"data: {json.dumps({'status': status, 'pct': pct, 'completed_mb': round(completed/1e6), 'total_mb': round(total/1e6)})}\n\n"
                            except json.JSONDecodeError:
                                pass
            yield f"data: {json.dumps({'status': 'done', 'pct': 100})}\n\n"
        except Exception as e:
            logger.error(f"Ollama pull error: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(stream_pull(), media_type="text/event-stream")


class OllamaActivateRequest(BaseModel):
    model: str
    num_ctx: int = 8192
    max_tokens: int = 4096


@app.post("/api/ollama/activate")
async def activate_ollama_model(request: OllamaActivateRequest):
    """Active un modele: met a jour settings.yaml et recharge la config."""
    config_path = os.environ.get("CONFIG_PATH", "config/settings.yaml")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = f.read()
            settings = yaml.safe_load(raw)

        old_model = settings.get("llm", {}).get("model", "")
        settings["llm"]["model"] = request.model
        settings["llm"]["num_ctx"] = request.num_ctx
        settings["llm"]["max_tokens"] = request.max_tokens

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(settings, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        logger.info(f"LLM model changed: {old_model} -> {request.model} (num_ctx={request.num_ctx})")

        result = await reload_config()
        return {
            "status": "ok",
            "old_model": old_model,
            "new_model": request.model,
            "num_ctx": request.num_ctx,
            "max_tokens": request.max_tokens,
            "reload": result,
        }
    except Exception as e:
        logger.error(f"Activate model error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ollama/search")
async def search_ollama_registry(q: str = ""):
    """Recherche de modeles sur ollama.com (scraping HTML)."""
    query = q.strip()
    if not query:
        return {"models": [], "error": "Parametre q requis"}

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                f"https://ollama.com/search?q={query}",
                headers={"User-Agent": "Luciole/3.0"}
            )
            resp.raise_for_status()
            html = resp.text

        models = []
        items = re.split(r'<li\s+x-test-model', html)
        for item in items[1:]:  # skip content before first <li>
            name_match = re.search(r'x-test-search-response-title[^>]*>([^<]+)<', item)
            desc_match = re.search(r'text-neutral-800[^>]*>([^<]+)<', item)
            sizes = re.findall(r'x-test-size[^>]*>([^<]+)<', item)
            pulls_match = re.search(r'Pulls', item)
            pulls = ""
            if pulls_match:
                before = item[:pulls_match.start()]
                num_match = re.search(r'>\s*([\d,.]+[KMB]?)\s*$', before)
                if num_match:
                    pulls = num_match.group(1).strip()

            if name_match:
                models.append({
                    "name": name_match.group(1).strip(),
                    "description": desc_match.group(1).strip() if desc_match else "",
                    "tags": [s.strip() for s in sizes],
                    "pulls": pulls,
                })

        return {"models": models, "query": query}
    except httpx.ConnectError:
        return {"models": [], "error": "Pas de connexion internet. Utilisez la saisie manuelle."}
    except Exception as e:
        logger.error(f"Ollama search error: {e}")
        return {"models": [], "error": str(e)}


@app.delete("/api/ollama/models")
async def delete_ollama_model(request: dict):
    """Supprime un modele Ollama. Refuse si c'est le modele actif."""
    model_name = request.get("model", "").strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="Nom du modele requis")

    config = _get_config()
    active_model = config.get("llm", {}).get("model", "")
    if model_name == active_model:
        raise HTTPException(status_code=409, detail=f"Impossible de supprimer le modele actif ({model_name}). Activez un autre modele d'abord.")

    base_url = _get_ollama_base_url()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request("DELETE", f"{base_url}/api/delete", json={"name": model_name})
            if resp.status_code != 200:
                detail = resp.text or f"Ollama a retourne {resp.status_code}"
                raise HTTPException(status_code=resp.status_code, detail=detail)
        logger.info(f"Ollama model deleted: {model_name}")
        return {"status": "ok", "deleted": model_name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ollama delete error: {e}")
        raise HTTPException(status_code=502, detail=f"Erreur communication Ollama: {e}")


# ============================================================================
# Startup / Shutdown
# ============================================================================

@app.on_event("startup")
async def startup_event():
    logger.info("🦋 Luciole Agent starting...")
    # Pre-warm the analyzer (optional)
    # get_analyzer()


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🦋 Luciole Agent shutting down...")


# ============================================================================
# Run with uvicorn
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.environ.get("AGENT_PORT", 8500))
    uvicorn.run(app, host="0.0.0.0", port=port)


