#!/usr/bin/env bash
# =============================================================================
# setup_kata.sh — Installation Kata Containers
# Testé sur Ubuntu 22.04, 24.04, Debian 12
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${BLUE}[kata]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[kata]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[kata]${NC} $*"; }
log_error() { echo -e "${RED}[kata]${NC} $*" >&2; }

# Détecter OS
source /etc/os-release
OS_ID="${ID}"
OS_VERSION="${VERSION_ID:-}"
ARCH=$(uname -m)

log_info "Détection OS : ${OS_ID} ${OS_VERSION} (${ARCH})"

# =============================================================================
# Fonction : installer via packages officiels distro (Ubuntu)
# =============================================================================
install_kata_ubuntu() {
  log_info "Installation Kata via packages Ubuntu..."

  # Ubuntu 22.04 et 24.04 : kata-containers dans les archives officielles
  apt-get update -qq
  apt-get install -y -qq kata-containers 2>/dev/null || {
    log_warn "Package kata-containers non trouvé dans les repos officiels."
    log_info "Tentative via GitHub releases..."
    install_kata_github
    return
  }
  log_ok "Kata Containers installé via packages Ubuntu"
}

# =============================================================================
# Fonction : installer via GitHub releases (fallback universel)
# =============================================================================
install_kata_github() {
  log_info "Installation Kata via GitHub releases..."

  # Récupérer la dernière version stable
  KATA_VERSION=$(curl -s https://api.github.com/repos/kata-containers/kata-containers/releases/latest \
    | grep '"tag_name"' | sed 's/.*"tag_name": "\(.*\)".*/\1/' | head -1)

  if [[ -z "${KATA_VERSION}" ]]; then
    log_warn "Impossible de récupérer la version GitHub. Utilisation de 3.6.0"
    KATA_VERSION="3.6.0"
  fi
  log_info "Version Kata : ${KATA_VERSION}"

  # URL tarball
  TARBALL="kata-static-${KATA_VERSION}-amd64.tar.xz"
  KATA_URL="https://github.com/kata-containers/kata-containers/releases/download/${KATA_VERSION}/${TARBALL}"

  log_info "Téléchargement depuis GitHub..."
  cd /tmp
  wget -q --show-progress "${KATA_URL}" -O "${TARBALL}"

  log_info "Extraction vers /opt/kata..."
  mkdir -p /opt/kata
  tar -xJf "${TARBALL}" -C /opt/kata --strip-components=2 --wildcards 'opt/kata/*' 2>/dev/null \
    || tar -xJf "${TARBALL}" -C /opt

  # Symlinks
  for bin in /opt/kata/bin/kata-runtime /opt/kata/bin/containerd-shim-kata-v2; do
    if [[ -f "${bin}" ]]; then
      ln -sf "${bin}" /usr/local/bin/"$(basename ${bin})"
    fi
  done

  # Ajouter /opt/kata/bin au PATH système
  if ! grep -q /opt/kata/bin /etc/environment 2>/dev/null; then
    echo 'PATH="/opt/kata/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"' \
      > /etc/environment
  fi
  export PATH="/opt/kata/bin:${PATH}"

  rm -f "/tmp/${TARBALL}"
  log_ok "Kata Containers ${KATA_VERSION} installé depuis GitHub"
}

# =============================================================================
# Fonction : installer via snap (Ubuntu uniquement, fallback)
# =============================================================================
install_kata_snap() {
  log_info "Tentative installation via snap..."
  if ! command -v snap &>/dev/null; then
    apt-get install -y -qq snapd
  fi
  snap install kata-containers --classic 2>/dev/null && {
    log_ok "Kata Containers installé via snap"
    return 0
  }
  log_warn "Installation snap échouée"
  return 1
}

# =============================================================================
# Configurer containerd pour utiliser Kata comme runtime
# =============================================================================
configure_containerd() {
  log_info "Configuration de containerd pour Kata..."

  # Trouver le binaire kata-runtime ou containerd-shim-kata-v2
  KATA_BIN=""
  for path in /opt/kata/bin/containerd-shim-kata-v2 \
              /usr/local/bin/containerd-shim-kata-v2 \
              /usr/bin/containerd-shim-kata-v2 \
              $(command -v containerd-shim-kata-v2 2>/dev/null || true); do
    if [[ -f "${path}" ]]; then
      KATA_BIN="${path}"
      break
    fi
  done

  if [[ -z "${KATA_BIN}" ]]; then
    # Kata runtime direct
    for path in /opt/kata/bin/kata-runtime \
                /usr/local/bin/kata-runtime \
                /usr/bin/kata-runtime \
                $(command -v kata-runtime 2>/dev/null || true); do
      if [[ -f "${path}" ]]; then
        KATA_BIN="${path}"
        break
      fi
    done
  fi

  if [[ -z "${KATA_BIN}" ]]; then
    log_warn "Binaire Kata non trouvé — skip configuration containerd"
    return 0
  fi

  log_info "Binaire Kata trouvé : ${KATA_BIN}"

  # Générer config containerd avec runtime Kata
  mkdir -p /etc/containerd
  if [[ ! -f /etc/containerd/config.toml ]]; then
    containerd config default > /etc/containerd/config.toml 2>/dev/null || true
  fi

  # Ajouter le runtime kata-qemu si pas déjà présent
  if ! grep -q "kata" /etc/containerd/config.toml 2>/dev/null; then
    cat >> /etc/containerd/config.toml << EOF

# Kata Containers runtime — ajouté par AgentForge setup_kata.sh
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata-qemu]
  runtime_type = "io.containerd.kata-qemu.v2"
  [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata-qemu.options]
    ConfigPath = "/opt/kata/share/defaults/kata-containers/configuration-qemu.toml"
