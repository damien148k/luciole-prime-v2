# Validation fonctionnelle — Module mail Luciole Prime + luciole-mail

Plan de recette complet pour valider le workflow email de bout en bout.

---

## 1. Pré-requis

- [ ] Stack Docker Luciole démarrée (`docker-compose --profile gpu up -d`)
- [ ] Container `luciole-mail` en statut `healthy`
- [ ] Comptes de test initialisés (`docker exec luciole-mail-<INSTANCE> /bin/sh /init/init-accounts.sh`)
- [ ] Thunderbird installé sur le poste du testeur
- [ ] Module mail configuré dans l'UI (`/config` → onglet Mail → Preset luciole-mail local → Sauvegarder)
- [ ] Module mail **activé** (toggle "Module mail activé" = On)

---

## 2. Comptes et clients de test

| Compte | Mot de passe | Utilisé par | Client |
|---|---|---|---|
| `luciole@local.lan` | `luciole2024` | Luciole (IMAP + SMTP) | Module mail Python |
| `testeur@local.lan` | `testeur2024` | Testeur humain | Thunderbird |

**Configuration Thunderbird pour `testeur@local.lan` :**
- IMAP : `<IP_SERVEUR>:143`, pas de TLS
- SMTP : `<IP_SERVEUR>:25`, pas de TLS, auth : mot de passe normal

---

## 3. Tests de connectivité

### 3.1 Vérification des ports (depuis l'hôte)

```bash
# SMTP accessible
Test-NetConnection -ComputerName localhost -Port 25

# IMAP accessible
Test-NetConnection -ComputerName localhost -Port 143

# Admin web accessible
Invoke-WebRequest http://localhost:8025 -UseBasicParsing | Select StatusCode
```

Attendu : `TcpTestSucceeded: True` pour 25 et 143, `StatusCode: 200` pour 8025.

### 3.2 Test IMAP depuis Docker (module Luciole)

