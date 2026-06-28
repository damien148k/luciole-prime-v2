# Politique de sécurité

## Versions supportées

Les versions suivantes de Luciole reçoivent des mises à jour de sécurité :

| Version | Support |
|---------|---------|
| 4.x     | ✅ Active |
| < 4.0   | ❌ Non supportée |

## Signaler une vulnérabilité

La sécurité de Luciole est une priorité. Si vous découvrez une vulnérabilité, **merci de ne pas l'ouvrir publiquement en issue GitHub**.

### Canal privé

Envoyez un email à : **security@148kprod.com**

Incluez si possible :
- Une description de la vulnérabilité
- Les étapes pour la reproduire
- L'impact potentiel
- Vos coordonnées (pour le suivi)

### Engagement de réponse

| Étape | Délai |
|---|---|
| Accusé de réception | sous 48 heures ouvrées |
| Évaluation initiale | sous 7 jours |
| Correctif et publication | selon criticité (jours à semaines) |

### Programme de divulgation responsable

Nous nous engageons à :
- Vous tenir informé de l'avancement
- Créditer publiquement votre découverte (sauf souhait contraire)
- Ne pas engager d'action légale contre les chercheurs agissant de bonne foi

## Bonnes pratiques de déploiement

Pour une installation Luciole sécurisée :

- **Changez le mot de passe admin par défaut** dès la première connexion
- **N'exposez pas les ports** (`8000`, `8080`, `8501`, `9200`, etc.) sur internet sans VPN ou reverse proxy avec authentification
- **Activez HTTPS** via un reverse proxy (Nginx, Traefik, Caddy) en production
- **Limitez les accès** à l'interface admin par IP ou VPN
- **Mettez à jour régulièrement** les images Docker et le code Luciole
- **Sauvegardez** régulièrement vos données et configurations
- **Auditez les logs** dans `data/logs/`

## Conformité

Luciole est conçu pour respecter le **RGPD** :
- Toutes les données restent sur votre infrastructure
- Aucune donnée n'est transmise à des tiers
- Les logs ne contiennent pas de données utilisateur sensibles par défaut

Pour les déploiements en environnement régulé (santé, défense, OIV), un audit de sécurité par un tiers est recommandé.

---

Merci de contribuer à la sécurité de Luciole.
