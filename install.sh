#!/usr/bin/env bash
# =============================================================================
# AgentForge — Script d'installation complet
# Testé sur : Ubuntu 22.04, Ubuntu 24.04, Debian 12
# Usage    : ./install.sh
# =============================================================================

set -euo pipefail

# --- Couleurs -----------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()      { echo -e "${GREEN}[OK]${NC}   $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_step()    { echo -e "\n${BOLD}${BLUE}==> $*${NC}"; }
log_banner()  {
  echo -e "${BOLD}${BLUE}"
  echo "  ╔═══════════════════════════════════════╗"
  echo "  ║         AgentForge Installer          ║"
  echo "  ║   Multi-agent AI Orchestration        ║"
  echo "  ╚═══════════════════════════════════════╝"
  echo -e "${NC}"
}

# --- Variables globales -------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
REQUIRED_RAM_MB=4096
MIN_DISK_GB=20
COMPOSE=""  # Set in detect_compose(); used everywhere

# =============================================================================
# ÉTAPE 0 — Vérifications prérequis
# =============================================================================
check_prerequisites() {
  log_step "Vérification des prérequis"

  # Root ou sudo
  if [[ $EUID -ne 0 ]]; then
    log_error "Ce script doit être lancé en root ou avec sudo."
    log_error "Usage : sudo ./install.sh"
    exit 1
  fi

  # OS supporté
  if [[ ! -f /etc/os-release ]]; then
    log_error "Impossible de détecter l'OS."
    exit 1
  fi
  source /etc/os-release
  case "${ID:-}" in
    ubuntu)
      case "${VERSION_ID:-}" in
        22.04|24.04) log_ok "OS supporté : Ubuntu ${VERSION_ID}" ;;
        *) log_warn "Ubuntu ${VERSION_ID:-?} non testé — on continue quand même" ;;
      esac
      ;;
    debian)
      case "${VERSION_ID:-}" in
        12) log_ok "OS supporté : Debian ${VERSION_ID}" ;;
        11) log_warn "Debian 11 non testé officiellement — on continue quand même" ;;
        *) log_warn "Debian ${VERSION_ID:-?} non testé — on continue quand même" ;;
      esac
      ;;
    *) log_warn "OS '${ID:-inconnu}' non testé — on continue quand même" ;;
  esac

  # Architecture
  ARCH=$(uname -m)
  if [[ "${ARCH}" != "x86_64" ]]; then
    log_warn "Architecture ${ARCH} détectée. Kata Containers nécessite x86_64 avec KVM."
    log_warn "On continue mais Kata pourrait ne pas fonctionner."
  fi

  # KVM
  if [[ ! -e /dev/kvm ]]; then
    log_warn "/dev/kvm absent — Kata Containers nécessite la virtualisation matérielle."
    log_warn "Sur un VPS, vérifiez que la virtualisation imbriquée est activée."
    log_warn "L'orchestrateur utilisera le runtime runc standard en fallback."
    KVM_AVAILABLE=false
  else
    log_ok "KVM disponible : /dev/kvm"
    KVM_AVAILABLE=true
  fi

  # RAM
  TOTAL_RAM_MB=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)
  if [[ ${TOTAL_RAM_MB} -lt ${REQUIRED_RAM_MB} ]]; then
    log_warn "RAM disponible : ${TOTAL_RAM_MB} MB (minimum recommandé : ${REQUIRED_RAM_MB} MB)"
    log_warn "On continue mais les performances seront limitées."
  else
    log_ok "RAM : ${TOTAL_RAM_MB} MB"
  fi

  # Disque (répertoire courant)
  DISK_FREE_GB=$(df -BG "${SCRIPT_DIR}" | awk 'NR==2 {gsub("G",""); print $4}')
  if [[ ${DISK_FREE_GB} -lt ${MIN_DISK_GB} ]]; then
    log_warn "Espace disque libre : ${DISK_FREE_GB} GB (minimum recommandé : ${MIN_DISK_GB} GB)"
  else
    log_ok "Disque libre : ${DISK_FREE_GB} GB"
  fi

  # Utilitaires de base
  for cmd in curl wget git openssl; do
    if ! command -v "${cmd}" &>/dev/null; then
      log_info "Installation de ${cmd}..."
      apt-get install -y "${cmd}" -qq
    fi
  done
  log_ok "Utilitaires de base présents"
}

