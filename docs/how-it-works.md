# Comment fonctionne AgentForge

Guide pratique — du démarrage à la première Pull Request.

## 1. Installation

```bash
git clone https://github.com/user/agentforge
cd agentforge
sudo ./install.sh
```

Le script fait :
1. Vérifie les prérequis (OS, KVM, RAM, disque)
2. Installe Docker si absent
3. Installe Kata Containers (isolation microVM)
4. Configure `.env` de manière interactive
5. Build les images Docker
6. Démarre la stack (Forgejo + LangFuse + Orchestrateur)
7. Initialise Forgejo (repo, webhook, labels)
8. Initialise LangFuse (projet, clés API)

## 2. Soumettre une tâche

### Via l'interface web Forgejo

1. Ouvrir `http://YOUR_IP:3000`
2. Se connecter avec les identifiants configurés
3. Aller dans le repo `agentforge-workspace`
4. Cliquer **"New Issue"**
5. Écrire un titre descriptif
6. Dans le corps, décrire la tâche avec les critères d'acceptation
7. Dans la colonne de droite, ajouter le label **`agent-task`**
8. Cliquer **"Submit New Issue"**

### Via l'API Forgejo (curl)

```bash
curl -X POST "http://YOUR_IP:3000/api/v1/repos/admin/agentforge-workspace/issues" \
  -H "Authorization: token YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Add a hello world function",
    "body": "Create a Python file with a hello() function that returns Hello, World!",
    "labels": [1]
  }'
```

### Via make

```bash
make test-task   # crée une issue de test automatiquement
```

## 3. Ce qui se passe automatiquement

### 3.1 Réception du webhook (< 1s)

Quand l'issue est créée avec le label `agent-task`, Forgejo envoie immédiatement un webhook POST à l'orchestrateur (`http://orchestrator:8000/webhook`).

L'orchestrateur :
1. Vérifie la signature HMAC (sécurité)
2. Extrait le numéro, titre et corps de l'issue
3. Lance le pipeline en arrière-plan (non-bloquant)
4. Répond immédiatement `202 Accepted`

### 3.2 Création de la branche (quelques secondes)

Via l'API Forgejo, l'orchestrateur crée la branche `task/42-add-hello-world-function` à partir de `main`.

Un commentaire est ajouté à l'issue : *"Branch `task/42-...` créée. Démarrage du pipeline agents..."*

### 3.3 Clone du workspace

Le repo est cloné dans `/tmp/agentforge-workspaces/{task_id}/` sur le host.
Ce dossier sera monté en bind mount dans la microVM.

### 3.4 Spawn du Kata Container

```bash
docker run \
  --runtime kata-qemu \           # isolation microVM
  --memory 2048m \                # 2GB RAM
  --cpus 2 \                      # 2 vCPU
  -v /tmp/agentforge-workspaces/task_42:/workspace:rw \
  -v ./secrets:/run/secrets:ro \  # clé API (read-only)
  -e TASK_DESCRIPTION="..." \
  -e HTTPS_PROXY=http://localhost:8877 \
  agentforge_agent_runtime:latest
```

Le container démarre en ~2-5s (vs ~30s pour une VM classique).

### 3.5 Pipeline LangGraph

Le runtime agent exécute le graphe :

```
Supervisor (analyse, ~5s)
    ↓
Architect (plan, ~15-30s)
    ↓
Coder (implémentation, ~60-180s)
    ↓
Tester (tests, ~30-60s)
    ↓
Reviewer (review, ~10-20s)
    ↓
[Si corrections nécessaires → Coder (max 3 itérations)]
    ↓
Done
```

Chaque appel LLM passe par le proxy sidecar qui injecte la clé API.

### 3.6 Commit & Push

Le Coder fait des commits à chaque fichier créé/modifié :
```
feat: implement hello function
test: add unit tests for hello()
docs: add docstring to hello()
```

### 3.7 Fichier sentinel

Quand le graphe termine, le runtime écrit `/workspace/.task_done`.
L'orchestrateur (qui poll toutes les 5s) détecte ce fichier.

### 3.8 Création de la PR