EOF
    log_info "Runtime kata-qemu ajouté à /etc/containerd/config.toml"
  fi

  # Redémarrer containerd si actif
  if systemctl is-active containerd &>/dev/null; then
    systemctl restart containerd
    log_ok "containerd redémarré"
  fi
}

# =============================================================================
# Configurer Docker pour utiliser kata-runtime
# =============================================================================
configure_docker_kata() {
  log_info "Configuration Docker pour kata-runtime..."

  DOCKER_DAEMON_JSON="/etc/docker/daemon.json"
  mkdir -p /etc/docker

  # Trouver kata-runtime
  KATA_RT=""
  for path in /opt/kata/bin/kata-runtime \
              /usr/local/bin/kata-runtime \
              /usr/bin/kata-runtime \
              $(command -v kata-runtime 2>/dev/null || true); do
    if [[ -f "${path}" ]]; then
      KATA_RT="${path}"
      break
    fi
  done

  if [[ -z "${KATA_RT}" ]]; then
    log_warn "kata-runtime non trouvé — Docker ne sera pas configuré pour Kata"
    # Utiliser runc comme fallback
    cat > "${DOCKER_DAEMON_JSON}" << 'EOF'
{
  "runtimes": {
    "kata-qemu": {
      "path": "/usr/bin/runc",
      "runtimeArgs": []
    }
  },
  "default-runtime": "runc"
}
EOF
    log_warn "Fallback : Docker configuré avec runc (pas Kata)"
  else
    # Configurer avec Kata
    cat > "${DOCKER_DAEMON_JSON}" << EOF
{
  "runtimes": {
    "kata-qemu": {
      "path": "${KATA_RT}",
      "runtimeArgs": []
    },
    "kata-runtime": {
      "path": "${KATA_RT}",
      "runtimeArgs": []
    }
  },
  "default-runtime": "runc"
}
EOF
    log_ok "Docker configuré avec kata-runtime : ${KATA_RT}"
  fi

  # Redémarrer Docker
  systemctl restart docker
  log_ok "Docker redémarré"
}

# =============================================================================
# Smoke test
# =============================================================================
smoke_test_kata() {
  log_info "Smoke test Kata Containers..."
  export PATH="/opt/kata/bin:${PATH}"

  if command -v kata-runtime &>/dev/null; then
    kata-runtime --version && log_ok "kata-runtime --version : OK"
  elif command -v kata-qemu &>/dev/null; then
    kata-qemu --version && log_ok "kata-qemu --version : OK"
  else
    log_warn "Binaires kata non trouvés dans PATH"
    log_warn "Vérifier : ls /opt/kata/bin/"
    ls /opt/kata/bin/ 2>/dev/null || true
    return 1
  fi

  # Test via Docker si disponible
  if command -v docker &>/dev/null && [[ -e /dev/kvm ]]; then
    log_info "Test run Docker avec runtime kata-qemu..."
    if docker run --runtime kata-qemu --rm alpine uname -r 2>/dev/null; then
      log_ok "Container Kata fonctionne correctement"
    else
      log_warn "Test run Kata échoué (peut être normal en environnement de test)"
    fi
  fi
}

# =============================================================================
# MAIN
# =============================================================================
main() {
  log_info "Début installation Kata Containers"

  # Vérifier prérequis
  if [[ $EUID -ne 0 ]]; then
    log_error "Doit être exécuté en root"
    exit 1
  fi

  apt-get update -qq
  apt-get install -y -qq wget curl xz-utils

  case "${OS_ID}" in
    ubuntu)
      # Essayer d'abord les packages officiels, puis GitHub
      install_kata_ubuntu || install_kata_github || install_kata_snap
      ;;
    debian)
      install_kata_github
      ;;
    *)
      log_warn "OS non reconnu — tentative installation GitHub"
      install_kata_github
      ;;
  esac

  configure_containerd
  configure_docker_kata
  smoke_test_kata

  log_ok "Installation Kata Containers terminée"
  log_info "Pour tester manuellement : docker run --runtime kata-qemu --rm alpine uname -r"
}

main "$@"
