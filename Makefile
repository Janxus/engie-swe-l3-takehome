.PHONY: install pipeline inject-faults dev test

VENV := .venv
PY := $(VENV)/bin/python

install:
	python3.14 -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt

pipeline:
	$(PY) src/pipeline.py

inject-faults:
	$(PY) src/inject_faults.py

# Equivalent of `npm run dev`: builds the store if it's missing, then serves
# the dashboard. The committed data/openmeteo.duckdb means this is usually
# just "start Streamlit" with no rebuild.
dev:
	@test -f data/openmeteo.duckdb || (echo "No store found -- running pipeline first..." && $(PY) src/pipeline.py)
	$(VENV)/bin/streamlit run app.py

test:
	$(PY) tests/test_invariants.py
