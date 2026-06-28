"""
BM25 Search - Sparse retrieval using OpenSearch
"""

from typing import List, Dict
from loguru import logger
from opensearchpy import OpenSearch


class BM25Search:
    """
    BM25 sparse search using OpenSearch
    """
    
    def __init__(self, host: str = "localhost", port: int = 9200, index_name: str = "documents_bm25"):
        """
        Initialize BM25 search
        
        Args:
            host: OpenSearch host
            port: OpenSearch port
            index_name: Index name to search
        """
        self.index_name = index_name
        self.client = OpenSearch(
            hosts=[{"host": host, "port": port}],
            http_compress=True,
            use_ssl=False,
            verify_certs=False
        )
        logger.info(f"BM25Search initialized: {host}:{port}/{index_name}")
    
    def search(self, query: str, top_k: int = 20) -> List[Dict]:
        """
        Search documents using BM25
        
        Recherche dans: texte, texte avec contexte, chemin du fichier, nom du fichier
        Les pondérations favorisent le contenu tout en valorisant le contexte fichier
        
        Args:
            query: Search query
            top_k: Number of results to return
            
        Returns:
            List of search results with scores
        """
        search_body = {
            "size": top_k,
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": [
                        "text^3",              # Contenu principal (priorité haute)
                        "text_with_context^2", # Texte avec contexte fichier
                        "file_name^2",         # Nom du fichier (souvent informatif)
                        "file_path^1.5",       # Chemin (contexte organisationnel)
                        "metadata.title^2",    # Titre du document si disponible
                        "metadata.author"      # Auteur
                    ],
                    "type": "best_fields",
                    "fuzziness": "AUTO"
                }
            },
            "_source": ["chunk_id", "document_id", "text", "file_path", "file_name", "metadata"]
        }
        
        try:
            response = self.client.search(index=self.index_name, body=search_body)
            
            results = []
            for hit in response["hits"]["hits"]:
                source = hit["_source"]
                results.append({
                    "chunk_id": source["chunk_id"],
                    "document_id": source["document_id"],
                    "text": source["text"],
                    "file_path": source.get("file_path", ""),
                    "file_name": source.get("file_name", ""),
                    "metadata": source.get("metadata", {}),
                    "score": hit["_score"],
                    "search_type": "bm25"
                })
            
            logger.debug(f"BM25 search returned {len(results)} results for: {query[:50]}...")
            return results
            
        except Exception as e:
            logger.error(f"BM25 search error: {e}")
            return []
    
    def health_check(self) -> bool:
        """Check if OpenSearch is available"""
        try:
            return self.client.ping()
        except Exception:
            return False

