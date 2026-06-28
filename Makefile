PYTHON ?= python3
IMAGE   ?= ghcr.io/ste-haus/circadiand
VERSION := $(shell tr -d '[:space:]' < VERSION)
CONFIG  ?= config.yaml

.PHONY: help install test run docker-build docker-push publish clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) \
		| awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

install: ## Install the package with test extras (editable)
	$(PYTHON) -m pip install -e '.[test]'

test: ## Run the test suite with coverage
	$(PYTHON) -m pytest

run: ## Run the service locally against $(CONFIG) (auto-created from the sample if missing)
	CIRCADIAND_CONFIG=$(CONFIG) $(PYTHON) -m circadiand

docker-build: ## Build the container image, tagged with VERSION and latest
	docker build -t $(IMAGE):$(VERSION) -t $(IMAGE):latest .

docker-push: ## Push the VERSION and latest image tags (requires prior login)
	docker push $(IMAGE):$(VERSION)
	docker push $(IMAGE):latest

publish: docker-build docker-push ## Build and push the image

clean: ## Remove build, cache, and coverage artifacts
	rm -rf build dist *.egg-info .pytest_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
