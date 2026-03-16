#!/usr/bin/env bash
# =============================================================================
# AgentForge — Script de désinstallation complet
# Supprime tout ce qu'install.sh a créé.
# Usage : ./uninstall.sh [OPTIONS]
#
# Options :
#   --force          Pas de confirmation interactive
#   --dry-run        Affiche ce qui serait supprimé sans rien faire
#   --remove-docker  Désinstalle Docker même sans fichier sentinelle
#   --remove-kata    Désinstalle Kata Containers même sans fichier sentinelle
#   --keep-data      Conserve .env et les workspaces agents (pour post-mortem)
# =============================================================================

set -uo pipefail   # pas de -e : on continue même si une suppression échoue

# --- Couleurs -----------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_step()  { echo -e "\n${BOLD}${BLUE}==> $*${NC}"; }
log_dry()   { echo -e "${YELLOW}[DRY-RUN]${NC} Aurait supprimé : $*"; }
log_skip()  { echo -e "  ${YELLOW}↳ ignoré${NC} (absent ou non créé par AgentForge)"; }

# --- Variables globales -------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

FORCE=false
DRY_RUN=false
REMOVE_DOCKER=false
REMOVE_KATA=false
KEEP_DATA=false
COMPOSE=""

# Compteurs pour le rapport final
REMOVED=0
SKIPPED=0

# --- Parsing des arguments ----------------------------------------------------
for arg in "$@"; do
  case "$arg" in
    --force)         FORCE=true ;;
    --dry-run)       DRY_RUN=true ;;
    --remove-docker) REMOVE_DOCKER=true ;;
    --remove-kata)   REMOVE_KATA=true ;;
    --keep-data)     KEEP_DATA=true ;;
    --help|-h)
      echo "Usage: $0 [--force] [--dry-run] [--remove-docker] [--remove-kata] [--keep-data]"
      exit 0
      ;;
    *)
      log_error "Option inconnue : $arg"
      echo "Usage: $0 [--force] [--dry-run] [--remove-docker] [--remove-kata] [--keep-data]"
      exit 1
      ;;
  esac
done

# =============================================================================
# HELPERS
# =============================================================================

do_remove() {
  # do_remove <description> <command...>
  local desc="$1"; shift
  if $DRY_RUN; then
    log_dry "$desc"
  else
    if "$@" 2>/dev/null; then
      log_ok "$desc"
      REMOVED=$((REMOVED + 1))
    else
      log_warn "Échec (ignoré) : $desc"
    fi
  fi
}

# =============================================================================
# AVERTISSEMENT ET CONFIRMATION
# =============================================================================
print_warning() {
  echo ""
  echo -e "${BOLD}${RED}"
  echo "  ╔══════════════════════════════════════════════════════════════╗"
  echo "  ║            DÉSINSTALLATION AGENTFORGE                       ║"
  echo "  ╠══════════════════════════════════════════════════════════════╣"
  echo "  ║  Ce script va supprimer DÉFINITIVEMENT :                    ║"
  echo "  ║   • Tous les containers Docker agentforge_*                 ║"
  echo "  ║   • Toutes les images Docker buildées localement            ║"
  echo "  ║   • Tous les volumes Docker (données Forgejo, LangFuse...)  ║"
  echo "  ║   • Le network Docker agentforge_net                        ║"
  echo "  ║   • Les workspaces agents (/tmp/agentforge-workspaces/)     ║"
  echo "  ║   • Le fichier .env et secrets/llm_api_key                  ║"
  echo "  ║                                                              ║"
  echo "  ║  ⚠  Cette opération est IRRÉVERSIBLE.                       ║"
  echo "  ║     Toutes les données Forgejo et LangFuse seront perdues.  ║"
  echo "  ╚══════════════════════════════════════════════════════════════╝"
  echo -e "${NC}"
  if $KEEP_DATA; then
    log_info "Mode --keep-data : .env et workspaces seront conservés."
  fi
  if $DRY_RUN; then
    log_info "Mode --dry-run : aucune suppression ne sera effectuée."
  fi
  echo ""
}

confirm() {
  if $FORCE || $DRY_RUN; then
    return 0
  fi
  echo -e "${BOLD}Pour confirmer, tapez exactement :${NC} ${RED}SUPPRIMER${NC}"
  read -rp "> " answer
  if [[ "$answer" != "SUPPRIMER" ]]; then
    echo "Désinstallation annulée."
    exit 0
  fi
}

