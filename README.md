# AgentForge

**Plateforme d'orchestration d'agents IA multi-agents, isolée par microVM (Kata Containers),
avec versioning Git intégré et dashboard web.**

`License: MIT` | `Python 3.11+` | `Docker` | `LangGraph 1.x` | `LangFuse 4.x`

---

## Vue d'ensemble

AgentForge permet de soumettre une tâche de développement sous forme d'issue Git et d'obtenir
automatiquement une Pull Request avec le code implémenté, testé et reviewé par un pipeline
d'agents IA enchaînés. Le système est 100 % open source et self-hosted — aucune dépendance
cloud propriétaire. Chaque tâche s'exécute dans un container isolé (microVM Kata Containers
avec fallback runc), les clés API ne sont jamais exposées aux agents.

---

## Architecture

```
Forgejo (Git self-hosted :3000)
    ↕ webhooks / API REST
Orchestrateur Python (:8000)
    ↕ spawn / destroy
Kata Containers (microVM par tâche, fallback runc)
    └── LangGraph (graphe d'agents stateful)
         ├── Supervisor   — analyse la tâche
         ├── Architect    — lit le repo, planifie
         ├── Coder        — implémente, git commit
         ├── Tester       — exécute les tests
         └── Reviewer     — valide ou itère (max 3x)
Network Proxy sidecar (:8877)
    └── Injecte les clés API (jamais exposées aux agents)
LangFuse (tracing self-hosted :3010)
Dashboard React + FastAPI (:3020)
    ├── Setup Wizard
    ├── Vue tâches / agents en temps réel
    ├── Vue PRs (diff, merge, reject)
    ├── Chat "Chef de projet" (SSE streaming)
    └── Settings (clés API, modèles par agent)
```

---

## Prérequis

- **OS** : Ubuntu 22.04, Ubuntu 24.04, ou Debian 12
- **RAM** : 4 GB minimum (8 GB recommandé)
- **Disque** : 20 GB minimum
- **CPU** : 2 cores minimum
- **KVM** (optionnel) : requis pour l'isolation microVM Kata Containers.
  Sans KVM, le système fonctionne avec runc (moins isolé mais entièrement fonctionnel).
  Pour activer KVM sur un VPS : activez la virtualisation imbriquée dans le panneau de
  contrôle de votre hébergeur.
- Connexion internet pour le build des images Docker

---

## Installation

```bash
git clone https://github.com/neo-schobert/Agent-Forge.git
cd Agent-Forge
./install.sh
```

Ce que fait `install.sh` :

1. Vérifie et installe les prérequis (Docker, buildx, dépendances système)
2. Tente d'activer KVM / installe Kata Containers si possible
3. Pose 6 questions de configuration (provider LLM, domaine, admin, email)
4. Build les images Docker
5. Démarre toute la stack (6 containers)
6. Initialise Forgejo (compte admin, repo, webhook, labels)
7. Initialise LangFuse (projet, clés API)
8. Affiche les URLs d'accès

À la fin de l'installation :

```
Dashboard  : http://YOUR_IP:3020   ← configurer les clés API ici
Forgejo    : http://YOUR_IP:3000
LangFuse   : http://YOUR_IP:3010
Orchestr.  : http://YOUR_IP:8000/health
```

---

## Premier démarrage

Après installation, ouvrir `http://YOUR_IP:3020`.
Le Setup Wizard s'affiche si les clés API ne sont pas configurées.

- **Étape 1** : choisir le provider LLM (Anthropic / OpenAI / OpenRouter recommandé)
- **Étape 2** : saisir la clé API et choisir les modèles par agent (si OpenRouter)
- **Étape 3** : vérifier que tous les services sont up
- **Étape 4** : cliquer "Lancer AgentForge"

Pour créer une première tâche :

- **Option A** — Via le dashboard : onglet Chat, décrire la tâche à l'agent
- **Option B** — Via Forgejo : créer une issue avec le label `agent-task`
- **Option C** — Via la CLI : `make test-task`

---

## Providers LLM supportés

| Provider   | Variable             | Modèles supportés        |
|------------|----------------------|--------------------------|
| OpenRouter | OPENROUTER_API_KEY   | Tous (liste live)        |
| Anthropic  | ANTHROPIC_API_KEY    | claude-* (directs)       |
| OpenAI     | OPENAI_API_KEY       | gpt-* (directs)          |
| Ollama     | OLLAMA_BASE_URL      | Modèles locaux           |

