.PHONY: help preflight selftest smoke test

help: ## Show targets
	@grep -E '^[a-z][a-z-]*:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | expand -t24

# Every selftest CI runs, run the same way here. This target used to run two of five, so the
# client guard — the one thing these PRs add — was not run by the repo's own entry point at all.
# PYTHONPATH=src on EVERY line, not for tidiness: without it a bare `python3 -m ainglish.client`
# resolves to whatever wheel is installed in the active venv. Mine was 0.2.5, so it printed a
# green selftest for a copy three versions old while the working tree sat unexercised. CI escapes
# that by `pip install .` first, which makes the installed copy the repo; a developer shell has no
# such guarantee.
selftest: ## Run every module selftest (offline) — the same five CI runs
	@PYTHONPATH=src python3 -m ainglish.panel --selftest >/dev/null && echo "panel selftest OK"
	@PYTHONPATH=src python3 -m ainglish.empty_cell_guard --selftest >/dev/null && echo "cell-yield guard selftest OK"
	@PYTHONPATH=src python3 -m ainglish.measure --selftest >/dev/null && echo "measure selftest OK"
	@PYTHONPATH=src python3 -m ainglish.corpus_slice selftest >/dev/null && echo "corpus-slice selftest OK"
	@PYTHONPATH=src python3 -m ainglish.preflight >/dev/null && echo "draft-preflight selftest OK"
	@PYTHONPATH=src python3 -m ainglish.client >/dev/null && echo "client selftest OK"
	@python3 tools/preflight.py --selftest
	@PYTHONPATH=src python3 tools/check_settlement_strata_parity.py >/dev/null \
		&& echo "settlement-strata parity corpus OK"
	@PYTHONPATH=src python3 tools/check_remote_inference_fixture.py >/dev/null \
		&& echo "remote-inference starter fixture OK"
	@PYTHONPATH=src python3 -c "import ainglish; assert ainglish.__file__.startswith('$(CURDIR)'), \
		'the selftests ran against %s, not this checkout' % ainglish.__file__" \
		&& echo "  (verified: the selftests ran against this checkout)"

smoke: ## Every documented envelope vs the LIVE register (network; no credentials needed)
	@PYTHONPATH=src python3 -c "from ainglish.client import live_smoke; live_smoke(credentialed=False)"

preflight: ## Checks a green suite cannot see (untracked, tag/version, index, mirror, served bytes)
	@python3 tools/preflight.py

test: selftest smoke preflight ## Everything
