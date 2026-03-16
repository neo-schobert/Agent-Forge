#!/usr/bin/env bash
# =============================================================================
# setup_langfuse.sh — Initialisation LangFuse self-hosted
# - Attendre que LangFuse soit prêt
# - Créer un utilisateur admin
# - Créer le projet "agentforge"
# - Récupérer les clés API et les écrire dans .env
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a; source "${ENV_FILE}"; set +a
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${BLUE}[langfuse]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[langfuse]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[langfuse]${NC} $*"; }
log_error() { echo -e "${RED}[langfuse]${NC} $*" >&2; }

LANGFUSE_PORT="${LANGFUSE_PORT:-3010}"
# L'URL LangFuse depuis le host
LANGFUSE_URL="http://localhost:${LANGFUSE_PORT}"
# L'URL depuis l'intérieur du réseau Docker
LANGFUSE_INTERNAL_URL="http://langfuse:3000"

FORGEJO_ADMIN_EMAIL="${FORGEJO_ADMIN_EMAIL:-admin@agentforge.local}"
FORGEJO_ADMIN_PASS="${FORGEJO_ADMIN_PASS:-changeme}"

# =============================================================================
# Attendre que LangFuse soit accessible
# =============================================================================
wait_langfuse() {
  local max=120 elapsed=0
  log_info "Attente que LangFuse soit prêt sur ${LANGFUSE_URL}..."

  while ! curl -sf "${LANGFUSE_URL}/api/public/health" &>/dev/null; do
    sleep 5
    elapsed=$((elapsed + 5))
    if [[ $elapsed -ge $max ]]; then
      log_warn "LangFuse pas prêt après ${max}s — skip initialisation"
      log_warn "LangFuse peut être initialisé manuellement via http://YOUR_IP:${LANGFUSE_PORT}"
      return 1
    fi
    log_info "  ... attente LangFuse (${elapsed}/${max}s)"
  done
  log_ok "LangFuse accessible"
  return 0
}

