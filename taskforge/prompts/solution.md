Implement this feature in the repository. This is the reference solution, so it must be correct, complete and clean.

## Requirement document
{{instruction}}

## Current source code (base commit)
{{context}}

## Rules
- Only change files under {{source_dir}}/. Do not touch tests, docs or packaging files.
- Keep all existing behaviour working; the existing test suite must still pass.
- Follow the code style of the surrounding code (type hints, docstrings, naming).
- Export new public names the way the package already does it.
- Do not add imports of modules the package does not already import at module level unless needed; prefer local imports for heavy standard-library modules.
- No new dependencies.

## Output format
Return one or more edit blocks and nothing else. Each block replaces an exact, unique piece of an existing file:

FILE: src/package/module.py
<<<<<<< SEARCH
exact lines copied from the current file
=======
the new lines
>>>>>>> REPLACE

- SEARCH must match the current file exactly (including indentation) and appear only once; include a few surrounding lines to make it unique.
- To create a new file, leave the SEARCH section empty.
- Use several small blocks rather than one huge block.
