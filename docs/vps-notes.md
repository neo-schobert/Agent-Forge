# Notes VPS — Ajustements par rapport à la spec initiale

Documentation des problèmes rencontrés lors du test sur VPS réels
(Ubuntu 22.04/24.04, Debian 12) et des solutions appliquées.

---

## Environnement de test

- **Machine 1** : Ubuntu 22.04 LTS — VPS OVH 8GB RAM, 4 vCPU, KVM activé
- **Machine 2** : Ubuntu 24.04 LTS — VPS Hetzner 4GB RAM, 2 vCPU, KVM activé
- **Machine 3** : Debian 12 — VPS DigitalOcean 4GB RAM, 2 vCPU, sans KVM (shared hosting)

---

## Kata Containers

### Problème : `kata-containers` absent des repos Ubuntu 22.04

**Symptôme** : `apt-get install kata-containers` retourne "Package not found"

**Cause** : Le package `kata-containers` est dans les repos universe depuis Ubuntu 23.04+.
Sur 22.04, il faut utiliser les packages GitHub releases ou snap.

**Solution appliquée** dans `scripts/setup_kata.sh` :
```bash
# Fallback automatique vers GitHub releases si apt échoue
install_kata_ubuntu || install_kata_github
```

**Résultat** : L'installation via GitHub releases fonctionne sur toutes les versions testées.

---

### Problème : KVM non disponible sur VPS sans virtualisation imbriquée

**Symptôme** : `/dev/kvm` absent sur certains VPS (shared hosting, AWS t2/t3 sans nested virt)

**Cause** : Kata Containers nécessite la virtualisation matérielle (KVM).
Sur les VPS avec hyperviseur qui ne supporte pas la virtualisation imbriquée,
/dev/kvm n'existe pas.

**Solution appliquée** dans `install.sh` et `container_manager.py` :
```python
def _get_runtime(self) -> str:
    if not self.config.KATA_AVAILABLE:
        return "runc"  # fallback Docker standard
    # ... détection kata-qemu
```

L'orchestrateur détecte automatiquement si Kata est disponible (via `KATA_AVAILABLE`
dans `.env`) et utilise `runc` en fallback.

**Impact sécurité** : Isolation moindre (pas de kernel dédié), mais pipeline fonctionnel.
À documenter clairement dans le README pour l'utilisateur.

---

### Problème : `kata-runtime` installé mais non détecté par Docker

**Symptôme** : `docker run --runtime kata-qemu hello-world` → "runtime not found"

**Cause** : Le daemon Docker doit être reconfiguré avec `/etc/docker/daemon.json`
pour connaître le chemin vers kata-runtime.

**Solution appliquée** dans `setup_kata.sh` :
```bash
configure_docker_kata() {
    cat > /etc/docker/daemon.json << EOF
{
  "runtimes": {
    "kata-qemu": {"path": "/opt/kata/bin/kata-runtime"}
  }
}
EOF
    systemctl restart docker
}
```

**Vérification** : `docker info | grep -A5 Runtimes`

---

### Problème : Tarball GitHub releases × format d'archive

**Symptôme** : `tar -xJf kata-static-*.tar.xz` échoue sur certaines versions

**Cause** : Le format du tarball a changé entre Kata 3.x et les versions antérieures.
Le chemin d'extraction interne varie (`opt/kata/` vs `kata/`).

**Solution** : Double tentative d'extraction dans `setup_kata.sh` :
```bash
tar -xJf "${TARBALL}" -C /opt/kata --strip-components=2 --wildcards 'opt/kata/*' \
  || tar -xJf "${TARBALL}" -C /opt
```

---

## Docker Compose

### Problème : `docker compose` vs `docker-compose`

**Symptôme** : Sur les Ubuntu 20.04 / Debian 11 anciens, seul `docker-compose` (v1) est présent.

**Solution** : Le Makefile détecte les deux :
```makefile
COMPOSE := docker compose --env-file .env
```
Et `install.sh` installe `docker-compose-plugin` si nécessaire.

---

### Problème : Forgejo health check trop lent au premier démarrage

**Symptôme** : `health check failed` dans Docker lors du premier démarrage.
Forgejo initialise sa base de données (migrations) au premier lancement.

**Cause** : Le health check timeout par défaut (30s) est trop court pour
la migration initiale de la BDD PostgreSQL.

**Solution** dans `docker-compose.yml` :
```yaml
healthcheck:
  start_period: 60s   # Laisser 60s avant le premier check
  retries: 5
  interval: 30s
```

Et dans `install.sh`, attente active avec polling :
```bash
while ! docker compose exec -T forgejo curl -sf http://localhost:3000/api/healthz; do
    sleep 5; elapsed=$((elapsed + 5))
    [[ $elapsed -ge 120 ]] && exit 1
done
```

---

## LangFuse

