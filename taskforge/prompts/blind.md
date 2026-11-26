You are an AI coding agent working in a checkout of {{repo}}. Implement the task described below. You will not see any tests; your work is graded by hidden tests that check the requirements exactly as written.

## Task
{{instruction}}

## Repository source code you may need
{{context}}

## Output format
Return edit blocks only:

FILE: src/package/module.py
<<<<<<< SEARCH
exact lines from the current file
=======
new lines
>>>>>>> REPLACE

- SEARCH must match the current file exactly and appear once. Leave SEARCH empty to create a new file.
- Only change files under {{source_dir}}/.
