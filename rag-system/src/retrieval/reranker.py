"""
Reranker — Cross-encoder reranking for improved precision
Mode OFFLINE : charge les modèles depuis le cache local
V3 : auto-device via resolve_device(), batch_size adaptatif
"""

import os
from typing import List, Dict, Optional
from pathlib import Path
import torch
from sentence_transformers import CrossEncoder
from loguru import logger

from ..utils.device import resolve_device

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"


class Reranker:
    """
    Cross-encoder reranker for improved retrieval precision.
    Default model v3 : BAAI/bge-reranker-v2-m3
    Mode OFFLINE supporté.
    """

    def _get_local_model_path(self, model_name: str) -> Optional[str]:
        """Cherche le modèle reranker dans le cache HuggingFace local."""
        model_folder = f"models--{model_name.replace('/', '--')}"
        cache_paths = [
            Path(os.environ.get("HF_HOME", "/app/models/huggingface")) / "hub",
            Path(os.environ.get("HF_HOME", "/app/models/huggingface")),
            Path.home() / ".cache" / "huggingface" / "hub",
        ]

        for hf_cache in cache_paths:
            model_path = hf_cache / model_folder
            logger.debug(f"Recherche du reranker dans: {model_path}")
            if model_path.exists():
                snapshots_dir = model_path / "snapshots"
                if snapshots_dir.exists():
                    snapshots = list(snapshots_dir.iterdir())
                    if snapshots:
                        snapshot_path = snapshots[0]
                        logger.info(f"Modèle reranker trouvé dans le cache local: {snapshot_path}")
                        return str(snapshot_path)

        logger.warning(f"Modèle reranker {model_name} non trouvé dans les caches: {cache_paths}")
        return None

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        device: str = "auto",
        batch_size: int = 32,
        top_n: int = 5
    ):
        self.top_n = top_n

        # Override par variable d'environnement (priorité sur settings.yaml)
        # Permet de forcer le reranker sur GPU sans toucher au fichier de config.
        env_device = os.environ.get("RERANKER_DEVICE", "").strip().lower()
        effective_device = env_device or device
        self.device = resolve_device(effective_device)

        if env_device:
            logger.info(f"Reranker device override via RERANKER_DEVICE='{env_device}' → résolu en '{self.device}'")

        self.batch_size = batch_size if self.device == "cuda" else min(batch_size, 8)

        logger.info(f"Loading reranker model: {model_name} on {self.device} (batch_size={self.batch_size})")

        local_path = self._get_local_model_path(model_name)
        model_kwargs = {"use_safetensors": True}

        if local_path:
            logger.info(f"Chargement du reranker en mode offline depuis: {local_path}")
            self.model = CrossEncoder(local_path, device=self.device, model_kwargs=model_kwargs)
        else:
            logger.warning("Cache local non trouvé, tentative de chargement standard...")
            try:
                self.model = CrossEncoder(model_name, device=self.device, model_kwargs=model_kwargs)
            except Exception as e:
                logger.error(f"Impossible de charger le reranker en mode offline: {e}")
                raise RuntimeError(
                    f"Modèle reranker {model_name} non trouvé dans le cache local. "
                    "Assurez-vous que le cache HuggingFace a été copié correctement."
                )

        logger.info("Reranker model loaded")

    def rerank(self, query: str, results: List[Dict], top_n: int = None) -> List[Dict]:
        if not results:
            return []

        top_n = top_n or self.top_n
        pairs = [(query, result["text"]) for result in results]

        logger.debug(f"Reranking {len(pairs)} results")
        scores = self.model.predict(pairs, show_progress_bar=False, batch_size=self.batch_size)

        for i, result in enumerate(results):
            result["rerank_score"] = float(scores[i])

        reranked = sorted(results, key=lambda x: x["rerank_score"], reverse=True)
        top_results = reranked[:top_n]

        logger.info(f"Reranked {len(results)} results, returning top {len(top_results)}")
        return top_results

    def rerank_with_threshold(
        self,
        query: str,
        results: List[Dict],
        threshold: float = 0.5,
        top_n: int = None
    ) -> List[Dict]:
        reranked = self.rerank(query, results, top_n=len(results))
        filtered = [r for r in reranked if r["rerank_score"] >= threshold]
        top_n = top_n or self.top_n
        return filtered[:top_n]
