# Contribuer à Luciole

Merci de votre intérêt pour Luciole. Ce projet vise à offrir une alternative souveraine aux IA cloud, et chaque contribution compte.

## Code de conduite

Soyez respectueux, constructif et bienveillant. Les comportements toxiques ne sont pas tolérés.

## Comment contribuer

### Signaler un bug

Ouvrez une [issue](../../issues) en précisant :
- Version de Luciole
- Version de Docker / système d'exploitation
- Étapes pour reproduire
- Comportement attendu vs observé
- Logs pertinents (anonymisés)

### Proposer une fonctionnalité

Ouvrez une issue avec le label `enhancement` décrivant :
- Le besoin métier ou technique
- La solution proposée
- Les alternatives envisagées

### Soumettre du code

1. **Fork** le dépôt
2. Créez une **branche** descriptive : `git checkout -b feature/connecteur-sharepoint`
3. **Codez** en respectant le style du projet (voir ci-dessous)
4. **Testez** : `pytest` doit passer
5. **Committez** avec des messages clairs en français ou anglais
6. **Push** et ouvrez une **Pull Request** vers `main`

### Style de code

- **Python** : suivez [PEP 8](https://peps.python.org/pep-0008/), utilisez `black` et `ruff`
- **Type hints** obligatoires sur les fonctions publiques
- **Docstrings** au format Google ou NumPy
- **Commits** : format conventionnel recommandé (`feat:`, `fix:`, `docs:`, `refactor:`...)

```bash
# Avant de committer
black .
ruff check . --fix
pytest
```

## Licence des contributions

En soumettant une PR, vous acceptez que votre contribution soit distribuée sous la même licence que le projet (**AGPL-3.0**).

## Questions

Pour toute question : [contact@148kprod.com](mailto:contact@148kprod.com)