# =============================================================================
# ÉTAPE 1 — Installation Docker
# =============================================================================
install_buildx() {
  if docker buildx version &>/dev/null; then
    return 0
  fi
  log_info "Installation Docker buildx plugin..."
  local buildx_url="https://api.github.com/repos/docker/buildx/releases/latest"
  local buildx_ver
  buildx_ver=$(curl -fsSL "$buildx_url" 2>/dev/null | grep tag_name | cut -d'"' -f4)
  if [[ -z "$buildx_ver" ]]; then
    log_warn "Impossible de récupérer la version de buildx — on continue sans"
    return 0
  fi
  mkdir -p ~/.docker/cli-plugins
  curl -fsSL \
    "https://github.com/docker/buildx/releases/download/${buildx_ver}/buildx-${buildx_ver}.linux-amd64" \
    -o ~/.docker/cli-plugins/docker-buildx 2>/dev/null || {
    log_warn "Téléchargement buildx échoué — on continue sans"
    return 0
  }
  chmod +x ~/.docker/cli-plugins/docker-buildx
  docker buildx install 2>/dev/null || true
  log_ok "Docker buildx installé : $(docker buildx version 2>/dev/null | head -1)"
}

install_docker() {
  log_step "Installation / vérification Docker"

  if command -v docker &>/dev/null; then
    DOCKER_VERSION=$(docker --version | awk '{print $3}' | tr -d ',')
    log_ok "Docker déjà installé : ${DOCKER_VERSION}"
  else
    log_info "Installation de Docker Engine..."
    # Méthode officielle Docker
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg lsb-release

    install -m 0755 -d /etc/apt/keyrings
    source /etc/os-release
    case "${ID}" in
      ubuntu|debian)
        curl -fsSL "https://download.docker.com/linux/${ID}/gpg" \
          | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        chmod a+r /etc/apt/keyrings/docker.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
          https://download.docker.com/linux/${ID} $(lsb_release -cs) stable" \
          > /etc/apt/sources.list.d/docker.list
        ;;
    esac

    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
      docker-buildx-plugin docker-compose-plugin

    systemctl enable docker
    systemctl start docker
    log_ok "Docker installé"
  fi

  # Vérification Docker Compose (plugin v2)
  if docker compose version &>/dev/null 2>&1; then
    log_ok "Docker Compose v2 disponible : $(docker compose version --short 2>/dev/null || true)"
  elif command -v docker-compose &>/dev/null; then
    log_ok "docker-compose (legacy) disponible"
  else
    log_info "Installation de docker-compose-plugin..."
    apt-get install -y -qq docker-compose-plugin
    log_ok "Docker Compose installé"
  fi

  # Détecter et stocker la commande compose dans la variable globale COMPOSE
  detect_compose

  # Test
  docker run --rm hello-world &>/dev/null && log_ok "Docker fonctionne correctement"

  # Buildx
  install_buildx
}

detect_compose() {
  if docker compose version &>/dev/null 2>&1; then
    COMPOSE="docker compose"
  elif command -v docker-compose &>/dev/null; then
    COMPOSE="docker-compose"
  else
    log_error "Ni 'docker compose' ni 'docker-compose' ne sont disponibles."
    exit 1
  fi
  log_info "Commande compose : ${COMPOSE}"
}

# =============================================================================
# ÉTAPE 2 — Installation Kata Containers
# =============================================================================
install_kata_runtime() {
  log_info "Installation Kata Containers..."
  if snap install kata-containers --classic 2>/dev/null; then
    log_ok "Kata Containers installé via snap"
    sed -i 's/^KATA_AVAILABLE=.*/KATA_AVAILABLE=true/' "${ENV_FILE}" 2>/dev/null || \
      echo "KATA_AVAILABLE=true" >> "${ENV_FILE}"
    return 0
  fi
  # Fallback : script dédié (GitHub releases)
  if bash "${SCRIPT_DIR}/scripts/setup_kata.sh" 2>/dev/null; then
    if kata-runtime --version &>/dev/null || kata-qemu --version &>/dev/null; then
      log_ok "Kata Containers installé via setup_kata.sh"
      sed -i 's/^KATA_AVAILABLE=.*/KATA_AVAILABLE=true/' "${ENV_FILE}" 2>/dev/null || \
        echo "KATA_AVAILABLE=true" >> "${ENV_FILE}"
      return 0
    fi
  fi
  log_warn "Installation Kata échouée — runc utilisé en fallback"
  sed -i 's/^KATA_AVAILABLE=.*/KATA_AVAILABLE=false/' "${ENV_FILE}" 2>/dev/null || \
    echo "KATA_AVAILABLE=false" >> "${ENV_FILE}"
}

