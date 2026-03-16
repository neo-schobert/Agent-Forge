# =============================================================================
# AgentForge — Makefile
# =============================================================================

.PHONY: start stop logs logs-orch logs-forgejo logs-langfuse \
        test-task clean update status build help

# Charger le .env si présent
ifneq (,$(wildcard ./.env))
  include .env
  export
endif

COMPOSE        := docker compose --env-file .env
COMPOSE_FILE   := docker-compose.yml
ENV_FILE       := .env
ORCH_PORT      ?= 8000
FORGEJO_PORT   ?= 3000
FORGEJO_DOMAIN ?= localhost

# =============================================================================
# Cibles principales
# =============================================================================

## Démarrer toute la stack
start:
	@echo "==> Démarrage AgentForge..."
	$(COMPOSE) up -d
	@echo ""
	@echo "Services démarrés :"
	@echo "  Forgejo       : http://$(FORGEJO_DOMAIN):$(FORGEJO_PORT)"
	@echo "  LangFuse      : http://$(FORGEJO_DOMAIN):$(LANGFUSE_PORT)"
	@echo "  Orchestrateur : http://$(FORGEJO_DOMAIN):$(ORCH_PORT)/health"

## Arrêter la stack (sans supprimer les volumes)
stop:
	@echo "==> Arrêt AgentForge..."
	$(COMPOSE) down
	@echo "Stack arrêtée. Les données sont conservées."

## Logs de tous les services (follow)
logs:
	$(COMPOSE) logs -f --tail=100

## Logs de l'orchestrateur uniquement
logs-orch:
	$(COMPOSE) logs -f --tail=200 orchestrator

## Logs Forgejo uniquement
logs-forgejo:
	$(COMPOSE) logs -f --tail=100 forgejo

## Logs LangFuse uniquement
logs-langfuse:
	$(COMPOSE) logs -f --tail=100 langfuse

## État de tous les containers
status:
	@echo "==> État des containers AgentForge :"
	@docker ps --filter "name=agentforge_" \
	  --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
	@echo ""
	@echo "==> Health des services :"
	@docker ps --filter "name=agentforge_" \
	  --format "  {{.Names}}: {{.Status}}" | grep -E "(healthy|unhealthy|starting)" || true

## Test end-to-end : crée une issue de test et attend la PR
test-task:
	@echo "==> Lancement du smoke test end-to-end..."
	bash scripts/test_task.sh

## Build des images Docker locales
build:
	@echo "==> Build des images Docker..."
	docker build -t agentforge_agent_runtime:latest ./agent_runtime/
	docker build -t agentforge_proxy:latest ./proxy/
	$(COMPOSE) build orchestrator
	@echo "==> Build terminé"

## git pull + rebuild + redémarrage
update:
	@echo "==> Mise à jour AgentForge..."
	git pull
	$(MAKE) build
	$(COMPOSE) up -d --force-recreate
	@echo "==> Mise à jour terminée"

## Arrêter + supprimer tous les volumes (DESTRUCTIF — perte de données)
clean:
	@echo "ATTENTION : Cette commande supprime tous les volumes Docker (données Forgejo, LangFuse, PostgreSQL)."
	@read -p "Continuer ? [y/N] " confirm && [ "$${confirm}" = "y" ] || exit 0
	$(COMPOSE) down -v
	docker volume prune -f --filter "label=com.docker.compose.project=agentforge"
	@echo "Nettoyage terminé."

## Recréer le fichier .env depuis .env.example
reset-env:
	@echo "Réinitialisation du fichier .env..."
	cp .env.example .env
	@echo ".env réinitialisé. Relancer ./install.sh pour reconfigurer."

## Vérifier la santé de tous les services
health:
	@echo "==> Vérification santé..."
	@curl -sf http://localhost:$(ORCH_PORT)/health && echo "  Orchestrateur : OK" || echo "  Orchestrateur : FAIL"
	@curl -sf http://localhost:$(FORGEJO_PORT)/api/healthz && echo "  Forgejo : OK" || echo "  Forgejo : FAIL"
	@curl -sf http://localhost:$(LANGFUSE_PORT)/api/public/health && echo "  LangFuse : OK" || echo "  LangFuse : FAIL"

## Ouvrir les URLs dans le navigateur (Linux avec xdg-open)
open:
	xdg-open "http://$(FORGEJO_DOMAIN):$(FORGEJO_PORT)" &
	xdg-open "http://$(FORGEJO_DOMAIN):$(LANGFUSE_PORT)" &

## Afficher cette aide
help:
	@echo ""
	@echo "AgentForge — Commandes disponibles"
	@echo "==================================="
	@echo ""
	@grep -E '^##' Makefile | sed 's/## /  /'
	@echo ""
	@echo "Exemples :"
	@echo "  make start       # démarrer la stack"
	@echo "  make test-task   # lancer un test complet"
	@echo "  make logs-orch   # surveiller l'orchestrateur"
	@echo "  make status      # voir l'état des containers"
	@echo ""

.DEFAULT_GOAL := help
