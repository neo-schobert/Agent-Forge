# =============================================================================
# AgentForge Proxy Sidecar — Image Docker
# Proxy HTTP transparent qui injecte les clés API LLM
# Les agents ne voient jamais les vraies clés API
# =============================================================================

FROM python:3.11-slim

WORKDIR /app

# Dépendances système
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Installer les dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copier le code du proxy
COPY proxy.py .

# Le secret est monté en read-only depuis le host via Docker volumes
# /run/secrets/llm_api_key contient la clé API brute (sans newline)
RUN mkdir -p /run/secrets

# Port du proxy (configurable)
EXPOSE 8877

ENV PYTHONUNBUFFERED=1

CMD ["python", "proxy.py"]
