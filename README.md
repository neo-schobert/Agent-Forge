# AgentForge

Plateforme d'orchestration d'agents IA multi-agents, isolés par microVM (Kata Containers), avec versioning Git intégré via Forgejo self-hosted et tracing via LangFuse.

## Vue d'ensemble

AgentForge permet de soumettre une tâche de développement sous forme d'issue Git et d'obtenir automatiquement une Pull Request avec le code implémenté, testé et reviewé par un pipeline d'agents IA enchaînés.

```
Issue Forgejo  →  Orchestrateur  →  Kata Container (microVM)
                                        └── LangGraph
                                             ├── Supervisor
                                             ├── Architect
                                             ├── Coder      → commits
                                             ├── Tester
                                             └── Reviewer
                                    →  Pull Request Forgejo
```

## Prérequis

- Ubuntu 22.04 / 24.04 ou Debian 12
- VPS ou machine physique avec support KVM (`/dev/kvm` présent)
- RAM >= 4 GB (8 GB recommandé)
- Disque >= 40 GB libre
- Accès root ou sudo

Vérifier le support KVM :
```bash
ls /dev/kvm && echo "KVM OK"
# ou
egrep -c '(vmx|svm)' /proc/cpuinfo  # doit retourner > 0
```

## Installation en une commande

```bash
git clone https://github.com/user/agentforge
cd agentforge
./install.sh
```

Le script installe automatiquement Docker, Kata Containers, configure la stack et lance tous les services.

## Architecture

```
Forgejo (port 3000)          — Forge Git self-hosted
  ↕ webhook
Orchestrateur (port 8000)    — Python FastAPI
  ↕ spawn/destroy
Kata Containers              — microVM par tâche (kernel isolé)
  └── Agent Runtime          — LangGraph + agents Python
       └── Proxy sidecar     — Injecte API keys (port 8877)
LangFuse (port 3010)         — Tracing & observabilité
PostgreSQL (interne)         — DB Forgejo + DB LangFuse
```

## Utilisation

### Lancer une tâche agent

1. Ouvrir Forgejo sur `http://YOUR_IP:3000`
2. Naviguer dans le repo `agentforge-workspace`
3. Créer une issue avec le label **`agent-task`**
4. Décrire la tâche dans le corps de l'issue
5. L'orchestrateur reçoit le webhook, spawn une microVM et démarre le pipeline
6. Suivre l'avancement dans LangFuse : `http://YOUR_IP:3010`
7. La PR apparaît automatiquement dans Forgejo quand c'est terminé

### Via Makefile

```bash
make test-task      # crée une issue de test et attend la PR
make status         # état de tous les containers
make logs           # logs de tous les services
make logs-orch      # logs orchestrateur uniquement
```

## Stack technique

| Composant     | Technologie              | License         |
|---------------|--------------------------|-----------------|
| Git forge     | Forgejo                  | MIT             |
| Isolation     | Kata Containers          | Apache 2.0      |
| Agents        | LangGraph                | Apache 2.0      |
| LLM           | Anthropic Claude (proxy) | —               |
| Tracing       | LangFuse self-hosted     | MIT             |
| Orchestrateur | Python 3.11 + FastAPI    | —               |
| Proxy creds   | mitmproxy custom         | Apache 2.0      |
| DB            | PostgreSQL               | PostgreSQL Lic. |

100% open source, 100% self-hosted, zéro dépendance cloud propriétaire.

## Configuration

Copier `.env.example` en `.env` et ajuster les valeurs :

```bash
cp .env.example .env
$EDITOR .env
```

Variables principales :

| Variable               | Description                          |
|------------------------|--------------------------------------|
| `LLM_PROVIDER`         | `anthropic` ou `openai` ou `ollama`  |
| `ANTHROPIC_API_KEY`    | Clé API Anthropic                    |
| `OPENAI_API_KEY`       | Clé API OpenAI (alternatif)          |
| `OLLAMA_BASE_URL`      | URL Ollama (alternatif local)        |
| `FORGEJO_DOMAIN`       | Domaine ou IP du serveur Forgejo     |
| `FORGEJO_ADMIN_USER`   | Login admin Forgejo                  |
| `FORGEJO_ADMIN_PASS`   | Mot de passe admin Forgejo           |
| `LANGFUSE_SECRET_KEY`  | Clé secrète LangFuse (auto-générée) |

## Sécurité

- Les clés API ne transitent **jamais** dans l'environnement des containers agents
- Un proxy sidecar intercepte les appels LLM et injecte les credentials
- Les containers utilisent Kata (kernel dédié), pas du Docker standard
- Réseau des agents isolé : seul `api.anthropic.com` / `api.openai.com` est accessible en sortie
- Whitelist stricte dans le proxy

## Makefile — toutes les commandes

```bash
make start          # démarrer la stack
make stop           # arrêter la stack
make logs           # logs tous services
make logs-orch      # logs orchestrateur
make test-task      # smoke test end-to-end
make clean          # stop + supprimer volumes
make update         # git pull + rebuild
make status         # état containers
```

## Structure du projet

```
agentforge/
├── install.sh                    # Installation complète en une commande
├── Makefile                      # Commandes de gestion
├── docker-compose.yml            # Stack principale
├── .env.example                  # Template de configuration
├── config/
│   ├── forgejo/app.ini           # Configuration Forgejo
│   └── langfuse/                 # Config LangFuse
├── orchestrator/                 # Service orchestrateur Python
│   ├── main.py                   # Point d'entrée FastAPI
│   ├── webhook_handler.py        # Traitement webhooks Forgejo
│   ├── git_manager.py            # Opérations Git via API Forgejo
│   ├── container_manager.py      # Spawn/destroy Kata Containers
│   └── task_monitor.py           # Poll fichier sentinel
├── agent_runtime/                # Runtime agents dans microVM
│   ├── Dockerfile
│   ├── main.py                   # Point d'entrée dans la microVM
│   ├── graph.py                  # Graphe LangGraph
│   ├── state.py                  # TaskState TypedDict
│   └── agents/                   # Implémentation de chaque agent
│       ├── supervisor.py
│       ├── architect.py
│       ├── coder.py
│       ├── tester.py
│       └── reviewer.py
├── proxy/                        # Proxy HTTP sidecar (injection API keys)
│   ├── proxy.py
│   └── Dockerfile
├── scripts/                      # Scripts d'installation et de test
│   ├── setup_kata.sh
│   ├── setup_forgejo.sh
│   ├── setup_langfuse.sh
│   └── test_task.sh
└── docs/                         # Documentation
    ├── architecture.md
    ├── how-it-works.md
    └── vps-notes.md
```

## Dépannage

Voir `docs/vps-notes.md` pour les problèmes courants rencontrés sur VPS réels.

```bash
# Vérifier que Kata est fonctionnel
kata-runtime --version
docker run --runtime kata-qemu hello-world

# Vérifier les logs de l'orchestrateur
make logs-orch

# Tester le webhook manuellement
curl -X POST http://localhost:8000/health
```

## Contribuer

1. Fork le projet
2. Créer une branche (`git checkout -b feature/ma-feature`)
3. Committer les changements
4. Ouvrir une Pull Request

## License

MIT — voir [LICENSE](LICENSE)
