# ace2k-u1: the host tests and the linter. Run from this directory or with make -C.
PYTHON ?= python3
U1_VERSION ?= $(shell git describe --tags --match 'v[0-9]*' --always --dirty 2>/dev/null | sed 's/^v//')
ACE2K_REQUIRES ?= $(shell cat REQUIRES)
DIST ?= dist
SHA256 := $(shell command -v sha256sum >/dev/null 2>&1 && echo sha256sum || echo shasum -a 256)
.PHONY: test lint format quality web-deps web-vendor web-test web-lint web-quality web-visual installer installer-sync installer-check installer-test-dash dist
test:
	$(PYTHON) -m pytest -q tests; rc=$$?; [ $$rc -eq 0 ] || [ $$rc -eq 5 ]
lint: installer-sync
	ruff check $(wildcard klippy tests scripts tools)
	ruff format --check $(wildcard klippy tests scripts tools)
format:
	ruff format $(wildcard klippy tests scripts tools)
# The quality gate: run when a plan closes, not on every commit (the coverage needs the whole
# suite).  Limits only move toward their final values (complexity 15, modules of 1000 lines of
# code, coverage 97 %); see CONTRIBUTING.md.  MODULE_MAX_CODE counts lines of code only (blank
# lines, comments and docstrings do not count); a module over MODULE_TARGET lines in total only
# prints a warning.
QUALITY_PY      := klippy/extras scripts tools
XENON_LIMITS    := --max-absolute C --max-modules A --max-average A
MODULE_MAX_CODE := 1000
MODULE_TARGET   := 1000
COVERAGE_MIN    := 97
VULTURE_RATCHET :=
quality: web-deps
	$(PYTHON) -m xenon $(XENON_LIMITS) $(QUALITY_PY)
	$(PYTHON) scripts/check_module_size.py \
	    --max-code-lines $(MODULE_MAX_CODE) --target-lines $(MODULE_TARGET) $(QUALITY_PY) \
	    $(INSTALLER_PARTS)
	$(PYTHON) -m vulture klippy/extras scripts tools vulture-whitelist.py --min-confidence 60 \
	    --exclude klippy/extras/ace2k_u1_printer_types.py \
	    --ignore-names '$(VULTURE_RATCHET)'
	mkdir -p .quality
	COVERAGE_FILE=.quality/.coverage $(PYTHON) -m pytest -q -p no:cacheprovider \
	    tests --cov=klippy/extras --cov=scripts --cov=tools --cov-config=.coveragerc \
	    --cov-report=term:skip-covered --cov-fail-under=$(COVERAGE_MIN)
	$(PYTHON) -m mypy
	$(MAKE) web-quality
	@echo "quality: OK"
