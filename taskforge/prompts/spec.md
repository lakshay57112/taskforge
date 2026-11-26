Write the requirement document for this feature task.

## Feature
{{proposal}}

## Relevant source code (base commit)
{{context}}

## How the document is used
An AI coding agent receives ONLY this document plus a checkout of the repository at /app. It cannot see any tests. Hidden tests will check every requirement you write, so the document must be complete and unambiguous on its own.

## Rules
- Write for a developer: start with a short "Background" paragraph, then "Requirements" as a numbered list (R1, R2, ...), then "Examples", then "Constraints".
- 4 to 8 requirements. Each requirement is one testable behaviour.
- State exact public names, signatures, parameter names, defaults, return types and where the new API must be importable from (for example: "available as `click.Duration`").
- When the behaviour includes an error, give the exact exception type and, if it matters, the exact message text or the exact substring users will see.
- Cover edge cases explicitly (empty input, boundaries, combinations with existing options).
- Say that existing behaviour must not change when the feature is not used.
- Do not mention tests, test files or how grading works. Do not give the implementation.
- Examples use a small runnable snippet and show the expected output.
- 350-900 words.

Return only the markdown document, starting with a `# Title` line. Write each requirement as a list item that begins with its id, for example `- R1: ...`.
