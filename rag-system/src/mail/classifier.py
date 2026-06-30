"""
Classificateur d'emails — Module mail Luciole Prime.

Classification en deux passes :
  1. Règles déterministes (rapide, sans LLM) — détection spam, auto-reply,
     domaines bloqués, mots-clés sensibles.
  2. Classification LLM via Ollama (optionnelle, fallback sur règles si KO).

La décision de routage tient compte de la catégorie, des scores
et des paramètres de politique configurés par l'admin.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

import httpx
from loguru import logger

from .config import OLLAMA_URL
from .constants import (
    DEFAULT_SENSITIVE_KEYWORDS,
    MAX_REPLY_PER_THREAD_PER_HOUR,
    EmailCategory,
    RoutingDecision,
)
from .models import ClassificationResult, MailSettings, ParsedEmail


class EmailClassifier:
    """
    Classifie un email entrant et produit une décision de routage.

    La classification LLM est optionnelle : si Ollama est indisponible
    ou si le résultat est invalide, les règles déterministes prennent
    le relais avec confidence=0.5 (forçant le brouillon en V1).
    """

    # ─────────────────────────────────────────────────────────────────────────
    # Point d'entrée principal
    # ─────────────────────────────────────────────────────────────────────────

    def classify(
        self,
        email: ParsedEmail,
        settings: MailSettings,
        thread_reply_count_last_hour: int = 0,
    ) -> ClassificationResult:
        """
        Classifie un email et retourne un ClassificationResult avec decision.

        Classification (règles + LLM) DÉSACTIVÉE volontairement :
        tout mail entrant est traité comme question_documentaire → brouillon.
        Seuls les garde-fous anti-boucle critiques restent actifs :
          - auto-reply / bounce détecté → quarantaine
          - dépassement du nombre de réponses par thread/heure → quarantaine

        Pour réactiver la classification complète, restaurer les appels à
        self._rule_based() et self._llm_classify() (code conservé ci-dessous).
        """
        # ── Garde-fou 1 : auto-reply / bounce (anti-boucle SMTP) ──────────
        if email.is_auto_reply:
            result = ClassificationResult(
                category=EmailCategory.SPAM,
                confidence_score=0.99,
                risk_score=0.0,
                decision=RoutingDecision.QUARANTINE,
                decision_reason=f"Auto-reply/bounce détecté : {email.auto_reply_reason}",
            )
        # ── Garde-fou 2 : anti-boucle conversationnelle ───────────────────
        elif thread_reply_count_last_hour >= MAX_REPLY_PER_THREAD_PER_HOUR:
            result = ClassificationResult(
                category=EmailCategory.SPAM,
                confidence_score=0.95,
                risk_score=0.5,
                decision=RoutingDecision.QUARANTINE,
                decision_reason=(
                    f"Anti-boucle : {thread_reply_count_last_hour} réponses "
                    f"déjà envoyées sur ce thread dans l'heure"
                ),
            )
        else:
            # ── Classification désactivée : traitement direct ─────────────
            result = ClassificationResult(
                category=EmailCategory.QUESTION_DOCUMENTAIRE,
                confidence_score=1.0,
                risk_score=0.0,
                decision=RoutingDecision.DRAFT,
                decision_reason="Classification désactivée — traitement direct (confiance utilisateur)",
            )

        # ── Surcharges de politique ────────────────────────────────────────
        result = self._apply_policy_overrides(result, email, settings, thread_reply_count_last_hour)

        logger.info(
            f"Email classifié — from={email.from_address!r} "
            f"category={result.category.value} "
            f"confidence={result.confidence_score:.2f} "
            f"risk={result.risk_score:.2f} "
            f"decision={result.decision.value}"
        )
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Passe 1 : règles déterministes
    # ─────────────────────────────────────────────────────────────────────────

    def _rule_based(
        self,
        email: ParsedEmail,
        settings: MailSettings,
        thread_reply_count_last_hour: int,
    ) -> Optional[ClassificationResult]:
        """
        Règles déterministes rapides.

        Retourne un ClassificationResult si la règle conclut,
        None si la passe LLM est nécessaire.
        """
        from_lower = email.from_address.lower()
        subject_lower = (email.subject or "").lower()
        body_lower = (email.body_text or "").lower()

        # Auto-reply détecté par le parser
        if email.is_auto_reply:
            return ClassificationResult(
                category=EmailCategory.SPAM,
                confidence_score=0.99,
                risk_score=0.0,
                decision=RoutingDecision.QUARANTINE,
                decision_reason=f"Auto-reply détecté : {email.auto_reply_reason}",
            )

        # Anti-boucle : trop de réponses sur ce thread dans la dernière heure
        if thread_reply_count_last_hour >= MAX_REPLY_PER_THREAD_PER_HOUR:
            return ClassificationResult(
                category=EmailCategory.SPAM,
                confidence_score=0.95,
                risk_score=0.5,
                decision=RoutingDecision.QUARANTINE,
                decision_reason=f"Anti-boucle : {thread_reply_count_last_hour} réponses dans l'heure",
            )

        # Domaine expéditeur bloqué
        blocked = settings.get_blocked_domains()
        if blocked and any(d in from_lower for d in blocked):
            return ClassificationResult(
                category=EmailCategory.SPAM,
                confidence_score=0.99,
                risk_score=0.0,
                decision=RoutingDecision.QUARANTINE,
                decision_reason="Domaine expéditeur bloqué",
            )

        # Domaine expéditeur hors whitelist (si whitelist non vide)
        allowed = settings.get_allowed_domains()
        if allowed and not any(d in from_lower for d in allowed):
            return ClassificationResult(
                category=EmailCategory.HORS_PERIMETRE,
                confidence_score=0.90,
                risk_score=0.10,
                decision=RoutingDecision.QUARANTINE,
                decision_reason="Domaine expéditeur hors périmètre autorisé",
            )

        # Mots-clés sensibles (forçage brouillon, pas quarantaine)
        keywords = settings.get_sensitive_keywords() or list(DEFAULT_SENSITIVE_KEYWORDS)
        for kw in keywords:
            if kw in subject_lower or kw in body_lower:
                return ClassificationResult(
                    category=EmailCategory.SENSIBLE,
                    confidence_score=0.85,
                    risk_score=0.70,
                    decision=RoutingDecision.DRAFT,
                    decision_reason=f"Mot-clé sensible détecté : '{kw}'",
                )

        # Besoin humain explicite dans le texte
        human_patterns = (
            "je souhaite parler à", "je voudrais un humain",
            "contactez-moi", "appelez-moi", "urgent", "rappeler",
        )
        if any(p in body_lower for p in human_patterns):
            return ClassificationResult(
                category=EmailCategory.BESOIN_HUMAIN,
                confidence_score=0.80,
                risk_score=0.30,
                decision=RoutingDecision.ESCALATE,
                decision_reason="Demande de contact humain détectée",
            )

        # Pièces jointes : traitement prudent (brouillon forcé)
        if email.attachments:
            return ClassificationResult(
                category=EmailCategory.QUESTION_DOCUMENTAIRE,
                confidence_score=0.55,
                risk_score=0.20,
                decision=RoutingDecision.DRAFT,
                decision_reason="Pièces jointes présentes — brouillon par précaution",
            )

        # Les règles ne concluent pas → laisser le LLM décider
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Passe 2 : classification LLM
    # ─────────────────────────────────────────────────────────────────────────

    _CLASSIFICATION_PROMPT = """Tu es un classificateur d'emails pour une entreprise française.
