.PHONY: install dev-install lint format typecheck test cov run-api run-streamlit run-chainlit \
        docker-up docker-down openapi pre-commit-install

install:
	pip install .

dev-install:
	pip install -e ".[dev]"
	pre-commit install

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy src

test:
	pytest

cov:
	pytest --cov-report=html && open htmlcov/index.html

run-api:
	uvicorn agentic_chatbot.api.server:app --reload --port 8080

run-streamlit:
	streamlit run src/agentic_chatbot/frontend/streamlit_app.py

run-chainlit:
	chainlit run src/agentic_chatbot/frontend/chainlit_app.py

docker-up:
	docker compose up --build

docker-down:
	docker compose down

openapi:
	python scripts/export_openapi.py

pre-commit-install:
	pre-commit install
