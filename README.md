# HarnessAudit

HarnessAudit-Bench is a code and task suite for auditing agent harness safety over
full execution trajectories. It evaluates whether a harness respects tool,
resource, and information-flow boundaries while still completing useful tasks,
and includes both multi-agent and single-agent control settings.

The repository is organized around two benchmark packages:

- `masp/`: multi-agent HarnessAudit-Bench tasks, tools, framework adapters,
  trace collection, access checking, completion scoring, and perturbation
  evaluation.
- `sasp/`: single-agent control tasks and runner using the same task format,
  checker, tool backends, and judging stack where applicable.

The benchmark currently includes 210 multi-agent tasks across eight domains and
34 single-agent control tasks. Multi-agent domains include finance, e-commerce,
healthcare, office, daily life, legal compliance, social interaction, and
software engineering.

## Repository Layout

```text
.
├── masp/                     # Multi-agent benchmark package
│   ├── tasks/                # 210 task YAMLs and task-local assets
│   ├── tools/                # Domain tool catalogs
│   ├── banks/                # Stateful mock service backends
│   ├── frameworks/           # Claw-Team, native harness, OAI, and ADK adapters
│   ├── perturbation_specs/   # Perturbation index and variant specifications
│   ├── schemas/              # Pydantic schemas for tasks, actions, and traces
│   └── trace/                # JSONL trace writer and event models
├── sasp/                     # Single-agent benchmark package
│   ├── tasks/                # 34 single-agent task YAMLs
│   ├── tools/                # Single-agent domain tool catalogs
│   └── frameworks/           # Single-agent runner integration
├── fixtures/                 # Real workspace fixtures for SDE tasks
├── scripts/                  # Task generation, perturbation, and validation tools
├── sar_final.py              # Metric aggregation helper
└── pyproject.toml            # Package metadata and CLI entry points
```

Local run artifacts such as traces, result JSONs, logs, workspaces, staging
files, `.env`, and paper sources are intentionally ignored by Git.

## Installation

Use Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Optional adapters require their own runtime dependencies:

```bash
# OpenAI Agents SDK / OpenAI-compatible model calls
python -m pip install -e ".[oai]"

# Claw-Team integration, if available in your environment
python -m pip install -e ".[clawteam]"
```

Native harness experiments additionally require the corresponding external CLI
runtime to be installed and authenticated, for example Claude Code, Codex, or
OpenClaw. These CLIs are not vendored in this repository.

## Environment Variables

The runners load `.env` from the repository root when present. Keep `.env`
local; it is ignored by Git.

Common variables:

```bash
# OpenAI-compatible calls used by judges and in-process adapters.
# Codex uses CODEX_API_KEY first, then OPENAI_API_KEY, then local codex CLI login state.
MASP_CODEX_AUTH_MODE=auto    # optional: auto | api_key | cli_login
CODEX_API_KEY=...            # optional
OPENAI_API_KEY=...
OPENAI_BASE_URL=...          # optional

# Claude Code official API-key auth; alternatively use local claude CLI login state
ANTHROPIC_API_KEY=...        # optional
ANTHROPIC_BASE_URL=...       # optional

# OpenClaw aliases
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Optional isolation/debug controls
MASP_WORKSPACES_ROOT=...
MASP_KEEP_CLAUDE_ISOLATION=1
MASP_KEEP_CODEX_ISOLATION=1
MASP_KEEP_OPENCLAW_ISOLATION=1
```

The repository does not include API keys or private provider URLs.

## Quick Checks

Verify that the package entry points import and expose their CLIs:

```bash
python -m masp --help
python -m sasp --help
```

Run Python bytecode checks over the tracked code:

```bash
python -m compileall masp sasp scripts fixtures
```

## Running a Multi-Agent Task

Run one task with the multi-agent runner:

```bash
python -m masp run tasks/daily_life/dining/dl-t11.yaml \
  --framework clawteam \
  --harness openclaw \
  --model gpt-5.4 \
  --output masp/results \
  --trace-dir masp/traces
```

Useful flags:

- `--skip-judge`: skip LLM-as-judge scoring while keeping deterministic checks.
- `--judge-model gpt-5.4`: select the model used for completion and operational
  judging.
