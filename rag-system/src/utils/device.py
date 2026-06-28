# -*- coding: utf-8 -*-
"""
Device Resolver — Auto-détection CUDA / CPU

Fichier partagé par embedder.py, reranker.py et ocr.py.
Quand settings.yaml contient device: "auto", ce module détecte
le matériel disponible et renvoie "cuda" ou "cpu".
"""

import torch
from loguru import logger


def resolve_device(setting: str) -> str:
    """Résout 'auto' → 'cuda' ou 'cpu' selon le matériel disponible."""
    if setting == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"Device auto-détecté : {device}")
        return device
    return setting
