# TaskForge

[![CI](https://github.com/lakshay57112/taskforge/actions/workflows/ci.yml/badge.svg)](https://github.com/lakshay57112/taskforge/actions/workflows/ci.yml)

TaskForge is an automated task factory for the Innodata AI Hackathon (Track 1, Coding Task Factory).
It reads a real repository, invents realistic feature-development tasks, and turns each one into a
validated [Harbor](https://pypi.org/project/harbor/) 0.25 task package: a requirement document, a
reference solution, hidden fail-to-pass tests, a Dockerfile and metadata.

Target repository: [`pallets/click`](https://github.com/pallets/click) at release **8.5.1**
(`cf19179e8e2fd54c601378cec9e0d21bb12b6fb1`).

## How it works

```
click @ base commit
  -> 1. Codebase map      split the source into areas, index every class and function
  -> 2. Proposer          the model proposes features, one area + one variation axis each
     Novelty check        reject anything already in the repo or its upstream history
  -> 3. Builder           instruction.md, reference solution, hidden tests
     Local check + fix    nop must score 0.0, oracle 1.0; the model repairs whatever is wrong
  -> 4. Breaker           the model writes cheating implementations; weak tests get hardened
     Mutation testing     inject faults into the solution; the tests must catch them
  -> 5. Blind solvers     independent attempts from instruction.md only; unclear specs get fixed
  -> 6. Harbor            nop x1 and oracle x3 inside Docker, exactly as the judges run it
  -> 7. Package + report  Harbor task folders and a scorecard with every metric
```

Every stage leaves evidence: fix diagnoses, cheat-catch rate, mutation score, blind solve rate,
Harbor rewards, and an overlap matrix showing that the tasks touch different code.

## Quick start (Windows PowerShell)

Requirements: Python 3.12+, git, Docker Desktop running.

```powershell
cd "$HOME\PycharmProjects\taskforge"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[harbor,dev]"

$env:GEMINI_API_KEY = "your-key"   # https://aistudio.google.com/apikey
taskforge models
taskforge run --tasks 1
```

## Quick start (Linux, macOS or WSL2 Ubuntu)

Requirements: Docker (on Windows: Docker Desktop with WSL integration turned on), Python 3.12, git.

```bash
# inside Ubuntu (WSL)
sudo apt update && sudo apt install -y python3.12-venv git
cd "/mnt/c/Users/<you>/PycharmProjects/taskforge"
python3.12 -m venv ~/.venvs/taskforge && source ~/.venvs/taskforge/bin/activate
pip install -e ".[harbor,dev]"

export GEMINI_API_KEY="your-key"          # https://aistudio.google.com/apikey
taskforge models                          # pick a model your key can use, set it in taskforge.toml
taskforge run                             # builds 5 tasks and validates them with Harbor
```

Tip: in `taskforge.toml` set `workdir = "~/.taskforge_work"` so the repo clone and test runs live on
the Linux filesystem; running the test suite on `/mnt/c` is several times slower.

Outputs land in `output/click@cf19179/`:

| Path | What it is |
|---|---|
| `tasks/<task-id>/` | Harbor task packages |
| `reports/scorecard.md` / `.json` | per-task metrics, Harbor results, overlap matrix, model usage |
| `codemap.json` | the area/symbol map used for placement and overlap |
| `logs/events.jsonl` | every repair, failure and build event |
| `harbor-jobs/` | raw Harbor trial output (logs, rewards) |

## Commands

| Command | Purpose |
|---|---|
| `taskforge run [--tasks N] [--skip-harbor]` | run the full factory |
| `taskforge run --replay` | re-run from recorded model answers, no API key needed |
| `taskforge validate [task_dir ...]` | Harbor nop/oracle matrix on existing packages |
| `taskforge check task_dir ...` | fast local nop/oracle check without Docker |
| `taskforge report` | rebuild the scorecard from the task folders |
| `taskforge codemap` | print the codebase map |
| `taskforge models` | list Gemini models available to your key |

Run Harbor by hand on any task:

```bash
harbor run -p output/click@cf19179/tasks/<task-id> -a nop    -k 1 -o jobs --no-delete -y   # 0.0
harbor run -p output/click@cf19179/tasks/<task-id> -a oracle -k 1 -o jobs --no-delete -y   # 1.0
```

## Repeatability

Every model answer is stored in `.taskforge_cache/`, keyed by a hash of the full request, together
with the order in which the run asked for it. `taskforge run --replay` rebuilds the same task
packages from that cache with no API key and no network calls to the model. The base commit,
pytest version, Docker base image and model seed are pinned in `taskforge.toml`.

## Task package layout

```
<task-id>/
  instruction.md            requirement document (the only file the agent sees)
  task.toml                 repo, base_commit (40 chars), licence, provenance, quality metrics
  environment/Dockerfile    click at the base commit with its test dependencies
  solution/solve.sh         applies solution/solution.patch (Harbor's oracle agent runs this)
  tests/test.sh             verifier: restores the test suite, runs pytest, writes the reward
  tests/taskforge_verify.py outcome recorder + reward rule (same code the pipeline uses)
  tests/fail_to_pass.json   new feature tests
  tests/pass_to_pass.json   existing regression tests
  tests/feature/*.py        hidden feature tests
  README.md, metrics.json   human-readable summary and all metrics
```

Reward: any regression test failing gives 0.0; otherwise the share of feature tests passing.

## Development

```bash
pytest                                               # unit tests
TASKFORGE_E2E=1 pytest tests/test_end_to_end.py      # full offline run with a scripted model
```

See [docs/TECHNICAL.md](docs/TECHNICAL.md) for design decisions, assumptions and limitations.

## License

MIT for TaskForge itself. Generated tasks contain code derived from click (BSD-3-Clause).
