# =============================================================================
# AgentForge Dashboard — Dockerfile multi-stage
# Stage 1 : Node.js — build du frontend React
# Stage 2 : Python — serveur FastAPI + frontend statique
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 : Build frontend
# ---------------------------------------------------------------------------
FROM node:20-alpine AS frontend-build

WORKDIR /build

# Copier les fichiers de dépendances en premier (cache Docker)
COPY frontend/package.json ./
RUN npm install

# Copier le reste du frontend
COPY frontend/ ./

# Build de production
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 : Runtime Python
# ---------------------------------------------------------------------------
FROM python:3.11-slim

WORKDIR /app

# Dépendances système
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copier et installer les dépendances Python
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copier le code backend
COPY backend/ ./backend/

# Copier le frontend buildé depuis le stage 1
COPY --from=frontend-build /build/dist ./frontend/dist

# Variables d'environnement par défaut
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DASHBOARD_PORT=3020
ENV FRONTEND_DIST=/app/frontend/dist

# Health check
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:3020/api/health || exit 1

# Point d'entrée
WORKDIR /app/backend
CMD ["python", "main.py"]