# =============================================================================
# DÉTECTION COMPOSE
# =============================================================================
detect_compose() {
  if docker compose version &>/dev/null 2>&1; then
    COMPOSE="docker compose"
  elif command -v docker-compose &>/dev/null; then
    COMPOSE="docker-compose"
  else
    COMPOSE=""  # compose non disponible, on utilisera docker direct
  fi
}

# =============================================================================
# SECTION A — Arrêt et suppression des containers
# =============================================================================
remove_containers() {
  log_step "Section A — Arrêt et suppression des containers"
  cd "${SCRIPT_DIR}"

  # docker compose down si compose disponible
  if [[ -n "$COMPOSE" ]]; then
    if $DRY_RUN; then
      log_dry "$COMPOSE down --volumes --remove-orphans"
    else
      log_info "Arrêt de la stack Docker Compose..."
      $COMPOSE down --volumes --remove-orphans 2>/dev/null || true
    fi
  fi

  # Forcer la suppression des containers résiduels par pattern de nom
  local found=false
  while IFS= read -r container_id; do
    [[ -z "$container_id" ]] && continue
    local cname
    cname=$(docker inspect --format '{{.Name}}' "$container_id" 2>/dev/null | tr -d '/')
    found=true
    if $DRY_RUN; then
      log_dry "docker rm -f $container_id  (${cname})"
    else
      docker stop "$container_id" 2>/dev/null || true
      docker rm -f "$container_id" 2>/dev/null || true
      log_ok "Container supprimé : ${cname} (${container_id:0:12})"
      REMOVED=$((REMOVED + 1))
    fi
  done < <(docker ps -aq --filter "name=agentforge" 2>/dev/null)

  # Containers de tâches agent (agentforge_task_*)
  while IFS= read -r container_id; do
    [[ -z "$container_id" ]] && continue
    local cname
    cname=$(docker inspect --format '{{.Name}}' "$container_id" 2>/dev/null | tr -d '/')
    found=true
    if $DRY_RUN; then
      log_dry "docker rm -f $container_id  (${cname})"
    else
      docker stop "$container_id" 2>/dev/null || true
      docker rm -f "$container_id" 2>/dev/null || true
      log_ok "Container tâche supprimé : ${cname} (${container_id:0:12})"
      REMOVED=$((REMOVED + 1))
    fi
  done < <(docker ps -aq --filter "name=agentforge_task_" 2>/dev/null)

  if ! $found && ! $DRY_RUN; then
    log_info "Aucun container agentforge_ trouvé."
    SKIPPED=$((SKIPPED + 1))
  fi
}

# =============================================================================
# SECTION B — Suppression des images Docker buildées localement
# =============================================================================
remove_images() {
  log_step "Section B — Suppression des images Docker locales AgentForge"

  # Liste exhaustive de toutes les variantes de nommage possibles
  # (docker build -t vs docker compose build, selon le nom du répertoire)
  local images=(
    "agentforge_orchestrator:latest"
    "agentforge_orchestrator:test"
    "agentforge_agent_runtime:latest"
    "agentforge_agent_runtime:test"
    "agentforge_proxy:latest"
    "agentforge_proxy:test"
    "agentforge_dashboard:latest"
    "agentforge-orchestrator:latest"
    "agentforge-agent-runtime:latest"
    "agentforge-proxy:latest"
    "agentforge-dashboard:latest"
    "agent-forge-orchestrator:latest"
    "agent-forge-agent-runtime:latest"
    "agent-forge-proxy:latest"
    "agent-forge-dashboard:latest"
  )

  local any_found=false
  for image in "${images[@]}"; do
    if docker image inspect "$image" &>/dev/null 2>&1; then
      any_found=true
      if $DRY_RUN; then
        log_dry "docker rmi -f $image"
      else
        docker rmi -f "$image" 2>/dev/null || true
        log_ok "Image supprimée : $image"
        REMOVED=$((REMOVED + 1))
      fi
    fi
  done

  if ! $any_found; then
    log_info "Aucune image AgentForge locale trouvée."
    SKIPPED=$((SKIPPED + 1))
  fi

  # Supprimer les images intermédiaires dangling (résidus de build)
  local dangling
  dangling=$(docker images -f "dangling=true" -q 2>/dev/null | wc -l)
  if [[ "$dangling" -gt 0 ]]; then
    if $DRY_RUN; then
      log_dry "docker image prune -f  (${dangling} images intermédiaires)"
    else
      docker image prune -f 2>/dev/null || true
      log_ok "Images intermédiaires (dangling) purgées"
    fi
  fi
}

