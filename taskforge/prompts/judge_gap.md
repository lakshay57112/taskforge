An independent developer implemented a task using only its requirement document, and some hidden tests failed. Decide, for each failing test, whose fault it is.

## Requirement document (all the developer saw)
{{instruction}}

## The developer's implementation (diff)
```diff
{{attempt}}
```

## Failing hidden tests and their output
{{failures}}

## Decide
- "spec_gap": the test checks something the requirement document does not state, states ambiguously, or contradicts. A careful developer could reasonably have done what this developer did.
- "solver_error": the document states the behaviour clearly and the developer simply got it wrong.

Reply with JSON only:
{
  "findings": [
    {"test": "test name", "verdict": "spec_gap | solver_error", "reason": "one sentence",
     "missing_detail": "for spec_gap: the exact fact the document must add, else empty"}
  ]
}
