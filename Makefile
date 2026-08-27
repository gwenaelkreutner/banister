.PHONY: up down logs backup reset-db shell

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f app

backup:
	docker exec banister_app python scripts/backup.py --out /app/data/backup_$(shell date +%Y%m%d_%H%M%S).db
	@echo "Backup saved inside the banister_data volume (data/backup_*.db)."

reset-db:
	docker compose down -v
	docker compose up --build -d

shell:
	docker exec -it banister_app bash
