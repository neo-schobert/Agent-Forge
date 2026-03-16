# Architecture AgentForge

## Vue d'ensemble

AgentForge est une plateforme d'orchestration d'agents IA multi-agents avec isolation forte par microVM (Kata Containers). Chaque tâche soumise via Forgejo déclenche un pipeline autonome qui planifie, implémente, teste et reviewe le code — puis ouvre une Pull Request.

```
┌─────────────────────────────────────────────────────────────────────┐
│                          UTILISATEUR                                 │
│                                                                     │
│  1. Crée une issue avec label "agent-task" dans Forgejo             │
│  2. Observe la PR générée automatiquement                           │
│  3. Consulte les traces dans LangFuse                               │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ webhook
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       ORCHESTRATEUR (Python FastAPI)                 │
│                                                                     │
│  ┌─────────────────┐  ┌──────────────────┐  ┌─────────────────┐   │
│  │ WebhookHandler  │  │   GitManager      │  │ ContainerManager│   │
│  │                 │  │                   │  │                 │   │
│  │ - Vérif HMAC    │  │ - Créer branche   │  │ - Spawn Kata    │   │
│  │ - Parse payload │  │ - Cloner repo     │  │ - Poll sentinel │   │
│  │ - Route tâche   │  │ - Push commits    │  │ - Destroy       │   │
│  │                 │  │ - Créer PR        │  │                 │   │
│  └─────────────────┘  └──────────────────┘  └─────────────────┘   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ docker run --runtime kata-qemu
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      KATA CONTAINER (microVM)                        │
│                    [kernel Linux isolé par tâche]                    │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                   AGENT RUNTIME (LangGraph)                   │  │
│  │                                                               │  │
│  │  START → supervisor → architect? → coder → tester → reviewer  │  │
│  │                                ↑                       │      │  │
│  │                                └──── (iterate) ────────┘      │  │
│  │                                                               │  │
│  │  État partagé (TaskState) :                                   │  │
│  │  - task_description, plan, code_changes                       │  │
│  │  - test_output, review_feedback, iterations                   │  │
│  │                                                               │  │
│  │  Checkpoint : /workspace/.checkpoint.db (SqliteSaver)         │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                               │                                     │
│  ┌────────────────────────────▼─────────────────────────────────┐  │
│  │                    PROXY SIDECAR (port 8877)                   │  │
│  │                                                               │  │
│  │  HTTPS_PROXY=http://localhost:8877                            │  │
│  │                                                               │  │
│  │  Intercepte → api.anthropic.com / api.openai.com             │  │
│  │  Injecte   → Authorization depuis /run/secrets/llm_api_key    │  │
│  │  Bloque    → toute autre destination                          │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  /workspace (bind mount depuis host)                                │
│  ├── [code source cloné]                                            │
│  ├── .checkpoint.db       ← checkpoint LangGraph                    │
│  ├── .task_done           ← sentinel de fin                         │
│  └── .task_result.json    ← résultat                                │
└─────────────────────────────────────────────────────────────────────┘
```

## Composants

### Forgejo (port 3000)

Forge Git self-hosted basée sur Gitea. Remplace GitHub/GitLab.

- **Rôle** : Stocker le code, gérer les issues, les branches et les PRs
- **Webhooks** : Envoie des événements à l'orchestrateur sur création d'issue
- **API REST** : Utilisée par l'orchestrateur pour créer branches et PRs
- **Branch protection** : La branche `main` est protégée, merge uniquement via PR

### Orchestrateur (port 8000)

Service Python (FastAPI) qui orchestre le cycle de vie des tâches.

```
Webhook reçu
    ↓
Vérification HMAC
    ↓
Extraction issue (#, titre, corps)
    ↓
Création branche task/{n}-{slug}
    ↓
Clone workspace → /tmp/agentforge-workspaces/{task_id}
    ↓
Spawn Kata Container
    ↓
Poll /workspace/.task_done (toutes les 5s)
    ↓
Push commits + Créer PR
    ↓
Destroy container + Cleanup workspace
```

### Kata Containers

Runtime OCI avec isolation microVM. Chaque container Kata :
- Reçoit un **kernel Linux dédié** (pas de partage avec le host)
- Est éphémère (détruit après la fin de la tâche)
- A accès uniquement à son workspace (bind mount)
- Ne peut contacter que `api.anthropic.com` / `api.openai.com` (via proxy)
- Ressources : 2 vCPU, 2GB RAM, 10GB disque (configurable)

**Fallback runc** : Si KVM n'est pas disponible (VPS sans virtualisation imbriquée), l'orchestrateur utilise le runtime `runc` standard (Docker classique). L'isolation est moindre mais le pipeline fonctionnel.

### LangGraph (graphe d'agents)

```python
class TaskState(TypedDict):
    task_description: str   # Issue Forgejo
    repo_path: str          # /workspace
    plan: str               # output Architect
    code_changes: list      # fichiers modifiés
    test_output: str        # output Tester
    review_feedback: str    # output Reviewer
    iterations: int         # compteur (max 3)
    done: bool
    final_summary: str
```

