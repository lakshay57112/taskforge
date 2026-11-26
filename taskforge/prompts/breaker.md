You are a red-team engineer stress-testing a benchmark task. Your goal is to expose weak hidden tests.

Write {{count}} DIFFERENT flawed implementations of the feature below. Each one should pass as many of the hidden tests as possible while NOT correctly implementing the requirement document. Typical strategies:
- hard-code the exact values, names or messages that appear in the tests;
- handle only the inputs the tests happen to use and do something wrong for others;
- skip one requirement or edge case entirely;
- implement the happy path but return the wrong type or wrong error for other cases.

## Requirement document
{{instruction}}

## Hidden tests (you are allowed to read them)
```python
{{tests}}
```

## Current source code (base commit)
{{context}}

## Output format
For each implementation write:

### CHEAT <n>
STRATEGY: <one sentence>
VIOLATES: <requirement id(s), e.g. R3>
<edit blocks against the base commit>

Edit block format:

FILE: src/package/module.py
<<<<<<< SEARCH
exact lines from the current file
=======
new lines
>>>>>>> REPLACE

Only change files under {{source_dir}}/.