install_kata() {
  log_step "Installation Kata Containers"

  # Kata déjà installé ?
  if command -v kata-runtime &>/dev/null || command -v kata-qemu &>/dev/null; then
    KATA_VER=$(kata-runtime --version 2>/dev/null | head -1 || kata-qemu --version 2>/dev/null | head -1 || echo "inconnu")
    log_ok "Kata Containers déjà installé : ${KATA_VER}"
    sed -i 's/^KATA_AVAILABLE=.*/KATA_AVAILABLE=true/' "${ENV_FILE}" 2>/dev/null || \
      echo "KATA_AVAILABLE=true" >> "${ENV_FILE}"
    return 0
  fi

  # Tenter d'activer KVM si le CPU supporte la virtualisation
  if [[ ! -e /dev/kvm ]]; then
    if grep -qE "vmx|svm" /proc/cpuinfo 2>/dev/null; then
      log_info "CPU supporte la virt, tentative d'activation KVM..."
      modprobe kvm 2>/dev/null || modprobe kvm-intel 2>/dev/null || modprobe kvm-amd 2>/dev/null || true
      sleep 1
    fi
  fi

  if [[ -e /dev/kvm ]]; then
    log_ok "KVM disponible — installation Kata Containers..."
    KVM_AVAILABLE=true
    install_kata_runtime
  else
    log_warn "KVM non disponible — Kata Containers désactivé"
    log_warn "Isolation microVM indisponible, runc sera utilisé (moins sécurisé)"
    log_info "Pour activer Kata sur un VPS : activez la virtualisation imbriquée"
    log_info "dans le panneau de contrôle de votre hébergeur, puis relancez install.sh"
    sed -i 's/^KATA_AVAILABLE=.*/KATA_AVAILABLE=false/' "${ENV_FILE}" 2>/dev/null || \
      echo "KATA_AVAILABLE=false" >> "${ENV_FILE}"
  fi
}