Configuration par agent (OpenRouter uniquement) :
Chaque agent peut utiliser un modèle différent, configuré depuis le dashboard.
Variables : `AGENT_SUPERVISOR_MODEL`, `AGENT_ARCHITECT_MODEL`,
`AGENT_CODER_MODEL`, `AGENT_TESTER_MODEL`, `AGENT_REVIEWER_MODEL`

---

## Makefile

```bash
make start       # Démarrer la stack
make stop        # Arrêter la stack
make logs        # Logs de tous les services
make logs-orch   # Logs orchestrateur uniquement
make test-task   # Créer une issue de test et attendre la PR
make clean       # Arrêter + supprimer les volumes
make update      # git pull + rebuild images
make status      # État de tous les containers
```

---

## Structure du projet

```
Agent-Forge/
├── install.sh                    # Installation complète en une commande
├── Makefile
├── docker-compose.yml            # Stack : 6 services
├── .env.example                  # Template de configuration
├── config/
│   └── forgejo/app.ini
├── orchestrator/                 # FastAPI :8000
│   ├── main.py
│   ├── webhook_handler.py        # Pipeline webhook → container → PR
│   ├── git_manager.py            # API Forgejo
│   ├── container_manager.py      # Spawn/destroy Kata/runc
│   └── task_monitor.py           # Poll sentinel + crash detection
├── agent_runtime/                # Image Docker des agents
│   ├── Dockerfile
│   ├── main.py                   # Proxy sidecar + LangGraph
│   ├── graph.py                  # Graphe LangGraph + SqliteSaver
│   ├── state.py                  # TaskState TypedDict
│   ├── agents/
│   │   ├── supervisor.py
│   │   ├── architect.py
│   │   ├── coder.py
│   │   ├── tester.py
│   │   └── reviewer.py
│   └── tools/
│       ├── git_tools.py
│       ├── file_tools.py
│       └── shell_tools.py
├── proxy/                        # Sidecar injection clés API
│   ├── proxy.py
│   └── Dockerfile
├── dashboard/                    # React + FastAPI :3020
│   ├── Dockerfile                # Multi-stage: node build → python serve
│   ├── backend/
│   │   ├── main.py
│   │   ├── config.py
│   │   └── routes/
│   │       ├── tasks.py
│   │       ├── system.py
│   │       ├── forgejo.py
│   │       ├── chat.py
│   │       ├── settings.py
│   │       └── models.py
│   └── frontend/
│       └── src/
│           ├── App.jsx
│           └── components/
│               ├── SetupWizard.jsx
│               ├── SystemStatus.jsx
│               ├── TaskList.jsx
│               ├── TaskDetail.jsx
│               ├── PRViewer.jsx
│               ├── Chat.jsx
│               ├── Settings.jsx
│               └── ModelSelector.jsx
├── scripts/
│   ├── setup_kata.sh
│   ├── setup_forgejo.sh
│   ├── setup_langfuse.sh
│   ├── test_task.sh
│   └── test_crash_recovery.sh
└── docs/
    ├── architecture.md
    ├── how-it-works.md
    ├── dashboard.md
    └── vps-notes.md
```

---

## Fonctionnement du pipeline

1. L'utilisateur crée une tâche (dashboard chat ou issue Forgejo avec label `agent-task`)
2. Forgejo envoie un webhook à l'orchestrateur
3. L'orchestrateur crée une branche Git `task/{issue-number}-{slug}`
4. Un container isolé (Kata microVM ou runc) est spawné avec le workspace
5. LangGraph démarre le graphe : Supervisor → Architect → Coder → Tester → Reviewer
6. Si Reviewer demande des corrections : retour au Coder (max 3 itérations)
7. Le Coder commit chaque fichier modifié avec un message descriptif
8. L'orchestrateur détecte la fin (fichier sentinel), pousse les commits, crée la PR
9. La PR apparaît dans le dashboard pour review et merge
10. En cas de crash mid-run : respawn automatique du container, reprise depuis le checkpoint

---

## Sécurité

- Les clés API ne transitent jamais dans l'environnement des agents
- Le proxy sidecar intercepte les appels LLM et injecte les clés depuis `/run/secrets/`
- Les agents ne peuvent pas exécuter de commandes système (whitelist stricte)
- Chaque tâche tourne dans un container isolé avec limite de ressources
- Les secrets ne sont jamais commités (protégés par `.gitignore`)

---

## Testé sur

- Ubuntu 22.04 LTS
- Ubuntu 24.04 LTS
- Debian 12
- VPS sans KVM (mode runc fallback)
- VPS avec KVM (mode Kata Containers)

---

## Licence

MIT License — voir `LICENSE`
