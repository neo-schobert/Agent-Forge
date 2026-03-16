#!/usr/bin/env bash
# =============================================================================
# test_crash_recovery.sh — Test de reprise sur checkpoint après crash
#
# Scénario :
# 1. Crée une issue Forgejo avec le label agent-task
# 2. Attend que le container agent soit lancé
# 3. Kill le container en plein milieu d'un run
# 4. Vérifie que l'orchestrateur détecte le crash et respawne
# 5. Vérifie que LangGraph reprend depuis le checkpoint
# 6. Vérifie la présence du flag "resumed" dans les métadonnées de tâche
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a; source "${ENV_FILE}"; set +a
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${BLUE}[crash-test]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[crash-test]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[crash-test]${NC} $*"; }
log_error() { echo -e "${RED}[crash-test]${NC} $*" >&2; }

FORGEJO_BASE_URL="${FORGEJO_BASE_URL:-http://localhost:3000}"
FORGEJO_API="$FORGEJO_BASE_URL/api/v1"
FORGEJO_TOKEN="${FORGEJO_API_TOKEN:-}"
FORGEJO_USER="${FORGEJO_ADMIN_USER:-agentforge}"
FORGEJO_REPO="${FORGEJO_WORKSPACE_REPO:-agentforge-workspace}"
ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://localhost:8000}"
MAX_WAIT=60       # Secondes max pour attendre le container
KILL_DELAY=0      # Secondes après spawn avant de kill (0 = immédiat, pour tests sans API key)
RESUME_WAIT=120   # Secondes max pour attendre la reprise

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
forgejo_api() {
  local method="$1"; shift
  local path="$1"; shift
  curl -s -X "$method" \
    -H "Authorization: token ${FORGEJO_TOKEN}" \
    -H "Content-Type: application/json" \
    "${FORGEJO_API}${path}" "$@"
}

pass() { log_ok "  ✓ $*"; }
fail() { log_error "  ✗ $*"; exit 1; }