- `--judge-workers 1`: run judge calls serially.
- `--agent-timeout 300`: set the per-agent timeout.
- `--repeat N`: repeat the same task sequentially.

Artifacts are written under harness/model-scoped directories, for example:

```text
masp/traces/openclaw/gpt-5.4/*.jsonl
masp/results/openclaw/gpt-5.4/*.json
masp/results/openclaw/gpt-5.4/*.sqlite
```

The JSONL trace is append-only and records normalized `tool_call`,
`communication`, `access_decision`, and run-boundary events. The result JSON
summarizes action counts, violation counts, Layer-1 penalties, operational
score, completion score, warnings, and any error status.

## Running the Smoke Suite

The smoke script runs five representative multi-agent tasks and writes logs,
traces, and result reports under ignored artifact directories.

```bash
SKIP_JUDGE=1 RUN_ALL_WORKERS=1 MASP_HARNESS=openclaw ./masp/run_smoke.sh
```

Select native harness/model labels with environment variables:

```bash
MASP_HARNESS=claude MASP_CLAUDE_MODEL=claude-sonnet-4-6 ./masp/run_smoke.sh
MASP_HARNESS=codex MASP_CODEX_MODEL=gpt-5.4 ./masp/run_smoke.sh
MASP_HARNESS=openclaw MASP_OPENCLAW_MODEL=qwen3.5-plus ./masp/run_smoke.sh
```

`masp/run_smoke_2.sh` contains the remaining larger task batch used for the
same harness/model execution path.

## Running a Single-Agent Control Task

```bash
python -m sasp run sasp/tasks/finance/sa-fin-t1.yaml \
  --model gpt-5.4 \
  --output sasp/results \
  --trace-dir sasp/traces
```

Single-agent reports use the same deterministic access checker and operational
judge machinery, but do not report inter-agent information-flow metrics.

## Perturbation Evaluation

Perturbation variants are stored in `masp/perturbation_specs/` and indexed by
`masp/perturbation_specs/index.jsonl`.

Run one perturbation variant directly:

```bash
python -m masp run tasks/ecommerce/after_sales/ec-t13.yaml \
  --framework clawteam \
  --harness openclaw \
  --model gpt-5.4 \
  --perturbation-id ec-t13-inj-1 \
  --output masp/results \
  --trace-dir masp/traces
```

Run a perturbation matrix:

```bash
python scripts/run_perturbations.py \
  --attack-type injection \
  --domain ecommerce \
  --harness openclaw \
  --model gpt-5.4 \
  --workers 1 \
  --skip-existing
```

Summarize perturbation reports:

```bash
python scripts/summarize_perturbations.py masp/results/openclaw/gpt-5.4
```

## Task Generation and Validation

The released tasks are already materialized under `masp/tasks/` and
`sasp/tasks/`. The scripts below are for extending or validating the benchmark.

Validate existing generated tasks for a domain:

```bash
python scripts/generate_tasks.py --domain finance --validate-existing
```

Generate new task candidates in dry-run mode:

```bash
python scripts/generate_tasks.py --domain finance --target-count 5 --dry-run
```

Generate perturbation specifications:

```bash
python scripts/generate_perturbations.py --domain finance --generator template --dry-run
```

The validation stack checks schema validity, tool coverage, useful-tool
invariants, tool partitioning, fixture coherence, recognizer registration, SQL
completion predicates, task loading, deduplication, and multimodal assets.

## Git Hygiene

This repository is prepared so GitHub archives contain only the benchmark code,
tasks, fixtures, and supporting scripts. The following are intentionally ignored:

- `.env` and `.env.*`
- `masp/traces/`, `masp/results/`, `masp/logs/`, `masp/workspaces/`
- `sasp/traces/`, `sasp/results/`, `sasp/logs/`
- `scripts/_staging/` and generated intermediate reports
- Python caches and build outputs
- paper sources and generated paper artifacts under `paper/`

Before publishing, useful checks are:

```bash
git status --short
git ls-files -ci --exclude-standard
git ls-files | rg '(^paper/|results/|traces/|logs/|workspaces/|__pycache__|\.env$|\.pyc$)'
```

The first command should show only intended changes. The second command should
be empty; it catches files that are ignored but still tracked.

## Citation

If you use this benchmark, cite the accompanying HarnessAudit paper once the
final citation metadata is available.
