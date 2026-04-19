.PHONY: up down logs backup reset-db shell

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f app

backup:
	docker exec banister_db pg_dump -U banister banister > backup_$(shell date +%Y%m%d_%H%M%S).sql
	@echo "Backup saved."

reset-db:
	docker compose down -v
	docker compose up --build -d

shell:
	docker exec -it banister_app bash
