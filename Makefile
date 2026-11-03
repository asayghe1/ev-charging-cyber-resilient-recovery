.DEFAULT_GOAL := paper

PYTHON ?= .venv/bin/python
WORKERS ?= 4
RESULTS_DIR ?= results
ANALYSIS_DIR ?= $(RESULTS_DIR)/analysis
FIGURES_DIR ?= manuscript/figures
TRACE ?= $(RESULTS_DIR)/representative_trace.json
REPRO_DIR ?= results/reproduced

export PYTHONPATH := src
export OPENBLAS_NUM_THREADS := 1
export OMP_NUM_THREADS := 1

.PHONY: paper analysis analysis-retry trace figures check simulate simulate-main simulate-sensitivity simulate-ablations simulate-retry reproduce clean-tex

# The default build consumes saved evidence; it never launches the study.
paper: figures
	cd manuscript && pdflatex -interaction=nonstopmode -halt-on-error main.tex
	cd manuscript && bibtex main
	cd manuscript && pdflatex -interaction=nonstopmode -halt-on-error main.tex
	cd manuscript && pdflatex -interaction=nonstopmode -halt-on-error main.tex

analysis:
	$(PYTHON) scripts/analyze_study.py --results $(RESULTS_DIR) --out $(ANALYSIS_DIR)
	mkdir -p manuscript/tables
	cp $(ANALYSIS_DIR)/main_table.tex manuscript/tables/main_table.tex

analysis-retry:
	$(PYTHON) scripts/analyze_retry.py --input $(RESULTS_DIR)/retry.jsonl --config $(RESULTS_DIR)/retry.config.json --out $(RESULTS_DIR)/retry_analysis
	mkdir -p manuscript/tables
	cp $(RESULTS_DIR)/retry_analysis/retry_table.tex manuscript/tables/retry_table.tex

# Explicit because these four descriptive replays are not part of a paper build.
trace:
	$(PYTHON) scripts/make_trace.py --main $(RESULTS_DIR)/main.jsonl --out $(TRACE) --feedback-margin .20

figures: analysis analysis-retry
	test -f $(TRACE)
	$(PYTHON) scripts/format_figures.py --summary $(ANALYSIS_DIR)/summary.json --retry-summary $(RESULTS_DIR)/retry_analysis/summary.json --trace $(TRACE) --out $(FIGURES_DIR)

check:
	$(PYTHON) -m pytest tests -q

# Existing phase files are preserved. The analyzer rejects incomplete evidence.
simulate: simulate-main simulate-sensitivity simulate-ablations simulate-retry

simulate-main: $(REPRO_DIR)/main.jsonl
simulate-sensitivity: $(REPRO_DIR)/sensitivity.jsonl
simulate-ablations: $(REPRO_DIR)/ablations.jsonl
simulate-retry: $(REPRO_DIR)/retry.jsonl

$(REPRO_DIR)/main.jsonl:
	$(PYTHON) scripts/run_study.py --phase main --workers $(WORKERS) --feedback-margin .20 --out $@

$(REPRO_DIR)/sensitivity.jsonl:
	$(PYTHON) scripts/run_study.py --phase sensitivity --workers $(WORKERS) --feedback-margin .20 --out $@

$(REPRO_DIR)/ablations.jsonl:
	$(PYTHON) scripts/run_study.py --phase ablations --workers $(WORKERS) --feedback-margin .20 --out $@

$(REPRO_DIR)/retry.jsonl:
	$(PYTHON) scripts/run_retry_study.py --workers $(WORKERS) --reviewed-manifest results/retry.freeze.json --out $@

# Reproduced analysis and graphics remain alongside the new simulation outputs.
reproduce: simulate
	$(PYTHON) scripts/analyze_study.py --results $(REPRO_DIR) --out $(REPRO_DIR)/analysis
	$(PYTHON) scripts/make_trace.py --main $(REPRO_DIR)/main.jsonl --out $(REPRO_DIR)/representative_trace.json --feedback-margin .20
	$(PYTHON) scripts/analyze_retry.py --input $(REPRO_DIR)/retry.jsonl --config $(REPRO_DIR)/retry.config.json --out $(REPRO_DIR)/retry_analysis
	$(PYTHON) scripts/format_figures.py --summary $(REPRO_DIR)/analysis/summary.json --retry-summary $(REPRO_DIR)/retry_analysis/summary.json --trace $(REPRO_DIR)/representative_trace.json --out $(REPRO_DIR)/figures

clean-tex:
	rm -f manuscript/main.aux manuscript/main.blg manuscript/main.log manuscript/main.out