L'orchestrateur :
1. Push la branche vers Forgejo
2. Crée la PR via l'API Forgejo avec le résumé du Reviewer
3. La PR référence l'issue (`Closes #42`)

### 3.9 Nettoyage

- Container Kata détruit
- Workspace local supprimé

## 4. Suivre l'avancement

### Logs orchestrateur

```bash
make logs-orch
# ou
docker logs -f agentforge_orchestrator
```

Logs typiques :
```
[INFO] webhook_received event=issues
[INFO] task_accepted issue=42 branch=task/42-add-hello-world
[INFO] spawning_container workspace=/tmp/agentforge-workspaces/...
[INFO] monitoring_task timeout=1800
[INFO] sentinel_found elapsed=187
[INFO] pushing_branch branch=task/42-add-hello-world
[INFO] pr_created pr=43 url=http://localhost:3000/...
[INFO] container_destroyed container_id=abc123
```

### LangFuse Dashboard

Ouvrir `http://YOUR_IP:3010` pour voir :
- La trace complète du run (un span par agent)
- Les tokens consommés par chaque appel LLM
- Les durées de chaque étape
- L'input/output de chaque agent

### Forgejo

- Issue #42 : commentaires d'avancement automatiques
- PR #43 : code + diff + résumé du Reviewer

## 5. Écrire de bonnes tâches

### Bonne tâche (précise et actionnable)

```
Titre : Add email validation to user registration

Corps :
Ajouter une validation d'email à la fonction `register_user()` dans `app/auth.py`.

## Critères d'acceptation
- [ ] La fonction rejette les emails sans @ avec une ValueError
- [ ] La fonction rejette les emails avec domaine invalide
- [ ] Un test unitaire est créé dans tests/test_auth.py
- [ ] La fonction existante ne régresse pas

## Contexte
Le projet utilise pytest pour les tests.
Les emails sont stockés dans PostgreSQL (pas de changement de DB nécessaire).
```

### Mauvaise tâche (trop vague)

```
Titre : Fix the bugs

Corps : There are some issues with the app, please fix them.
```

### Règles

1. **Titre clair** : décrit ce qui doit être fait, pas le problème
2. **Critères d'acceptation** : liste de ce qui doit être vrai à la fin
3. **Contexte** : frameworks utilisés, contraintes, dépendances existantes
4. **Scope limité** : une fonctionnalité par issue (les agents ne peuvent pas tout faire en une passe)

## 6. Résolution de problèmes courants

### La PR n'apparaît pas

1. Vérifier les logs : `make logs-orch`
2. Vérifier que le webhook Forgejo est configuré (Settings → Webhooks dans le repo)
3. Vérifier que la clé API LLM est correcte dans `secrets/llm_api_key`
4. Timeout ? Augmenter `TASK_TIMEOUT_SECONDS` dans `.env`

### Container ne démarre pas

```bash
# Vérifier que Kata est fonctionnel
docker run --runtime kata-qemu --rm alpine uname -r

# Si erreur kata, utiliser runc en fallback
echo "KATA_AVAILABLE=false" >> .env
make stop && make start
```

### Agents en boucle

Les agents itèrent max 3 fois. Si le feedback Reviewer est toujours négatif,
le pipeline se termine après 3 itérations (état "done" forcé).

### LangFuse non disponible

Le tracing est optionnel. Si les clés LangFuse sont absentes, le pipeline
fonctionne normalement sans tracing.

## 7. Configuration avancée

### Changer le modèle LLM

```bash
# Dans .env
LLM_PROVIDER=anthropic
LLM_MODEL=claude-opus-4-6  # modèle plus puissant mais plus lent

# Redémarrer l'orchestrateur
make stop && make start
```

### Utiliser Ollama (local, gratuit)

```bash
# Installer Ollama sur le host
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull codellama

# Configurer .env
LLM_PROVIDER=ollama
LLM_MODEL=codellama
OLLAMA_BASE_URL=http://host-gateway:11434

make stop && make start
```

### Augmenter les ressources par container

```bash
# Dans .env
KATA_VCPUS=4
KATA_RAM_MB=4096
TASK_TIMEOUT_SECONDS=3600

make stop && make start
```
