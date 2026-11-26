The validation of this task failed. Diagnose the cause and fix it.

## Requirement document
{{instruction}}

## Reference solution (diff against the base commit)
```diff
{{solution}}
```

## Hidden tests ({{test_path}})
```python
{{tests}}
```

## What went wrong
{{problems}}

## Test output
{{report}}

## Relevant source code (base commit)
{{context}}

## How to decide what to change
- A feature test that PASSES before the solution is applied does not test the new feature: rewrite it so it depends on the new behaviour.
- A feature test that FAILS with the solution: decide whether the solution is wrong (it does not do what the document says) or the test is wrong (it checks something the document does not state, or checks it incorrectly). Fix whichever is wrong.
- An existing repository test that fails with the solution means the solution broke existing behaviour: fix the solution, never the existing test.
- Keep at least one test per requirement. Do not delete tests just to make the run green.

## Output format
Start with one line: `DIAGNOSIS: <one sentence>`.
Then, if the solution must change, give edit blocks against the CURRENT solution state (the base file with the solution applied):

FILE: src/package/module.py
<<<<<<< SEARCH
exact current lines
=======
new lines
>>>>>>> REPLACE

Then, if the tests must change, give the COMPLETE new test file in one ```python code block.
Give only what needs to change.
