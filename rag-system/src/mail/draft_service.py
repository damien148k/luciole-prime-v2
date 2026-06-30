"""
Service de génération de brouillons — Module mail Luciole Prime.

Appelle le moteur RAG existant (Agent API) pour générer une réponse
à un email entrant, puis crée un brouillon pour validation humaine.

L'envoi n'est JAMAIS déclenché depuis ce service.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Optional

import httpx
from loguru import logger

from .config import AGENT_URL
from .constants import AuditAction, AuditOutcome, InboundStatus
from .exceptions import RAGQueryError
from .models import DraftApproval, InboundMessage, MailSettings, MailThread
from .state import AuditRepo, DraftRepo, InboundRepo


class DraftService:
    """
    Génère un brouillon de réponse via le moteur RAG.

    Workflow :
      1. Construire la requête RAG depuis l'email et le contexte du thread
      2. Appeler l'Agent API (POST /api/query)
      3. Vérifier la réponse (guardrails basiques)
      4. Insérer le brouillon en DB
      5. Mettre à jour le statut du message entrant
    """

    def create_draft(
        self,
        inbound: InboundMessage,
        thread: Optional[MailThread],
        settings: MailSettings,
        classification_category: str,
        decision_reason: str,
        confidence_score: float,
        risk_score: float,
    ) -> DraftApproval:
        """
        Génère et persiste un brouillon pour validation humaine.

        Retourne le DraftApproval créé (avec son id en DB).
        """
        t_start = time.monotonic()

        # ── 1. Construire la requête RAG ──────────────────────────────────
        rag_query = self._build_query(inbound, thread)

        # ── 2. Appeler l'Agent API ────────────────────────────────────────
        InboundRepo.update_status(inbound.id, InboundStatus.GENERATING)
        AuditRepo.log(
            action=AuditAction.RAG_QUERY.value,
            inbound_id=inbound.id,
            thread_id=inbound.thread_id,
            detail={"query_excerpt": rag_query[:200]},
        )

        try:
            rag_result = self._call_rag(rag_query, settings.index_name)
        except RAGQueryError as e:
            AuditRepo.log(
                action=AuditAction.GUARDRAIL_BLOCK.value,
                actor="system",
                outcome=AuditOutcome.FAILURE.value,
                inbound_id=inbound.id,
                detail={"error": str(e)},
            )
            raise

        response_text  = rag_result.get("response", "")
        sources        = rag_result.get("sources", [])
        passages       = rag_result.get("passages", [])
        rag_confidence = rag_result.get("confidence", confidence_score)

        # ── 3. Guardrails post-génération ─────────────────────────────────
        guardrail_reason = self._check_guardrails(response_text)
        if guardrail_reason:
            logger.warning(f"Guardrail déclenché (inbound #{inbound.id}) : {guardrail_reason}")
            response_text = (
                "[RÉPONSE BLOQUÉE PAR GUARDRAIL]\n\n"
                f"Raison : {guardrail_reason}\n\n"
                "Veuillez rédiger manuellement la réponse."
            )
            decision_reason += f" | Guardrail: {guardrail_reason}"
            AuditRepo.log(
                action=AuditAction.GUARDRAIL_BLOCK.value,
                outcome=AuditOutcome.BLOCKED.value,
                inbound_id=inbound.id,
                detail={"reason": guardrail_reason},
            )

        # ── 4. Créer le brouillon en DB ───────────────────────────────────
        draft = DraftApproval(
            inbound_message_id = inbound.id,
            thread_id          = inbound.thread_id,
            generated_response = response_text,
            sources_used       = json.dumps(sources, ensure_ascii=False),
            passages_used      = json.dumps(passages, ensure_ascii=False),
            rag_query          = rag_query,
            confidence_score   = float(rag_confidence),
            risk_score         = risk_score,
            classification     = classification_category,
            decision_reason    = decision_reason,
        )
        draft_id = DraftRepo.create(draft)
        draft.id = draft_id

        # ── 5. Mettre à jour le statut entrant ────────────────────────────
        InboundRepo.update_status(inbound.id, InboundStatus.DRAFT_PENDING)

        duration = int((time.monotonic() - t_start) * 1000)
        AuditRepo.log(
            action=AuditAction.DRAFT_CREATED.value,
            outcome=AuditOutcome.SUCCESS.value,
            inbound_id=inbound.id,
            thread_id=inbound.thread_id,
            draft_id=draft_id,
            duration_ms=duration,
            detail={
                "confidence": float(rag_confidence),
                "risk": risk_score,
                "sources_count": len(sources),
                "passages_count": len(passages),
                "category": classification_category,
            },
        )

        logger.info(
            f"Brouillon #{draft_id} créé pour inbound #{inbound.id} "
            f"(conf={rag_confidence:.2f}, risque={risk_score:.2f}, {duration}ms)"
        )
        return draft

    # ─────────────────────────────────────────────────────────────────────────
    # Construction de la requête RAG
    # ─────────────────────────────────────────────────────────────────────────

    # Sujets jugés non informatifs (test, réponse, transfert…)
    # → ignorés dans la query pour ne pas polluer le retrieval RAG
    _TRIVIAL_SUBJECT_RE = re.compile(
        r"^\s*(re|rép|rep|fwd|tr|fw|aw)\s*:?\s*$|^\s*(test|essai|ping|hello|bonjour|coucou)\s*\??\s*$",
        re.IGNORECASE,
    )

    @classmethod
    def _is_informative_subject(cls, subject: str) -> bool:
        """
        Détermine si un sujet apporte du signal sémantique exploitable.

        Retourne False pour les sujets vides, très courts (< 3 mots utiles),
        ou matchant les patterns triviaux (test, re:, fwd, etc.).
        """
        if not subject:
            return False
        s = subject.strip()
        if cls._TRIVIAL_SUBJECT_RE.match(s):
            return False
        # Retirer les préfixes Re:/Fwd: répétés pour compter les vrais mots
        cleaned = re.sub(r"^\s*(re|rép|rep|fwd|tr|fw|aw)\s*:\s*", "", s, flags=re.IGNORECASE)
        words = [w for w in re.split(r"\s+", cleaned) if len(w) >= 2]
        return len(words) >= 3

    @classmethod
    def _build_query(cls, inbound: InboundMessage, thread: Optional[MailThread]) -> str:
        """
        Transforme un email entrant en requête pour le moteur RAG.

        Stratégie :
          - Le corps du mail est la question principale (envoyée BRUTE,
            sans préfixe "Question :" qui dégrade le retrieval et le LLM).
          - Le sujet n'est ajouté que s'il est informatif (≥ 3 mots, pas
            un "test"/"re:"/"fwd:"), sous forme de phrase cohérente.
          - Le résumé de thread (si présent) est ajouté comme contexte.

        Fallback : si le corps est vide, on prend le sujet seul.
        """
        body = (inbound.body_text or "").strip()
        subject = (inbound.subject or "").strip()

        # Cas 1 : corps présent → corps = question principale
        if body:
            query = body[:1200]
            # On enrichit avec le sujet uniquement s'il apporte du signal
            if cls._is_informative_subject(subject):
                query = f"{query}\n\n(Sujet du mail : {subject})"
            if thread and thread.thread_summary:
                query = f"{query}\n\n(Contexte de la conversation : {thread.thread_summary[:400]})"
            return query

        # Cas 2 : pas de corps → fallback sur le sujet (même trivial)
        if subject:
            return subject

        return "Question sans contenu"

    # ─────────────────────────────────────────────────────────────────────────
    # Appel à l'Agent API RAG
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _call_rag(query: str, index_name: str) -> dict:
        """
        Appelle l'Agent API existant (POST /api/query).

        Réutilise toute la chaîne RAG : hybrid search + reranker + LLM.
        Retourne le dict JSON de la réponse.
        Lève RAGQueryError en cas de problème.
        """
        payload = {
            "query": query,
            "index_name": index_name,
            "top_k": 20,           # aligné sur le défaut chat (ChatRequest.top_k=20)
            "enable_rewriting": True,
        }

        try:
            with httpx.Client(timeout=600.0) as client:   # 10min — chargement modèle 14B inclus
                resp = client.post(f"{AGENT_URL}/api/query", json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException:
            raise RAGQueryError(f"Timeout appel Agent API (120s) pour index={index_name}")
        except httpx.ConnectError:
            raise RAGQueryError(f"Agent API inaccessible : {AGENT_URL}")
        except httpx.HTTPStatusError as e:
            raise RAGQueryError(f"Agent API erreur {e.response.status_code} : {e.response.text[:200]}")
        except Exception as e:
            raise RAGQueryError(f"Erreur appel RAG : {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Guardrails post-génération
    # ─────────────────────────────────────────────────────────────────────────

    _NO_INFO_PATTERNS = (
        "je n'ai pas trouvé",
        "pas d'information",
        "aucune information",
        "je ne dispose pas",
        "pas disponible dans",
        "n'est pas présente dans",
        "no information",
        "not found",
    )

    def _check_guardrails(self, response: str) -> Optional[str]:
        """
        Vérifie la réponse générée avant création du brouillon.

        Retourne une raison de blocage (str) ou None si tout est OK.
        Note : le brouillon est créé même si un guardrail se déclenche,
        mais avec une réponse placeholder et la raison de blocage visible.
        """
        if not response or len(response.strip()) < 30:
            return "Réponse vide ou trop courte"

        response_lower = response.lower()

        # Trop d'indicateurs "pas d'info trouvée" → brouillon informatif
        no_info_count = sum(1 for p in self._NO_INFO_PATTERNS if p in response_lower)
        if no_info_count >= 2:
            return "Contexte documentaire insuffisant (pas de sources pertinentes)"

        # Détection de potentielles fuites de données système
        system_leak_patterns = (
            "system prompt",
            "instructions internes",
            "<|system|>",
            "[INST]",
        )
        for pattern in system_leak_patterns:
            if pattern.lower() in response_lower:
                return f"Fuite de contexte système détectée : {pattern!r}"

        return None