# =============================================================================
# Créer le premier utilisateur admin dans LangFuse
# LangFuse v2 crée automatiquement un admin au premier démarrage si
# LANGFUSE_INIT_ORG_NAME / LANGFUSE_INIT_ORG_ID sont définis.
# Sinon, on passe par l'API.
# =============================================================================
create_langfuse_user() {
  log_info "Vérification/création utilisateur LangFuse..."

  # Essayer de se connecter avec l'email forgejo admin
  local login_response
  login_response=$(curl -sf -X POST "${LANGFUSE_URL}/api/auth/callback/credentials" \
    -H "Content-Type: application/json" \
    -d "{\"email\": \"${FORGEJO_ADMIN_EMAIL}\", \"password\": \"${FORGEJO_ADMIN_PASS}\"}" \
    -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")

  if [[ "${login_response}" =~ ^(200|302)$ ]]; then
    log_ok "Utilisateur LangFuse déjà existant"
    return 0
  fi

  # Créer l'utilisateur via l'API d'enregistrement
  log_info "Création de l'utilisateur admin LangFuse..."
  local reg_response
  reg_response=$(curl -sf -X POST "${LANGFUSE_URL}/api/auth/signup" \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"AgentForge Admin\",
      \"email\": \"${FORGEJO_ADMIN_EMAIL}\",
      \"password\": \"${FORGEJO_ADMIN_PASS}\",
      \"referralSource\": \"self-hosted\"
    }" 2>/dev/null || echo "")

  if [[ -n "${reg_response}" ]]; then
    log_ok "Utilisateur LangFuse créé"
  else
    log_warn "Création utilisateur LangFuse via API échouée"
    log_warn "LangFuse v2 : connectez-vous sur http://localhost:${LANGFUSE_PORT} et créez le compte manuellement"
  fi
}

# =============================================================================
# Créer le projet "agentforge" et récupérer les clés API
# =============================================================================
create_langfuse_project() {
  log_info "Création du projet 'agentforge' dans LangFuse..."

  # Se connecter pour obtenir un cookie de session
  local cookie_jar="/tmp/langfuse_cookies_$$.txt"
  local csrf_token=""

  # Récupérer le token CSRF
  local csrf_response
  csrf_response=$(curl -sf -c "${cookie_jar}" \
    "${LANGFUSE_URL}/api/auth/csrf" 2>/dev/null || echo "{}")

  csrf_token=$(echo "${csrf_response}" | python3 -c \
    "import sys, json; print(json.load(sys.stdin).get('csrfToken', ''))" 2>/dev/null || echo "")

  # Authentification
  curl -sf -X POST -b "${cookie_jar}" -c "${cookie_jar}" \
    "${LANGFUSE_URL}/api/auth/callback/credentials" \
    -H "Content-Type: application/json" \
    -d "{
      \"email\": \"${FORGEJO_ADMIN_EMAIL}\",
      \"password\": \"${FORGEJO_ADMIN_PASS}\",
      \"csrfToken\": \"${csrf_token}\",
      \"callbackUrl\": \"/\",
      \"json\": true
    }" &>/dev/null || true

  # Créer le projet via API
  local proj_response
  proj_response=$(curl -sf -X POST -b "${cookie_jar}" \
    "${LANGFUSE_URL}/api/projects" \
    -H "Content-Type: application/json" \
    -d '{"name": "agentforge"}' 2>/dev/null || echo "")

  local project_id=""
  if [[ -n "${proj_response}" ]]; then
    project_id=$(echo "${proj_response}" | python3 -c \
      "import sys, json; d=json.load(sys.stdin); print(d.get('id', ''))" 2>/dev/null || echo "")
    if [[ -n "${project_id}" ]]; then
      log_ok "Projet 'agentforge' créé (id: ${project_id})"
    fi
  fi

  # Si pas de project_id, récupérer le projet existant
  if [[ -z "${project_id}" ]]; then
    local list_response
    list_response=$(curl -sf -b "${cookie_jar}" \
      "${LANGFUSE_URL}/api/projects" 2>/dev/null || echo "{\"data\":[]}")
    project_id=$(echo "${list_response}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
projects = data.get('data', data) if isinstance(data, dict) else data
for p in (projects if isinstance(projects, list) else []):
    if p.get('name') == 'agentforge':
        print(p.get('id', ''))
        break
" 2>/dev/null || echo "")
  fi

  if [[ -z "${project_id}" ]]; then
    log_warn "Impossible de récupérer l'ID du projet LangFuse"
    log_warn "Créer manuellement les clés API sur http://localhost:${LANGFUSE_PORT}"
    rm -f "${cookie_jar}"
    return 0
  fi

  # Créer les clés API pour ce projet
  log_info "Création des clés API LangFuse pour le projet ${project_id}..."
  local api_key_response
  api_key_response=$(curl -sf -X POST -b "${cookie_jar}" \
    "${LANGFUSE_URL}/api/projects/${project_id}/apikeys" \
    -H "Content-Type: application/json" \
    -d '{"note": "agentforge-orchestrator"}' 2>/dev/null || echo "")

  if [[ -n "${api_key_response}" ]]; then
    local public_key secret_key
    public_key=$(echo "${api_key_response}" | python3 -c \
      "import sys, json; d=json.load(sys.stdin); print(d.get('publicKey', ''))" 2>/dev/null || echo "")
    secret_key=$(echo "${api_key_response}" | python3 -c \
      "import sys, json; d=json.load(sys.stdin); print(d.get('secretKey', ''))" 2>/dev/null || echo "")

    if [[ -n "${public_key}" ]] && [[ -n "${secret_key}" ]]; then
      # Écrire dans .env
      if grep -q "^LANGFUSE_PUBLIC_KEY=" "${ENV_FILE}" 2>/dev/null; then
        sed -i "s|^LANGFUSE_PUBLIC_KEY=.*|LANGFUSE_PUBLIC_KEY=${public_key}|" "${ENV_FILE}"
      else
        echo "LANGFUSE_PUBLIC_KEY=${public_key}" >> "${ENV_FILE}"
      fi
      if grep -q "^LANGFUSE_SECRET_KEY_API=" "${ENV_FILE}" 2>/dev/null; then
        sed -i "s|^LANGFUSE_SECRET_KEY_API=.*|LANGFUSE_SECRET_KEY_API=${secret_key}|" "${ENV_FILE}"
      else
        echo "LANGFUSE_SECRET_KEY_API=${secret_key}" >> "${ENV_FILE}"
      fi
      log_ok "Clés API LangFuse enregistrées dans .env"
      log_ok "  Public key : ${public_key}"
    else
      log_warn "Impossible d'extraire les clés API LangFuse — configurer manuellement"
    fi
  fi

  rm -f "${cookie_jar}"
}

# =============================================================================
# MAIN
# =============================================================================
main() {
  log_info "Début initialisation LangFuse"

  if ! wait_langfuse; then
    log_warn "LangFuse non disponible — skip initialisation automatique"
    log_warn "Accès manuel : http://localhost:${LANGFUSE_PORT}"
    exit 0
  fi

  create_langfuse_user
  create_langfuse_project

  log_ok "Initialisation LangFuse terminée"
  log_info "Dashboard : http://localhost:${LANGFUSE_PORT}"
  log_info "Identifiants : ${FORGEJO_ADMIN_EMAIL} / (mot de passe configuré)"
}

main "$@"