# =============================================================================
# SECTION C — Suppression des volumes Docker
# =============================================================================
remove_volumes() {
  log_step "Section C — Suppression des volumes Docker AgentForge"

  local any_found=false
  while IFS= read -r volume; do
    [[ -z "$volume" ]] && continue
    any_found=true
    if $DRY_RUN; then
      log_dry "docker volume rm $volume"
    else
      docker volume rm "$volume" 2>/dev/null || true
      log_ok "Volume supprimé : $volume"
      REMOVED=$((REMOVED + 1))
    fi
  done < <(docker volume ls -q 2>/dev/null | grep -E "^agentforge|^agent.forge" || true)

  if ! $any_found; then
    log_info "Aucun volume agentforge trouvé."
    SKIPPED=$((SKIPPED + 1))
  fi
}

# =============================================================================
# SECTION D — Suppression du network Docker
# =============================================================================
remove_network() {
  log_step "Section D — Suppression du network Docker"

  local networks=("agentforge_net" "agent-forge_default")
  local any_found=false

  for net in "${networks[@]}"; do
    if docker network inspect "$net" &>/dev/null 2>&1; then
      any_found=true
      if $DRY_RUN; then
        log_dry "docker network rm $net"
      else
        docker network rm "$net" 2>/dev/null || true
        log_ok "Network supprimé : $net"
        REMOVED=$((REMOVED + 1))
      fi
    fi
  done

  if ! $any_found; then
    log_info "Aucun network agentforge trouvé."
    SKIPPED=$((SKIPPED + 1))
  fi
}

# =============================================================================
# SECTION E — Suppression des fichiers de données locaux
# =============================================================================
remove_data() {
  log_step "Section E — Suppression des données locales"

  if $KEEP_DATA; then
    log_warn "Mode --keep-data : données conservées (.env, workspaces, secrets)"
    SKIPPED=$((SKIPPED + 3))
    return 0
  fi

  # Workspaces des agents
  local workspaces_dir="/tmp/agentforge-workspaces"
  if [[ -f "${ENV_FILE}" ]]; then
    workspaces_dir=$(grep "^WORKSPACES_DIR=" "${ENV_FILE}" 2>/dev/null \
      | cut -d= -f2- | tr -d '"' || echo "/tmp/agentforge-workspaces")
    workspaces_dir="${workspaces_dir:-/tmp/agentforge-workspaces}"
  fi

  if [[ -d "$workspaces_dir" ]]; then
    local size
    size=$(du -sh "$workspaces_dir" 2>/dev/null | cut -f1 || echo "?")
    if $DRY_RUN; then
      log_dry "rm -rf $workspaces_dir  (${size})"
    else
      rm -rf "$workspaces_dir"
      log_ok "Workspaces agents supprimés : $workspaces_dir (${size} libérés)"
      REMOVED=$((REMOVED + 1))
    fi
  else
    log_info "Workspaces agents absents : $workspaces_dir"
    SKIPPED=$((SKIPPED + 1))
  fi

  # Également /tmp/agentforge-workspaces si différent
  if [[ "$workspaces_dir" != "/tmp/agentforge-workspaces" && -d "/tmp/agentforge-workspaces" ]]; then
    if $DRY_RUN; then
      log_dry "rm -rf /tmp/agentforge-workspaces"
    else
      rm -rf /tmp/agentforge-workspaces
      log_ok "Workspaces supprimés : /tmp/agentforge-workspaces"
      REMOVED=$((REMOVED + 1))
    fi
  fi

  # Secret runtime (/run/secrets/llm_api_key)
  if [[ -f /run/secrets/llm_api_key ]]; then
    if $DRY_RUN; then
      log_dry "rm -f /run/secrets/llm_api_key"
    else
      rm -f /run/secrets/llm_api_key
      log_ok "Secret runtime supprimé : /run/secrets/llm_api_key"
      REMOVED=$((REMOVED + 1))
    fi
    # Nettoyer /run/secrets/ si vide
    if [[ -d /run/secrets ]] && [[ -z "$(ls -A /run/secrets 2>/dev/null)" ]]; then
      rmdir /run/secrets 2>/dev/null || true
    fi
  else
    log_info "Secret runtime absent : /run/secrets/llm_api_key"
    SKIPPED=$((SKIPPED + 1))
  fi
}

