A deliberately flawed implementation passed every hidden test, so the tests are too weak. Strengthen them.

## Requirement document
{{instruction}}

## Current hidden tests ({{test_path}})
```python
{{tests}}
```

## Flawed implementations that passed
{{cheats}}

## Rules
- Keep every existing test unless it is wrong.
- Add tests that FAIL on each flawed implementation but PASS on a correct implementation of the requirement document, for example by using different input values, names or messages than the existing tests, or by covering the skipped requirement.
- Only test behaviour stated in the requirement document. If a flawed implementation actually satisfies the document, do not add a test for it; say so in a comment at the top of the file instead.
- Same conventions as before: `test_r<N>_...` names, new API reached at call time, deterministic.

Return only the complete new test file in a single ```python code block.
