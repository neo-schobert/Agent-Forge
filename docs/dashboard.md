# Dashboard AgentForge — Documentation

Interface web complète pour piloter AgentForge sans ligne de commande.
Accessible sur `http://localhost:3020` (ou `http://<votre-vps>:3020`).

---

## Architecture

```
dashboard/
├── backend/            # FastAPI (Python 3.11)
│   ├── main.py         # Point d'entrée, montage des routes, WebSocket hub
│   ├── config.py       # Singleton Config — lit le .env, reload() à chaud
│   └── routes/
│       ├── system.py   # GET /api/system/status|config|tasks
│       ├── tasks.py    # GET|POST /api/tasks, WS /ws/tasks/{id}/logs
│       ├── forgejo.py  # GET /api/prs|issues, POST merge/comment
│       ├── models.py   # GET /api/models/openrouter (live depuis API)
│       ├── chat.py     # POST /api/chat (SSE streaming, Chef de projet)
│       └── settings.py # GET|POST /api/settings, test-provider
└── frontend/           # React 18 + Vite
    ├── src/
    │   ├── App.jsx                 # Navigation principale, Setup Wizard gate
    │   ├── hooks/
    │   │   ├── useWebSocket.js     # Reconnexion auto avec backoff exponentiel
    │   │   └── useSSE.js           # EventSource wrapper
    │   └── components/
    │       ├── SetupWizard.jsx     # Wizard 4 étapes
    │       ├── SystemStatus.jsx    # Status, tâches actives, feed live
    │       ├── TaskList.jsx        # Liste des tâches (tabs All/Running/Done/Failed)
    │       ├── TaskDetail.jsx      # Timeline agents, logs live, badge crash recovery
    │       ├── PRViewer.jsx        # Diff viewer, merge/reject
    │       ├── Chat.jsx            # Chat "Chef de projet" (SSE streaming)
    │       ├── Settings.jsx        # Clés API, modèles par agent
    │       └── ModelSelector.jsx   # Sélecteur modèle live OpenRouter
    └── package.json
```

---

## Démarrage

### Avec Docker Compose (recommandé)

```bash
# Le dashboard est inclus dans la stack principale
docker compose up -d dashboard

# Vérifier qu'il est UP
docker compose ps dashboard
curl -s http://localhost:3020/api/health
```

### En développement local (hot-reload)

```bash
# Backend
cd dashboard/backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 3020 --reload

# Frontend (autre terminal)
cd dashboard/frontend
npm install
npm run dev   # port 5173, proxy vers localhost:3020
```

---

## Setup Wizard