# =============================================================================
# SECTION F — Suppression des fichiers de configuration du projet
# =============================================================================
remove_config() {
  log_step "Section F — Suppression des fichiers de configuration"

  if $KEEP_DATA; then
    log_warn "Mode --keep-data : configuration conservée"
    return 0
  fi

  # .env
  if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    if $DRY_RUN; then
      log_dry "rm -f ${SCRIPT_DIR}/.env"
    else
      rm -f "${SCRIPT_DIR}/.env"
      log_ok "Fichier .env supprimé"
      REMOVED=$((REMOVED + 1))
    fi
  else
    log_info ".env absent."
    SKIPPED=$((SKIPPED + 1))
  fi

  # secrets/llm_api_key dans le repo
  if [[ -f "${SCRIPT_DIR}/secrets/llm_api_key" ]]; then
    if $DRY_RUN; then
      log_dry "rm -f ${SCRIPT_DIR}/secrets/llm_api_key"
    else
      rm -f "${SCRIPT_DIR}/secrets/llm_api_key"
      log_ok "secrets/llm_api_key supprimé"
      REMOVED=$((REMOVED + 1))
    fi
  else
    log_info "secrets/llm_api_key absent."
    SKIPPED=$((SKIPPED + 1))
  fi

  # Dockerfile orchestrateur généré dynamiquement par install.sh
  # (seulement si généré automatiquement — vérifié par sentinelle)
  if [[ -f /tmp/.agentforge_generated_orchestrator_dockerfile ]]; then
    if $DRY_RUN; then
      log_dry "rm -f ${SCRIPT_DIR}/orchestrator/Dockerfile  (généré par install.sh)"
    else
      rm -f "${SCRIPT_DIR}/orchestrator/Dockerfile" 2>/dev/null || true
      rm -f /tmp/.agentforge_generated_orchestrator_dockerfile
      log_ok "orchestrator/Dockerfile (généré) supprimé"
      REMOVED=$((REMOVED + 1))
    fi
  fi
}

# =============================================================================
# SECTION G — Désinstallation optionnelle de Docker
# =============================================================================
remove_docker() {
  log_step "Section G — Désinstallation Docker (optionnelle)"

  local should_remove=false
  if [[ -f /tmp/.agentforge_installed_docker ]]; then
    log_info "Docker a été installé par AgentForge (sentinelle trouvée)."
    should_remove=true
  elif $REMOVE_DOCKER; then
    log_warn "Flag --remove-docker fourni — Docker sera désinstallé."
    should_remove=true
  else
    log_info "Docker n'a PAS été installé par AgentForge — conservé."
    log_info "(Utilisez --remove-docker pour forcer la suppression)"
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi

  if ! $should_remove; then return 0; fi

  if ! $FORCE && ! $DRY_RUN; then
    echo ""
    echo -e "${YELLOW}Docker va être DÉSINSTALLÉ. Cela supprimera TOUS les containers et images,${NC}"
    echo -e "${YELLOW}pas seulement ceux d'AgentForge.${NC}"
    read -rp "Confirmer la désinstallation de Docker ? [y/N] " answer
    [[ "${answer,,}" != "y" ]] && { log_info "Docker conservé."; SKIPPED=$((SKIPPED + 1)); return 0; }
  fi

  if $DRY_RUN; then
    log_dry "apt-get remove docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
    log_dry "rm -rf /var/lib/docker /etc/docker /etc/apt/sources.list.d/docker.list /etc/apt/keyrings/docker.gpg"
    return 0
  fi

  log_info "Désinstallation de Docker..."
  apt-get remove -y docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin 2>/dev/null || true
  apt-get autoremove -y 2>/dev/null || true
  rm -rf /var/lib/docker
  rm -rf /etc/docker
  rm -f /etc/apt/sources.list.d/docker.list
  rm -f /etc/apt/keyrings/docker.gpg
  rm -f /tmp/.agentforge_installed_docker
  log_ok "Docker désinstallé"
  REMOVED=$((REMOVED + 1))
}

