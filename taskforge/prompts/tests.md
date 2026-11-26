Write the hidden pytest tests for this feature task.

## Requirement document (the only thing the implementer sees)
{{instruction}}

## Requirements to cover
{{requirements}}

## Example of the repository's own test style
```python
{{style_example}}
```

## Rules
- One pytest file. It will be saved as {{test_path}} and run with the repository's own test runner (pytest {{pytest_version}}). The repository treats warnings as errors.
- Name every test `test_r<N>_<what>` where R<N> is the requirement it checks. Every requirement needs at least one test; aim for 8-16 tests in total, including edge and error cases.
- Test ONLY behaviour that the requirement document states. Never assert on internal helpers, private names or exact wording that the document does not specify.
- Every test must FAIL on the current repository (before the feature exists) and PASS once the feature is implemented as described. A test that would pass today is useless.
- Reach new API through the package at call time (for example `click.Duration()` inside the test function), not with a module-level `from package import NewThing`, so the file still imports before the feature exists.
- For command-line behaviour use `click.testing.CliRunner` (a `runner` fixture returning `CliRunner()` already exists in conftest.py). Check `result.exit_code` and `result.output`.
- Deterministic: no network, no sleep, no real time, no randomness, no environment leakage (use `monkeypatch` / `tmp_path`).
- Only the standard library, pytest and the package itself.

Return only the complete test file in a single ```python code block.
