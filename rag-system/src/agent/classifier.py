"""
Query Classifier - Classification intelligente des requêtes utilisateur
Détermine le mode de traitement: files, folder, cross, ou chat
"""

import re
from typing import Dict, Tuple
from loguru import logger


class QueryClassifier:
    """
    Classifie les requêtes utilisateur pour déterminer le mode de traitement approprié.
    
    Modes:
    - files: Recherche de fichiers spécifiques
    - folder: Analyse d'un dossier/arborescence
    - cross: Analyse croisée/comparative
    - chat: Question générale (conversation)
    """
    
    # Patterns pour chaque mode
    PATTERNS = {
        "files": [
            r"\b(trouve|cherche|recherche|localise|où est|où sont)\b.*\b(fichier|document|pdf|word|excel|contrat|rapport|cv|facture)\b",
            r"\b(fichier|document|pdf|word|excel)\b.*\b(de|du|pour|concernant)\b",
            r"\b(cv|curriculum|resume)\b.*\b(de|du)\b",
            r"\b(contrat|facture|devis|bon de commande)\b.*\b(n°|numéro|client|fournisseur)\b",
            r"\b(montre|affiche|ouvre)\b.*\b(le|la|les)\b.*\b(fichier|document)\b",
        ],
        "folder": [
            r"\b(dossier|répertoire|arborescence|structure)\b",
            r"\b(que contient|qu'y a-t-il dans|liste les fichiers|explore)\b",
            r"\b(projet|client)\b.*\b(dossier|documents)\b",
            r"\b(tous les|l'ensemble des)\b.*\b(documents|fichiers)\b.*\b(de|du|dans)\b",
        ],
        "cross": [
            r"\b(compare|comparaison|différence|vs|versus)\b",
            r"\b(entre|commun|similaire|différent)\b.*\b(et|ou)\b",
            r"\b(synthèse|résumé|agrège|consolide)\b.*\b(tous|plusieurs|différents)\b",
            r"\b(analyse croisée|multi-documents|multi-fichiers)\b",
            r"\b(tendance|évolution|historique)\b.*\b(sur|entre|de)\b",
        ],
    }
    
    # Mots-clés de renforcement
    KEYWORDS = {
        "files": ["fichier", "document", "pdf", "word", "excel", "powerpoint", "cv", "contrat", 
                  "facture", "devis", "rapport", "mail", "email", "pièce", "justificatif"],
        "folder": ["dossier", "répertoire", "projet", "client", "arborescence", "structure",
                   "contenu", "organisation", "hiérarchie"],
        "cross": ["compare", "comparaison", "différence", "similitude", "synthèse", "agrégation",
                  "consolider", "résumer", "tendance", "évolution", "analyse"],
    }
    
    def __init__(self, use_llm: bool = False, llm_client=None):
        """
        Initialize classifier
        
        Args:
            use_llm: Use LLM for classification (more accurate but slower)
            llm_client: LLM client instance (required if use_llm=True)
        """
        self.use_llm = use_llm
        self.llm_client = llm_client
        logger.info(f"QueryClassifier initialized: use_llm={use_llm}")
    
    def classify(self, query: str) -> Dict:
        """
        Classifie une requête utilisateur
        
        Args:
            query: Requête utilisateur
            
        Returns:
            Dict avec mode, confidence, et reasoning
        """
        query_lower = query.lower()
        
        # Compter les scores pour chaque mode
        scores = {"files": 0, "folder": 0, "cross": 0, "chat": 0}
        matched_patterns = {"files": [], "folder": [], "cross": []}
        
        # Score basé sur les patterns regex
        for mode, patterns in self.PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, query_lower):
                    scores[mode] += 2
                    matched_patterns[mode].append(pattern)
        
        # Score basé sur les mots-clés
        for mode, keywords in self.KEYWORDS.items():
            for keyword in keywords:
                if keyword in query_lower:
                    scores[mode] += 1
        
        # Déterminer le mode avec le score le plus élevé
        max_score = max(scores.values())
        
        if max_score == 0:
            # Aucun pattern trouvé -> mode chat (question générale)
            mode = "chat"
            confidence = 0.5
            reasoning = "Aucun pattern spécifique détecté, traitement comme question générale"
        else:
            mode = max(scores, key=scores.get)
            # Calculer la confiance (0-1)
            total_score = sum(scores.values())
            confidence = scores[mode] / total_score if total_score > 0 else 0
            confidence = min(confidence, 0.95)  # Cap à 95%
            
            reasoning = f"Patterns détectés pour mode '{mode}': score={scores[mode]}"
            if matched_patterns.get(mode):
                reasoning += f", patterns={len(matched_patterns[mode])}"
        
        result = {
            "mode": mode,
            "confidence": round(confidence, 2),
            "reasoning": reasoning,
            "scores": scores,
            "query": query
        }
        
        logger.debug(f"Classification: {mode} (confidence={confidence:.2f})")
        return result
    
    def classify_with_llm(self, query: str) -> Dict:
        """
        Classification avec LLM pour plus de précision
        
        Args:
            query: Requête utilisateur
            
        Returns:
            Dict avec mode, confidence, et reasoning
        """
        if not self.llm_client:
            logger.warning("LLM client not available, falling back to rule-based")
            return self.classify(query)
        
        prompt = f"""Analyse cette requête et détermine son type.

Requête: "{query}"

Types possibles:
- files: Recherche de fichiers spécifiques (ex: "trouve le CV de Jean", "où est le contrat X")
- folder: Analyse d'un dossier/projet (ex: "que contient le projet Y", "liste les documents RH")
- cross: Analyse comparative/croisée (ex: "compare ces contrats", "synthétise les rapports")
- chat: Question générale/conversation (ex: "comment fonctionne X", "explique-moi Y")

Réponds UNIQUEMENT avec un JSON:
{{"mode": "...", "confidence": 0.X, "reasoning": "..."}}
"""
        
        try:
            response = self.llm_client.generate_simple(prompt)
            # Parser la réponse JSON
            import json
            # Extraire le JSON de la réponse
            json_match = re.search(r'\{[^}]+\}', response)
            if json_match:
                result = json.loads(json_match.group())
                result["query"] = query
                result["method"] = "llm"
                return result
        except Exception as e:
            logger.error(f"LLM classification failed: {e}")
        
        # Fallback vers règles
        return self.classify(query)


def classify_query(query: str) -> Tuple[str, float, str]:
    """
    Fonction helper pour classification rapide
    
    Returns:
        Tuple (mode, confidence, reasoning)
    """
    classifier = QueryClassifier()
    result = classifier.classify(query)
    return result["mode"], result["confidence"], result["reasoning"]