# =============================================================================
# SECTION H — Désinstallation optionnelle de Kata Containers
# =============================================================================
remove_kata() {
  log_step "Section H — Désinstallation Kata Containers (optionnelle)"

  local should_remove=false
  if [[ -f /tmp/.agentforge_installed_kata ]]; then
    log_info "Kata Containers a été installé par AgentForge (sentinelle trouvée)."
    should_remove=true
  elif $REMOVE_KATA; then
    log_warn "Flag --remove-kata fourni — Kata sera désinstallé."
    should_remove=true
  else
    log_info "Kata Containers n'a PAS été installé par AgentForge — conservé."
    SKIPPED=$((SKIPPED + 1))
    return 0
  fi

  if ! $should_remove; then return 0; fi

  if ! $FORCE && ! $DRY_RUN; then
    read -rp "Confirmer la désinstallation de Kata Containers ? [y/N] " answer
    [[ "${answer,,}" != "y" ]] && { log_info "Kata conservé."; SKIPPED=$((SKIPPED + 1)); return 0; }
  fi

  if $DRY_RUN; then
    log_dry "snap remove kata-containers"
    return 0
  fi

  log_info "Désinstallation de Kata Containers..."
  snap remove kata-containers 2>/dev/null || true
  rm -f /tmp/.agentforge_installed_kata
  log_ok "Kata Containers désinstallé"
  REMOVED=$((REMOVED + 1))
}

# =============================================================================
# SECTION I — Nettoyage Docker buildx
# =============================================================================
remove_buildx() {
  log_step "Section I — Nettoyage Docker buildx"

  if [[ -f /tmp/.agentforge_installed_buildx ]]; then
    if $DRY_RUN; then
      log_dry "rm -f ~/.docker/cli-plugins/docker-buildx"
    else
      rm -f ~/.docker/cli-plugins/docker-buildx 2>/dev/null || true
      rm -f /tmp/.agentforge_installed_buildx
      log_ok "Docker buildx supprimé"
      REMOVED=$((REMOVED + 1))
    fi
  else
    log_info "Buildx n'a pas été installé par AgentForge — conservé."
    SKIPPED=$((SKIPPED + 1))
  fi
}

# =============================================================================
# SECTION J — Cache Docker de build (optionnel)
# =============================================================================
prune_build_cache() {
  log_step "Section J — Cache Docker de build (optionnel)"

  if ! command -v docker &>/dev/null; then
    log_info "Docker non disponible — saut."
    return 0
  fi

  echo ""
  docker system df 2>/dev/null || true
  echo ""

  if $FORCE || $DRY_RUN; then
    if $DRY_RUN; then
      log_dry "docker builder prune -f"
    else
      docker builder prune -f 2>/dev/null || true
      log_ok "Cache Docker purgé"
      REMOVED=$((REMOVED + 1))
    fi
    return 0
  fi

  read -rp "Purger aussi le cache Docker de build ? (libère de l'espace) [y/N] " answer
  if [[ "${answer,,}" == "y" ]]; then
    docker builder prune -f 2>/dev/null || true
    log_ok "Cache Docker de build purgé"
    REMOVED=$((REMOVED + 1))
  else
    log_info "Cache Docker conservé."
    SKIPPED=$((SKIPPED + 1))
  fi
}

# =============================================================================
# RAPPORT FINAL
# =============================================================================
print_report() {
  echo ""
  echo -e "${BOLD}${GREEN}"
  echo "  ╔═══════════════════════════════════════════════════════╗"
  if $DRY_RUN; then
    echo "  ║     DRY-RUN terminé — aucune suppression effectuée   ║"
  else
    echo "  ║         AgentForge désinstallé avec succès !         ║"
  fi
  echo "  ╚═══════════════════════════════════════════════════════╝"
  echo -e "${NC}"

  if ! $DRY_RUN; then
    echo -e "  ${GREEN}Éléments supprimés${NC} : ${REMOVED}"
    echo -e "  ${YELLOW}Éléments ignorés${NC}   : ${SKIPPED} (absents ou non créés par AgentForge)"
    echo ""
    echo -e "  ${BOLD}Le répertoire ${SCRIPT_DIR}/ n'a PAS été supprimé.${NC}"
    echo -e "  Pour supprimer aussi le code source :"
    echo -e "  ${BLUE}  rm -rf ${SCRIPT_DIR}${NC}"
    echo ""
    if $KEEP_DATA; then
      echo -e "  ${YELLOW}Mode --keep-data : .env et workspaces conservés pour analyse.${NC}"
    fi
  fi
}

# =============================================================================
# MAIN
# =============================================================================
main() {
  # Root requis
  if [[ $EUID -ne 0 ]]; then
    log_error "Ce script doit être lancé en root ou avec sudo."
    log_error "Usage : sudo ./uninstall.sh"
    exit 1
  fi

  cd "${SCRIPT_DIR}"

  print_warning
  confirm
  detect_compose

  remove_containers
  remove_images
  remove_volumes
  remove_network
  remove_data
  remove_config
  remove_docker
  remove_kata
  remove_buildx
  prune_build_cache

  print_report
}

main "$@"
