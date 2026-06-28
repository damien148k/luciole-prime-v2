"""
Embedder — Generate dense embeddings
Mode OFFLINE : charge les modèles depuis le cache local
V3 : auto-device via resolve_device(), support BGE-M3 + E5
"""

import os
from typing import List, Optional
from pathlib import Path
import torch
from sentence_transformers import SentenceTransformer
from loguru import logger

from .chunker import Chunk
from ..utils.device import resolve_device

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"


class Embedder:
    """
    Generate embeddings using HuggingFace sentence-transformers.
    Default model v3 : BAAI/bge-m3
    Mode OFFLINE supporté.
    """

    def _get_local_model_path(self, model_name: str) -> Optional[str]:
        """Cherche le modèle dans les caches HuggingFace locaux."""
        model_folder = f"models--{model_name.replace('/', '--')}"

        cache_paths = [
            Path(os.environ.get("HF_HOME", "/app/models/huggingface")) / "hub",
            Path(os.environ.get("HF_HOME", "/app/models/huggingface")),
            Path(os.environ.get("SENTENCE_TRANSFORMERS_HOME", "/app/models/sentence_transformers")),
            Path.home() / ".cache" / "huggingface" / "hub",
        ]

        for cache_path in cache_paths:
            model_path = cache_path / model_folder
            logger.debug(f"Recherche du modèle dans: {model_path}")
            if model_path.exists():
                snapshots_dir = model_path / "snapshots"
                if snapshots_dir.exists():
                    snapshots = list(snapshots_dir.iterdir())
                    if snapshots:
                        snapshot_path = snapshots[0]
                        logger.info(f"Modèle trouvé dans le cache local: {snapshot_path}")
                        return str(snapshot_path)

        logger.warning(f"Modèle {model_name} non trouvé dans les caches: {cache_paths}")
        return None

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        device: str = "auto",
        batch_size: int = 32
    ):
        self.model_name = model_name
        self.device = resolve_device(device)
        self.batch_size = batch_size if self.device == "cuda" else min(batch_size, 8)

        logger.info(f"Loading embedding model: {model_name} on {self.device} (batch_size={self.batch_size})")

        local_path = self._get_local_model_path(model_name)

        model_kwargs = {"use_safetensors": True}

        if local_path:
            logger.info(f"Chargement en mode offline depuis: {local_path}")
            self.model = SentenceTransformer(local_path, device=self.device, model_kwargs=model_kwargs)
        else:
            hf_cache = Path(os.environ.get("HF_HOME", "/app/models/huggingface")) / "hub"
            logger.info(f"Chargement avec local_files_only=True (cache: {hf_cache})")
            try:
                self.model = SentenceTransformer(
                    model_name,
                    device=self.device,
                    cache_folder=str(hf_cache),
                    model_kwargs=model_kwargs,
                )
            except Exception as e:
                logger.error(f"Impossible de charger le modèle en mode offline: {e}")
                raise RuntimeError(
                    f"Modèle {model_name} non trouvé dans le cache local ({hf_cache}). "
                    "Assurez-vous que le volume huggingface_cache est monté correctement."
                )

        self.embedding_dim = self.model.get_sentence_embedding_dimension()
        logger.info(f"Embedding model loaded. Dimension: {self.embedding_dim}")

    def _needs_prefix(self) -> bool:
        """Vérifie si le modèle nécessite un préfixe query:/passage: (E5)."""
        return "e5" in self.model_name.lower()

    def embed_text(self, text: str, is_query: bool = False) -> List[float]:
        if self._needs_prefix():
            prefix = "query: " if is_query else "passage: "
            text = prefix + text

        embedding = self.model.encode(text, convert_to_tensor=False)
        return embedding.tolist()

    def embed_chunks(self, chunks: List[Chunk]) -> List[dict]:
        if not chunks:
            return []

        texts = []
        for chunk in chunks:
            text = chunk.text_with_context
            if self._needs_prefix():
                text = "passage: " + text
            texts.append(text)

        logger.info(f"Generating embeddings for {len(texts)} chunks (with file context)")

        embeddings = self.model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=True,
            convert_to_tensor=False
        )

        results = []
        for chunk, embedding in zip(chunks, embeddings):
            results.append({
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "text": chunk.text,
                "text_with_context": chunk.text_with_context,
                "file_path": chunk.file_path,
                "file_name": chunk.file_name,
                "embedding": embedding.tolist(),
                "metadata": chunk.metadata
            })

        logger.info(f"Generated {len(results)} embeddings")
        return results

    def embed_query(self, query: str) -> List[float]:
        return self.embed_text(query, is_query=True)
