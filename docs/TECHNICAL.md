# TaskForge: technical explanation

## 1. Goal

Track 1 asks for a pipeline, not hand-written tasks: something that reads a supplied repository and
keeps producing realistic feature-development task packages that pass Harbor's validation matrix.
TaskForge treats task generation as a manufacturing line with quality gates. Each gate either
proves a property of the task or repairs it, and records evidence of what happened.

## 2. Inputs and fixed choices

| Choice | Value | Why |
|---|---|---|
| Repository | `pallets/click` | Python, fast test suite (about 2,400 tests in a few seconds), clear public API, no runtime dependencies |
| Base commit | `cf19179e8e2fd54c601378cec9e0d21bb12b6fb1` (release 8.5.1) | latest stable release; builds and passes its whole suite |
| Test runner | pytest 9.2.0 (pinned) | the repository's own runner |
| Container | `python:3.12-slim` + shallow fetch of the exact commit | small, reproducible; `test "$(git rev-parse HEAD)" = <hash>` guards the checkout |
| Pipeline language | Python 3.12 | same as the target; the pipeline and the tasks share the reward code |
| Model | Google Gemini (configurable, default `gemini-3.5-flash`) | long context (whole source files fit), strong coding, low cost per task |

## 3. Pipeline stages and design decisions

### Stage 1: codebase map
The source tree is parsed with `ast`. Large modules are split into one area per major class
(`core.Option`, `core.Group`, `types.Choice`, ...); smaller modules are one area each. Every
function and method is indexed with its line range. The map is used twice: to place each task in a
different area, and to map any patch back onto the symbols it touches (for the overlap metric).

### Stage 2: proposals, novelty and diversity
The model proposes candidates as structured JSON: area, variation axis, public API, behaviours and
`novelty_terms` (strings that would have to exist in the code if the feature already existed).
- **Novelty check:** each term is searched in the base commit (`git grep -F`) and in the whole
  upstream history including commits newer than the base (`git log --all -S`). A hit rejects the
  candidate, so no task asks for a feature that exists upstream.
- **Diversity:** eight variation axes (parsing, validation, routing, output, interactive,
  configuration, completion, lifecycle). Selection is greedy: a candidate whose area and axis are
  both already used is rejected while other candidates remain. Spare candidates are used when a
  build fails.

### Stage 3: builder and local validation loop
1. **Requirement document:** numbered requirements (R1..Rn) with exact names, signatures, error
   types and messages, edge cases and examples. It never mentions tests.
2. **Reference solution:** the model returns search/replace edit blocks, not a raw diff. Blocks
   are applied to a clean git worktree, rejected if they touch anything outside `src/click/`, and
   the patch is computed with `git diff`. This avoids the most common failure of LLM-written
   patches (malformed hunks).
3. **Hidden tests:** written from the requirement document only, never from the solution, so they
   test behaviour rather than implementation. Names follow `test_r<N>_...`, which gives
   requirement-to-test traceability in each task README.
4. **Local nop/oracle check:** the whole click suite runs twice in a private virtualenv: on the
   untouched repo with the feature tests added (every feature test must fail; every regression
   test must pass) and with the solution applied (everything must pass). Scoring uses
   `taskforge_verify.py`, the same file that ships inside the task, so local and Harbor scores
   cannot drift apart.
5. **Repair:** on failure the model gets the problems, the failing test output and the current
   code, and decides whether the solution or the tests are wrong. The rules forbid editing existing
   tests or deleting tests to go green. Each diagnosis is logged.

### Stage 4: adversarial hardening
- **Breaker:** the model is asked to write several flawed implementations that pass as many
  hidden tests as possible (hard-coding test values, skipping a requirement, handling only the
  inputs the tests use). Any cheat that passes every feature test exposes weak tests. The tests
  are then strengthened, re-validated with nop/oracle, and every cheat is re-run. The final
  cheat-catch rate is recorded.
- **Mutation testing:** mechanical faults (flipped comparisons, `and`/`or`, off-by-one constants,
  `return None`) are injected into lines added by the reference solution. A mutant is killed when
  any feature test fails. The mutation score is recorded per task.

### Stage 5: blind solvers and specification gaps
Independent model attempts implement the task from `instruction.md` only, the way a benchmarked
agent would. When hidden tests fail, a judge classifies each failure as a *spec gap* (the document
did not state the checked behaviour clearly) or a *solver error*. Spec gaps are written back into
the document and one more blind attempt is run. The solve rate, excluding attempts that failed only
because of a gap, gives the difficulty label (easy / medium / hard).

