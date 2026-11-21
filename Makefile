# Convenience targets. Requires Docker with the Compose v2 plugin.
.PHONY: up down build logs ps rebuild test

up:  ## Build and start the whole stack (backend :8000, frontend :3000)
	docker compose up --build -d
	@echo ""
	@echo "  Backend:  http://localhost:8000  (GET /health)"
	@echo "  Frontend: http://localhost:3000"
	@echo ""

down:  ## Stop and remove containers (named volume is preserved)
	docker compose down

build:  ## Build images without starting
	docker compose build

rebuild:  ## Rebuild from scratch and start
	docker compose build --no-cache
	docker compose up -d

logs:  ## Tail logs from both services
	docker compose logs -f

ps:  ## Show running services
	docker compose ps

test:  ## Run backend tests locally (needs Python deps installed)
	cd backend && python -m pytest