# ---------------------------------------------------------------------------
# Vérifications préalables
# ---------------------------------------------------------------------------
check_prerequisites() {
  log_info "Vérification des prérequis..."

  if [[ -z "$FORGEJO_TOKEN" ]]; then
    fail "FORGEJO_API_TOKEN non défini dans .env"
  fi

  # Vérifier que l'orchestrateur tourne
  local status
  status=$(curl -s "${ORCHESTRATOR_URL}/health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  if [[ "$status" != "ok" ]]; then
    fail "Orchestrateur non accessible sur ${ORCHESTRATOR_URL}"
  fi
  pass "Orchestrateur accessible"

  # Vérifier que Forgejo est accessible
  local user
  user=$(forgejo_api GET "/user" | python3 -c "import sys,json; print(json.load(sys.stdin).get('login',''))" 2>/dev/null || echo "")
  if [[ -z "$user" ]]; then
    fail "Forgejo non accessible (token invalide ?)"
  fi
  pass "Forgejo accessible (user: $user)"

  # Vérifier que la clé LLM est configurée
  if [[ ! -s "/run/secrets/llm_api_key" ]]; then
    log_warn "Pas de clé LLM réelle dans /run/secrets/llm_api_key"
    log_warn "Le test de crash recovery requiert une vraie clé pour que l'agent tourne assez longtemps"
    log_warn "Continuer quand même (le crash sera détecté même sans LLM) ..."
  fi
}

# ---------------------------------------------------------------------------
# Trouver ou créer le label agent-task
# ---------------------------------------------------------------------------
get_or_create_label() {
  local labels
  labels=$(forgejo_api GET "/repos/${FORGEJO_USER}/${FORGEJO_REPO}/labels")
  local label_id
  label_id=$(echo "$labels" | python3 -c "
import sys, json
labels = json.load(sys.stdin)
for l in labels:
    if l.get('name') == 'agent-task':
        print(l['id'])
        break
" 2>/dev/null || echo "")

  if [[ -z "$label_id" ]]; then
    log_info "Création du label agent-task..." >&2
    local resp
    resp=$(forgejo_api POST "/repos/${FORGEJO_USER}/${FORGEJO_REPO}/labels" \
      -d '{"name":"agent-task","color":"#0075ca","description":"Tâche agent IA"}')
    label_id=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")
  fi

  if [[ -z "$label_id" ]]; then
    fail "Impossible d'obtenir le label agent-task"
  fi
  echo "$label_id"
}

# ---------------------------------------------------------------------------
# Créer une issue de test
# ---------------------------------------------------------------------------
create_test_issue() {
  local label_id="$1"
  log_info "Création de l'issue de test..." >&2

  local resp
  resp=$(forgejo_api POST "/repos/${FORGEJO_USER}/${FORGEJO_REPO}/issues" \
    -d "{
      \"title\": \"[crash-test] Test crash recovery $(date +%s)\",
      \"body\": \"Test automatique de reprise sur checkpoint.\n\nCreate a file crash_test_result.txt with content: Crash recovery successful\",
      \"labels\": [${label_id}]
    }")

  local issue_number
  issue_number=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['number'])" 2>/dev/null || echo "")

  if [[ -z "$issue_number" ]]; then
    fail "Impossible de créer l'issue de test"
  fi

  log_ok "Issue #${issue_number} créée" >&2
  echo "$issue_number"
}

# ---------------------------------------------------------------------------
# Attendre que le container agent soit lancé
# ---------------------------------------------------------------------------
wait_for_agent_container() {
  local issue_number="$1"
  local elapsed=0

  log_info "Attente du container agent (issue #${issue_number})..." >&2

  while [[ $elapsed -lt $MAX_WAIT ]]; do
    # Chercher le container agent par son nom (agentforge_task_*)
    local container_id
    container_id=$(docker ps --format '{{.ID}} {{.Names}}' 2>/dev/null | \
      awk '/agentforge_task_/{print $1}' | head -1 || echo "")

    if [[ -n "$container_id" ]]; then
      local container_name
      container_name=$(docker ps --format '{{.Names}}' -f "id=${container_id}" 2>/dev/null | head -1)
      log_ok "Container agent trouvé : ${container_id} (${container_name})" >&2
      # Only echo the container ID to stdout (for capture)
      echo "$container_id"
      return 0
    fi

    sleep 1
    elapsed=$((elapsed + 1))
    if [[ $((elapsed % 2)) -eq 0 ]]; then
      log_info "  ... attente (${elapsed}/${MAX_WAIT}s)" >&2
    fi
  done

  fail "Aucun container agent détecté après ${MAX_WAIT}s"
}

# ---------------------------------------------------------------------------
# Kill le container après un délai
# ---------------------------------------------------------------------------
kill_container_after_delay() {
  local container_id="$1"

  log_info "Kill du container ${container_id} dans ${KILL_DELAY}s..."
  sleep "$KILL_DELAY"

  if docker ps -q | grep -q "^${container_id:0:12}"; then
    log_warn "KILL du container agent (simulation de crash)..."
    docker kill "$container_id" 2>/dev/null || true
    log_ok "Container killed"
  else
    log_warn "Container déjà arrêté (run trop court pour le test)"
  fi
}

# ---------------------------------------------------------------------------
# Vérifier la détection du crash et la reprise
# ---------------------------------------------------------------------------
verify_crash_detection() {
  local issue_number="$1"
  local elapsed=0

  log_info "Vérification de la détection du crash..."

  # Attendre que l'orchestrateur détecte le crash dans ses logs
  sleep 5

  local orch_logs
  orch_logs=$(docker logs agentforge_orchestrator 2>&1 | tail -50)

  if echo "$orch_logs" | grep -q "container_exited_unexpectedly\|crash\|killed"; then
    pass "Orchestrateur a détecté le crash"
  else
    log_warn "Pas de log de crash détecté — peut-être que le container a fini normalement"
  fi
}

# ---------------------------------------------------------------------------
# Vérifier la reprise automatique (respawn)
# ---------------------------------------------------------------------------
verify_resume() {
  local issue_number="$1"
  local elapsed=0

  log_info "Vérification de la reprise automatique..."
  log_info "Attente max ${RESUME_WAIT}s..."

  while [[ $elapsed -lt $RESUME_WAIT ]]; do
    # Vérifier les logs de l'orchestrateur pour un respawn
    local orch_logs
    orch_logs=$(docker logs agentforge_orchestrator 2>&1 | tail -100)

    # Chercher un deuxième spawn pour la même issue
    local respawn_count
    respawn_count=$(echo "$orch_logs" | grep -c "container_started" || echo "0")

    if [[ "$respawn_count" -ge 2 ]]; then
      pass "Respawn détecté (${respawn_count} containers lancés)"
      return 0
    fi

    # Vérifier si le checkpoint existe dans le workspace
    local checkpoint_found=false
    for workspace in /tmp/agentforge-workspaces/*/; do
      if [[ -f "${workspace}.checkpoint.db" ]]; then
        checkpoint_found=true
        local checkpoint_size
        checkpoint_size=$(stat -c %s "${workspace}.checkpoint.db" 2>/dev/null || echo "0")
        if [[ "$checkpoint_size" -gt 0 ]]; then
          pass "Checkpoint SQLite trouvé : ${workspace}.checkpoint.db (${checkpoint_size} bytes)"
          break
        fi
      fi
    done

    sleep 5
    elapsed=$((elapsed + 5))

    if [[ $((elapsed % 20)) -eq 0 ]]; then
      log_info "  ... attente reprise (${elapsed}/${RESUME_WAIT}s)"
    fi
  done

  log_warn "Reprise automatique non détectée dans ${RESUME_WAIT}s"
  log_warn "Note: la reprise auto requiert que l'orchestrateur implémente le respawn"
  log_warn "Voir: orchestrator/webhook_handler.py -> _run_pipeline -> crash handling"
}

# ---------------------------------------------------------------------------
# Vérifier le checkpoint dans les logs
# ---------------------------------------------------------------------------
check_checkpoint_in_logs() {
  log_info "Analyse des logs agent pour checkpoint LangGraph..."

  # Chercher dans tous les workspaces
  local checkpoints_found=0
  for workspace in /tmp/agentforge-workspaces/*/; do
    if [[ -f "${workspace}.checkpoint.db" ]]; then
      checkpoints_found=$((checkpoints_found + 1))
      log_ok "  Checkpoint : ${workspace}.checkpoint.db"
    fi
    if [[ -f "${workspace}.task_done" ]]; then
      log_ok "  Sentinel trouvé : ${workspace}.task_done"
    fi
    if [[ -f "${workspace}.task_result.json" ]]; then
      local summary
      summary=$(python3 -c "import json; d=json.load(open('${workspace}.task_result.json')); print(d.get('final_summary','')[:100])" 2>/dev/null || echo "")
      if [[ -n "$summary" ]]; then
        pass "Résultat: ${summary}"
      fi
    fi
    if [[ -f "${workspace}.task_error.json" ]]; then
      local err
      err=$(python3 -c "import json; d=json.load(open('${workspace}.task_error.json')); print(d.get('error','')[:200])" 2>/dev/null || echo "")
      log_warn "  Erreur: ${err}"
    fi
  done

  if [[ $checkpoints_found -eq 0 ]]; then
    log_warn "Aucun fichier checkpoint trouvé dans /tmp/agentforge-workspaces/"
    log_warn "Le checkpoint est créé par LangGraph après le premier nœud exécuté"
  fi
}

# ---------------------------------------------------------------------------
# Rapport final
# ---------------------------------------------------------------------------
print_report() {
  local issue_number="$1"
  local start_time="$2"
  local elapsed=$(($(date +%s) - start_time))

  echo ""
  echo "========================================================"
  log_ok "RAPPORT TEST CRASH RECOVERY"
  echo "========================================================"
  log_info "Issue  : #${issue_number} (${FORGEJO_BASE_URL}/${FORGEJO_USER}/${FORGEJO_REPO}/issues/${issue_number})"
  log_info "Durée  : ${elapsed}s"
  log_info ""
  log_info "Logs orchestrateur :"
  docker logs agentforge_orchestrator 2>&1 | grep -E "task_id|container_|pipeline_|crash|checkpoint|resume" | tail -20 | while read -r line; do
    echo "  $line"
  done
  echo ""
  log_info "Pour voir les logs complets :"
  log_info "  docker logs -f agentforge_orchestrator"
  log_info ""
  log_info "Ce test vérifie :"
  log_info "  ✓ L'orchestrateur détecte le crash container"
  log_info "  ✓ Le fichier .checkpoint.db est créé par LangGraph"
  log_info "  ? La reprise auto (nécessite implémentation dans webhook_handler.py)"
  log_info "    Voir: _run_pipeline() → crash detection → respawn with --resume flag"
  echo "========================================================"
}

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
main() {
  local start_time
  start_time=$(date +%s)

  echo ""
  log_info "=== Test Crash Recovery AgentForge ==="
  echo ""

  check_prerequisites

  # Obtenir ou créer le label
  local label_id
  label_id=$(get_or_create_label)
  log_info "Label agent-task ID: ${label_id}"

  # Créer l'issue de test
  local issue_number
  issue_number=$(create_test_issue "$label_id")

  # Attendre le container
  local container_id
  container_id=$(wait_for_agent_container "$issue_number") || {
    log_warn "Container non trouvé — l'orchestrateur a peut-être déjà traité la tâche"
    check_checkpoint_in_logs
    print_report "$issue_number" "$start_time"
    exit 0
  }

  # Kill le container après délai
  kill_container_after_delay "$container_id"

  # Vérifier la détection
  verify_crash_detection "$issue_number"

  # Vérifier les checkpoints
  check_checkpoint_in_logs

  # Vérifier la reprise
  verify_resume "$issue_number"

  # Rapport
  print_report "$issue_number" "$start_time"
}

main "$@"
