Propose {{count}} candidate feature-development tasks for {{repo}}.

## Codebase map (areas you can place a feature in)
{{codemap}}

## Variation axes
Each candidate must be classified under exactly one axis:
{{axes}}

## Already chosen (do not repeat or closely resemble these)
{{taken}}

## What makes a good candidate
- Something a real maintainer could plausibly merge: useful to users of {{repo}}, consistent with its design.
- Substantively different from the other candidates: different behaviour, rules or interactions. Renaming or rewording an idea is not a new idea.
- Spread across areas and axes. Prefer a different area AND a different axis for every candidate.
- Implementable in roughly 40-250 lines of library code, testable through the public API (for a CLI library: click.testing.CliRunner).
- Must not already exist in the repository. Give "novelty_terms": 1-3 exact strings that would necessarily appear in the source code if the feature already existed (for example a new parameter name like "max_attempts=" or a class name like "class Duration"). Avoid generic words.
- Has several distinct, checkable behaviours (normal cases, edge cases, error messages).

Reply with JSON only:
{
  "candidates": [
    {
      "slug": "kebab-case-id",
      "title": "Short title",
      "area": "one area id from the map",
      "axis": "one axis name",
      "summary": "2-3 sentences: what the feature does and why users want it",
      "public_api": ["exact new or changed API, e.g. click.Duration(min=None, max=None)"],
      "behaviours": ["concrete behaviour 1", "edge case 2", "error case 3"],
      "novelty_terms": ["exact string"],
      "files": ["src/... files that will most likely change"],
      "difficulty": "easy | medium | hard"
    }
  ]
}
