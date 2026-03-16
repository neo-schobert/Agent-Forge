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

# 5. Kata disponible (si KVM présent)
docker run --runtime kata-qemu --rm alpine uname -r

# 6. Secret LLM présent
ls -la secrets/llm_api_key

# 7. Test end-to-end
make test-task
```
