"""Metrics Store — Interface simplifiée pour l'évaluation RAGAS."""
from typing import Optional, Dict
from loguru import logger


class MetricsStore:
    """Fournit un accès simplifié aux métriques RAGAS."""

    def __init__(self, evaluator=None):
        self.evaluator = evaluator

    def get_summary(self, index_name: str, days: int = 30) -> Dict:
        if not self.evaluator:
            return {"status": "ragas_not_configured"}
        try:
            return self.evaluator.get_dashboard(index_name, days)
        except Exception as e:
            logger.error(f"Erreur récupération métriques RAGAS: {e}")
            return {"status": "error", "message": str(e)}

    def evaluate(self, question: str, answer: str, contexts: list, index_name: str) -> Dict:
        if not self.evaluator:
            return {"status": "ragas_not_configured"}
        try:
            return self.evaluator.evaluate_single(question, answer, contexts, index_name)
        except Exception as e:
            logger.error(f"Erreur évaluation RAGAS: {e}")
            return {"status": "error", "message": str(e)}