Analyse l'email suivant et retourne UNIQUEMENT un objet JSON valide, rien d'autre.

Email :
Sujet: {subject}
Corps: {body}

Catégories possibles :
- question_documentaire : question sur des documents internes
- support : demande d'aide technique ou fonctionnelle
- demande_administrative : RH, juridique, contractuel
- hors_perimetre : hors sujet pour cet assistant
- spam : publicité, newsletter, contenu non sollicité
- sensible : contenu confidentiel ou à risque légal
- besoin_humain : l'expéditeur demande explicitement un interlocuteur humain

JSON attendu (et rien d'autre) :
{{"category": "...", "confidence": 0.0, "risk": 0.0, "reason": "..."}}"""

    def _llm_classify(self, email: ParsedEmail) -> Optional[ClassificationResult]:
        """
        Appel Ollama pour classification.

        Retourne None si l'appel échoue ou si le JSON est invalide.
        Timeout court (15s) pour ne pas bloquer le pipeline.
        """
        try:
            # Récupérer le modèle configuré (fallback sur qwen2.5:7b)
            from .config import AGENT_URL
            try:
                import yaml
                with open("/app/config/settings.yaml") as f:
                    cfg = yaml.safe_load(f)
                model = cfg.get("llm", {}).get("model", "qwen2.5:7b")
            except Exception:
                model = "qwen2.5:7b"

            body_excerpt = (email.body_text or "")[:800]
            prompt = self._CLASSIFICATION_PROMPT.format(
                subject=email.subject[:200],
                body=body_excerpt,
            )

            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 200},
            }

            with httpx.Client(timeout=90.0) as client:   # 90s — cold start modèle inclus
                resp = client.post(f"{OLLAMA_URL}/api/chat", json=payload)
                resp.raise_for_status()
                content = resp.json()["message"]["content"].strip()

            # Extraire le JSON (parfois le LLM ajoute du texte autour)
            match = re.search(r"\{[^}]+\}", content, re.DOTALL)
            if not match:
                logger.warning(f"Classification LLM : JSON introuvable dans : {content[:200]}")
                return None

            data = json.loads(match.group(0))
            category = EmailCategory(data.get("category", "hors_perimetre"))
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
            risk = max(0.0, min(1.0, float(data.get("risk", 0.2))))
            reason = str(data.get("reason", "Classification LLM"))

            decision = self._category_to_decision(category, confidence, risk)

            return ClassificationResult(
                category=category,
                confidence_score=confidence,
                risk_score=risk,
                decision=decision,
                decision_reason=reason,
            )

        except (httpx.TimeoutException, httpx.ConnectError):
            logger.warning("Classification LLM : Ollama indisponible (timeout)")
            return None
        except Exception as e:
            logger.warning(f"Classification LLM échouée : {e}")
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Décision et surcharges de politique
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _category_to_decision(
        category: EmailCategory,
        confidence: float,
        risk: float,
        settings: Optional[MailSettings] = None,
    ) -> RoutingDecision:
        """Matrice de décision par défaut (avant surcharges de politique)."""
        # Quarantaine systématique
        if category in (EmailCategory.SPAM, EmailCategory.HORS_PERIMETRE):
            return RoutingDecision.QUARANTINE

        # Escalade systématique
        if category in (EmailCategory.SENSIBLE, EmailCategory.BESOIN_HUMAIN):
            return RoutingDecision.ESCALATE

        # Demande administrative : toujours brouillon
        if category == EmailCategory.DEMANDE_ADMINISTRATIVE:
            return RoutingDecision.DRAFT

        # Pour les autres catégories : brouillon (V1, auto_reply jamais activé)
        return RoutingDecision.DRAFT

    def _apply_policy_overrides(
        self,
        result: ClassificationResult,
        email: ParsedEmail,
        settings: MailSettings,
        thread_reply_count_last_hour: int,
    ) -> ClassificationResult:
        """
        Applique les surcharges de politique après la classification initiale.

        Cas d'interdiction absolue d'auto-réponse (V1 : auto_reply = False donc inopérant,
        mais le code est prêt pour V2) :
          - auto_reply_enabled = False
          - category sensible, escalade ou hors périmètre
          - risk >= risk_threshold
          - confidence < confidence_threshold
          - mots-clés sensibles
          - pièces jointes
          - trop de réponses dans l'heure
        """
        # V1 : auto-réponse toujours interdite
        if not settings.auto_reply_enabled:
            if result.decision == RoutingDecision.AUTO_REPLY:
                result.decision = RoutingDecision.DRAFT
                result.decision_reason += " [auto_reply désactivé → brouillon]"
            return result

        # En V2, les règles suivantes pourraient s'appliquer
        # (déjà codées pour faciliter la transition) :

        thresholds_fail = (
            result.confidence_score < settings.confidence_threshold
            or result.risk_score >= settings.risk_threshold
        )

        if result.decision == RoutingDecision.AUTO_REPLY and thresholds_fail:
            result.decision = RoutingDecision.DRAFT
            result.decision_reason += (
                f" [confiance {result.confidence_score:.2f} < {settings.confidence_threshold} "
                f"ou risque {result.risk_score:.2f} >= {settings.risk_threshold}]"
            )

        return result
