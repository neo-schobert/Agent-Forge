#!/usr/bin/env bash
# =============================================================================
# test_task.sh — Smoke test end-to-end AgentForge
# - Crée une issue avec le label "agent-task" dans Forgejo
# - Attend que l'orchestrateur la traite
# - Vérifie qu'une PR est créée
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a; source "${ENV_FILE}"; set +a
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'; BOLD='\033[1m'
log_info()  { echo -e "${BLUE}[test]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[test]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[test]${NC} $*"; }
log_error() { echo -e "${RED}[test]${NC} $*" >&2; }

FORGEJO_DOMAIN="${FORGEJO_DOMAIN:-localhost}"
FORGEJO_PORT="${FORGEJO_PORT:-3000}"
FORGEJO_BASE_URL="http://${FORGEJO_DOMAIN}:${FORGEJO_PORT}"
FORGEJO_API="${FORGEJO_BASE_URL}/api/v1"
FORGEJO_ADMIN_USER="${FORGEJO_ADMIN_USER:-admin}"
FORGEJO_ADMIN_PASS="${FORGEJO_ADMIN_PASS:-changeme}"
FORGEJO_API_TOKEN="${FORGEJO_API_TOKEN:-}"
FORGEJO_WORKSPACE_REPO="${FORGEJO_WORKSPACE_REPO:-agentforge-workspace}"
ORCHESTRATOR_PORT="${ORCHESTRATOR_PORT:-8000}"

# Timeout max pour attendre la PR (en secondes)
MAX_WAIT="${1:-600}"

# =============================================================================
# Helpers
# =============================================================================
forgejo_curl() {
  local method="${1}"; shift
  local path="${1}";   shift
  local extra_args=("$@")

  if [[ -n "${FORGEJO_API_TOKEN:-}" ]]; then
    curl -sf -X "${method}" "${FORGEJO_API}${path}" \
      -H "Authorization: token ${FORGEJO_API_TOKEN}" \
      -H "Content-Type: application/json" \
      "${extra_args[@]}" 2>/dev/null
  else
    curl -sf -X "${method}" "${FORGEJO_API}${path}" \
      -u "${FORGEJO_ADMIN_USER}:${FORGEJO_ADMIN_PASS}" \
      -H "Content-Type: application/json" \
      "${extra_args[@]}" 2>/dev/null
  fi
}

# =============================================================================
# Vérification prérequis
# =============================================================================
check_services() {
  log_info "Vérification que les services sont actifs..."

  # Forgejo
  if ! curl -sf "${FORGEJO_BASE_URL}/api/healthz" &>/dev/null; then
    log_error "Forgejo non accessible sur ${FORGEJO_BASE_URL}"
    log_error "Lancer d'abord : make start"
    exit 1
  fi
  log_ok "Forgejo accessible"

  # Orchestrateur
  if ! curl -sf "http://localhost:${ORCHESTRATOR_PORT}/health" &>/dev/null; then
    log_error "Orchestrateur non accessible sur port ${ORCHESTRATOR_PORT}"
    log_error "Vérifier : make logs-orch"
    exit 1
  fi
  log_ok "Orchestrateur accessible"
}

# =============================================================================
# Récupérer l'ID du label "agent-task"
# =============================================================================
get_label_id() {
  local labels
  labels=$(forgejo_curl GET \
    "/repos/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/labels" 2>/dev/null || echo "[]")

  echo "${labels}" | python3 -c "
import sys, json
labels = json.load(sys.stdin)
for l in labels:
    if l.get('name') == 'agent-task':
        print(l.get('id', ''))
        break
" 2>/dev/null || echo ""
}

# =============================================================================
# Créer l'issue de test
# =============================================================================
create_test_issue() {
  log_info "Création de l'issue de test..."

  local label_id
  label_id=$(get_label_id)

  if [[ -z "${label_id}" ]]; then
    log_warn "Label 'agent-task' non trouvé — création de l'issue sans label"
    log_warn "Relancer scripts/setup_forgejo.sh pour créer le label"
  fi

  local timestamp
  timestamp=$(date +%Y%m%d_%H%M%S)

  local issue_body
  issue_body="Ceci est une tâche de test automatique générée par \`make test-task\`.

## Tâche demandée

Créer un fichier \`hello_world.py\` à la racine du repo qui :
1. Définit une fonction \`hello(name: str) -> str\` qui retourne \`\"Hello, {name}!\"\`
2. Affiche \`Hello, World!\` quand le script est exécuté directement
3. Inclut un test unitaire dans \`test_hello.py\` qui vérifie la fonction

## Critères d'acceptation
- [ ] \`hello_world.py\` créé avec la fonction \`hello()\`
- [ ] \`test_hello.py\` créé avec au moins un test
- [ ] Les tests passent (\`python -m pytest test_hello.py\`)

## Contexte
Test automatique AgentForge — ${timestamp}"

  local payload
  payload=$(python3 -c "
import json, sys
d = {
    'title': 'Test AgentForge - Hello World - ${timestamp}',
    'body': sys.stdin.read(),
    'labels': [${label_id:-}]
}
if not ${label_id:-0}:
    d.pop('labels')
print(json.dumps(d))
" <<< "${issue_body}" 2>/dev/null) || \
  payload="{\"title\": \"Test AgentForge - Hello World - ${timestamp}\", \"body\": \"Test automatique\"}"

  local issue_response
  issue_response=$(forgejo_curl POST \
    "/repos/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/issues" \
    -d "${payload}" 2>/dev/null || echo "")

  if [[ -z "${issue_response}" ]]; then
    log_error "Impossible de créer l'issue"
    exit 1
  fi

  local issue_number
  issue_number=$(echo "${issue_response}" | python3 -c \
    "import sys, json; print(json.load(sys.stdin).get('number', ''))" 2>/dev/null || echo "")

  if [[ -z "${issue_number}" ]]; then
    log_error "Impossible d'extraire le numéro de l'issue"
    echo "${issue_response}"
    exit 1
  fi

  echo "${issue_number}"
}

# =============================================================================
# Attendre qu'une PR soit créée pour cette issue
# =============================================================================
wait_for_pr() {
  local issue_number="${1}"
  local elapsed=0
  local check_interval=15

  log_info "Issue #${issue_number} créée. Attente de la Pull Request (timeout: ${MAX_WAIT}s)..."
  log_info "Suivre l'avancement :"
  log_info "  Logs orchestrateur : make logs-orch"
  log_info "  LangFuse : http://${FORGEJO_DOMAIN}:${LANGFUSE_PORT:-3010}"
  echo ""

  while true; do
    # Chercher les PRs liées à cette issue (branch task/{issue_number}-*)
    local prs
    prs=$(forgejo_curl GET \
      "/repos/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/pulls?state=open&limit=10" \
      2>/dev/null || echo "[]")

    local pr_number pr_url
    pr_number=$(echo "${prs}" | python3 -c "
import sys, json
prs = json.load(sys.stdin)
for pr in prs:
    branch = pr.get('head', {}).get('label', '')
    if 'task/${issue_number}-' in branch or 'task-${issue_number}-' in branch:
        print(pr.get('number', ''))
        break
" 2>/dev/null || echo "")

    if [[ -n "${pr_number}" ]]; then
      pr_url="${FORGEJO_BASE_URL}/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/pulls/${pr_number}"
      echo ""
      log_ok "Pull Request créée avec succès !"
      log_ok "  Issue  : ${FORGEJO_BASE_URL}/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/issues/${issue_number}"
      log_ok "  PR #${pr_number} : ${pr_url}"
      return 0
    fi

    # Afficher un indicateur de progression
    printf "\r${BLUE}[test]${NC} Attente PR... (${elapsed}/${MAX_WAIT}s) "

    sleep "${check_interval}"
    elapsed=$((elapsed + check_interval))

    if [[ ${elapsed} -ge ${MAX_WAIT} ]]; then
      echo ""
      log_warn "Timeout atteint (${MAX_WAIT}s) — pas de PR créée"
      log_warn "L'orchestrateur est peut-être encore en cours de traitement."
      log_warn "Vérifier : make logs-orch"
      log_warn "Issue : ${FORGEJO_BASE_URL}/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/issues/${issue_number}"
      return 1
    fi
  done
}

# =============================================================================
# MAIN
# =============================================================================
main() {
  echo -e "${BOLD}=== AgentForge — Smoke Test End-to-End ===${NC}"
  echo ""

  check_services

  local issue_number
  issue_number=$(create_test_issue)
  log_ok "Issue #${issue_number} créée"
  log_info "URL : ${FORGEJO_BASE_URL}/${FORGEJO_ADMIN_USER}/${FORGEJO_WORKSPACE_REPO}/issues/${issue_number}"

  if wait_for_pr "${issue_number}"; then
    echo ""
    echo -e "${BOLD}${GREEN}Test REUSSI${NC}"
    exit 0
  else
    echo ""
    echo -e "${BOLD}${YELLOW}Test INCOMPLET${NC} (PR pas encore visible, vérifier les logs)"
    exit 1
  fi
}

main "$@"
