# HarnessAudit

[![Project](https://img.shields.io/badge/Project-Website-blue.svg)](https://harnessaudit.github.io/)
[![Paper](https://img.shields.io/badge/Paper-arXiv-b31b1b.svg)](https://arxiv.org/pdf/2605.14271)
[![Dataset](https://img.shields.io/badge/Dataset-Hugging%20Face-yellow.svg)](https://huggingface.co/datasets/eric-ai-lab/HarnessAudit)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> HarnessAudit is an evaluation harness for auditing whether agent systems obey
> tool, resource, and information-flow boundaries while still completing useful
> tasks.

HarnessAudit evaluates full execution trajectories rather than final answers
only. It supports multi-agent runs, single-agent control runs, stateful domain
banks, native CLI harnesses, and LLM-as-judge scoring for completion and action
validity.

The canonical task release is available on Hugging Face:

```text
https://huggingface.co/datasets/eric-ai-lab/HarnessAudit
```

This GitHub repository contains the runner, framework adapters, stateful mock
services, schemas, and reproducibility scripts. Local traces, result JSONs,
SQLite bank snapshots, workspaces, private keys, and paper build artifacts are
not committed.

---

## What HarnessAudit Measures

HarnessAudit reports paper-facing metrics from normalized traces:

| Metric | Meaning |
| --- | --- |
| `sar_tool` | Safety adherence for out-of-scope tool use |
| `sar_resource` | Safety adherence for protected-resource access |
| `sar_flow` | Safety adherence for information-flow constraints |
| `sar_avg` | Average Safety Adherence Rate across the three L1 channels |
| `avs` | Action Validity Score for L2 execution fidelity |
| `tcr` | Task Completion Rate from deterministic and LLM completion checks |

The trace schema records normalized tool calls, communications, access
decisions, completion scores, operational judgments, and run-level metadata.

---

## Repository Layout

```text
.
├── multi_agent/             # Multi-agent runner, tasks, banks, adapters, traces
│   ├── run_ma.sh            # Full multi-agent sweep script
│   ├── banks/               # Stateful SQLite-backed domain services
│   ├── frameworks/          # ClawTeam, OpenClaw, Claude Code, Codex, OAI, ADK
│   ├── schemas/             # Task, action, access-rule, and trace schemas
│   ├── tasks/               # Materialized task YAMLs when using the HF release
│   └── tools/               # Domain tool catalogs
├── single_agent/            # Single-agent control runner and task format
│   ├── run_sa.sh            # Full single-agent sweep script
│   ├── banks/               # Single-agent fixture-aware bank factory
│   ├── tasks/               # Materialized task YAMLs when using the HF release
│   └── tools/               # Single-agent tool catalogs
├── fixtures/                # SDE workspace fixtures used by task runs
├── pyproject.toml           # Package metadata and CLI entry points
└── README.md
```

---

## Quick Start

Use Python 3.11 or newer. A clean virtual environment is recommended.

```bash
git clone https://github.com/eric-ai-lab/HarnessAudit.git
cd HarnessAudit

python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,oai]"
```

If you use ClawTeam-backed harnesses, install ClawTeam in the same environment:

```bash
python -m pip install -e ".[clawteam]"
```

Install and authenticate any native harness CLIs you plan to evaluate:

- OpenClaw for `HARNESS=openclaw`
- Codex CLI for `HARNESS=codex`
- Claude Code CLI for `HARNESS=claude`

OpenClaw requires a recent Node.js runtime. If your system `node` is old, put a
newer Node binary earlier in `PATH` before running OpenClaw experiments.

---

## Download Tasks and Fixtures

Tasks are distributed through Hugging Face. After installing
`huggingface_hub`, download the release into the repository root:

```bash
python -m pip install huggingface_hub
huggingface-cli download eric-ai-lab/HarnessAudit \
  --repo-type dataset \
  --local-dir . \
  --local-dir-use-symlinks False
```

The expected materialized layout is:

```text
multi_agent/tasks/
multi_agent/tools/
multi_agent/fixtures/
single_agent/tasks/
single_agent/tools/
single_agent/fixtures/
fixtures/
```

If you already cloned a repository snapshot that contains those directories, no
additional download step is required.

---

## Configure Secrets

The CLIs load `.env` from the repository root. Keep it local; it is ignored by
Git.

```bash
# Used by judges, OAI/ADK adapters, and Codex API-key mode.
OPENAI_API_KEY=...
OPENAI_BASE_URL=...              # optional

# Codex can use either OPENAI_API_KEY or local codex CLI login state.
CODEX_AUTH_MODE=auto             # auto | api_key | cli_login

# Claude Code can use either ANTHROPIC_API_KEY or local claude CLI login state.
ANTHROPIC_API_KEY=...
ANTHROPIC_BASE_URL=...           # optional
CLAUDE_CODE_AUTH_MODE=auto       # auto | api_key | cli_login

# OpenClaw model routing through OpenRouter.
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

Optional debugging controls:

```bash
MASP_WORKSPACES_ROOT=/tmp/harnessaudit_workspaces
MASP_KEEP_CLAUDE_ISOLATION=1
MASP_KEEP_CODEX_ISOLATION=1
MASP_KEEP_OPENCLAW_ISOLATION=1
```

---

## Reproduce Multi-Agent Results

Run a single task first to validate the environment:

```bash
python -m multi_agent run multi_agent/tasks/daily_life/wellness/dl-t4.yaml \
  --framework clawteam \
  --harness openclaw \
  --model gpt-5.4 \
  --judge-model gpt-5.4 \
  --judge-workers 4 \
  --trace-dir multi_agent/traces \
  --output multi_agent/results
```

Run the full multi-agent task suite:

```bash
HARNESS=openclaw \
MODEL=gpt-5.4 \
TASK_WORKERS=4 \
JUDGE_MODEL=gpt-5.4 \
JUDGE_WORKERS=4 \
bash multi_agent/run_ma.sh
```

`run_ma.sh` supports:

```text
HARNESS=openclaw|claude|codex|oai|adk
FRAMEWORK=clawteam|oai|adk
MODEL=<harness model name>
TASK_WORKERS=<concurrent task runs>
JUDGE_MODEL=gpt-5.4
JUDGE_WORKERS=<concurrent judge calls per task>
SKIP_JUDGE=1
SKIP_EXISTING=1
TASK_FILE=<one yaml>
TASK_LIST=<newline-delimited yaml list>
PYTHON_CMD="conda run -n harnessaudit python"
EXTRA_ARGS="..."
```

When `HARNESS=openclaw`, the script first records the enabled plugin snapshot:

```bash
openclaw plugins list --enabled --json > /tmp/openclaw_plugins_enabled.json
```

Artifacts are written under harness/model-scoped directories, for example:

```text
multi_agent/traces/openclaw/gpt-5.4/*.jsonl
multi_agent/results/openclaw/gpt-5.4/*.json
multi_agent/results/openclaw/gpt-5.4/*.sqlite
```

---

## Reproduce Single-Agent Results

Run a single control task:

```bash
python -m single_agent run single_agent/tasks/finance/sa-fin-t1.yaml \
  --framework openclaw_local \
  --model gpt-5.4 \
  --judge-model gpt-5.4 \
  --judge-workers 4 \
  --trace-dir single_agent/traces \
  --output single_agent/results
```

Run the full single-agent task suite:

```bash
MODEL=gpt-5.4 \
TASK_WORKERS=1 \
JUDGE_MODEL=gpt-5.4 \
JUDGE_WORKERS=4 \
bash single_agent/run_sa.sh
```

`run_sa.sh` supports the same `TASK_WORKERS`, `JUDGE_MODEL`, `JUDGE_WORKERS`,
`SKIP_JUDGE`, `SKIP_EXISTING`, `TASK_FILE`, `TASK_LIST`, `PYTHON_CMD`, and
`EXTRA_ARGS` controls. Single-agent evaluation intentionally supports only the
`openclaw_local` framework:

```text
FRAMEWORK=openclaw_local
MODEL=<OpenClaw model name>
```

`run_sa.sh` writes `/tmp/openclaw_plugins_enabled.json` before launching tasks.

---

## Output Files

Each completed run writes:

| File | Purpose |
| --- | --- |
| `*.jsonl` | Normalized append-only trajectory trace |
| `*.json` | Run summary with SAR, AVS, TCR, violations, warnings, and errors |
| `*.sqlite` | Per-run stateful bank snapshot for post-hoc inspection |
| `_run_logs/<harness>/<model>/*.log` | Per-task stdout/stderr from full-suite scripts |

Local output directories are ignored by Git.

---

## Sanity Checks

```bash
python -m multi_agent --help
python -m single_agent --help
python -m py_compile multi_agent/cli.py single_agent/cli.py
bash -n multi_agent/run_ma.sh single_agent/run_sa.sh
```

For a cheap environment smoke test, add `SKIP_JUDGE=1` and `TASK_FILE=<one yaml>`
to either full-suite script.

---

## Git Hygiene

The repository intentionally ignores local secrets and generated artifacts:

- `.env`, `.env.*`
- traces, results, logs, workspaces
- local scratch reports and paper build outputs
- Python caches and build outputs

Before publishing changes:

```bash
git status --short
git ls-files -ci --exclude-standard
git ls-files | rg '(^paper/|results/|traces/|logs/|workspaces/|__pycache__|\.env$|\.pyc$)'
```

The second command should be empty; it catches files that are ignored but still
tracked.

---

## Citation

If you use HarnessAudit in research, cite the accompanying paper once final
citation metadata is available.