```bash
docker exec luciole-feedback-watcher python -c "
import imaplib
m = imaplib.IMAP4('mail', 143)
m.login('luciole@local.lan', 'luciole2024')
m.select('INBOX')
_, msgs = m.search(None, 'ALL')
print('IMAP OK —', len(msgs[0].split()), 'message(s)')
m.logout()
"
```
Attendu : `IMAP OK — N message(s)` (pas d'exception).

### 3.3 Test SMTP depuis Docker

```bash
docker exec luciole-feedback-watcher python -c "
import smtplib
s = smtplib.SMTP('mail', 25, timeout=5)
s.sendmail('test@local.lan', 'luciole@local.lan', 'Subject: Test SMTP\n\nTest.')
print('SMTP OK')
s.quit()
"
```
Attendu : `SMTP OK` (pas d'exception).

### 3.4 Boutons de test dans l'UI

- Aller sur `http://localhost:8503/config` → onglet **📧 Mail**
- Cliquer **📥 Tester IMAP** → attendu : `IMAP : ✅ LOGIN OK — INBOX: N messages`
- Cliquer **📤 Tester SMTP** → attendu : `SMTP : ✅ EHLO + AUTH OK`
- Saisir `testeur@local.lan`, cliquer **📧 Envoyer un mail de test**
  → attendu : `✅ Email envoyé à testeur@local.lan`
  → Thunderbird reçoit l'email de test dans la boîte `testeur@local.lan`

---

## 4. Test réception d'un mail entrant

1. Dans Thunderbird, rédiger un nouvel email :
   - **À :** `luciole@local.lan`
   - **Sujet :** `Question sur la procédure de congés`
   - **Corps :** `Bonjour, pouvez-vous m'indiquer la procédure pour poser des congés ?`
2. Envoyer
3. Vérifier dans l'admin Stalwart (`http://localhost:8025`) que le message est arrivé dans `luciole@local.lan`

Attendu : l'email est visible dans la boîte `luciole@local.lan`.

---

## 5. Test détection par Luciole

Luciole poll la boîte toutes les 60 secondes. Pour forcer immédiatement :

```bash
curl -X POST http://localhost:8503/api/mail/sync
```

Réponse attendue : `{"received": 1, "processed": 1, "errors": 0, "skipped": 0}`

Vérification dans les logs :
```bash
docker logs luciole-feedback-watcher --tail 20 | grep -i "email_received\|classified"
```

---

## 6. Test génération de brouillon

Après la sync, aller sur `http://localhost:8503/mail/drafts`.

Attendu :
- [ ] Le brouillon est visible avec le sujet de l'email reçu
- [ ] L'expéditeur (`testeur@local.lan`) est affiché
- [ ] La réponse proposée par Luciole est présente
- [ ] Les sources RAG utilisées sont listées
- [ ] Un score de confiance et un score de risque sont affichés
- [ ] La raison de la décision de brouillon est indiquée

---

## 7. Test validation humaine

Depuis `/mail/drafts` ou `/config` → onglet Mail :

### Cas 1 : Approbation sans modification
- Cliquer **✅ Approuver**
- Attendu : brouillon disparaît de la liste, message de confirmation

### Cas 2 : Approbation avec modification
- Modifier le texte de la réponse dans la zone de texte
- Cliquer **✏️ Modifier + Approuver**
- Attendu : le texte modifié est envoyé (pas l'original)

### Cas 3 : Rejet
- Cliquer **❌ Rejeter**, saisir une raison
- Attendu : brouillon disparaît, aucun email envoyé, status `rejected` en audit

---

## 8. Test envoi de la réponse

Après approbation, vérifier :

```bash
# Statut du message sortant
curl http://localhost:8503/api/mail/messages | python -m json.tool | grep status
```

Attendu : au moins un message avec `"status": "sent"`.

Vérification dans les logs :
```bash
docker logs luciole-feedback-watcher --tail 20 | grep "email_sent"
```

---

## 9. Test lecture côté testeur

Dans Thunderbird, relever les emails de `testeur@local.lan` (IMAP).

Attendu :
- [ ] L'email de réponse de `luciole@local.lan` est reçu
- [ ] Le sujet commence par `Re: Question sur la procédure de congés`
- [ ] Le corps contient la réponse de Luciole
- [ ] Le header `In-Reply-To` référence le Message-ID de l'email original

---

## 10. Tests d'erreur

| Scénario | Comment simuler | Comportement attendu |
|---|---|---|
| `luciole-mail` arrêté | `docker stop luciole-mail-watcher` | Bouton test IMAP → `❌ TIMEOUT` ou `CONNECTION_REFUSED`. Module continue de tourner, erreur dans `/mail/errors`. |
| Mauvais mot de passe | Changer le mdp dans l'UI | Bouton test IMAP → `❌ AUTH_FAILED` |
| Email vers compte inexistant | Envoyer à `inconnu@local.lan` | Email en quarantaine ou rebond SMTP selon config |
| Auto-reply envoyé à Luciole | Thunderbird : réponse automatique simulée | Luciole détecte l'auto-reply, met en quarantaine, **n'appelle pas le RAG** |
| RAG sans résultat | Question hors base documentaire | Brouillon créé avec mention "contexte insuffisant", guardrail déclenché |

---

## 11. Critères d'acceptation V1

### Infrastructure
- [ ] `luciole-mail` démarre sans erreur et atteint le statut `healthy`
- [ ] Ports 25 et 143 répondent sur le LAN
- [ ] Admin web accessible sur `http://localhost:8025`
- [ ] Comptes `luciole@local.lan` et `testeur@local.lan` créés et fonctionnels
- [ ] Les emails survivent à `docker restart luciole-mail-watcher`

### Module Luciole
- [ ] Test IMAP retourne ✅ dans l'UI
- [ ] Test SMTP retourne ✅ dans l'UI
- [ ] Mail de test reçu dans `testeur@local.lan`
- [ ] Sync IMAP détecte un email entrant
- [ ] Brouillon créé avec réponse RAG, sources et scores
- [ ] Approbation déclenche l'envoi SMTP
- [ ] Rejet ferme le brouillon sans envoi
- [ ] Email de réponse reçu dans Thunderbird avec `Re:` dans le sujet

### Robustesse
- [ ] Doublon ignoré (même email envoyé deux fois → 1 brouillon)
- [ ] Auto-reply mis en quarantaine sans appel RAG
- [ ] Erreur SMTP → retry automatique visible dans `/mail/errors`
- [ ] Module mail désactivable depuis l'UI sans redémarrage

---

## 12. Régressions à surveiller

Après chaque modification du module mail, revérifier :

- [ ] La page `/config` onglet Mail se charge sans erreur 500
- [ ] Le badge du header (nombre de brouillons) se met à jour
- [ ] Les autres onglets (`settings.yaml`, `prompts.yaml`, etc.) fonctionnent toujours
- [ ] Le Chat (`http://localhost:8503`) répond normalement
- [ ] Le Dashboard feedbacks (`http://localhost:8503/feedbacks`) affiche les données
- [ ] `GET /api/mail/health` retourne un JSON valide
- [ ] Les 43 tests pytest passent : `pytest rag-system/tests/mail/ -v`