Au premier accès (ou si aucune clé LLM n'est configurée), le Wizard s'affiche automatiquement.

**Étape 1 — Provider LLM**
- Choisir : `anthropic` | `openai` | `openrouter`
- Saisir la clé API correspondante
- Bouton "Tester" → appel minimal pour vérifier la validité

**Étape 2 — Modèles par agent**
- 5 agents : Supervisor, Architect, Coder, Tester, Reviewer
- Chaque agent a son propre sélecteur de modèle
- La liste des modèles est chargée **en direct depuis l'API OpenRouter** (jamais hardcodée)
- Pour Anthropic/OpenAI : saisie libre avec suggestions

**Étape 3 — Status de la stack**
- Affiche le statut de Forgejo, Orchestrateur, LangFuse
- Vérification des prérequis avant le premier run

**Étape 4 — Lancement**
- Résumé de la configuration
- Bouton "Démarrer" → applique le .env et démarre les services

---

## Dashboard principal

### System Status

- Grille de santé des services (Forgejo, Orchestrateur, LangFuse) avec latence
- Compteurs : tâches actives / terminées / échouées
- Modèles configurés par agent
- Feed d'activité en temps réel (WebSocket `/ws`)

### Liste des tâches

URL : `/tasks`

- Tabs : Toutes | En cours | Terminées | Échouées
- Rafraîchissement automatique toutes les 10s
- Cliquer → ouvre le détail de la tâche

### Détail d'une tâche

URL : `/tasks/{task_id}`

- Timeline des agents (Supervisor → Architect → Coder → Tester → Reviewer)
- Logs en temps réel (WebSocket)
- Panel coût (tokens consommés, prix estimé)
- Badge **Crash Recovery** si la tâche a été reprise depuis un checkpoint
- Lien vers la PR Forgejo

### PR Viewer

URL : `/prs`

- Liste des Pull Requests ouvertes
- Visualisation du diff (lignes vertes/rouges)
- Boutons **Merge** et **Fermer** (avec commentaire optionnel)

### Chat "Chef de projet"

URL : `/chat`

Dialogue en langage naturel avec un LLM qui peut :
- Créer des issues Forgejo (→ déclenche un pipeline agent)
- Lister les tâches et PR en cours
- Donner le statut du système

Le streaming SSE affiche la réponse mot par mot.
Les appels d'outils sont affichés dans l'interface (action cards).

### Paramètres

URL : `/settings`

- Changer les clés API (Anthropic, OpenAI, OpenRouter)
- Configurer les modèles par agent
- Tester la connectivité de chaque provider
- Affichage masqué des clés (preview `sk-ant-...****`)

---

## API Backend

### Health

```
GET /api/health
→ {"status": "ok", "version": "2.0.0"}
```

### System

```
GET /api/system/status
→ {
    "services": {
        "forgejo": {"healthy": true, "latency_ms": 12},
        "orchestrator": {"healthy": true, "latency_ms": 5},
        "langfuse": {"healthy": false, "latency_ms": null}
    },
    "tasks": {"active": 1, "completed": 4, "failed": 0}
  }

GET /api/system/config
→ {
    "llm_provider": "openrouter",
    "llm_model": "anthropic/claude-sonnet-4-6",
    "is_configured": true,
    "per_agent_models": {
        "supervisor": "anthropic/claude-haiku-4-5",
        "architect": "anthropic/claude-sonnet-4-6",
        ...
    }
  }
```

### Modèles OpenRouter (live)

```
GET /api/models/openrouter
→ {data: [...modèles depuis api.openrouter.ai...]}

GET /api/models/openrouter/filtered?role=coder
→ Liste filtrée selon critères :
  - supervisor  : price < $3/M + context >= 32k
  - architect   : context >= 100k
  - coder       : context >= 64k
  - tester      : price < $5/M
  - reviewer    : context >= 100k
```

### Tâches

```
GET  /api/tasks              → liste des tâches
GET  /api/tasks/{id}         → détail + issue + PR croisée
POST /api/tasks              → créer issue Forgejo avec label agent-task
WS   /ws/tasks/{id}/logs     → stream de logs en temps réel
```

### Pull Requests

```
GET  /api/prs                    → liste des PRs
GET  /api/prs/{num}              → PR + diff
POST /api/prs/{num}/merge        → merge
POST /api/prs/{num}/comment      → commenter
```

### Chat

```
POST /api/chat
Body: {"messages": [...], "stream": true}
→ SSE stream:
  data: {"type": "text", "content": "Voici..."}
  data: {"type": "action", "content": "Création de l'issue..."}
  data: {"type": "done", "content": ""}
```

### Settings

```
GET  /api/settings              → config (clés masquées)
POST /api/settings              → sauvegarder (écrit .env + /run/secrets/llm_api_key)
POST /api/settings/test-provider → test live du provider
POST /api/settings/verify-openrouter → vérification clé OpenRouter
```

---

## WebSocket temps réel

Le endpoint `/ws` diffuse les événements du système :

```json
{"type": "task_started",   "task_id": "...", "issue": 42}
{"type": "task_completed", "task_id": "...", "pr_url": "..."}
{"type": "task_failed",    "task_id": "...", "error": "..."}
{"type": "agent_step",     "task_id": "...", "agent": "coder", "action": "..."}
{"type": "container_event","event": "container_started", "container": "..."}
```

Reconnexion automatique avec backoff exponentiel (1s, 2s, 4s, 8s, max 30s).

---

## Configuration OpenRouter

Pour utiliser OpenRouter comme provider :

1. Créer un compte sur [openrouter.ai](https://openrouter.ai)
2. Générer une clé API
3. Dans Settings, choisir "openrouter" comme provider
4. Les modèles disponibles se chargent automatiquement depuis l'API
5. Configurer un modèle différent par agent si souhaité

Variables d'environnement générées :

```bash
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
AGENT_SUPERVISOR_MODEL=anthropic/claude-haiku-4-5-20251001
AGENT_ARCHITECT_MODEL=anthropic/claude-sonnet-4-6
AGENT_CODER_MODEL=anthropic/claude-sonnet-4-6
AGENT_TESTER_MODEL=google/gemini-flash-1.5
AGENT_REVIEWER_MODEL=anthropic/claude-sonnet-4-6
```

Le proxy sidecar (`proxy.py`) lit le header `X-Agent-Name` pour router
chaque requête vers le bon modèle selon `AGENT_{NAME}_MODEL`.

---

## Crash Recovery

Le dashboard affiche un badge **Reprise depuis checkpoint** sur les tâches qui
ont été interrompues et reprises automatiquement.

Le checkpoint LangGraph est sauvegardé dans `/workspace/.checkpoint.db` après
chaque nœud du graphe d'agents. En cas de crash du container :

1. L'orchestrateur détecte la fin inattendue du container
2. Il respawne un nouveau container avec le flag `--resume`
3. LangGraph charge le checkpoint et reprend depuis le dernier nœud complété
4. Le badge "Crash Recovery" apparaît dans le détail de la tâche

Pour tester manuellement :
```bash
bash scripts/test_crash_recovery.sh
```

---

## Sécurité

- Les clés API ne sont **jamais** exposées dans les réponses API (preview masqué)
- La clé LLM est stockée dans `/run/secrets/llm_api_key` (hors .env)
- Le dashboard valide la signature HMAC des webhooks Forgejo
- CORS configuré pour autoriser uniquement les origines locales en développement
- En production, placer derrière un reverse proxy (nginx/Caddy) avec TLS

---

## Rebuild après modification

```bash
# Rebuild et restart du service dashboard uniquement
docker compose build dashboard
docker compose up -d --no-deps dashboard

# Voir les logs
docker compose logs -f dashboard

# Rebuild complet (si changement de dépendances)
docker compose build --no-cache dashboard
```
