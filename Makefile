.PHONY: help preflight selftest test

help: ## Show targets
	@grep -E '^[a-z][a-z-]*:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | expand -t24

selftest: ## Run the panel + guard selftests
	@PYTHONPATH=src python3 -m ainglish.panel --selftest >/dev/null && echo "panel selftest OK"
	@PYTHONPATH=src python3 -m ainglish.empty_cell_guard --selftest >/dev/null && echo "cell-yield guard selftest OK"

preflight: ## Checks a green suite cannot see (untracked, tag/version, index, mirror, served bytes)
	@python3 tools/preflight.py

test: selftest preflight ## Everything
