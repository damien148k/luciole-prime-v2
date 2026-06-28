# -*- coding: utf-8 -*-
"""
Query Rewriter - Reformulation hybride des requêtes avec détection de mots-clés

Version neutre multi-métier. Les règles métier (BUSINESS_RULES) sont vides par
défaut et doivent être configurées selon le métier déployé. Les synonymes métier
se définissent dans config/synonyms.txt (rechargeable à chaud).

Approche :
1. Détection du type de requête (folder_search vs content_search vs general)
2. Règles métier pour les patterns connus (à configurer par métier)
3. Multi-query generation pour recherches de dossiers
4. LLM fallback intelligent pour cas complexes (optionnel)
"""

import re
import os
import logging
from typing import Optional, Tuple, List, Dict, Set

logger = logging.getLogger(__name__)


class QueryRewriter:
    """
    Reformule les requêtes utilisateur pour améliorer la pertinence de la recherche.

    Approche hybride :
    1. Détection du type de requête (folder_search vs general)
    2. Règles métier simples (< 1ms) - pour les patterns connus
    3. Multi-query generation - pour les dossiers imbriqués
    4. LLM fallback (2-3s) - pour les cas complexes (optionnel)
    """

    # =========================================================================
    # RÈGLES MÉTIER - ICPE / ÉTUDES D'IMPACT ENVIRONNEMENTALE
    # =========================================================================
    # Format: (pattern regex, enrichissement ajouté, description)
    #
    # Ces règles enrichissent les requêtes utilisateur avec des termes techniques
    # pour améliorer la pertinence de la recherche. La requête originale est
    # conservée intacte, les termes sont AJOUTÉS à la fin.
    #
    # Vocabulaire neutre applicable à tout projet soumis à autorisation ICPE
    # (éolien, photovoltaïque, carrière, industrie, etc.)
    #
    # Pour ajouter des synonymes simples sans redémarrage :
    #   → config/synonyms.txt (rechargeable à chaud via l'UI Admin)
    # =========================================================================
    BUSINESS_RULES = [
        # --- Biodiversité : reformulations complexes multi-termes ---
        (r"\bimpact\s+(sur\s+)?(les?\s+)?(oiseaux|avifaune|especes?\s+avicoles?)",
         "mortalite collision avifaune rapaces migrateurs nicheurs sensibilite",
         "impact_avifaune"),

        (r"\bimpact\s+(sur\s+)?(les?\s+)?(chiropteres?|chauves?[- ]?souris)",
         "mortalite barotraumatisme chiropteres gites corridors activite ultrasonore bridage",
         "impact_chiropteres"),

        (r"\bimpact\s+(sur\s+)?(les?\s+)?(biodiversite|milieu\s+naturel|faune\s+et\s+flore)",
         "habitats especes protegees ZNIEFF Natura 2000 continuites ecologiques trame verte trame bleue",
         "impact_biodiversite"),

        # --- Paysage : co-visibilité, photomontages ---
        (r"\bimpact\s+(sur\s+)?(les?\s+)?paysage",
         "impact paysager co-visibilite photomontage ZVI perception visuelle monuments historiques sites inscrits SPR",
         "impact_paysage"),

        (r"\b(co[- ]?visibilite|intervisibilite|visibilite)",
         "photomontage ZVI zone influence visuelle monuments historiques sites classes sites inscrits ABF",
         "covisibilite"),

        # --- Acoustique ---
        (r"\b(impact|nuisance|emission)s?\s+(acoustique|sonore|bruit)",
         "emergence acoustique recepteur sensible campagne mesure plan bridage habitation riveraine",
         "impact_acoustique"),

        # --- Mesures ERC ---
        (r"\bmesures?\s+(d[e']?\s*)?(evitement|reduction|compensation|ERC)",
         "mesures ERC eviter reduire compenser accompagnement suivi post-implantation",
         "mesures_erc"),

        (r"\b(eviter|reduire|compenser)\b",
         "mesures ERC evitement reduction compensation sequenceERC",
         "sequence_erc"),

        # --- Suivi post-implantation ---
        (r"\bsuivi\s+(post[- ]?implantation|environnemental|ecologique|mortalite)",
         "monitoring mortalite bridage protocole suivi avifaune chiropteres habitats retour experience",
         "suivi_post"),

        # --- Avis MRAe / procédures ---
        (r"\b(avis|recommandation|observation)\s+(MRAe|MRAE|autorite\s+environnementale|AE)",
         "avis autorite environnementale recommandations insuffisances memoire reponse",
         "avis_mrae"),

        (r"\b(memoire|reponse)\s+(en\s+)?(reponse|MRAe|MRAE|autorite)",
         "memoire reponse avis MRAe recommandations justifications complements",
         "memoire_reponse"),

        # --- Étude de dangers ---
        (r"\b(etude|analyse)\s+(de\s+)?(dangers?|risques?)",
         "etude dangers analyse risques scenario accidentel probabilite gravite effets dominos perimetre danger",
         "etude_dangers"),

        # --- Hydrologie / zones humides ---
        (r"\b(zone|milieu)s?\s+humides?",
         "zone humide inventaire pedologie sondage piezometrique fonctionnalite compensation",
         "zones_humides"),

        (r"\b(hydrologi|bassin\s+versant|cours\s+d.eau|nappe)",
         "hydrologie hydrographie bassin versant nappe phreatique qualite eau SDAGE SAGE captage",
         "hydrologie"),
    ]

    # =========================================================================
    # CHEMIN PAR DÉFAUT POUR synonyms.txt
    # =========================================================================
    DEFAULT_SYNONYMS_PATH = "config/synonyms.txt"

    def __init__(self, llm_client=None, enable_llm_fallback: bool = False,
                 synonyms_path: str = None):
        """
        Initialise le Query Rewriter.

        Args:
            llm_client: Client LLM optionnel pour le fallback intelligent
            enable_llm_fallback: Active le fallback LLM si aucune règle ne matche
            synonyms_path: Chemin vers synonyms.txt (défaut: config/synonyms.txt)
        """
        self.llm_client = llm_client
        self.enable_llm_fallback = enable_llm_fallback and llm_client is not None
        self.synonyms_path = synonyms_path or self.DEFAULT_SYNONYMS_PATH

        # Compiler les regex pour performance
        self._compiled_rules = [
            (re.compile(pattern, re.IGNORECASE), replacement, desc)
            for pattern, replacement, desc in self.BUSINESS_RULES
        ]

        # Charger les synonymes depuis synonyms.txt
        self._synonym_groups: List[Set[str]] = []
        self._term_to_group: Dict[str, int] = {}
        self._load_synonyms()

        logger.info(
            f"QueryRewriter initialisé avec {len(self._compiled_rules)} règles, "
            f"{len(self._synonym_groups)} groupes de synonymes, "
            f"LLM fallback: {self.enable_llm_fallback}"
        )

    # =========================================================================
    # CHARGEMENT DES SYNONYMES DEPUIS synonyms.txt
    # =========================================================================

    def _load_synonyms(self):
        """
        Charge les groupes de synonymes depuis synonyms.txt.

        Format du fichier :
            # Commentaire
            terme1, terme2, terme3   (bidirectionnel)

        Chaque ligne non vide et non commentée définit un groupe de synonymes.
        Quand un terme du groupe est trouvé dans la requête, les AUTRES termes
        du groupe sont ajoutés pour enrichir la recherche.
        """
        self._synonym_groups = []
        self._term_to_group = {}

        # Résoudre le chemin (absolu ou relatif)
        path = self.synonyms_path
        if not os.path.isabs(path):
            # Essayer depuis le répertoire de travail, puis /app/config
            candidates = [
                path,
                os.path.join("/app", path),
                os.path.join("/app/config", "synonyms.txt"),
            ]
            for candidate in candidates:
                if os.path.exists(candidate):
                    path = candidate
                    break

        if not os.path.exists(path):
            logger.info(f"Fichier synonymes non trouvé: {path} (fonctionnement sans synonymes)")
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()

                    # Ignorer lignes vides et commentaires
                    if not line or line.startswith("#"):
                        continue

                    # Parser la ligne : "terme1, terme2, terme3"
                    terms = [t.strip().lower() for t in line.split(",") if t.strip()]

                    if len(terms) < 2:
                        continue  # Un synonyme seul n'a pas de sens

                    # Créer le groupe
                    group_idx = len(self._synonym_groups)
                    term_set = set(terms)
                    self._synonym_groups.append(term_set)

                    # Indexer chaque terme vers son groupe
                    for term in terms:
                        if term in self._term_to_group:
                            # Terme déjà dans un autre groupe → fusionner
                            existing_idx = self._term_to_group[term]
                            self._synonym_groups[existing_idx] |= term_set
                            # Réindexer les nouveaux termes vers le groupe existant
                            for t in term_set:
                                self._term_to_group[t] = existing_idx
                            # Vider le nouveau groupe (déjà fusionné)
                            self._synonym_groups[group_idx] = set()
                            break
                        else:
                            self._term_to_group[term] = group_idx

            # Compter les groupes non vides
            active_groups = [g for g in self._synonym_groups if g]
            logger.info(
                f"Synonymes chargés: {len(active_groups)} groupes, "
                f"{len(self._term_to_group)} termes indexés depuis {path}"
            )

        except Exception as e:
            logger.warning(f"Erreur chargement synonymes {path}: {e}")

    def reload_synonyms(self):
        """Recharge les synonymes depuis le fichier (pour hot-reload)."""
        logger.info("🔄 Rechargement des synonymes...")
        self._load_synonyms()

    def _apply_synonyms(self, query: str) -> Tuple[str, List[str]]:
        """
        Enrichit la requête avec les synonymes trouvés dans synonyms.txt.

        Stratégie : pour chaque mot/expression de la requête qui correspond
        à un terme indexé, ajouter les AUTRES termes du même groupe.

        Args:
            query: Requête utilisateur

        Returns:
            Tuple (requête enrichie, liste des groupes activés)
        """
        if not self._synonym_groups:
            return query, []

        query_lower = query.lower()
        enrichment_terms = []
        activated_groups = []
        seen_groups: Set[int] = set()

        # Trier les termes par longueur décroissante pour matcher les expressions longues d'abord
        sorted_terms = sorted(self._term_to_group.keys(), key=len, reverse=True)

        for term in sorted_terms:
            # Vérifier si le terme apparaît dans la requête (mot entier ou expression)
            # Utiliser \b pour les mots simples, recherche directe pour les expressions
            if " " in term:
                # Expression multi-mots : recherche directe
                found = term in query_lower
            else:
                # Mot simple : recherche avec frontière de mot
                found = bool(re.search(r"\b" + re.escape(term) + r"\b", query_lower))

            if found:
                group_idx = self._term_to_group[term]

                # Éviter de traiter le même groupe plusieurs fois
                if group_idx in seen_groups:
                    continue
                seen_groups.add(group_idx)

                group = self._synonym_groups[group_idx]
                if not group:
                    continue

                # Ajouter les termes du groupe qui ne sont PAS déjà dans la requête
                new_terms = [t for t in group if t not in query_lower]
                if new_terms:
                    enrichment_terms.extend(new_terms)
                    activated_groups.append(f"syn:{term}")

        if enrichment_terms:
            enriched = f"{query} {' '.join(enrichment_terms)}"
            return enriched, activated_groups

        return query, []

    # =========================================================================
    # DÉTECTION DU TYPE DE REQUÊTE
    # =========================================================================

    def detect_query_type(self, query: str) -> str:
        """
        Détecte le type de requête pour adapter la stratégie.

        Types reconnus :
        - 'folder_search' : cherche un dossier ou fichier (chemin complet)
        - 'folder_listing' : demande le contenu d'un dossier
        - 'content_search' : cherche un type de fichier (cartes, figures, annexes, etc.)
        - 'general' : requête métier générale

        Args:
            query: Requête utilisateur

        Returns:
            str: Type de requête ('folder_search', 'folder_listing', 'content_search', ou 'general')
        """
        query_lower = query.lower()

        # PRIORITÉ 0 : Si la requête demande une analyse/résumé → c'est du "general"
        # (même si elle mentionne des mots-clés de contenu comme "étude d'impact")
        general_indicators = [
            "résume", "resume", "résumer",
            "explique", "expliquer",
            "quels sont les impacts", "quel est l'impact",
            "que dit", "que disent",
            "quelles sont les mesures", "quelles sont les conclusions",
            "synthèse", "synthétise",
            "compare", "comparer",
            "décris", "décrire", "description",
            "quels sont les enjeux", "quel est l'enjeu",
            "quels sont les risques", "quels sont les effets",
            "comment est traité", "comment sont traités",
        ]
        if any(indicator in query_lower for indicator in general_indicators):
            logger.debug(f"General query detected (analysis/summary verb): '{query}'")
            return "general"

        # PRIORITÉ 1 : Recherche de TYPE de fichier (cartes, figures, annexes, etc.)
        # Uniquement les types de fichiers/ressources SPÉCIFIQUES, pas les termes métier
        content_keywords = [
            "carte",
            "cartes",
            "plan",
            "plans",
            "profil en long",
            "profil en travers",
            "figure",
            "figures",
            "schéma",
            "schémas",
            "photo",
            "photos",
            "annexe",
            "annexes",
            "tableau",
            "tableaux",
        ]
        if any(keyword in query_lower for keyword in content_keywords):
            logger.debug(f"Content search detected: keyword found in '{query}'")
            return "content_search"

        # PRIORITÉ 2 : Détection folder_search (chemin avec \ ou racine connue)
        if "\\" in query:
            return "folder_search"

        # PRIORITÉ 3 : Détection folder_listing (contenu d'un dossier SPÉCIFIQUE nommé)
        # Doit mentionner un nom de dossier connu
        folder_names = [
            "annexes",
            "archives",
            "documents",
            "rapports",
            "comptes rendus",
            "modèles",
            "templates",
            "procédures",
            "notes",
            "courriers",
        ]
        folder_listing_patterns = [
            r"(?:contenu|fichiers|sous-dossiers?)\s+(?:du|de|dans)\s+(?:dossier\s+)?(\d+\s+)?([a-zA-ZéèêëàâäùûüôöîïÉÈÊËÀÂÄÙÛÜÔÖÎÏ\s]+)",
            r"(?:lister|afficher|quels sont)\s+(?:les\s+)?(?:fichiers|dossiers|sous-dossiers)\s+(?:du|de|dans)",
        ]

        # Vérifier si un nom de dossier spécifique est mentionné
        has_folder_name = any(folder in query_lower for folder in folder_names)
        has_listing_pattern = any(re.search(p, query_lower) for p in folder_listing_patterns)

        if has_folder_name and has_listing_pattern:
            return "folder_listing"

        # Mots-clés de navigation
        if any(
            keyword in query_lower
            for keyword in ["dossier", "folder", "répertoire", "arborescence", "chemin"]
        ) and has_folder_name:
            return "folder_listing"

        # Requête générale (métier)
        return "general"

    # =========================================================================
    # REWRITE POUR RECHERCHE DE DOSSIERS
    # =========================================================================

    def rewrite_for_folder_search(self, query: str) -> List[str]:
        """
        Reformule une recherche de dossier en multi-requêtes.

        Stratégie :
        1. Requête exacte (chemin complet)
        2. Dernier segment du chemin
        3. Tous les segments du chemin
        4. Mots-clés extraits
        5. Variantes sans accents

        Args:
            query: Chemin du dossier (ex: "Etudes d'impact\\Avifaune")

        Returns:
            List[str]: Liste de requêtes reformulées (3-5 requêtes)
        """
        rewritten_queries = []

        # 1. Requête exacte
        rewritten_queries.append(query)

        # 2. Extraire les segments du chemin
        segments = re.split(r"[\\\/]", query)
        segments = [s.strip() for s in segments if s.strip()]

        # 3. Dernier segment (le plus spécifique)
        if segments:
            rewritten_queries.append(segments[-1])

        # 4. Tous les segments combinés
        if segments:
            all_segments = " ".join(segments)
            rewritten_queries.append(all_segments)

        # 5. Mots-clés extraits (minuscules, sans numéros)
        keywords = self._extract_keywords_from_path(query)
        if keywords:
            rewritten_queries.append(keywords)

        # 6. Variantes sans accents/tirets
        normalized = self._normalize_path(query)
        if normalized != query:
            rewritten_queries.append(normalized)

        # Dédupliquer en gardant l'ordre
        seen = set()
        unique_queries = []
        for q in rewritten_queries:
            if q not in seen and q.strip():
                seen.add(q)
                unique_queries.append(q)

        logger.info(
            f"📝 Requêtes reformulées pour dossier ({len(unique_queries)} variantes):"
        )
        for i, q in enumerate(unique_queries, 1):
            logger.info(f"   {i}. {q}")

        return unique_queries

    def _extract_keywords_from_path(self, path: str) -> str:
        """
        Extrait les mots-clés pertinents d'un chemin de dossier.

        Args:
            path: Chemin du dossier

        Returns:
            str: Mots-clés extraits et combinés
        """
        # Enlever les numéros de dossiers (01, 02, etc.)
        path = re.sub(r"^\d+\s+", "", path)
        path = re.sub(r"\s+\d+\s+", " ", path)

        # Enlever les caractères spéciaux
        path = path.replace("\\", " ").replace("/", " ").replace("-", " ")

        # Splitter et filtrer
        stopwords = {"le", "la", "les", "un", "une", "des", "et", "ou", "de", "du", "à", "au"}
        words = [
            w.lower()
            for w in path.split()
            if w.lower() not in stopwords and len(w) > 2
        ]

        # Retirer les doublons
        unique_words = []
        seen = set()
        for w in words:
            if w not in seen:
                unique_words.append(w)
                seen.add(w)

        return " ".join(unique_words[:7])  # Max 7 mots

    def _normalize_path(self, path: str) -> str:
        """
        Normalise un chemin (enlève accents, standardise séparateurs).

        Args:
            path: Chemin à normaliser

        Returns:
            str: Chemin normalisé
        """
        import unicodedata

        # Enlever les accents
        nfd = unicodedata.normalize("NFD", path)
        normalized = "".join(c for c in nfd if unicodedata.category(c) != "Mn")

        # Standardiser les séparateurs (tout en espaces pour la recherche)
        normalized = normalized.replace("\\", " ").replace("/", " ")
        # Nettoyer les espaces multiples
        normalized = re.sub(r"\s+", " ", normalized).strip()

        return normalized

    # =========================================================================
    # REWRITE POUR LISTAGE DE DOSSIER
    # =========================================================================

    def rewrite_for_folder_listing(self, query: str) -> List[str]:
        """
        Reformule une requête de listage de dossier.

        Args:
            query: Requête de listage

        Returns:
            List[str]: Requêtes reformulées pour listage
        """
        patterns = [
            r"(?:dossier|folder|répertoire)\s+([^.!?]+)",  # "dossier X"
            r"(?:contenu|fichiers)\s+(?:du|de|dans)\s+(?:dossier|folder)?\s*([^.!?]+)",  # "contenu du X"
            r"([A-Za-z0-9\s\\\/]+)\s+(?:fichiers|dossiers|contenu)",  # "X fichiers"
        ]

        extracted_name = None
        for pattern in patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                extracted_name = match.group(1).strip()
                break

        if extracted_name:
            # Reformuler comme recherche de dossier
            return self.rewrite_for_folder_search(extracted_name)
        else:
            # Fallback : utiliser la requête originale
            return [query]

    # =========================================================================
    # REWRITE POUR RECHERCHE DE CONTENU (cartes, figures, annexes, etc.)
    # =========================================================================

    def rewrite_for_content_search(self, query: str) -> List[str]:
        """
        Reformule une recherche de contenu (cartes, photos, figures, annexes, etc.)

        Stratégie SIMPLIFIÉE :
        - Nettoyage léger de la requête (garder les mots-clés)
        - Laisser le RAG hybride (BM25 + kNN) faire son travail
        - Enrichissement optionnel sans troncation

        Args:
            query: Requête de recherche de contenu

        Returns:
            List[str]: Requêtes reformulées optimisées
        """
        rewritten_queries = []

        # === ÉTAPE 1 : Nettoyage léger (SANS tronquer les mots-clés) ===
        clean_query = query.lower()

        # Enlever le contexte de conversation
        clean_query = re.sub(r"\(contexte:.*?\)", "", clean_query)

        # Enlever les formules de politesse et verbes introductifs
        clean_query = re.sub(
            r"^(peux tu|peut on|pouvez vous|est-ce que|y a-t-il|il y a t.il|"
            r"où|comment|je cherche|je veux|je voudrais|montre moi|"
            r"liste|lister|trouver|cherche|affiche|donne moi)\s+",
            "",
            clean_query,
            flags=re.IGNORECASE,
        )

        # Enlever les articles et mots de liaison au début
        clean_query = re.sub(r"^(les|le|la|l'|un|une|des|du|de la)\s+", "", clean_query)

        # Enlever les fins de phrase
        clean_query = re.sub(
            r"\s+(se trouve|sont|est|s'il vous plait|svp)\s*[?.!]*$",
            "",
            clean_query,
        )
        clean_query = clean_query.strip(" ?.!")

        # Requête nettoyée comme requête principale
        if clean_query:
            rewritten_queries.append(clean_query)

        # === ÉTAPE 2 : Enrichissement optionnel (AJOUTER, pas remplacer) ===
        query_lower = clean_query.lower()

        if "carte" in query_lower or "cartes" in query_lower:
            rewritten_queries.append(f"{clean_query} cartes plans cartographie")

        if "plan" in query_lower or "plans" in query_lower:
            rewritten_queries.append(f"{clean_query} plans cartographie")

        if "photo" in query_lower or "photos" in query_lower:
            rewritten_queries.append(f"{clean_query} photos photomontages")

        if "figure" in query_lower or "figures" in query_lower:
            rewritten_queries.append(f"{clean_query} figures schémas annexes")

        if "annexe" in query_lower or "annexes" in query_lower:
            rewritten_queries.append(f"{clean_query} annexes tableaux figures")

        # === ÉTAPE 3 : Dédupliquer ===
        seen = set()
        unique_queries = []
        for q in rewritten_queries:
            q_clean = q.strip()
            if q_clean and q_clean.lower() not in seen:
                seen.add(q_clean.lower())
                unique_queries.append(q_clean)

        logger.info(
            f"📝 Requêtes reformulées pour content_search ({len(unique_queries)} variantes):"
        )
        for i, q in enumerate(unique_queries, 1):
            logger.info(f"   {i}. {q}")

        return unique_queries if unique_queries else [query]

    # =========================================================================
    # INTERFACE PRINCIPALE
    # =========================================================================

    def rewrite(self, query: str) -> Tuple[List[str], str, bool]:
        """
        Reformule une requête utilisateur.

        Stratégie adaptée selon le type de requête :
        - folder_search : multi-query avec variantes du chemin
        - folder_listing : recherche du dossier mentionné
        - content_search : recherche de ressources (cartes, photos, annexes)
        - general : règles métier + fallback LLM

        Args:
            query: Requête originale

        Returns:
            Tuple contenant :
            - List[str] : Liste des requêtes reformulées
            - str : Type de requête détecté ('folder_search', 'folder_listing', 'content_search', 'general')
            - bool : True si modification, False sinon
        """
        if not query or not query.strip():
            return [query], "general", False

        original_query = query.strip()
        query_type = self.detect_query_type(original_query)

        logger.debug(f"🔍 Détection type requête: {query_type}")
        logger.debug(f"   Requête: {original_query}")

        # Stratégie selon le type
        if query_type == "folder_search":
            rewritten_queries = self.rewrite_for_folder_search(original_query)
            return rewritten_queries, query_type, True

        elif query_type == "folder_listing":
            rewritten_queries = self.rewrite_for_folder_listing(original_query)
            return rewritten_queries, query_type, True

        elif query_type == "content_search":
            rewritten_queries = self.rewrite_for_content_search(original_query)
            return rewritten_queries, query_type, True

        else:  # general
            # Appliquer les règles métier
            rewritten, rule_applied = self._apply_rules(original_query)

            if rewritten != original_query:
                logger.info(
                    f"Query rewriting (règle '{rule_applied}'): "
                    f"'{original_query}' → '{rewritten}'"
                )
                return [rewritten], query_type, True

            # Fallback LLM si activé
            if self.enable_llm_fallback:
                llm_rewritten = self._llm_rewrite(original_query)
                if llm_rewritten and llm_rewritten != original_query:
                    logger.info(
                        f"Query rewriting (LLM): "
                        f"'{original_query}' → '{llm_rewritten}'"
                    )
                    return [llm_rewritten], query_type, True

            # Aucune modification
            return [original_query], query_type, False

    def _apply_rules(self, query: str) -> Tuple[str, Optional[str]]:
        """
        Applique les règles métier ET les synonymes à la requête.

        Stratégie : garder la requête originale INTACTE et AJOUTER les
        mots-clés d'enrichissement à la fin. Cela préserve la précision
        BM25 tout en élargissant le rappel sémantique (dense search).

        Ordre d'application :
        1. BUSINESS_RULES (regex → enrichissement technique lourd)
        2. synonyms.txt (termes → synonymes légers bidirectionnels)

        Args:
            query: Requête originale

        Returns:
            Tuple (requête_modifiée, nom_règle_appliquée)
        """
        enrichment_terms = []
        applied_rules = []

        # === 1. Appliquer les BUSINESS_RULES ===
        for compiled_pattern, replacement, description in self._compiled_rules:
            if compiled_pattern.search(query):
                enrichment_terms.append(replacement)
                applied_rules.append(description)

        # === 2. Appliquer les synonymes de synonyms.txt ===
        # On passe la requête enrichie (ou originale) pour que les synonymes
        # ne re-détectent pas des termes déjà ajoutés par les BUSINESS_RULES
        current_query = f"{query} {' '.join(enrichment_terms)}" if enrichment_terms else query
        synonym_enriched, synonym_groups = self._apply_synonyms(current_query)

        if synonym_groups:
            # Extraire seulement les termes ajoutés par les synonymes
            # (tout ce qui est après current_query)
            synonym_addition = synonym_enriched[len(current_query):].strip()
            if synonym_addition:
                enrichment_terms.append(synonym_addition)
                applied_rules.extend(synonym_groups)

        if enrichment_terms:
            # Garder la requête originale + ajouter tous les enrichissements
            enriched = f"{query} {' '.join(enrichment_terms)}"
            rule_names = "+".join(applied_rules)
            return enriched, rule_names

        return query, None

    def _llm_rewrite(self, query: str) -> Optional[str]:
        """
        Utilise le LLM pour reformuler une requête complexe.

        Args:
            query: Requête originale

        Returns:
            str: Requête reformulée par le LLM, ou None si erreur
        """
        if not self.llm_client:
            return None

        try:
            prompt = f"""Tu es un expert en reformulation de requêtes pour un système de recherche
documentaire. Reformule cette question pour optimiser la recherche documentaire.
Ajoute des synonymes et mots-clés techniques pertinents en gardant le sens original.

Réponds UNIQUEMENT avec la requête reformulée, sans explication.

Question originale: {query}

Requête reformulée:"""

            response = self.llm_client.generate(prompt, max_tokens=100)

            if response and response.strip():
                # Nettoyer la réponse
                rewritten = response.strip().split("\n")[0]
                return rewritten

            return None

        except Exception as e:
            logger.warning(f"Erreur LLM rewrite: {e}")
            return None

    # =========================================================================
    # API DE GESTION DES RÈGLES
    # =========================================================================

    def add_rule(self, pattern: str, replacement: str, description: str = "custom"):
        """
        Ajoute une règle métier dynamiquement.

        Args:
            pattern: Regex pattern à matcher
            replacement: Texte de remplacement (peut utiliser \\1, \\2, etc.)
            description: Description de la règle

        Raises:
            ValueError: Si le pattern regex est invalide
        """
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
            self._compiled_rules.append((compiled, replacement, description))
            logger.info(f"Règle ajoutée: '{description}' - {pattern}")
        except re.error as e:
            logger.error(f"Erreur regex invalide: {e}")
            raise ValueError(f"Pattern regex invalide: {e}")


# ============================================================================
# SINGLETON PATTERN
# ============================================================================

_rewriter_instance: Optional[QueryRewriter] = None


def get_query_rewriter(llm_client=None, enable_llm_fallback: bool = False) -> QueryRewriter:
    """
    Retourne une instance singleton du QueryRewriter.

    Args:
        llm_client: Client LLM optionnel
        enable_llm_fallback: Active le fallback LLM

    Returns:
        QueryRewriter: Instance singleton
    """
    global _rewriter_instance

    if _rewriter_instance is None:
        _rewriter_instance = QueryRewriter(
            llm_client=llm_client,
            enable_llm_fallback=enable_llm_fallback,
        )

    return _rewriter_instance
