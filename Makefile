# ace2k-u1: the host tests and the linter. Run from this directory or with make -C.
PYTHON ?= python3
.PHONY: test lint format web-test
test:
	$(PYTHON) -m pytest -q tests; rc=$$?; [ $$rc -eq 0 ] || [ $$rc -eq 5 ]
lint:
	ruff check $(wildcard klippy tests)
	ruff format --check $(wildcard klippy tests)
format:
	ruff format $(wildcard klippy tests)
web-test:
	node --test web/tests/
