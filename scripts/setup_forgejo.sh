#!/usr/bin/env bash
# =============================================================================
# setup_forgejo.sh — Initialisation Forgejo via API REST
# - Crée le compte admin (si premier démarrage)
# - Crée le repo de travail "agentforge-workspace"
# - Configure le webhook vers l'orchestrateur
# - Crée le label "agent-task"
# - Génère un token API et l'écrit dans .env
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a; source "${ENV_FILE}"; set +a
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${BLUE}[forgejo]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[forgejo]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[forgejo]${NC} $*"; }
log_error() { echo -e "${RED}[forgejo]${NC} $*" >&2; }

# Variables (avec fallbacks)
FORGEJO_DOMAIN="${FORGEJO_DOMAIN:-localhost}"
FORGEJO_PORT="${FORGEJO_PORT:-3000}"
FORGEJO_BASE_URL="http://${FORGEJO_DOMAIN}:${FORGEJO_PORT}"
FORGEJO_ADMIN_USER="${FORGEJO_ADMIN_USER:-admin}"
FORGEJO_ADMIN_PASS="${FORGEJO_ADMIN_PASS:-changeme}"
FORGEJO_ADMIN_EMAIL="${FORGEJO_ADMIN_EMAIL:-admin@agentforge.local}"
FORGEJO_WORKSPACE_REPO="${FORGEJO_WORKSPACE_REPO:-agentforge-workspace}"
ORCHESTRATOR_PORT="${ORCHESTRATOR_PORT:-8000}"
WEBHOOK_SECRET="${FORGEJO_WEBHOOK_SECRET:-change_this_webhook_secret}"

# URL API interne (depuis le host, pas depuis le container)
# En mode install.sh, Forgejo tourne sur localhost:FORGEJO_PORT
FORGEJO_API="${FORGEJO_BASE_URL}/api/v1"

# =============================================================================
# Helpers HTTP
# =============================================================================

# Requête authentifiée (user:pass ou token)
forgejo_curl() {
  local method="${1}"; shift
  local path="${1}";   shift
  local extra_args=("$@")

  if [[ -n "${FORGEJO_API_TOKEN:-}" ]]; then
    curl -s -X "${method}" "${FORGEJO_API}${path}" \
      -H "Authorization: token ${FORGEJO_API_TOKEN}" \
      -H "Content-Type: application/json" \
      "${extra_args[@]}" 2>/dev/null
  else
    curl -s -X "${method}" "${FORGEJO_API}${path}" \
      -u "${FORGEJO_ADMIN_USER}:${FORGEJO_ADMIN_PASS}" \
      -H "Content-Type: application/json" \
      "${extra_args[@]}" 2>/dev/null
  fi
}

# Vérification que Forgejo répond
wait_forgejo() {
  local max=240 elapsed=0
  log_info "Vérification que Forgejo répond sur ${FORGEJO_BASE_URL}..."
  while true; do
    # Essai 1 : URL externe via curl ou wget
    if curl -sf "${FORGEJO_BASE_URL}/api/healthz" &>/dev/null \
    || wget -qO- "${FORGEJO_BASE_URL}/api/healthz" &>/dev/null; then
      break
    fi
    # Essai 2 : accès interne via docker exec (plus fiable au démarrage)
    if docker exec agentforge_forgejo \
        curl -sf http://localhost:3000/api/healthz &>/dev/null 2>&1; then
      log_info "Forgejo répond en interne — API prête"
      break
    fi
    sleep 3
    elapsed=$((elapsed + 3))
    if [[ $elapsed -ge $max ]]; then
      log_error "Forgejo ne répond pas après ${max}s"
      exit 1
    fi
    log_info "  ... attente (${elapsed}/${max}s)"
  done
  log_ok "Forgejo accessible"
}

