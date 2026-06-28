# luciole-mail — Serveur mail local de test

Serveur SMTP + IMAP local pour le développement et la validation du module mail de Luciole Prime.
Basé sur [Stalwart Mail](https://stalw.art).

**Ce n'est pas un serveur mail de production.** Il ne route rien vers Internet.

---

## Démarrage

```bash
# Depuis C:\RAG\luciole-watcher (ou le dossier de votre instance)
docker-compose --profile gpu up -d luciole-mail

# Vérifier que le container est healthy
docker ps --filter "name=luciole-mail" --format "{{.Names}} {{.Status}}"
```

---

## Initialisation des comptes (une seule fois)

Après le premier démarrage (attendre le statut `healthy`) :

```bash
docker exec luciole-mail-watcher /bin/sh /init/init-accounts.sh
```

Comptes créés :

| Adresse | Mot de passe | Rôle |
|---|---|---|
| `luciole@local.lan` | `luciole2024` | Boîte lue par Luciole en IMAP |
| `testeur@local.lan` | `testeur2024` | Boîte du testeur (Thunderbird) |
| `demo@local.lan` | `demo2024` | Optionnel pour les démos |

---

## Interface d'administration web

```
http://localhost:8025
Login : admin
Mot de passe : admin_luciole_2024
```

Permet de : créer/supprimer des comptes, voir les boîtes, consulter les logs, configurer les domaines.

---

## Paramètres à saisir dans l'UI Luciole

Aller sur `http://localhost:8503/config` → onglet **📧 Mail** → bouton **Preset local test**.

Ou saisir manuellement :

| Champ | Valeur |
|---|---|
| IMAP Hôte | `mail` |
| IMAP Port | `143` |
| IMAP SSL/TLS | Non |
| IMAP Utilisateur | `luciole@local.lan` |
| IMAP Mot de passe | `luciole2024` |
| IMAP Dossier | `INBOX` |
| IMAP Polling | `60` secondes |
| SMTP Hôte | `mail` |
| SMTP Port | `25` |
| SMTP TLS | Non |
| SMTP Utilisateur | `luciole@local.lan` |
| SMTP Mot de passe | `luciole2024` |
| Nom affiché | `Luciole — Assistant documentaire` |
| Adresse expéditeur | `luciole@local.lan` |

---

## Configuration Thunderbird (côté testeur)

Créer un compte `testeur@local.lan` avec les paramètres suivants :

**Réception (IMAP) :**
- Serveur : `<IP_DU_SERVEUR>` (ex: 192.168.1.100)
- Port : `143`
- Sécurité : Aucune

**Envoi (SMTP) :**
- Serveur : `<IP_DU_SERVEUR>`
- Port : `25`
- Sécurité : Aucune
- Authentification : Mot de passe normal
- Identifiants : `testeur@local.lan` / `testeur2024`

---

## Tests de connectivité (depuis le container feedback)

```bash
# Test SMTP
docker exec luciole-feedback-watcher python -c "
import smtplib
s = smtplib.SMTP('mail', 25, timeout=5)
s.sendmail('test@local.lan', 'luciole@local.lan', 'Subject: Test\n\nCorps')
print('SMTP OK')
s.quit()
"

# Test IMAP
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

---

## Scénario de test complet

1. Thunderbird envoie un email à `luciole@local.lan` (SMTP port 25)
2. Luciole lit sa boîte en IMAP (polling 60s, ou `/api/mail/sync` manuel)
3. Un brouillon apparaît dans `/mail/drafts` (ou onglet Mail de `/config`)
4. L'admin approuve le brouillon
5. Luciole envoie la réponse à `testeur@local.lan` via SMTP
6. Thunderbird reçoit la réponse dans la boîte `testeur@local.lan`

---

## Logs et debug

```bash
# Logs en temps réel
docker logs luciole-mail-watcher --tail 50 -f

# Statut health
docker inspect --format "{{.State.Health.Status}}" luciole-mail-watcher
```

---

## Limites connues (V1)

- Pas de TLS (connexions en clair sur le LAN)
- Pas d'antispam
- Pas de filtrage des pièces jointes
- Pas d'accès Internet
- Pas de haute disponibilité
- Admin password en clair dans config.toml — à ne pas exposer en prod

---

## À faire pour passer en production

Quand le module est validé, remplacer dans l'UI Luciole :
- `mail` → `mail.entreprise.fr`
- Port 143 → 993, SSL/TLS = Oui
- Port 25 → 587, STARTTLS = Oui
- `luciole@local.lan` → `luciole@entreprise.fr`
- Mots de passe → identifiants du vrai compte de service