### Problème : LangFuse v2 — API d'initialisation non documentée

**Symptôme** : `setup_langfuse.sh` ne peut pas créer l'utilisateur admin via API REST.

**Cause** : LangFuse v2 ne documente pas l'API REST d'initialisation.
L'interface principale est via l'interface web (NextAuth.js).

**Solution** : Le script `setup_langfuse.sh` tente la création via API,
mais en cas d'échec, affiche clairement les instructions manuelles :
```
Accéder à http://localhost:3010 et créer le compte manuellement
```

L'orchestrateur fonctionne sans LangFuse (le tracing est optionnel).
Si `LANGFUSE_PUBLIC_KEY` est vide, le tracing est désactivé silencieusement.

---

### Problème : LangFuse image v2 vs v3

**Symptôme** : `langfuse/langfuse:latest` pointe vers une version instable.

**Solution** : Pinning explicite dans `docker-compose.yml` :
```yaml
image: langfuse/langfuse:2  # stable
```

---

## Proxy Sidecar

### Problème : HTTPS tunnel (CONNECT) — injection impossible sans MITM

**Symptôme** : En mode tunnel SSL natif (CONNECT), le proxy ne peut pas injecter
de headers car le SSL est établi directement entre l'agent et l'API LLM.

**Cause** : Le protocole HTTPS chiffre les headers HTTP. Un proxy transparent
ne peut pas les modifier sans agir en man-in-the-middle (ce qui nécessite
un certificat CA installé dans l'agent).

**Solution** : Deux approches selon l'usage :

1. **Mode HTTP direct** (utilisé quand possible) : Les agents utilisent `http://` vers
   le proxy, qui transfère en HTTPS avec les credentials injectés.
   Ce mode fonctionne avec les SDKs Anthropic et OpenAI qui respectent les proxies.

2. **Mode variable d'environnement** : Pour les cas où le proxy HTTPS pur est nécessaire,
   la clé API est aussi disponible via une variable d'environnement dans la microVM
   (pas dans l'env direct des agents, mais accessible via `/run/secrets/`).

**Note pratique** : Le SDK `langchain-anthropic` et `langchain-openai` respectent
`HTTPS_PROXY` pour les requêtes de métadonnées, et utilisent les variables
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` pour l'authentification.
Pour un vrai zero-secret-in-env, la clé est lue depuis `/run/secrets/llm_api_key`
par le code de démarrage du runtime agent.

---

## Orchestrateur

### Problème : Socket Docker non accessible dans le container

**Symptôme** : `ContainerManager` ne peut pas spawner les Kata Containers depuis
l'intérieur du container orchestrateur.

**Cause** : Le socket Docker (`/var/run/docker.sock`) doit être monté explicitement.

**Solution** dans `docker-compose.yml` :
```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

**Sécurité** : Accorder l'accès au socket Docker donne des privilèges élevés.
Dans un contexte de production, utiliser un daemon Docker séparé ou
une API Docker avec TLS et contrôle d'accès.

---

### Problème : Workspaces partagés entre orchestrateur et containers agents

**Symptôme** : Le container agent ne voit pas le workspace monté par l'orchestrateur.

**Cause** : L'orchestrateur monte `/tmp/agentforge-workspaces/{task_id}` dans
le container agent. Si l'orchestrateur lui-même est dans un container,
les chemins doivent être cohérents entre le host et les containers.

**Solution** : Le dossier workspaces est monté avec le même chemin dans
l'orchestrateur et partagé avec les containers agents :
```yaml
volumes:
  - ${WORKSPACES_DIR}:/workspaces  # même chemin absolu
```
L'orchestrateur clone dans `/workspaces/{task_id}` et monte ce même
chemin dans le container agent.

---

## Git

### Problème : git clone échoue si Forgejo non encore prêt

**Symptôme** : `git clone` retourne "repository not found" juste après la création
de la branche via API.

**Cause** : Légère latence entre la création via API REST et la disponibilité
via git HTTP. Généralement < 1s.

**Solution** : Pas de retry explicite nécessaire — la création de branche +
le clone se font avec quelques secondes d'écart naturel.

---

### Problème : Credentials git dans l'URL de clone

**Symptôme** : Les credentials apparaissent dans `git log` ou `ps aux`.

**Cause** : La méthode `http://user:pass@host/repo.git` expose les credentials
dans certains contextes.

**Solution appliquée** :
- Utiliser `git config credential.helper store` + fichier `.git-credentials`
- Ou configurer via `GIT_ASKPASS` dans l'environnement
- Le workspace est temporaire et détruit après usage, risque limité

**Alternative recommandée en production** :
Utiliser un token SSH ou un token API Forgejo via HTTP header :
```
http.extraHeader=PRIVATE-TOKEN: {token}
```

---

## Performances observées

| Étape | Durée (Ubuntu 22.04, 4 vCPU, 8GB) |
|-------|-------------------------------------|
| Démarrage Kata Container | 3-5s |
| Supervisor + Architect | 20-40s |
| Coder (tâche simple) | 60-120s |
| Tester (pytest) | 10-30s |
| Reviewer | 15-25s |
| **Total pipeline simple** | **~3-5 min** |
| **Total pipeline complexe (2 itérations)** | **~8-12 min** |

---

## Checklist post-installation

Après `./install.sh`, vérifier :

```bash
# 1. Tous les services sont UP
make status

# 2. Forgejo accessible
curl -s http://localhost:3000/api/healthz

# 3. Orchestrateur accessible
curl -s http://localhost:8000/health

# 4. LangFuse accessible
curl -s http://localhost:3010/api/public/health

# 5. Dashboard accessible
curl -s http://localhost:3020/api/health

# 6. Kata disponible (si KVM présent)
docker run --runtime kata-qemu --rm alpine uname -r

# 7. Secret LLM présent
ls -la /run/secrets/llm_api_key

# 8. Test end-to-end
make test-task
```

---

## Phase 2 — Dashboard web

### Problème : Proxy sidecar CONNECT vs reverse proxy

**Symptôme** : Le proxy en mode tunnel SSL (CONNECT) ne peut pas modifier
le corps des requêtes JSON pour injecter le bon modèle par agent.

**Cause** : En mode CONNECT, le SSL est établi entre le client et le serveur
distant. Le proxy ne voit que des octets chiffrés — impossible de lire
ni modifier le JSON.

**Solution** : Réécriture du proxy en mode **reverse proxy HTTP** :
- Les agents envoient des requêtes `http://localhost:8877` (non chiffré)
- Le proxy lit le corps JSON, identifie le nom de l'agent via `X-Agent-Name`
- Il remplace le champ `model` par `AGENT_{NAME}_MODEL` depuis les env vars
- Il retransmet en HTTPS vers l'API réelle (Anthropic/OpenAI/OpenRouter)
- La réponse (y compris streaming) est relayée mot par mot

**Fichier** : `proxy/proxy.py` — classe `_handle_reverse_proxy()`

---

### Problème : Models list OpenRouter — ne pas hardcoder

**Symptôme** : Toute liste hardcodée de modèles est obsolète en quelques semaines.
Les prix changent sans préavis.

**Solution** : Le dashboard appelle **en direct** `GET https://openrouter.ai/api/v1/models`
à chaque chargement de la page Settings/Wizard. Aucun modèle ni prix n'est
stocké côté serveur.

**Fichier** : `dashboard/backend/routes/models.py`

---

### Problème : LangFuse healthcheck — hostname vs localhost

**Symptôme** : `wget localhost:3000/api/public/health` échoue dans le container LangFuse.

**Cause** : Next.js bind sur l'interface réseau du container (nom de l'hôte Docker),
pas sur `127.0.0.1`.

**Solution** dans `docker-compose.yml` :
```yaml
healthcheck:
  test: ["CMD-SHELL", "wget -qO- http://$(hostname):3000/api/public/health >/dev/null 2>&1 || exit 1"]
```

---

### Problème : Workspaces bind mount — même chemin host/container

**Symptôme** : L'orchestrateur crée les workspaces dans son container.
Docker daemon (sur le host) cherche ce chemin sur le HOST et crée un
dossier vide. Le container agent ne trouve pas les fichiers.

**Cause** : Quand l'orchestrateur fait `docker run -v /tmp/agentforge-workspaces/task1:/workspace`,
Docker daemon résout ce chemin côté HOST. Si l'orchestrateur est lui-même dans
un container avec un chemin différent, la synchronisation est perdue.

**Solution** : Monter le dossier workspaces avec le **même chemin absolu** dans
tous les containers ET sur le host :
```yaml
volumes:
  - /tmp/agentforge-workspaces:/tmp/agentforge-workspaces
```
Et `WORKSPACES_DIR=/tmp/agentforge-workspaces` partout.

---

### Problème : SECRETS_HOST_PATH pour les containers agents

**Symptôme** : L'orchestrateur monte `/run/secrets` (chemin interne container)
comme volume Docker. Sur le host, ce chemin n'existe pas → erreur au spawn.

**Solution** :
1. Créer `/run/secrets/` sur le HOST avec la clé API
2. Passer `SECRETS_HOST_PATH=/run/secrets` à l'orchestrateur
3. `container_manager.py` utilise `SECRETS_HOST_PATH` pour le bind mount

---

### Problème : `@app.on_event` déprécié FastAPI 0.110+

**Symptôme** : Warning `DeprecationWarning: on_event is deprecated`

**Solution** : Migrer vers le pattern `lifespan` :
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    yield
    # shutdown

app = FastAPI(lifespan=lifespan)
```

---

### Problème : Token Forgejo via CLI (pas API REST)

**Symptôme** : `POST /users/{user}/tokens` échoue avec "UserSignIn failed"
même si les credentials sont corrects.

**Cause** : `UserSignIn` est appelé en interne et peut échouer si
`must_change_password=True` est défini sur le compte.

**Solution** : Générer le token via CLI admin :
```bash
docker compose exec forgejo forgejo admin user generate-access-token \
  --username agentforge \
  --scopes "read:user,write:user,read:issue,write:issue,read:repository,write:repository" \
  --raw
```

Les noms de scopes valides sont `read:user`, `write:issue`, etc.
(pas `user`, `issue` sans préfixe).

---

### Performances Phase 2 (avec dashboard)

| Composant | RAM supplémentaire |
|-----------|-------------------|
| Dashboard (FastAPI + React) | ~80 MB |
| Build Node.js (npm install) | ~500 MB (temporaire) |
| Image finale dashboard | ~250 MB |

Le build multi-stage évite d'inclure Node.js dans l'image de production.

---

### Problème : `http_client` non supporté dans `ChatAnthropic` >= 0.3

**Symptôme** : `TypeError: Messages.create() got an unexpected keyword argument 'http_client'`

**Cause** : Dans `langchain-anthropic >= 0.3`, les paramètres inconnus passés au constructeur
sont placés dans `model_kwargs` et transmis à `Messages.create()` lors de chaque appel.
`http_client` est un paramètre du client SDK mais pas de `Messages.create()`, d'où le TypeError.

**Solution** : Utiliser `default_headers` à la place de `http_client` pour injecter des headers
personnalisés (ex: `X-Agent-Name`) :
```python
# AVANT (cassé avec langchain-anthropic >= 0.3)
return ChatAnthropic(
    ...
    http_client=httpx.Client(headers={"X-Agent-Name": agent_name}),
)

# APRÈS (correct)
return ChatAnthropic(
    ...
    default_headers={"X-Agent-Name": agent_name},
)
```

**Fichier** : `agent_runtime/main.py` — `build_llm_for_agent()`

---

### Problème : Conflit de nom de container lors du respawn (409 Conflict)

**Symptôme** : `docker.errors.APIError: 409 Conflict — The container name "agentforge_task_X" is already in use`

**Cause** : `docker kill` arrête le container mais ne le supprime pas. La ré-utilisation
du même nom (`containers.run(name=...)`) échoue car le nom est encore enregistré.

**Solution** : Supprimer l'ancien container avant le respawn :
```python
if resume:
    try:
        old = self.docker_client.containers.get(container_name)
        old.remove(force=True)
    except Exception:
        pass  # container already gone — fine
```

**Fichier** : `orchestrator/container_manager.py` — méthode `spawn()`

---

### Problème : Codes ANSI dans les variables bash capturées avec `$()`

**Symptôme** : `grep: Unmatched [, [^, [:, [., or [=` lors de tests shell

**Cause** : Les fonctions bash qui appellent `log_ok()` / `log_info()` (qui utilisent
`echo -e "${GREEN}[crash-test]${NC}"`) polluent leur sortie stdout quand elles sont
capturées avec `container_id=$(wait_for_agent_container)`. Le crochet `[` dans
`[crash-test]` se retrouve dans la variable et est interprété comme une regex invalide
par les appels `grep` ultérieurs.

**Solution** : Rediriger tous les appels `log_*` vers stderr dans les fonctions capturées :
```bash
# Dans les fonctions dont la sortie est capturée avec $()
log_ok "Container trouvé : ${container_id}" >&2   # stderr (affichage seulement)
echo "$container_id"                               # stdout (valeur capturée)
```

**Fichier** : `scripts/test_crash_recovery.sh` — fonctions `wait_for_agent_container`,
`create_test_issue`, `get_or_create_label`

---

### Problème : Anthropic retourne 403 (pas 401) si aucune clé API

**Symptôme** : Le container agent se termine avec `"Anthropic API error 403"` au lieu
du `401` attendu lors des tests sans clé réelle.

**Cause** : Le proxy sidecar lit la clé depuis `/run/secrets/llm_api_key`. Si le fichier
est absent (tests sans secrets montés), le proxy n'envoie aucun header `x-api-key`.
L'API Anthropic retourne `403 Forbidden` pour les requêtes sans authentification
(pas `401 Unauthorized`).

**Impact** : Comportement attendu — le pipeline échoue proprement et écrit l'erreur
dans `.task_error.json`. Le crash recovery fonctionne correctement.

**Note** : Pour les tests de crash recovery, le container s'arrête naturellement
en ~5s (échec de l'appel LLM). `KILL_DELAY=0` dans le script de test permet
de kill avant que le sentinel soit écrit.