# =============================================================================
# Création compte admin (installation initiale Forgejo)
# =============================================================================
create_admin_account() {
  log_info "Vérification/création du compte admin Forgejo..."

  # IMPORTANT : "admin" est un nom réservé dans Forgejo (conflit avec /admin URL)
  # Utiliser un nom différent comme "agentforge", "forge_admin", etc.
  if [[ "${FORGEJO_ADMIN_USER}" == "admin" ]]; then
    log_warn "Le nom 'admin' est réservé dans Forgejo. Changement en 'agentforge'."
    FORGEJO_ADMIN_USER="agentforge"
    # Mettre à jour .env si possible
    sed -i "s|^FORGEJO_ADMIN_USER=.*|FORGEJO_ADMIN_USER=agentforge|" "${SCRIPT_DIR}/.env" 2>/dev/null || true
  fi

  # Tenter de se connecter (sans -f pour éviter exit code 22)
  local http_code
  http_code=$(curl -s -u "${FORGEJO_ADMIN_USER}:${FORGEJO_ADMIN_PASS}" \
    "${FORGEJO_API}/user" -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")

  if [[ "${http_code}" == "200" ]]; then
    log_ok "Compte admin '${FORGEJO_ADMIN_USER}' déjà existant"
    # S'assurer que must_change_password est false (requis pour la génération de token)
    docker exec --user git agentforge_forgejo \
      forgejo admin user change-password \
      --username "${FORGEJO_ADMIN_USER}" \
      --password "${FORGEJO_ADMIN_PASS}" \
      --must-change-password=false 2>/dev/null || true
    return 0
  fi

  log_info "Création du compte admin via CLI Forgejo (dans le container)..."
  # IMPORTANT : doit tourner en tant que 'git' (pas root) dans le container Forgejo
  docker exec --user git agentforge_forgejo \
    gitea admin user create \
    --username "${FORGEJO_ADMIN_USER}" \
    --password "${FORGEJO_ADMIN_PASS}" \
    --email "${FORGEJO_ADMIN_EMAIL}" \
    --admin \
    --must-change-password=false 2>/dev/null || {
      # Forgejo peut aussi s'appeler 'forgejo' selon la version de l'image
      docker exec --user git agentforge_forgejo \
        forgejo admin user create \
        --username "${FORGEJO_ADMIN_USER}" \
        --password "${FORGEJO_ADMIN_PASS}" \
        --email "${FORGEJO_ADMIN_EMAIL}" \
        --admin \
        --must-change-password=false 2>/dev/null || {
          log_warn "Création compte CLI échouée — le compte existe peut-être déjà"
        }
    }

  # Vérifier
  local check
  check=$(curl -s -u "${FORGEJO_ADMIN_USER}:${FORGEJO_ADMIN_PASS}" \
    "${FORGEJO_API}/user" -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")

  if [[ "${check}" == "200" ]]; then
    log_ok "Compte admin créé avec succès"
  else
    log_warn "Impossible de vérifier le compte admin (HTTP ${check}) — on continue"
  fi
}

# =============================================================================
# Générer un token API et l'enregistrer dans .env
# =============================================================================
generate_api_token() {
  log_info "Génération du token API Forgejo..."

  # Vérifier que le mot de passe ne nécessite pas de changement forcé
  # (Forgejo CLI change-password met must_change_password=true par défaut)
  docker exec --user git agentforge_forgejo \
    forgejo admin user change-password \
    --username "${FORGEJO_ADMIN_USER}" \
    --password "${FORGEJO_ADMIN_PASS}" \
    --must-change-password=false 2>/dev/null || true

  # Utiliser la CLI Forgejo pour générer le token (évite le bug UserSignIn)
  # Supprimer le token existant s'il y en a un (via nom unique)
  local token
  token=$(docker exec --user git agentforge_forgejo \
    forgejo admin user generate-access-token \
    --username "${FORGEJO_ADMIN_USER}" \
    --token-name "agentforge" \
    --scopes "read:user,write:user,read:issue,write:issue,read:repository,write:repository" \
    --raw 2>/dev/null || echo "")

  # Si le token existe déjà (nom pris), supprimer via API et recréer
  if [[ -z "${token}" ]] || echo "${token}" | grep -q "already"; then
    log_info "Token existant détecté — suppression et recréation..."
    # Supprimer l'ancien token via API (sans must_change_password restriction)
    curl -s -X DELETE \
      -H "Authorization: token ${FORGEJO_API_TOKEN:-}" \
      "${FORGEJO_API}/users/${FORGEJO_ADMIN_USER}/tokens/agentforge" \
      &>/dev/null || true
    # Retenter avec un nouveau nom temporaire puis supprimer
    local tmp_token
    tmp_token=$(docker exec --user git agentforge_forgejo \
      forgejo admin user generate-access-token \
      --username "${FORGEJO_ADMIN_USER}" \
      --token-name "agentforge-tmp" \
      --scopes "read:user,write:user,read:issue,write:issue,read:repository,write:repository" \
      --raw 2>/dev/null || echo "")
    if [[ -n "${tmp_token}" ]] && ! echo "${tmp_token}" | grep -q "Command error"; then
      # Utiliser le token tmp pour supprimer agentforge et recréer
      curl -s -X DELETE \
        -H "Authorization: token ${tmp_token}" \
        "${FORGEJO_API}/users/${FORGEJO_ADMIN_USER}/tokens/agentforge" \
        &>/dev/null || true
      token=$(docker exec --user git agentforge_forgejo \
        forgejo admin user generate-access-token \
        --username "${FORGEJO_ADMIN_USER}" \
        --token-name "agentforge" \
        --scopes "read:user,write:user,read:issue,write:issue,read:repository,write:repository" \
        --raw 2>/dev/null || echo "${tmp_token}")
    fi
  fi

  if [[ -n "${token}" ]] && ! echo "${token}" | grep -q "Command error"; then
    FORGEJO_API_TOKEN="${token}"
    # Mettre à jour .env
    if grep -q "^FORGEJO_API_TOKEN=" "${ENV_FILE}" 2>/dev/null; then
      sed -i "s|^FORGEJO_API_TOKEN=.*|FORGEJO_API_TOKEN=${token}|" "${ENV_FILE}"
    else
      echo "FORGEJO_API_TOKEN=${token}" >> "${ENV_FILE}"
    fi
    log_ok "Token API Forgejo enregistré dans .env"
  else
    log_warn "Impossible de générer le token — vérifier manuellement"
    log_warn "  Réponse: ${token:-vide}"
  fi
}

# =============================================================================
# Créer le repo de travail
# =============================================================================
create_workspace_repo() {
  log_info "Création du repo '${FORGEJO_WORKSPACE_REPO}'..."

  # Vérifier si le repo existe déjà
  local check
  check=$(forgejo_curl GET "/repos/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}" \
          -o /dev/null -w "%{http_code}" 2>/dev/null || echo "404")

  if [[ "${check}" == "200" ]]; then
    log_ok "Repo '${FORGEJO_WORKSPACE_REPO}' déjà existant"
    return 0
  fi

  local response
  response=$(forgejo_curl POST "/user/repos" \
    -d "{
      \"name\": \"${FORGEJO_WORKSPACE_REPO}\",
      \"description\": \"AgentForge — Workspace des agents IA\",
      \"private\": false,
      \"auto_init\": true,
      \"default_branch\": \"main\",
      \"readme\": \"Default\"
    }" 2>/dev/null || echo "")

  if echo "${response}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null | grep -q .; then
    log_ok "Repo '${FORGEJO_WORKSPACE_REPO}' créé"
  else
    log_warn "Création repo retourné : ${response:-vide}"
    log_warn "Le repo existe peut-être déjà — on continue"
  fi
}

# =============================================================================
# Configurer branch protection sur main
# =============================================================================
setup_branch_protection() {
  log_info "Configuration protection branche main..."

  forgejo_curl POST \
    "/repos/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/branch_protections" \
    -d '{
      "branch_name": "main",
      "enable_push": false,
      "enable_push_whitelist": true,
      "push_whitelist_usernames": [],
      "require_signed_commits": false,
      "required_approvals": 0
    }' &>/dev/null || log_warn "Branch protection déjà configurée (ignoré)"

  log_ok "Branch protection main configurée"
}

# =============================================================================
# Créer label "agent-task"
# =============================================================================
create_agent_task_label() {
  log_info "Création du label 'agent-task'..."

  # Vérifier si le label existe
  local existing
  existing=$(forgejo_curl GET \
    "/repos/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/labels" 2>/dev/null || echo "[]")

  if echo "${existing}" | python3 -c "
import sys, json
labels = json.load(sys.stdin)
found = any(l.get('name') == 'agent-task' for l in labels)
sys.exit(0 if found else 1)
" 2>/dev/null; then
    log_ok "Label 'agent-task' déjà existant"
    return 0
  fi

  forgejo_curl POST \
    "/repos/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/labels" \
    -d '{
      "name": "agent-task",
      "color": "#0075ca",
      "description": "Tâche à traiter par les agents IA"
    }' &>/dev/null && log_ok "Label 'agent-task' créé" \
    || log_warn "Création label échouée (peut déjà exister)"
}

