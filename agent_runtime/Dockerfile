# =============================================================================
# AgentForge Agent Runtime — Image Docker
# Cette image est exécutée dans une microVM Kata Container par tâche
# =============================================================================

FROM python:3.11-slim

WORKDIR /app

# Dépendances système
# - git : pour les commits des agents Coder
# - curl : pour les health checks et appels API
# - bash : pour les scripts de test (Tester agent)
# - build-essential : pour certaines dépendances Python avec extensions C
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    bash \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Configurer git globalement (les agents font des commits)
RUN git config --global user.email "agents@agentforge.local" \
    && git config --global user.name "AgentForge Bot" \
    && git config --global init.defaultBranch main \
    && git config --global http.sslVerify false

# Installer les dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copier le code du runtime agent
COPY . .

# Créer le répertoire du proxy sidecar et y placer le script
# (proxy.py est copié depuis agent_runtime/proxy.py dans le contexte de build)
RUN mkdir -p /app/proxy && \
    if [ -f /app/proxy.py ]; then mv /app/proxy.py /app/proxy/proxy.py; fi

# Installer les dépendances du proxy sidecar (aiohttp si présent dans proxy/requirements.txt)
# Le proxy n'a besoin que d'aiohttp — déjà installé via requirements.txt de langchain

# Le workspace sera monté en bind mount depuis l'orchestrateur
# Point de montage attendu : /workspace
RUN mkdir -p /workspace

# Rendre le script de démarrage du proxy exécutable
RUN chmod +x /app/main.py 2>/dev/null || true

# Variables d'environnement par défaut (surchargées par l'orchestrateur)
ENV WORKSPACE_PATH=/workspace
ENV SENTINEL_FILE=/workspace/.task_done
ENV RESULT_FILE=/workspace/.task_result.json
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Point d'entrée : démarre le proxy sidecar PUIS le runtime agent
CMD ["python", "/app/main.py"]
