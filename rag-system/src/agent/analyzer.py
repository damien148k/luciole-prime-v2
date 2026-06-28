"""
Document Analyzer - Analyse avancée de documents
Orchestrateur principal de l'agent Luciole
"""

import os
import time
import hashlib
from typing import Dict, List, Optional, Any
from pathlib import Path
from collections import defaultdict
from loguru import logger

from .classifier import QueryClassifier


class DocumentAnalyzer:
    """
    Analyseur de documents intelligent.
    Orchestre la recherche hybride et la génération LLM pour produire
    des analyses contextuelles sur les documents.
    """
    
    # Limites selon le niveau de détail
    LIMITS = {
        "quick": {
            "max_files": 10,
            "max_chunks_per_file": 3,
            "max_total_chunks": 20,
            "timeout_seconds": 30
        },
        "standard": {
            "max_files": 30,
            "max_chunks_per_file": 5,
            "max_total_chunks": 100,
            "timeout_seconds": 120
        },
        "deep": {
            "max_files": 100,
            "max_chunks_per_file": 10,
            "max_total_chunks": 500,
            "timeout_seconds": 300
        }
    }
    
    def __init__(
        self,
        hybrid_search,
        llm_generator,
        reranker=None,
        cache_enabled: bool = True,
        cache_ttl: int = 3600,
        retrieval_config: Dict = None
    ):
        """
        Initialize DocumentAnalyzer
        
        Args:
            hybrid_search: Instance de HybridSearch
            llm_generator: Instance de LLMGenerator
            reranker: Instance de Reranker (optionnel)
            cache_enabled: Activer le cache en mémoire
            cache_ttl: TTL du cache en secondes
            retrieval_config: Paramètres de retrieval depuis settings.yaml
        """
        self.hybrid_search = hybrid_search
        self.llm_generator = llm_generator
        self.reranker = reranker
        self.classifier = QueryClassifier()
        
        self.cache_enabled = cache_enabled
        self.cache_ttl = cache_ttl
        self._cache = {}  # Simple in-memory cache
        self._cache_timestamps = {}
        
        rc = retrieval_config or {}
        self.fusion_top_k = rc.get("fusion_top_k", 30)
        self.rerank_top_n = rc.get("rerank_top_n", 15)
        
        logger.info(f"DocumentAnalyzer initialized (fusion_top_k={self.fusion_top_k}, rerank_top_n={self.rerank_top_n})")
    
    def analyze(
        self,
        query: str,
        mode: str = "auto",
        scope: Dict = None,
        options: Dict = None,
        history: list = None,
        search_queries: list = None
    ) -> Dict:
        """
        Point d'entrée principal pour l'analyse
        
        Args:
            query: Requête utilisateur (utilisée pour le prompt LLM)
            mode: Mode de traitement (auto, files, folder, cross, chat)
            scope: Filtres optionnels (paths, file_types, date_range)
            options: Options (detail_level, max_items, include_sources)
            history: Historique de conversation optionnel
            search_queries: Liste de requêtes pour la recherche multi-query (optionnel)
            
        Returns:
            Dict avec résultats de l'analyse
        """
        start_time = time.time()
        
        # Options par défaut
        options = options or {}
        detail_level = options.get("detail_level", "standard")
        max_items = options.get("max_items", self.LIMITS[detail_level]["max_files"])
        include_sources = options.get("include_sources", True)
        
        # Classification automatique si mode=auto
        if mode == "auto":
            classification = self.classifier.classify(query)
            mode = classification["mode"]
            classification_reason = classification["reasoning"]
        else:
            classification_reason = f"Mode explicite: {mode}"
        
        logger.info(f"Analyzing query: mode={mode}, detail={detail_level}")
        
        # Récupérer le custom_prompt si fourni
        custom_prompt = options.get("custom_prompt")
        
        # Dispatch selon le mode
        if mode == "files":
            result = self._analyze_files(query, scope, detail_level, max_items)
        elif mode == "folder":
            result = self._analyze_folder(query, scope, detail_level, max_items)
        elif mode == "cross":
            result = self._analyze_cross(query, scope, detail_level, max_items)
        else:  # chat
            result = self._analyze_chat(query, detail_level, custom_prompt, history, search_queries)
        
        # Enrichir le résultat
        processing_time = int((time.time() - start_time) * 1000)
        result["metadata"] = result.get("metadata", {})
        result["metadata"].update({
            "mode": mode,
            "classification_reason": classification_reason,
            "detail_level": detail_level,
            "processing_time_ms": processing_time,
            "query": query
        })
        
        return result
    
    def _analyze_files(
        self,
        query: str,
        scope: Dict,
        detail_level: str,
        max_items: int
    ) -> Dict:
        """
        Mode files: Recherche et résume des fichiers spécifiques
        """
        limits = self.LIMITS[detail_level]
        
        # Recherche hybride
        search_results = self.hybrid_search.search(
            query,
            top_k=limits["max_total_chunks"]
        )
        
        if self.reranker and search_results:
            search_results = self.reranker.rerank(query, search_results[:self.fusion_top_k])[:self.rerank_top_n]
        
        # Grouper par fichier
        files_data = self._group_by_file(search_results, limits["max_chunks_per_file"])
        
        # Limiter le nombre de fichiers
        files_data = files_data[:max_items]
        
        if not files_data:
            return {
                "result_type": "files",
                "summary": "Aucun fichier trouvé correspondant à votre recherche.",
                "files": [],
                "metadata": {"search_results": 0}
            }
        
        # Générer des mini-résumés pour chaque fichier
        files_with_summaries = []
        for file_info in files_data:
            file_summary = self._summarize_file_chunks(
                file_info["file_name"],
                file_info["chunks"],
                query,
                detail_level
            )
            files_with_summaries.append({
                "file_name": file_info["file_name"],
                "file_path": file_info["file_path"],
                "score": file_info["avg_score"],
                "summary": file_summary,
                "metadata": file_info.get("metadata", {}),
                "chunks_count": len(file_info["chunks"])
            })
        
        # Générer un résumé global
        global_summary = self._generate_global_summary(
            query, files_with_summaries, "files"
        )
        
        return {
            "result_type": "files",
            "summary": global_summary,
            "files": files_with_summaries,
            "metadata": {
                "search_results": len(search_results),
                "files_found": len(files_with_summaries)
            }
        }
    
    def _analyze_folder(
        self,
        query: str,
        scope: Dict,
        detail_level: str,
        max_items: int
    ) -> Dict:
        """
        Mode folder: Analyse le contenu d'un dossier
        """
        # Pour le MVP, on utilise la même logique que files
        # mais orientée structure/organisation
        result = self._analyze_files(query, scope, detail_level, max_items)
        result["result_type"] = "folder"
        
        # Enrichir le résumé pour mentionner l'aspect dossier
        if result.get("files"):
            # Grouper par dossier parent
            folders = defaultdict(list)
            for f in result["files"]:
                parent = str(Path(f["file_path"]).parent)
                folders[parent].append(f["file_name"])
            
            result["folders"] = dict(folders)
            result["metadata"]["folders_count"] = len(folders)
        
        return result
    
    def _analyze_cross(
        self,
        query: str,
        scope: Dict,
        detail_level: str,
        max_items: int
    ) -> Dict:
        """
        Mode cross: Analyse comparative entre fichiers/dossiers
        """
        limits = self.LIMITS[detail_level]
        
        # Recherche étendue pour l'analyse croisée
        search_results = self.hybrid_search.search(
            query,
            top_k=limits["max_total_chunks"] * 2
        )
        
        if self.reranker and search_results:
            search_results = self.reranker.rerank(query, search_results[:self.fusion_top_k])[:self.rerank_top_n]
        
        # Grouper par fichier
        files_data = self._group_by_file(search_results, limits["max_chunks_per_file"])
        files_data = files_data[:max_items]
        
        if len(files_data) < 2:
            return {
                "result_type": "cross",
                "analysis_summary": "Pas assez de documents trouvés pour une analyse comparative.",
                "findings": [],
                "metadata": {"files_compared": len(files_data)}
            }
        
        # Construire le contexte pour la comparaison
        comparison_context = self._build_comparison_context(files_data, query)
        
        # Générer l'analyse comparative via LLM
        analysis = self._generate_cross_analysis(query, comparison_context, files_data)
        
        return {
            "result_type": "cross",
            "analysis_summary": analysis.get("summary", ""),
            "findings": analysis.get("findings", []),
            "files_compared": [
                {"file_name": f["file_name"], "file_path": f["file_path"]}
                for f in files_data
            ],
            "metadata": {
                "files_compared": len(files_data),
                "search_results": len(search_results)
            }
        }
    
    def _analyze_chat(self, query: str, detail_level: str, custom_prompt: str = None, history: list = None, search_queries: list = None) -> Dict:
        """
        Mode chat: Question générale avec contexte RAG
        
        Args:
            query: Question utilisateur (pour le prompt LLM)
            detail_level: Niveau de détail
            custom_prompt: Prompt personnalisé optionnel
            history: Historique de conversation optionnel
            search_queries: Liste de requêtes pour recherche multi-query (optionnel)
        """
        limits = self.LIMITS[detail_level]
        
        # Recherche pour contexte (multi-query si disponible)
        if search_queries and len(search_queries) > 1 and hasattr(self.hybrid_search, 'search_multi'):
            logger.info(f"🔄 Multi-query search avec {len(search_queries)} variantes")
            search_results = self.hybrid_search.search_multi(
                search_queries,
                top_k=limits["max_total_chunks"]
            )
        else:
            search_results = self.hybrid_search.search(
                query,
                top_k=limits["max_total_chunks"]
            )
        
        if self.reranker and search_results:
            search_results = self.reranker.rerank(query, search_results[:self.fusion_top_k])[:self.rerank_top_n]
        
        # Construire le contexte
        context = self._build_context(search_results)
        
        # DEBUG: Log de la query passée au LLM
        logger.info(f"DEBUG ANALYZER - Passing query to LLM: '{query}' (len={len(query)})")
        
        # Générer la réponse avec le custom_prompt et l'historique si fournis
        llm_result = self.llm_generator.generate(
            query, 
            context, 
            search_results,
            custom_prompt=custom_prompt,
            history=history
        )
        
        return {
            "result_type": "chat",
            "response": llm_result.get("response", ""),
            "sources": llm_result.get("sources", []),
            "search_results": search_results,
            "metadata": {
                "confidence": llm_result.get("confidence", 0),
                "model": llm_result.get("model", "unknown"),
                "custom_prompt_used": custom_prompt is not None,
                "history_used": history is not None and len(history) > 0
            }
        }
    
    def summarize_file(
        self,
        file_path: str,
        detail_level: str = "standard"
    ) -> Dict:
        """
        Résume un fichier spécifique
        
        Args:
            file_path: Chemin du fichier
            detail_level: Niveau de détail
            
        Returns:
            Dict avec résumé et points clés
        """
        # Vérifier le cache
        cache_key = self._get_cache_key(file_path, detail_level)
        cached = self._get_from_cache(cache_key)
        if cached:
            cached["metadata"]["cache_hit"] = True
            return cached
        
        limits = self.LIMITS[detail_level]
        file_name = Path(file_path).name
        
        # Rechercher les chunks de ce fichier
        # On utilise le nom du fichier comme requête
        search_results = self.hybrid_search.search(
            file_name,
            top_k=limits["max_total_chunks"]
        )
        
        # Filtrer pour ne garder que ce fichier
        file_chunks = [
            r for r in search_results
            if r.get("file_path") == file_path or r.get("file_name") == file_name
        ][:limits["max_chunks_per_file"] * 2]
        
        if not file_chunks:
            return {
                "file_name": file_name,
                "file_path": file_path,
                "summary": "Fichier non trouvé dans l'index.",
                "key_points": [],
                "metadata": {"cache_hit": False, "chunks_used": 0}
            }
        
        # Générer le résumé
        context = "\n\n".join([c.get("text", "") for c in file_chunks])
        
        prompt = f"""Résume ce document de manière concise.

Document: {file_name}

Contenu:
{context[:8000]}

Fournis:
1. Un résumé global (2-3 phrases)
2. Les points clés (liste de 3-5 éléments)

Format ta réponse ainsi:
RÉSUMÉ: [ton résumé]

POINTS CLÉS:
- [point 1]
- [point 2]
- [point 3]
"""
        
        try:
            llm_response = self.llm_generator.call_llm(
                "Tu es un assistant expert en analyse documentaire.",
                prompt
            )
            
            # Parser la réponse
            summary, key_points = self._parse_summary_response(llm_response)
            
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            summary = "Erreur lors de la génération du résumé."
            key_points = []
        
        result = {
            "file_name": file_name,
            "file_path": file_path,
            "summary": summary,
            "key_points": key_points,
            "metadata": {
                "cache_hit": False,
                "chunks_used": len(file_chunks)
            }
        }
        
        # Mettre en cache
        self._set_cache(cache_key, result)
        
        return result
    
    def _group_by_file(self, chunks: List[Dict], max_per_file: int) -> List[Dict]:
        """Groupe les chunks par fichier"""
        files = defaultdict(lambda: {"chunks": [], "scores": []})
        
        for chunk in chunks:
            file_path = chunk.get("file_path", "unknown")
            file_name = chunk.get("file_name", Path(file_path).name)
            
            if len(files[file_path]["chunks"]) < max_per_file:
                files[file_path]["chunks"].append(chunk)
                score = chunk.get("rrf_score", chunk.get("score", 0))
                files[file_path]["scores"].append(score)
                files[file_path]["file_name"] = file_name
                files[file_path]["file_path"] = file_path
                files[file_path]["metadata"] = chunk.get("metadata", {})
        
        # Calculer le score moyen et trier
        result = []
        for file_path, data in files.items():
            avg_score = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
            result.append({
                "file_path": file_path,
                "file_name": data["file_name"],
                "chunks": data["chunks"],
                "avg_score": round(avg_score, 4),
                "metadata": data["metadata"]
            })
        
        return sorted(result, key=lambda x: x["avg_score"], reverse=True)
    
    def _summarize_file_chunks(
        self,
        file_name: str,
        chunks: List[Dict],
        query: str,
        detail_level: str
    ) -> str:
        """Génère un mini-résumé d'un fichier basé sur ses chunks"""
        if not chunks:
            return "Pas de contenu disponible."
        
        # Extraire le texte des chunks
        texts = [c.get("text", "")[:500] for c in chunks[:3]]
        context = "\n---\n".join(texts)
        
        if detail_level == "quick":
            # Résumé très court sans appel LLM
            first_chunk = chunks[0].get("text", "")[:200]
            return first_chunk + "..." if len(first_chunk) == 200 else first_chunk
        
        # Générer via LLM
        try:
            prompt = f"""En 1-2 phrases, résume ce que contient ce document par rapport à la question.

Question: {query}
Document: {file_name}
Extraits: {context[:2000]}

Résumé court:"""
            
            summary = self.llm_generator.call_llm(
                "Tu es un assistant concis.",
                prompt
            )
            return summary.strip()[:500]
            
        except Exception as e:
            logger.warning(f"Mini-summary failed: {e}")
            return chunks[0].get("text", "")[:200] + "..."
    
    def _generate_global_summary(
        self,
        query: str,
        files: List[Dict],
        result_type: str
    ) -> str:
        """Génère un résumé global des résultats"""
        if not files:
            return "Aucun résultat trouvé."
        
        file_list = "\n".join([
            f"- {f['file_name']}: {f.get('summary', '')[:100]}"
            for f in files[:5]
        ])
        
        try:
            prompt = f"""Résume en 2-3 phrases les résultats de cette recherche.

Question: {query}
{len(files)} fichiers trouvés:
{file_list}

Résumé:"""
            
            summary = self.llm_generator.call_llm(
                "Tu es un assistant de recherche documentaire.",
                prompt
            )
            return summary.strip()
            
        except Exception:
            return f"J'ai trouvé {len(files)} fichiers correspondant à votre recherche."
    
    def _build_context(self, chunks: List[Dict]) -> str:
        """Construit le contexte à partir des chunks"""
        if not chunks:
            return ""
        
        context_parts = []
        for chunk in chunks[:15]:
            text = chunk.get("text", "")
            file_name = chunk.get("file_name", "")
            context_parts.append(f"[Source: {file_name}]\n{text}")
        
        return "\n\n---\n\n".join(context_parts)
    
    def _build_comparison_context(
        self,
        files_data: List[Dict],
        query: str
    ) -> str:
        """Construit le contexte pour une analyse comparative"""
        parts = []
        for file_info in files_data[:5]:
            file_name = file_info["file_name"]
            chunks_text = "\n".join([
                c.get("text", "")[:300]
                for c in file_info["chunks"][:3]
            ])
            parts.append(f"=== {file_name} ===\n{chunks_text}")
        
        return "\n\n".join(parts)
    
    def _generate_cross_analysis(
        self,
        query: str,
        context: str,
        files_data: List[Dict]
    ) -> Dict:
        """Génère une analyse comparative via LLM"""
        file_names = [f["file_name"] for f in files_data]
        
        try:
            prompt = f"""Analyse comparative demandée: {query}

Documents à comparer:
{context[:6000]}

Fournis:
1. Une synthèse comparative (3-4 phrases)
2. Les différences/points communs clés (liste)

Format:
SYNTHÈSE: [ta synthèse]

POINTS CLÉS:
- [point 1]
- [point 2]
"""
            
            response = self.llm_generator.call_llm(
                "Tu es un expert en analyse documentaire comparative.",
                prompt
            )
            
            # Parser
            summary = ""
            findings = []
            
            if "SYNTHÈSE:" in response:
                summary = response.split("SYNTHÈSE:")[1].split("POINTS")[0].strip()
            if "POINTS CLÉS:" in response:
                points_section = response.split("POINTS CLÉS:")[1]
                findings = [
                    {"description": line.strip("- ").strip(), "sources": file_names}
                    for line in points_section.split("\n")
                    if line.strip().startswith("-")
                ]
            
            return {"summary": summary, "findings": findings}
            
        except Exception as e:
            logger.error(f"Cross analysis failed: {e}")
            return {
                "summary": f"Analyse de {len(files_data)} documents.",
                "findings": []
            }
    
    def _parse_summary_response(self, response: str) -> tuple:
        """Parse la réponse LLM pour le résumé"""
        summary = ""
        key_points = []
        
        if "RÉSUMÉ:" in response:
            summary = response.split("RÉSUMÉ:")[1].split("POINTS")[0].strip()
        
        if "POINTS CLÉS:" in response:
            points_section = response.split("POINTS CLÉS:")[1]
            key_points = [
                line.strip("- ").strip()
                for line in points_section.split("\n")
                if line.strip().startswith("-")
            ]
        
        return summary or response[:500], key_points
    
    def _get_cache_key(self, *args) -> str:
        """Génère une clé de cache"""
        key_str = "|".join(str(a) for a in args)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def _get_from_cache(self, key: str) -> Optional[Dict]:
        """Récupère depuis le cache"""
        if not self.cache_enabled:
            return None
        
        if key in self._cache:
            timestamp = self._cache_timestamps.get(key, 0)
            if time.time() - timestamp < self.cache_ttl:
                return self._cache[key].copy()
            else:
                del self._cache[key]
                del self._cache_timestamps[key]
        
        return None
    
    def _set_cache(self, key: str, value: Dict):
        """Stocke dans le cache"""
        if self.cache_enabled:
            self._cache[key] = value.copy()
            self._cache_timestamps[key] = time.time()
    
    def get_cache_stats(self) -> Dict:
        """Retourne les statistiques du cache"""
        return {
            "entries": len(self._cache),
            "enabled": self.cache_enabled,
            "ttl_seconds": self.cache_ttl
        }
    
    def clear_cache(self, file_path: str = None):
        """Vide le cache (tout ou pour un fichier)"""
        if file_path:
            keys_to_delete = [k for k in self._cache if file_path in str(k)]
            for k in keys_to_delete:
                del self._cache[k]
                self._cache_timestamps.pop(k, None)
        else:
            self._cache.clear()
            self._cache_timestamps.clear()



