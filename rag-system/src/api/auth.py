# -*- coding: utf-8 -*-
"""
Auth Module — Authentification DESACTIVEE (version développement / accès physique)

Ce module remplace le module d'auth original pour supprimer toute
authentification. Toutes les fonctions retournent des valeurs passantes.

Pour réactiver l'authentification, supprimer ce fichier override et
redémarrer les conteneurs.
"""

import os
from typing import Optional

AUTH_COOKIE_NAME = "luciole_admin"
AUTH_COOKIE_MAX_AGE = 86400


def verify_credentials(username: str, password: str) -> bool:
    return True


def make_session_token(username: str) -> str:
    return f"{username}:0:noauth"


def validate_session_token(token: str) -> Optional[str]:
    return "admin"


def get_login_html(error: str = "") -> str:
    return '<html><body><script>window.location="/"</script></body></html>'