# =============================================================================
# Créer le webhook vers l'orchestrateur
# =============================================================================
create_webhook() {
  log_info "Création du webhook Forgejo → Orchestrateur..."

  # URL du webhook — l'orchestrateur doit être accessible depuis Forgejo
  # Dans Docker Compose, l'orchestrateur est joignable via son nom de service
  local WEBHOOK_URL="http://orchestrator:${ORCHESTRATOR_PORT}/webhook"

  # Vérifier si un webhook vers l'orchestrateur existe déjà
  local existing_hooks
  existing_hooks=$(forgejo_curl GET \
    "/repos/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/hooks" 2>/dev/null || echo "[]")

  if echo "${existing_hooks}" | python3 -c "
import sys, json
hooks = json.load(sys.stdin)
found = any('orchestrator' in h.get('config', {}).get('url', '') for h in hooks)
sys.exit(0 if found else 1)
" 2>/dev/null; then
    log_ok "Webhook orchestrateur déjà configuré"
    return 0
  fi

  forgejo_curl POST \
    "/repos/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/hooks" \
    -d "{
      \"type\": \"forgejo\",
      \"active\": true,
      \"events\": [\"issues\"],
      \"config\": {
        \"url\": \"${WEBHOOK_URL}\",
        \"content_type\": \"json\",
        \"secret\": \"${WEBHOOK_SECRET}\",
        \"http_method\": \"POST\"
      }
    }" &>/dev/null && log_ok "Webhook créé → ${WEBHOOK_URL}" \
    || log_warn "Création webhook échouée — configurer manuellement dans Forgejo"
}