# =============================================================================
# ÉTAPE 3 — Configuration .env
# =============================================================================
configure_env() {
  log_step "Configuration de l'environnement (.env)"

  if [[ -f "${ENV_FILE}" ]]; then
    log_info ".env existant détecté."
    read -rp "Écraser le fichier .env existant ? [y/N] " overwrite
    if [[ "${overwrite,,}" != "y" ]]; then
      log_info "Conservation du .env existant."
      return 0
    fi
  fi

  cp "${SCRIPT_DIR}/.env.example" "${ENV_FILE}"
  log_info "Fichier .env créé depuis .env.example"

  echo ""
  echo -e "${BOLD}Configuration interactive${NC}"
  echo "Appuyez sur Entrée pour conserver la valeur par défaut entre [crochets]."
  echo ""

  # --- LLM Provider ---
  echo -e "${YELLOW}--- Fournisseur LLM ---${NC}"
  read -rp "LLM Provider (anthropic/openai/openrouter/ollama) [anthropic]: " llm_provider
  llm_provider="${llm_provider:-anthropic}"
  sed -i "s|^LLM_PROVIDER=.*|LLM_PROVIDER=${llm_provider}|" "${ENV_FILE}"

  case "${llm_provider}" in
    openrouter)
      read -rp "OPENROUTER_API_KEY (sk-or-...): " api_key
      if [[ -n "${api_key}" ]]; then
        sed -i "s|^OPENROUTER_API_KEY=.*|OPENROUTER_API_KEY=${api_key}|" "${ENV_FILE}"
        echo -n "${api_key}" > "${SCRIPT_DIR}/secrets/llm_api_key"
        chmod 600 "${SCRIPT_DIR}/secrets/llm_api_key"
        log_ok "Clé OpenRouter enregistrée dans secrets/llm_api_key"
      fi
      read -rp "Modèle (ex: anthropic/claude-3-5-sonnet, openai/gpt-4o) [anthropic/claude-sonnet-4-5]: " llm_model
      llm_model="${llm_model:-anthropic/claude-sonnet-4-5}"
      ;;
    anthropic)
      read -rp "ANTHROPIC_API_KEY (sk-ant-...): " api_key
      if [[ -n "${api_key}" ]]; then
        sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${api_key}|" "${ENV_FILE}"
        # Écrire aussi dans secrets/llm_api_key pour le proxy
        echo -n "${api_key}" > "${SCRIPT_DIR}/secrets/llm_api_key"
        chmod 600 "${SCRIPT_DIR}/secrets/llm_api_key"
        log_ok "Clé Anthropic enregistrée dans secrets/llm_api_key"
      fi
      read -rp "Modèle [claude-sonnet-4-6]: " llm_model
      llm_model="${llm_model:-claude-sonnet-4-6}"
      ;;
    openai)
      read -rp "OPENAI_API_KEY (sk-...): " api_key
      if [[ -n "${api_key}" ]]; then
        sed -i "s|^OPENAI_API_KEY=.*|OPENAI_API_KEY=${api_key}|" "${ENV_FILE}"
        echo -n "${api_key}" > "${SCRIPT_DIR}/secrets/llm_api_key"
        chmod 600 "${SCRIPT_DIR}/secrets/llm_api_key"
        log_ok "Clé OpenAI enregistrée dans secrets/llm_api_key"
      fi
      read -rp "Modèle [gpt-4o]: " llm_model
      llm_model="${llm_model:-gpt-4o}"
      ;;
    ollama)
      read -rp "OLLAMA_BASE_URL [http://localhost:11434]: " ollama_url
      ollama_url="${ollama_url:-http://localhost:11434}"
      sed -i "s|^OLLAMA_BASE_URL=.*|OLLAMA_BASE_URL=${ollama_url}|" "${ENV_FILE}"
      read -rp "Modèle Ollama [llama3]: " llm_model
      llm_model="${llm_model:-llama3}"
      # Pas de clé pour Ollama
      echo -n "ollama_no_key" > "${SCRIPT_DIR}/secrets/llm_api_key"
      chmod 600 "${SCRIPT_DIR}/secrets/llm_api_key"
      ;;
  esac
  sed -i "s|^LLM_MODEL=.*|LLM_MODEL=${llm_model:-claude-sonnet-4-6}|" "${ENV_FILE}"

  # --- Forgejo ---
  echo ""
  echo -e "${YELLOW}--- Forgejo (forge Git) ---${NC}"

  # Détecter l'IP publique
  PUBLIC_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
  read -rp "FORGEJO_DOMAIN (IP ou domaine) [${PUBLIC_IP}]: " forgejo_domain
  forgejo_domain="${forgejo_domain:-${PUBLIC_IP}}"
  sed -i "s|^FORGEJO_DOMAIN=.*|FORGEJO_DOMAIN=${forgejo_domain}|" "${ENV_FILE}"

  while true; do
    read -rp "FORGEJO_ADMIN_USER [agentforge]: " forgejo_user
    forgejo_user="${forgejo_user:-agentforge}"
    if [[ "${forgejo_user}" == "admin" ]]; then
      log_error "Le nom 'admin' est réservé par Forgejo. Choisissez un autre nom."
    else
      break
    fi
  done
  sed -i "s|^FORGEJO_ADMIN_USER=.*|FORGEJO_ADMIN_USER=${forgejo_user}|" "${ENV_FILE}"

  while true; do
    read -rsp "FORGEJO_ADMIN_PASS (min 8 caractères): " forgejo_pass
    echo ""
    if [[ ${#forgejo_pass} -ge 8 ]]; then
      break
    fi
    log_warn "Mot de passe trop court (min 8 caractères)"
  done
  sed -i "s|^FORGEJO_ADMIN_PASS=.*|FORGEJO_ADMIN_PASS=${forgejo_pass}|" "${ENV_FILE}"

  read -rp "FORGEJO_ADMIN_EMAIL [admin@agentforge.local]: " forgejo_email
  forgejo_email="${forgejo_email:-admin@agentforge.local}"
  sed -i "s|^FORGEJO_ADMIN_EMAIL=.*|FORGEJO_ADMIN_EMAIL=${forgejo_email}|" "${ENV_FILE}"

  # Générer webhook secret
  WEBHOOK_SECRET=$(openssl rand -hex 32)
  sed -i "s|^FORGEJO_WEBHOOK_SECRET=.*|FORGEJO_WEBHOOK_SECRET=${WEBHOOK_SECRET}|" "${ENV_FILE}"
  log_ok "Webhook secret généré automatiquement"

  # --- LangFuse ---
  echo ""
  echo -e "${YELLOW}--- LangFuse (tracing) ---${NC}"

  LANGFUSE_SECRET=$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-32)
  LANGFUSE_SALT=$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-32)
  sed -i "s|^LANGFUSE_SECRET_KEY=.*|LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET}|" "${ENV_FILE}"
  sed -i "s|^LANGFUSE_SALT=.*|LANGFUSE_SALT=${LANGFUSE_SALT}|" "${ENV_FILE}"
  log_ok "Clés LangFuse générées automatiquement"

  # --- DB passwords ---
  DB_PASS_FORGEJO=$(openssl rand -hex 16)
  DB_PASS_LANGFUSE=$(openssl rand -hex 16)
  sed -i "s|^POSTGRES_FORGEJO_PASS=.*|POSTGRES_FORGEJO_PASS=${DB_PASS_FORGEJO}|" "${ENV_FILE}"
  sed -i "s|^POSTGRES_LANGFUSE_PASS=.*|POSTGRES_LANGFUSE_PASS=${DB_PASS_LANGFUSE}|" "${ENV_FILE}"
  log_ok "Mots de passe PostgreSQL générés automatiquement"

  log_ok "Configuration .env terminée"
}