### Stage 6: Harbor validation matrix
For each package, inside Docker, using the commands from the participant guide:
- `harbor lint` (static checks), recorded.
- `nop` once, expected reward 0.0.
- `oracle` three times, expected 1.0 every time; different results mean a flaky task.
- **pass_to_pass calibration:** the regression list is computed on the host. If the nop run shows
  that a few listed tests do not pass inside the container (platform or Python-version
  dependent), they are removed and nop is re-run. The removed tests are listed in the metrics.

### Stage 7: packaging and report
Harbor schema 1.4 `task.toml` with `base_commit`, repo, licence, generator, model and quality
metrics in `[metadata]`. The scorecard (`reports/scorecard.md` and `.json`) lists, per task: local
and Harbor rewards, feature-test count, cheat-catch rate, mutation score, blind solve rate,
difficulty and repair rounds, plus the pairwise overlap matrix and model usage (calls, cache hits,
tokens).

## 4. Verifier design

`tests/test.sh` runs inside the agent's container after the attempt:
1. restores `tests/` and the pytest configuration files from the base commit and removes any
   untracked `conftest.py`, `pytest.ini`, `tox.ini`, `sitecustomize.py` or `usercustomize.py`, so an
   agent cannot change grading by editing or adding test hooks;
2. copies in the hidden feature test file;
3. runs the full suite with `--continue-on-collection-errors` and a small plugin that records the
   outcome of every test id;
4. computes the reward: 0.0 if any pass_to_pass test does not pass, otherwise the share of
   fail_to_pass tests that pass. A missing result counts as a failure.

Checked offline by reproducing the container layout (`/app`, `/tests`, `/solution`, `/logs`) with
the Dockerfile's own git commands: nop gives 0.0, oracle gives 1.0, and an attempt that adds a
root `conftest.py` forcing every test to pass, or edits an existing test, still gives 0.0.

## 5. Task variation strategy

Variation is enforced, not hoped for:
- one area per task from the codebase map, and a different variation axis where possible;
- novelty check against the repository and its history;
- an objective measure afterwards: the Jaccard overlap between the sets of symbols each reference
  solution touches. The scorecard reports the full matrix and the maximum pairwise overlap.

## 6. Repeatability

- All model answers are cached under `.taskforge_cache/llm/`, keyed by SHA-256 of
  (model, system prompt, prompt, sampling settings, seed, variant). A transcript records the order
  of requests per stage.
- `taskforge run --replay` rebuilds everything from the cache with no API key. If a prompt differs
  only cosmetically on another machine (paths, timings), the answer is matched by request order.
- Test output is normalised (worktree paths, timings, memory addresses) before it goes into a
  prompt, so prompts are stable across machines.
- Pinned: base commit, pytest version, base image, model name and seed.
- Harbor oracle runs are repeated three times to detect flaky tests.

## 7. Model choices

Gemini is used for every generation stage. Whole source files (click's `core.py` is about 150 KB)
fit in context, so the model always sees real code instead of summaries. Gemini 3 models are used
with their default sampling (`send_temperature = false`); variety between blind solvers comes from
different seeds. JSON mode is used for structured stages; code is exchanged as edit blocks or a
single fenced test file.

The model layer is provider-agnostic: besides the Gemini SDK, any OpenAI-compatible endpoint
(OpenRouter, Groq, a local Ollama server) can be selected with `--provider`. When a model is
overloaded (503) or out of quota (429) the client moves to the next configured model; models the
key cannot use (404/403) are dropped for the rest of the run.

## 8. Assumptions and limitations

- **Shared verifier.** The verifier runs in the agent's container (the standard SWE-bench layout).
  `harbor lint` recommends a separate verifier environment; test-file and hook tampering is
  neutralised as described above, but an agent with root access could still tamper with the
  installed pytest. A separate-verifier mode is planned.
- **Blind solvers are single-shot model calls**, not full agents. They are a proxy for clarity and
  difficulty, not a benchmark result.
- **The regression list is computed on the host** and calibrated against the container's nop
  run; use Python 3.12 on the host to keep calibration to zero tests.
- Equivalent mutants (faults that do not change behaviour) cannot be detected automatically and
  lower the mutation score slightly.

## 9. Reused components

Harbor 0.25 (task format, Docker execution, nop and oracle agents), pytest, git worktrees,
click's own test suite and `CliRunner`, and the `google-genai` SDK.

## 10. Tools and models used

- **Pipeline runtime model:** Google Gemini via the Gemini API by default; the model and provider
  actually used are recorded in every `task.toml` and in `reports/scorecard.json`.
- **Execution:** Harbor 0.25.0, Docker, pytest 9.2.0, Python 3.12.