# =============================================================================
# Créer un fichier README initial dans le repo
# =============================================================================
create_initial_readme() {
  log_info "Vérification du README du workspace..."

  local check
  check=$(forgejo_curl GET \
    "/repos/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/contents/README.md" \
    -o /dev/null -w "%{http_code}" 2>/dev/null || echo "404")

  if [[ "${check}" == "200" ]]; then
    log_ok "README.md déjà présent dans le workspace"
    return 0
  fi

  # Encoder en base64
  local content
  content=$(cat << 'EOF' | base64 -w0
# AgentForge Workspace

Ce repository est le workspace de travail des agents AgentForge.

## Comment soumettre une tâche

1. Créer une issue avec le label `agent-task`
2. Décrire clairement la tâche dans le corps de l'issue
3. L'orchestrateur reçoit le webhook et démarre automatiquement le pipeline agents
4. Une Pull Request est créée automatiquement à la fin

## Labels

- `agent-task` : déclenche le pipeline agents IA
EOF
)

  forgejo_curl POST \
    "/repos/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/contents/README.md" \
    -d "{
      \"message\": \"Initial README\",
      \"content\": \"${content}\"
    }" &>/dev/null && log_ok "README workspace créé" || true
}

# =============================================================================
# MAIN
# =============================================================================
main() {
  log_info "Début initialisation Forgejo"

  wait_forgejo
  create_admin_account
  generate_api_token
  create_workspace_repo
  setup_branch_protection
  create_agent_task_label
  create_webhook
  create_initial_readme

  log_ok "Initialisation Forgejo terminée"
  log_info "URL : ${FORGEJO_BASE_URL}"
  log_info "Repo workspace : ${FORGEJO_BASE_URL}/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}"
}

main "$@"