# =============================================================================
# ÉTAPE 4 — Build des images Docker
# =============================================================================
build_images() {
  log_step "Build des images Docker locales"
  cd "${SCRIPT_DIR}"

  # Créer le Dockerfile de l'orchestrateur s'il n'existe pas encore
  if [[ ! -f orchestrator/Dockerfile ]]; then
    cat > orchestrator/Dockerfile << 'DOCKERFILE'
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
DOCKERFILE
  fi

  log_info "Build image orchestrateur..."
  docker build -t agentforge_orchestrator:latest ./orchestrator/ -q

  log_info "Build image agent runtime..."
  docker build -t agentforge_agent_runtime:latest ./agent_runtime/ -q

  log_info "Build image proxy..."
  docker build -t agentforge_proxy:latest ./proxy/ -q

  log_info "Build image dashboard (React + FastAPI)..."
  $COMPOSE build dashboard
  log_ok "Image dashboard construite"

  log_ok "Images Docker construites"
}

# =============================================================================
# ÉTAPE 5 — Démarrage stack Docker Compose
# =============================================================================
start_stack() {
  log_step "Démarrage de la stack Docker Compose"
  cd "${SCRIPT_DIR}"

  # Créer le dossier workspaces
  source "${ENV_FILE}"
  WORKSPACES="${WORKSPACES_DIR:-/tmp/agentforge-workspaces}"
  mkdir -p "${WORKSPACES}"
  chmod 777 "${WORKSPACES}"

  # Démarrer sans l'orchestrateur d'abord (Forgejo et LangFuse doivent être up)
  log_info "Démarrage PostgreSQL + Forgejo + LangFuse..."
  $COMPOSE up -d postgres_forgejo postgres_langfuse forgejo langfuse

  # Attendre que Forgejo soit healthy
  log_info "Attente que Forgejo soit prêt (peut prendre 60s)..."
  TIMEOUT=120
  ELAPSED=0
  while ! docker exec agentforge_forgejo \
    curl -sf http://localhost:3000/api/healthz &>/dev/null; do
    sleep 5
    ELAPSED=$((ELAPSED + 5))
    if [[ ${ELAPSED} -ge ${TIMEOUT} ]]; then
      log_error "Forgejo n'a pas démarré après ${TIMEOUT}s"
      $COMPOSE logs forgejo | tail -20
      exit 1
    fi
    log_info "  ... attente (${ELAPSED}/${TIMEOUT}s)"
  done
  log_ok "Forgejo est prêt"

  # Attendre LangFuse
  log_info "Attente que LangFuse soit prêt..."
  ELAPSED=0
  while ! docker exec agentforge_langfuse \
    wget -qO- "http://$(docker exec agentforge_langfuse hostname 2>/dev/null || echo localhost):3000/api/public/health" &>/dev/null; do
    sleep 5
    ELAPSED=$((ELAPSED + 5))
    if [[ ${ELAPSED} -ge ${TIMEOUT} ]]; then
      log_warn "LangFuse n'est pas encore prêt — on continue quand même"
      break
    fi
    log_info "  ... attente LangFuse (${ELAPSED}/${TIMEOUT}s)"
  done
  log_ok "LangFuse est prêt (ou timeout ignoré)"
}

# =============================================================================
# ÉTAPE 6 — Initialisation Forgejo via API
# =============================================================================
init_forgejo() {
  log_step "Initialisation Forgejo via API"
  bash "${SCRIPT_DIR}/scripts/setup_forgejo.sh"
}