web-test:
	node --test web/tests/*.test.js

# The page's tools (biome, typescript, knip) come from `npm ci --prefix web`.  web-lint and
# web-quality run in CI; web-quality is also part of `make quality`.  All limits are at their
# final values.  The coverage floor below is the measured floor (at least 98 % of branches); it
# only goes up.  main.js is excluded because it needs a browser.  theme-boot.js is outside the
# measure without an exclude: its tests run its text in node:vm and never import it.
WEB_COV := --test-coverage-lines=100 --test-coverage-branches=98 --test-coverage-functions=99
# Stops before any step when the page's tools are not installed, and says how to install them.
web-deps:
	@test -d web/node_modules || { echo "web/node_modules is missing: run npm ci --prefix web first"; exit 1; }
# Preact and htm are served from web/vendor/, because the U1 has no internet. The files are
# copied from the pinned development dependencies; VERSIONS records which. The source-map line
# goes: the maps are not served, and a browser's developer tools would ask for them.
WEB_VENDOR := web/vendor
NO_MAP := sed '/^\/\/\# sourceMappingURL=/d'
web-vendor: web-deps
	mkdir -p $(WEB_VENDOR)
	$(NO_MAP) web/node_modules/preact/dist/preact.module.js > $(WEB_VENDOR)/preact.module.js
	$(NO_MAP) web/node_modules/preact/hooks/dist/hooks.module.js > $(WEB_VENDOR)/hooks.module.js
	$(NO_MAP) web/node_modules/htm/dist/htm.module.js > $(WEB_VENDOR)/htm.module.js
	$(NO_MAP) web/node_modules/htm/preact/index.module.js > $(WEB_VENDOR)/htm-preact.module.js
	cd web && node -e "for (const p of ['preact','htm']) console.log(p, require('./node_modules/'+p+'/package.json').version)" > vendor/VERSIONS
# Renders every demo scenario and tab to PNGs in VISUAL_OUT and fails on console errors or overflow.
# Needs Chrome (CHROME overrides the macOS path). It is not part of quality.
VISUAL_OUT ?= /tmp/ace2k-shots
web-visual:
	node scripts/visual_check.mjs $(VISUAL_OUT)
web-lint: web-deps
	cd web && npx --no-install biome check .
web-quality: web-deps
	cd web && npx --no-install tsc -p tsconfig.json
	node scripts/check_web_module_size.mjs --self-test
	node scripts/check_contrast.mjs --self-test
	node scripts/check_contrast.mjs web/css/tokens.css
	node scripts/check_web_jsdoc.mjs --self-test
	node scripts/visual_check.mjs --self-test
	node scripts/check_web_jsdoc.mjs web/main.js web/theme-boot.js web/js
	node scripts/check_web_module_size.mjs --max-code-lines 1000 --target-lines 1000 web/*.js web/js web/css
	cd web && npx --no-install knip
	node --test --experimental-test-coverage $(WEB_COV) \
	    --test-coverage-exclude='web/tests/**' --test-coverage-exclude='web/main.js' web/tests/*.test.js

# install.sh is generated: its first two lines (the shebang and "generated", which belong to
# install.sh, not to a part), then the parts, in this order.  Edit the parts, then run make
# installer.  installer-sync, part of make lint, fails when install.sh is not what the parts make,
# or when a file in installer/ is missing from INSTALLER_PARTS (it would never reach install.sh).
# The size gate counts the parts, not the generated file.
INSTALLER_PARTS := installer/01-common.sh installer/02-status.sh installer/03-units.sh \
    installer/04-start-checks.sh installer/05-release-files.sh installer/06-klipper-flasher.sh \
    installer/07-finish.sh installer/08-flows.sh installer/09-remove.sh installer/10-main.sh
INSTALLER_HEAD := printf '%s\n' '\#!/bin/sh' \
    '\# Generated from installer/*.sh by make installer; edit the parts, not this file.'
installer:
	{ $(INSTALLER_HEAD); cat $(INSTALLER_PARTS); } >install.sh
installer-sync:
	@test "$(sort $(wildcard installer/*.sh))" = "$(sort $(INSTALLER_PARTS))" || \
	    { echo "installer/*.sh and INSTALLER_PARTS differ: list every part in INSTALLER_PARTS, in order"; exit 1; }
	@t=$$(mktemp) && { $(INSTALLER_HEAD); cat $(INSTALLER_PARTS); } >"$$t" && \
	    { cmp -s "$$t" install.sh || { rm -f "$$t"; echo "install.sh is not what installer/*.sh make: run make installer"; exit 1; }; }; rm -f "$$t"

# The installer's static checks: POSIX sh as shellcheck sees it, and dash's parser.
installer-check:
	shellcheck -s sh install.sh
	dash -n install.sh

# The installer's tests with dash as sh.  The tests run install.sh with the sh found first in
# PATH, and on macOS that is bash.  The U1 runs it under BusyBox sh; dash is the strict POSIX
# shell nearest to it.
installer-test-dash:
	@command -v dash >/dev/null 2>&1 || { echo "installer-test-dash: dash is not installed (brew install dash, or apt install dash)"; exit 1; }
	d=$$(mktemp -d) && ln -s "$$(command -v dash)" "$$d/sh" && \
	    PATH="$$d:$$PATH" $(PYTHON) -m pytest -q tests/installer; rc=$$?; rm -rf "$$d"; exit $$rc

# A release's files for install.sh: the adapter bundle, the ace2k release it was tested with, the
# installer and their checksums.  In the private tree pass U1_VERSION (its tags are ace2k's).
dist:
	@test -n "$(U1_VERSION)" || { echo "dist: no version; pass U1_VERSION=X.Y.Z"; exit 1; }
	rm -rf $(DIST)
	mkdir -p $(DIST)/stage/klippy/extras $(DIST)/stage/config $(DIST)/stage/tools
	cp klippy/extras/ace2k_u1*.py $(DIST)/stage/klippy/extras/
	cp config/ace2k-u1.cfg $(DIST)/stage/config/
	cp tools/ace2k_cfg.py $(DIST)/stage/tools/
	cp LICENSE $(DIST)/stage/
	cp -R web $(DIST)/stage/web
	rm -rf $(DIST)/stage/web/tests $(DIST)/stage/web/dev $(DIST)/stage/web/node_modules $(DIST)/stage/web/package.json $(DIST)/stage/web/package-lock.json $(DIST)/stage/web/biome.jsonc $(DIST)/stage/web/tsconfig.json $(DIST)/stage/web/knip.json $(DIST)/stage/web/js/types.js
	find $(DIST)/stage \( -name __pycache__ -o -name .DS_Store -o -name '._*' \) -prune -exec rm -rf {} +
	COPYFILE_DISABLE=1 tar -czf $(DIST)/ace2k-u1-$(U1_VERSION).tar.gz -C $(DIST)/stage klippy config tools web LICENSE
	rm -rf $(DIST)/stage
	printf '%s\n' "$(ACE2K_REQUIRES)" > $(DIST)/ace2k-u1-$(U1_VERSION).requires
	cp install.sh $(DIST)/install.sh
	cd $(DIST) && $(SHA256) ace2k-u1-$(U1_VERSION).tar.gz ace2k-u1-$(U1_VERSION).requires install.sh > SHA256SUMS
