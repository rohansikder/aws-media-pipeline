PYTHON ?= .venv/bin/python
TERRAFORM ?= terraform
export TERRAFORM

.PHONY: setup build package up serve dev test lint integration down clean
setup:
	python3 -m venv .venv
	$(PYTHON) -m pip install -e '.[dev]'
	cd web && npm ci

build: package
	cd web && npm run build
	docker build -f docker/worker.Dockerfile -t media-pipeline-worker:local .

package:
	$(PYTHON) scripts/package_lambda.py

up: build
	docker compose up -d
	$(PYTHON) scripts/wait_for_floci.py
	$(TERRAFORM) -chdir=infra/environments/local init -input=false
	$(TERRAFORM) -chdir=infra/environments/local apply -auto-approve -input=false

serve:
	$(PYTHON) scripts/local_env.py

dev:
	cd web && npm run dev

test:
	$(PYTHON) -m pytest -m 'not integration'

lint:
	.venv/bin/ruff check services scripts tests
	$(TERRAFORM) -chdir=infra fmt -check -recursive

integration: up
	$(PYTHON) scripts/integration.py

down:
	docker compose down

clean:
	$(TERRAFORM) -chdir=infra/environments/local destroy -auto-approve -input=false
	docker compose down -v