# =============================================================================
# ÉTAPE 7 — Initialisation LangFuse
# =============================================================================
init_langfuse() {
  log_step "Initialisation LangFuse"
  bash "${SCRIPT_DIR}/scripts/setup_langfuse.sh"
}

# =============================================================================
# ÉTAPE 8 — Démarrage orchestrateur
# =============================================================================
start_orchestrator() {
  log_step "Démarrage de l'orchestrateur"
  cd "${SCRIPT_DIR}"

  $COMPOSE up -d orchestrator

  log_info "Attente que l'orchestrateur soit prêt..."
  TIMEOUT=60
  ELAPSED=0
  while ! curl -sf "http://localhost:${ORCHESTRATOR_PORT:-8000}/health" &>/dev/null; do
    sleep 3
    ELAPSED=$((ELAPSED + 3))
    if [[ ${ELAPSED} -ge ${TIMEOUT} ]]; then
      log_warn "Orchestrateur pas encore healthy — vérifier avec 'make logs-orch'"
      return 0
    fi
  done
  log_ok "Orchestrateur prêt"
}

start_dashboard() {
  log_step "Démarrage du dashboard"
  cd "${SCRIPT_DIR}"

  $COMPOSE up -d dashboard

  log_info "Attente que le dashboard soit prêt (build React inclus, 60s max)..."
  TIMEOUT=120
  ELAPSED=0
  while ! curl -sf "http://localhost:${DASHBOARD_PORT:-3020}/api/health" &>/dev/null; do
    sleep 5
    ELAPSED=$((ELAPSED + 5))
    if [[ ${ELAPSED} -ge ${TIMEOUT} ]]; then
      log_warn "Dashboard pas encore prêt — vérifier avec : $COMPOSE logs dashboard"
      return 0
    fi
    log_info "  ... attente dashboard (${ELAPSED}/${TIMEOUT}s)"
  done
  log_ok "Dashboard prêt"
}

# =============================================================================
# ÉTAPE 9 — Résumé final
# =============================================================================
print_summary() {
  source "${ENV_FILE}"
  local domain="${FORGEJO_DOMAIN:-localhost}"
  local dash_port="${DASHBOARD_PORT:-3020}"
  local forgejo_port="${FORGEJO_PORT:-3000}"
  local langfuse_port="${LANGFUSE_PORT:-3010}"
  local orch_port="${ORCHESTRATOR_PORT:-8000}"

  echo ""
  echo -e "${BOLD}${GREEN}"
  echo "  ╔═══════════════════════════════════════════════════════╗"
  echo "  ║          AgentForge installé avec succès !            ║"
  echo "  ╚═══════════════════════════════════════════════════════╝"
  echo -e "${NC}"
  echo -e "${GREEN}[OK]${NC} Dashboard     : http://${domain}:${dash_port}   ${BOLD}← COMMENCER ICI${NC}"
  echo -e "${GREEN}[OK]${NC} Forgejo       : http://${domain}:${forgejo_port}"
  echo -e "${GREEN}[OK]${NC} LangFuse      : http://${domain}:${langfuse_port}"
  echo -e "${GREEN}[OK]${NC} Orchestrateur : http://${domain}:${orch_port}/health"
  echo ""
  echo -e "${BOLD}→ Ouvrir le dashboard pour configurer les clés API et lancer une tâche.${NC}"
  echo ""
  echo -e "${BOLD}Pour lancer une tâche de test :${NC}"
  echo "  make test-task"
  echo ""
  echo -e "${BOLD}Logs :${NC}"
  echo "  make logs          # tous les services"
  echo "  make logs-orch     # orchestrateur uniquement"
  echo "  $COMPOSE logs dashboard --tail=50"
  echo ""
  echo -e "${BOLD}Identifiants Forgejo :${NC}"
  echo "  Utilisateur : ${FORGEJO_ADMIN_USER:-agentforge}"
  echo "  URL         : http://${domain}:${forgejo_port}"
  echo ""
}

# =============================================================================
# MAIN
# =============================================================================
main() {
  log_banner

  check_prerequisites
  install_docker
  install_kata

  configure_env

  # Recharger .env
  set -a
  source "${ENV_FILE}"
  set +a

  build_images
  start_stack
  init_forgejo
  init_langfuse
  start_orchestrator
  start_dashboard
  print_summary
}

main "$@"
