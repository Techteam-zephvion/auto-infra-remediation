.PHONY: help build deploy-prod deploy-dev logs stop clean backup restore test health

# Production deployment commands
help: ## Show this help message
	@echo "AutoInfraRemediation - Production Deployment Commands"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

build: ## Build production Docker image
	docker build -f Dockerfile.prod -t auto-remediation:latest .

deploy-prod: build ## Deploy to production
	@echo "Deploying AutoInfraRemediation to production..."
	@if [ ! -f .env.prod ]; then \
		echo "ERROR: .env.prod file not found. Copy from .env.prod.example and configure."; \
		exit 1; \
	fi
	docker-compose -f docker-compose.prod.yml --env-file .env.prod up -d
	@echo "Deployment complete! Check health: make health"

deploy-dev: ## Deploy development version  
	docker-compose -f service/webhook/docker-compose.yml up -d
	@echo "Development environment started"

logs: ## Show application logs
	docker-compose -f docker-compose.prod.yml logs -f auto-remediation

logs-all: ## Show all service logs
	docker-compose -f docker-compose.prod.yml logs -f

stop: ## Stop all services
	docker-compose -f docker-compose.prod.yml down

clean: ## Stop and remove all containers, volumes
	docker-compose -f docker-compose.prod.yml down -v
	docker image rm auto-remediation:latest || true

health: ## Check application health
	@echo "Checking system health..."
	@curl -s http://localhost:8001/health | python -m json.tool || echo "Service not responding"

test-alert: ## Send test alert
	curl -X POST http://localhost:8001/test-alert \
		-H "Content-Type: application/json" \
		-d '{"alert_type": "cpu_spike"}'

backup: ## Backup PostgreSQL database
	@mkdir -p backups
	docker-compose -f docker-compose.prod.yml exec -T db \
		pg_dump -U $${POSTGRES_USER:-autoremediation} $${POSTGRES_DB:-auto_remediation} \
		> backups/backup-$(shell date +%Y%m%d-%H%M%S).sql
	@echo "Database backup created in backups/ directory"

restore: ## Restore database from backup (usage: make restore BACKUP_FILE=backup.sql)
	@if [ -z "$(BACKUP_FILE)" ]; then \
		echo "ERROR: Specify backup file: make restore BACKUP_FILE=backup.sql"; \
		exit 1; \
	fi
	@if [ ! -f "$(BACKUP_FILE)" ]; then \
		echo "ERROR: Backup file $(BACKUP_FILE) not found"; \
		exit 1; \
	fi
	docker-compose -f docker-compose.prod.yml exec -T db \
		psql -U $${POSTGRES_USER:-autoremediation} -d $${POSTGRES_DB:-auto_remediation} \
		< $(BACKUP_FILE)
	@echo "Database restored from $(BACKUP_FILE)"

update: ## Update deployment with latest changes
	@echo "Updating deployment..."
	make build
	docker-compose -f docker-compose.prod.yml up -d --force-recreate auto-remediation
	@echo "Update complete!"

status: ## Show deployment status
	docker-compose -f docker-compose.prod.yml ps

shell: ## Open shell in application container
	docker-compose -f docker-compose.prod.yml exec auto-remediation bash