**Topologie** :

```
START
  │
  ▼
supervisor ─────────────────────────────────┐
  │                                         │ (micro-task)
  │ (complex task)                          ▼
  ▼                                      coder
architect                                   │
  │                                         ▼
  └────────────────────────────────────► tester
                                            │
                                            ▼
                                         reviewer
                                           / \
                                  (LGTM) /   \ (changes needed)
                                        ▼     ▼
                                       END   coder (iter++)
                                              │
                                        (iter >= 3) → END
```

**Checkpointing** : SqliteSaver sur `/workspace/.checkpoint.db`. Si le container crashe, il peut reprendre depuis le dernier checkpoint.

### Agents

| Agent | Rôle | Outils |
|-------|------|--------|
| **Supervisor** | Analyse la tâche, détermine micro vs complexe | LLM call (JSON) |
| **Architect** | Lit le repo, produit un plan détaillé | LLM call (Markdown) |
| **Coder** | Implémente le plan fichier par fichier | read_file, write_file, commit_files |
| **Tester** | Exécute les tests, analyse les résultats | run_tests, run_command |
| **Reviewer** | Review le diff, approuve ou demande corrections | LLM call (diff/log) |

### Proxy Sidecar

Proxy HTTP transparent sur `localhost:8877`.

**Mécanisme** :
1. Les agents configurent `HTTPS_PROXY=http://localhost:8877`
2. Toutes les requêtes HTTP/HTTPS passent par le proxy
3. Pour les requêtes vers `api.anthropic.com` ou `api.openai.com` :
   - Le proxy lit `/run/secrets/llm_api_key` (monté depuis le host)
   - Injecte le header `x-api-key` (Anthropic) ou `Authorization: Bearer` (OpenAI)
4. Toutes les autres destinations sont bloquées (403 Forbidden)

**Résultat** : Les agents n'ont jamais accès à la vraie clé API. La clé n'est pas dans les variables d'environnement du container.

### LangFuse (port 3010)

Tracing & observabilité self-hosted.

- Chaque run LangGraph = une trace LangFuse
- Spans par agent (durée, tokens consommés, input/output)
- Dashboard pour débugger exactement ce que chaque agent a fait
- Configurable via `LANGFUSE_PUBLIC_KEY` et `LANGFUSE_SECRET_KEY_API` dans `.env`

## Flux de données complet

```
1. Utilisateur crée issue #42 "Add user login" avec label "agent-task"
                 │
                 ▼ webhook HTTP POST
2. Orchestrateur reçoit : {issue: {number: 42, title: "Add user login", labels: ["agent-task"]}}
                 │
                 ▼ API Forgejo
3. Branche créée : task/42-add-user-login
                 │
                 ▼ git clone
4. Workspace : /tmp/agentforge-workspaces/admin-agentforge-workspace-42/
                 │
                 ▼ docker run --runtime kata-qemu
5. MicroVM démarrée :
   - /workspace = bind mount du workspace
   - /run/secrets/llm_api_key = bind mount read-only
   - HTTPS_PROXY=http://localhost:8877
                 │
                 ▼ LangGraph
6. Pipeline agents :
   Supervisor  → is_micro_task=false, plan préliminaire
   Architect   → plan détaillé (fichiers à créer, approach)
   Coder       → crée auth.py, tests/test_auth.py, commit "feat: add user login"
   Tester      → pytest → 3/3 tests passed
   Reviewer    → "LGTM — implémentation propre avec tests"
                 │
                 ▼ fichier sentinel
7. /workspace/.task_done créé → orchestrateur détecte la fin
                 │
                 ▼ git push + API Forgejo
8. PR créée : "Add user login" (#43), closes #42
                 │
                 ▼
9. Container détruit, workspace supprimé
```

## Sécurité

### Isolation des secrets

```
Host filesystem          Kata Container (microVM)
─────────────────        ─────────────────────────
./secrets/               /run/secrets/
  llm_api_key ────────►    llm_api_key (read-only)
                                   │
                                   ▼
                           Proxy sidecar
                           (injecte dans les requêtes)
                                   │
                              JAMAIS dans ENV
```

### Isolation réseau

```
Host network ─── Docker network (agentforge_net) ─── Kata microVM
                         │                                │
                    Forgejo:3000                    localhost:8877 (proxy)
                    LangFuse:3000                          │
                    Orchestrateur:8000                     ▼
                                                    api.anthropic.com ✓
                                                    api.openai.com    ✓
                                                    *.* (bloqué)      ✗
```

### Isolation kernel (Kata)

Chaque container Kata tourne avec un kernel Linux dédié :
- Pas de partage du kernel avec le host
- Exploitation d'une vuln kernel dans l'agent → impact limité au container
- Comparable à une VM légère (~50ms de démarrage vs ~30s pour une VM classique)
