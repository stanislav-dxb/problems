# Problem Scout — common tasks. Run `make help` for a list.
PYTHON ?= python3
VENV   ?= .venv
SCOUT  := $(VENV)/bin/scout
DIGEST_DIR ?= $(HOME)/scout/digests
LOG_DIR    ?= $(HOME)/scout/logs
PLIST_LABEL := com.problemscout.daily
PLIST_SRC   := scheduling/$(PLIST_LABEL).plist
PLIST_DST   := $(HOME)/Library/LaunchAgents/$(PLIST_LABEL).plist

.PHONY: help install test collect classify cluster evaluate digest run daily stats dry-run install-launchd uninstall-launchd clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)

install: $(VENV)/bin/activate ## Create .venv and install scout + dependencies
	$(VENV)/bin/pip install -U pip
	$(VENV)/bin/pip install -r requirements.txt
	$(VENV)/bin/pip install -e .
	@test -f .env || cp .env.example .env
	@echo "Now put your keys in .env"

test: ## Run unit tests
	$(VENV)/bin/python -m pytest -q

dry-run: ## Show what a full run would do without fetching or calling Claude
	$(SCOUT) run --dry-run

collect: ## Fetch new items from all enabled sources
	$(SCOUT) collect

classify: ## Classify unclassified items with Claude
	$(SCOUT) classify

cluster: ## Re-cluster classified problems
	$(SCOUT) cluster

evaluate: ## Evaluate clusters with >= 5 items
	$(SCOUT) evaluate

digest: ## Write today's digest to digests/YYYY-MM-DD.md
	$(SCOUT) digest

run: ## Full pipeline: collect -> classify -> cluster -> evaluate -> digest
	$(SCOUT) run

daily: ## Full pipeline, digest written to ~/scout/digests/YYYY-MM-DD.md (what launchd runs)
	@mkdir -p $(DIGEST_DIR) $(LOG_DIR)
	$(SCOUT) run --out "$(DIGEST_DIR)/$$(date +%Y-%m-%d).md"

stats: ## Counts per source/domain/week and API cost
	$(SCOUT) stats

install-launchd: ## Install the macOS launchd agent (daily at 06:00)
	@mkdir -p $(HOME)/Library/LaunchAgents $(DIGEST_DIR) $(LOG_DIR)
	sed -e 's|__PROJECT_DIR__|$(CURDIR)|g' -e 's|__HOME__|$(HOME)|g' $(PLIST_SRC) > $(PLIST_DST)
	-launchctl unload $(PLIST_DST) 2>/dev/null
	launchctl load $(PLIST_DST)
	@echo "Installed $(PLIST_DST). Test it now with: launchctl start $(PLIST_LABEL)"

uninstall-launchd: ## Remove the launchd agent
	-launchctl unload $(PLIST_DST)
	rm -f $(PLIST_DST)

clean: ## Remove caches (keeps scout.db and digests)
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache
