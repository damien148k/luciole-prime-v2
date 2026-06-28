# -*- coding: utf-8 -*-
"""
Hybrid Query Engine - Recherche RAG multi-stratégie avec fusion de résultats

Implémente :
1. BM25 (recherche textuelle)
2. Dense retrieval (embeddings + KNN)
3. Reciprocal Rank Fusion (fusion des résultats)
4. Reranking optionnel
5. Multi-query search pour dossiers
"""

import logging
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass
import numpy as np
from datetime import datetime

from src.retrieval.query_rewriter import get_query_rewriter, QueryRewriter

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Représente un résultat de recherche unique."""
    doc_id: str
    content: str
    score: float
    source: str  # 'bm25', 'dense', 'reranked'
    metadata: Dict[str, Any] = None
    rank: int = 0
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class SearchResults:
    """Représente l'ensemble des résultats de recherche."""
    results: List[SearchResult]
    total_count: int
    search_time_ms: float
    queries: List[str]
    query_type: str
    was_multi_query: bool
    
    def __iter__(self):
        return iter(self.results)
    
    def __len__(self):
        return len(self.results)
    
    def __getitem__(self, index):
        return self.results[index]


class HybridQueryEngine:
    """
    Moteur de recherche hybride combinant BM25, Dense Search et Reranking.
    
    Flux :
    1. QueryRewriter : reformule et détecte le type de requête
    2. Multi-query search : lance plusieurs requêtes pour dossiers
    3. BM25 search : recherche textuelle
    4. Dense search : recherche par similarité sémantique
    5. RRF fusion : combine les résultats
    6. Reranking : optimise le scoring final (optionnel)
    7. Deduplication : supprime les doublons avec scoring fusionné
    """
    
    def __init__(self, 
                 bm25_engine=None,
                 dense_engine=None,
                 reranker=None,
                 query_rewriter: Optional[QueryRewriter] = None,
                 enable_multi_query: bool = True,
                 enable_reranking: bool = True):
        """
        Initialise le moteur hybride.
        
        Args:
            bm25_engine: Instance du moteur BM25
            dense_engine: Instance du moteur Dense (KNN)
            reranker: Instance du reranker (optionnel)
            query_rewriter: Instance du QueryRewriter (ou crée une nouvelle)
            enable_multi_query: Active la multi-query pour dossiers
            enable_reranking: Active le reranking final
        """
        self.bm25_engine = bm25_engine
        self.dense_engine = dense_engine
        self.reranker = reranker
        self.query_rewriter = query_rewriter or get_query_rewriter()
        self.enable_multi_query = enable_multi_query
        self.enable_reranking = enable_reranking
        
        logger.info(
            f"HybridQueryEngine initialisé: "
            f"BM25={bm25_engine is not None}, "
            f"Dense={dense_engine is not None}, "
            f"Reranker={reranker is not None}, "
            f"MultiQuery={enable_multi_query}, "
            f"Reranking={enable_reranking}"
        )
    
    # =========================================================================
    # SEARCH PRINCIPAL
    # =========================================================================
    
    def search(self, 
               query: str, 
               top_k: int = 10,
               min_score: float = 0.0) -> SearchResults:
        """
        Lance une recherche hybride avec reformulation intelligente.
        
        Args:
            query: Requête utilisateur
            top_k: Nombre de résultats à retourner
            min_score: Score minimum pour les résultats
            
        Returns:
            SearchResults: Résultats fusionnés et dédupliqués
        """
        start_time = datetime.now()
        
        # 1. Reformuler la requête
        rewritten_queries, query_type, was_modified = self.query_rewriter.rewrite(query)
        
        logger.info(
            f"🔍 Recherche: '{query}' → Type: {query_type}, "
            f"Variantes: {len(rewritten_queries)}, Modifié: {was_modified}"
        )
        
        # 2. Effectuer la recherche (simple ou multi-query)
        if self.enable_multi_query and len(rewritten_queries) > 1:
            all_results = self._perform_multi_query_search(
                rewritten_queries, 
                query_type, 
                top_k
            )
        else:
            all_results = self._perform_single_query_search(
                rewritten_queries[0], 
                top_k
            )
        
        # 3. Dédupliquer et fusionner
        final_results = self._deduplicate_and_score_results(all_results)
        
        # 4. Filtrer par score minimum
        final_results = [r for r in final_results if r.score >= min_score]
        
        # 5. Retourner les top_k
        final_results = final_results[:top_k]
        
        elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
        
        logger.info(
            f"✅ Recherche complétée: {len(final_results)} résultats "
            f"en {elapsed_ms:.0f}ms"
        )
        
        return SearchResults(
            results=final_results,
            total_count=len(final_results),
            search_time_ms=elapsed_ms,
            queries=rewritten_queries,
            query_type=query_type,
            was_multi_query=len(rewritten_queries) > 1
        )
    
    # =========================================================================
    # SINGLE vs MULTI QUERY
    # =========================================================================
    
    def _perform_single_query_search(self, query: str, top_k: int) -> List[SearchResult]:
        """Effectue une recherche simple (une requête)."""
        all_results = []
        
        # BM25 search
        if self.bm25_engine:
            try:
                bm25_results = self.bm25_engine.search(query, top_k=top_k)
                all_results.extend(bm25_results)
                logger.debug(f"   BM25: {len(bm25_results)} résultats")
            except Exception as e:
                logger.warning(f"Erreur BM25: {e}")
        
        # Dense search
        if self.dense_engine:
            try:
                dense_results = self.dense_engine.search(query, top_k=top_k)
                all_results.extend(dense_results)
                logger.debug(f"   Dense: {len(dense_results)} résultats")
            except Exception as e:
                logger.warning(f"Erreur Dense: {e}")
        
        return all_results
    
    def _perform_multi_query_search(self, 
                                   queries: List[str], 
                                   query_type: str, 
                                   top_k: int) -> List[SearchResult]:
        """Effectue une recherche multi-requête pour dossiers."""
        all_results = []
        
        logger.info(f"🔄 Multi-query search avec {len(queries)} variantes:")
        
        for i, variant_query in enumerate(queries, 1):
            logger.debug(f"   Requête {i}/{len(queries)}: {variant_query}")
            
            # Adapter top_k pour avoir assez de résultats après fusion
            adjusted_top_k = max(top_k, int(top_k * 1.5))
            
            variant_results = self._perform_single_query_search(
                variant_query, 
                adjusted_top_k
            )
            
            logger.debug(
                f"      → {len(variant_results)} résultats pour cette variante"
            )
            
            all_results.extend(variant_results)
        
        logger.info(f"📊 Total avant déduplication: {len(all_results)} résultats")
        
        return all_results
    
    # =========================================================================
    # DEDUPLICATION ET FUSION
    # =========================================================================
    
    def _deduplicate_and_score_results(self, 
                                      results: List[SearchResult]) -> List[SearchResult]:
        """
        Déduplique les résultats et fusionne les scores.
        
        Stratégie :
        1. Grouper par doc_id
        2. Fusionner les scores avec RRF (Reciprocal Rank Fusion)
        3. Appliquer le reranking si activé
        4. Trier par score final
        """
        if not results:
            return []
        
        # 1. Grouper par doc_id
        docs_dict: Dict[str, Dict] = {}
        
        for result in results:
            if result.doc_id not in docs_dict:
                docs_dict[result.doc_id] = {
                    'result': result,
                    'scores': {},
                    'sources': [],
                    'occurrences': 0
                }
            
            # Enregistrer le score par source
            source_key = f"{result.source}"
            docs_dict[result.doc_id]['scores'][source_key] = result.score
            docs_dict[result.doc_id]['sources'].append(result.source)
            docs_dict[result.doc_id]['occurrences'] += 1
        
        # 2. Calculer les scores fusionnés (RRF)
        merged_results = []
        
        for doc_id, doc_info in docs_dict.items():
            base_result = doc_info['result']
            
            # RRF: combiner les scores
            rrf_score = self._calculate_rrf_score(doc_info['scores'])
            
            # Boosting par occurrences (apparait dans plusieurs variantes = plus pertinent)
            occurrence_boost = 1.0 + (doc_info['occurrences'] - 1) * 0.1
            
            final_score = rrf_score * occurrence_boost
            
            # Reranking optionnel
            if self.enable_reranking and self.reranker:
                try:
                    final_score = self._apply_reranking(
                        base_result.content, 
                        final_score
                    )
                except Exception as e:
                    logger.warning(f"Erreur reranking: {e}")
            
            # Créer le résultat fusionné
            merged_result = SearchResult(
                doc_id=base_result.doc_id,
                content=base_result.content,
                score=final_score,
                source='hybrid',
                metadata={
                    **base_result.metadata,
                    'original_sources': list(set(doc_info['sources'])),
                    'occurrences': doc_info['occurrences'],
                    'individual_scores': doc_info['scores']
                },
                rank=0
            )
            
            merged_results.append(merged_result)
        
        # 3. Trier par score
        merged_results.sort(key=lambda x: x.score, reverse=True)
        
        # 4. Assigner les rangs
        for i, result in enumerate(merged_results, 1):
            result.rank = i
        
        logger.debug(
            f"✅ Déduplication: {len(results)} → {len(merged_results)} résultats"
        )
        
        return merged_results
    
    def _calculate_rrf_score(self, scores_by_source: Dict[str, float], k: int = 60) -> float:
        """
        Calcule le score RRF (Reciprocal Rank Fusion).
        
        Formula: RRF(d) = sum(1 / (k + rank(d)))
        
        Args:
            scores_by_source: Dict {source: score}
            k: Paramètre RRF (par défaut 60)
            
        Returns:
            float: Score RRF final
        """
        if not scores_by_source:
            return 0.0
        
        # Convertir scores en rangs
        rrf_sum = 0.0
        
        for source, score in scores_by_source.items():
            # Normaliser le score en rang (0-100 → 1-100)
            # Plus le score est haut, plus le rang est bas
            rank = 100 - (score * 100) if score <= 1.0 else 1
            
            # Appliquer RRF
            rrf_value = 1.0 / (k + rank)
            rrf_sum += rrf_value
        
        return rrf_sum / len(scores_by_source)
    
    def _apply_reranking(self, content: str, base_score: float) -> float:
        """
        Applique le reranking pour affiner le score.
        
        Args:
            content: Contenu du document
            base_score: Score avant reranking
            
        Returns:
            float: Score après reranking
        """
        if not self.reranker:
            return base_score
        
        try:
            rerank_score = self.reranker.score(content)
            # Combiner les scores
            final_score = (base_score * 0.7) + (rerank_score * 0.3)
            return final_score
        except Exception as e:
            logger.warning(f"Erreur reranking: {e}, utilisation du score de base")
            return base_score
    
    # =========================================================================
    # DEBUGGING
    # =========================================================================
    
    def debug_query(self, query: str) -> Dict[str, Any]:
        """
        Affiche les détails complets du processus de recherche.
        
        Args:
            query: Requête à déboguer
            
        Returns:
            Dict: Informations de déboggage
        """
        # Reformulation
        rewritten, query_type, was_modified = self.query_rewriter.rewrite(query)
        
        debug_info = {
            'original_query': query,
            'query_type': query_type,
            'was_modified': was_modified,
            'rewritten_queries': rewritten,
            'num_variants': len(rewritten),
            'query_rewriter_info': {
                'type_detection': query_type,
                'multi_query': len(rewritten) > 1,
            },
            'search_config': {
                'enable_multi_query': self.enable_multi_query,
                'enable_reranking': self.enable_reranking,
                'engines': {
                    'bm25': self.bm25_engine is not None,
                    'dense': self.dense_engine is not None,
                    'reranker': self.reranker is not None,
                }
            }
        }
        
        logger.info(f"Debug Query: {query}")
        logger.info(f"  Type: {query_type}")
        logger.info(f"  Variantes: {len(rewritten)}")
        for i, q in enumerate(rewritten, 1):
            logger.info(f"    {i}. {q}")
        
        return debug_info
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Retourne les statistiques du moteur.
        
        Returns:
            Dict: Statistiques
        """
        return {
            'query_rewriter': self.query_rewriter is not None,
            'bm25_engine': self.bm25_engine is not None,
            'dense_engine': self.dense_engine is not None,
            'reranker': self.reranker is not None,
            'enable_multi_query': self.enable_multi_query,
            'enable_reranking': self.enable_reranking,
        }


# ============================================================================
# SINGLETON PATTERN
# ============================================================================

_engine_instance: Optional[HybridQueryEngine] = None


def get_hybrid_query_engine(
    bm25_engine=None,
    dense_engine=None,
    reranker=None,
    enable_multi_query: bool = True,
    enable_reranking: bool = True
) -> HybridQueryEngine:
    """
    Retourne une instance singleton du HybridQueryEngine.
    
    Args:
        bm25_engine: Instance du moteur BM25
        dense_engine: Instance du moteur Dense
        reranker: Instance du reranker
        enable_multi_query: Active la multi-query
        enable_reranking: Active le reranking
        
    Returns:
        HybridQueryEngine: Instance singleton
    """
    global _engine_instance
    
    if _engine_instance is None:
        _engine_instance = HybridQueryEngine(
            bm25_engine=bm25_engine,
            dense_engine=dense_engine,
            reranker=reranker,
            enable_multi_query=enable_multi_query,
            enable_reranking=enable_reranking
        )
    
    return _engine_instance


def reset_engine_instance():
    """Réinitialise l'instance singleton."""
    global _engine_instance
    _engine_instance = None
    logger.info("HybridQueryEngine singleton réinitialisé")
