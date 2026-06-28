"""
RAG Evaluation Metrics
"""

from typing import List, Dict
from loguru import logger
import time


class RAGEvaluator:
    """
    Evaluate RAG system performance
    Tracks timing and quality metrics
    """
    
    def __init__(self):
        self.metrics_history = []
    
    def log_query(
        self,
        query: str,
        response: str,
        sources: List[Dict],
        timing: Dict,
        confidence: float
    ):
        """
        Log a query execution for analysis
        
        Args:
            query: User query
            response: Generated response
            sources: Retrieved sources
            timing: Timing breakdown
            confidence: Confidence score
        """
        metric = {
            "timestamp": time.time(),
            "query": query,
            "response_length": len(response),
            "num_sources": len(sources),
            "confidence": confidence,
            "timing": timing
        }
        
        self.metrics_history.append(metric)
        logger.info(f"Query logged: confidence={confidence:.2f}, sources={len(sources)}")
    
    def get_summary(self) -> Dict:
        """Get summary statistics"""
        if not self.metrics_history:
            return {"total_queries": 0}
        
        confidences = [m["confidence"] for m in self.metrics_history]
        
        return {
            "total_queries": len(self.metrics_history),
            "avg_confidence": sum(confidences) / len(confidences),
            "avg_sources": sum(m["num_sources"] for m in self.metrics_history) / len(self.metrics_history)
        }

