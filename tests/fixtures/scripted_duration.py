"""Canned model answers for an offline end-to-end run (feature: click.Duration).

The script deliberately contains a buggy first solution, a cheat that survives the
first test suite, and a weak blind attempt, so that the fix loop, the Breaker
hardening and the spec-gap path all run. Used only by the test suite.
"""

import json

IMPORT_EDITS = '''FILE: src/click/types.py
<<<<<<< SEARCH
import os
import stat
=======
import os
import re
import stat
>>>>>>> REPLACE

FILE: src/click/types.py
<<<<<<< SEARCH
from datetime import datetime
=======
from datetime import datetime
from datetime import timedelta
>>>>>>> REPLACE

FILE: src/click/__init__.py
<<<<<<< SEARCH
from .types import DateTime as DateTime
=======
from .types import DateTime as DateTime
from .types import Duration as Duration
>>>>>>> REPLACE
'''

DURATION_CLASS = '''

class Duration(ParamType[timedelta]):
    """Converts a length of time such as ``90s``, ``5m``, ``1h30m`` or
    ``2d`` into a :class:`datetime.timedelta`.

    :param min: The shortest accepted duration, or ``None`` for no limit.
    :param max: The longest accepted duration, or ``None`` for no limit.

    .. versionadded:: 8.6
    """

    name = "duration"
    _pattern = re.compile(PATTERN)

    def __init__(
        self, min: timedelta | None = None, max: timedelta | None = None
    ) -> None:
        self.min = min
        self.max = max

    def convert(
        self, value: t.Any, param: Parameter | None, ctx: Context | None
    ) -> timedelta:
        if isinstance(value, timedelta):
            return value

        text = TEXT
        if text.isdigit():
            result = timedelta(seconds=int(text))
        else:
            match = self._pattern.fullmatch(text)
            if not text or match is None or not any(match.groups()):
                self.fail(
                    _(MESSAGE).format(value=text),
                    param,
                    ctx,
                )
            result = timedelta(**{k: int(v or 0) for k, v in match.groupdict().items()})
BOUNDS
        return result

    def __repr__(self) -> str:
        return "Duration()"'''

BOUNDS = '''
        if self.min is not None and result < self.min:
            self.fail(
                _("{value!r} is shorter than the minimum of {min}.").format(
                    value=text, min=self.min
                ),
                param,
                ctx,
            )
        if self.max is not None and result > self.max:
            self.fail(
                _("{value!r} is longer than the maximum of {max}.").format(
                    value=text, max=self.max
                ),
                param,
                ctx,
            )'''

FULL_PATTERN = 'r"(?:(?P<days>\\d+)d)?(?:(?P<hours>\\d+)h)?(?:(?P<minutes>\\d+)m)?(?:(?P<seconds>\\d+)s)?"'
NO_DAYS_PATTERN = 'r"(?:(?P<hours>\\d+)h)?(?:(?P<minutes>\\d+)m)?(?:(?P<seconds>\\d+)s)?"'
GOOD_MESSAGE = '"{value!r} is not a valid duration."'


def duration_class(pattern=FULL_PATTERN, text="str(value).strip().lower()", message=GOOD_MESSAGE, bounds=True):
    return (DURATION_CLASS.replace("PATTERN", pattern).replace("TEXT", text)
            .replace("MESSAGE", message).replace("BOUNDS", BOUNDS if bounds else ""))


def solution_blocks(**kw) -> str:
    return IMPORT_EDITS + f'''
FILE: src/click/types.py
<<<<<<< SEARCH
    def __repr__(self) -> str:
        return "UUID"
=======
    def __repr__(self) -> str:
        return "UUID"{duration_class(**kw)}
>>>>>>> REPLACE
'''


INSTRUCTION = """# Add a `click.Duration` parameter type

## Background
Many command-line tools accept lengths of time such as timeouts, retry delays or cache
lifetimes. Click has no built-in way to parse values like `90s` or `1h30m`, so every
application re-implements it. Add a `Duration` parameter type that converts such text
into a `datetime.timedelta`.

## Requirements
- R1: Add `click.Duration`, a `click.ParamType` subclass importable as `click.Duration`.
  `click.Duration(min=None, max=None)` converts values to `datetime.timedelta`.
- R2: A value is one or more `<integer><unit>` groups with the units `d` (days), `h`
  (hours), `m` (minutes) and `s` (seconds), written in that order, each unit at most once,
  with nothing between groups (for example `90s`, `5m`, `1h30m`, `2d`, `1d2h3m4s`). A bare
  integer such as `45` means seconds. Leading and trailing whitespace is ignored and units
  are case-insensitive (`2H` equals `2h`).
- R3: Any other value is rejected with the standard parameter error, so the command exits
  with code 2 and the message contains `'<value>' is not a valid duration.` where `<value>`
  is the input without surrounding whitespace (for example `'abc' is not a valid duration.`).
- R4: `min` and `max` are optional `timedelta` bounds and are inclusive. A shorter value
  fails with a message containing `is shorter than the minimum`; a longer value fails with
  a message containing `is longer than the maximum`.
- R5: A value that is already a `timedelta` (for example an option default) is returned
  unchanged.
- R6: The type's name is `duration`, so help pages show the metavar `DURATION`.

## Examples
```python
@click.command()
@click.option("--wait", type=click.Duration(max=timedelta(hours=2)))
def cli(wait):
    click.echo(wait.total_seconds())
```
`cli --wait 1h30m` prints `5400.0`; `cli --wait 3h` exits with code 2.

## Constraints
Existing parameter types and their behaviour must not change. Do not add dependencies.
"""

TESTS_V1 = '''from datetime import timedelta

import click


def _cli(duration_type=None, **option_kwargs):
    @click.command()
    @click.option("--wait", type=duration_type or click.Duration(), **option_kwargs)
    def cli(wait):
        click.echo(repr(wait.total_seconds()) if wait is not None else "none")

    return cli


def test_r1_converts_to_timedelta(runner):
    result = runner.invoke(_cli(), ["--wait", "90s"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "90.0"


def test_r1_is_a_param_type():
    assert isinstance(click.Duration(), click.ParamType)
    assert click.Duration().convert("5m", None, None) == timedelta(minutes=5)


def test_r2_combined_units(runner):
    result = runner.invoke(_cli(), ["--wait", "1h30m"])
    assert result.output.strip() == "5400.0"


def test_r2_bare_number_is_seconds(runner):
    result = runner.invoke(_cli(), ["--wait", "45"])
    assert result.output.strip() == "45.0"


def test_r2_whitespace_and_case_are_ignored(runner):
    result = runner.invoke(_cli(), ["--wait", "  2H  "])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "7200.0"


def test_r3_rejects_garbage(runner):
    result = runner.invoke(_cli(), ["--wait", "abc"])
    assert result.exit_code == 2
    assert "'abc' is not a valid duration." in result.output


def test_r3_rejects_wrong_unit_order(runner):
    result = runner.invoke(_cli(), ["--wait", "30m1h"])
    assert result.exit_code == 2
    assert "'30m1h' is not a valid duration." in result.output


def test_r4_minimum(runner):
    result = runner.invoke(_cli(click.Duration(min=timedelta(seconds=10))), ["--wait", "5s"])
    assert result.exit_code == 2
    assert "is shorter than the minimum" in result.output


def test_r4_maximum(runner):
    result = runner.invoke(_cli(click.Duration(max=timedelta(hours=1))), ["--wait", "2h"])
    assert result.exit_code == 2
    assert "is longer than the maximum" in result.output


def test_r4_bounds_are_inclusive(runner):
    result = runner.invoke(_cli(click.Duration(min=timedelta(seconds=10))), ["--wait", "10s"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "10.0"


def test_r5_timedelta_default_passes_through(runner):
    result = runner.invoke(_cli(default=timedelta(minutes=1)), [])
    assert result.output.strip() == "60.0"


def test_r6_help_shows_metavar(runner):
    result = runner.invoke(_cli(), ["--help"])
    assert "DURATION" in result.output
'''

TESTS_V2 = TESTS_V1 + '''

def test_r2_days(runner):
    result = runner.invoke(_cli(), ["--wait", "2d"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "172800.0"


def test_r2_all_units(runner):
    result = runner.invoke(_cli(), ["--wait", "1d2h3m4s"])
    assert result.output.strip() == "93784.0"
'''

FIX_REPLY = '''DIAGNOSIS: The solution does not strip whitespace or lower-case the input as R2 requires.

FILE: src/click/types.py
<<<<<<< SEARCH
        text = str(value)
        if text.isdigit():
=======
        text = str(value).strip().lower()
        if text.isdigit():
>>>>>>> REPLACE
'''

HARDCODE_CHEAT = IMPORT_EDITS + '''
FILE: src/click/types.py
<<<<<<< SEARCH
    def __repr__(self) -> str:
        return "UUID"
=======
    def __repr__(self) -> str:
        return "UUID"


class Duration(ParamType[timedelta]):
    name = "duration"

    def __init__(self, min=None, max=None):
        self.min, self.max = min, max

    def convert(self, value, param, ctx):
        if isinstance(value, timedelta):
            return value
        known = {"90s": 90, "1h30m": 5400, "45": 45, "5m": 300}
        if value.strip() not in known:
            self.fail(f"{value!r} is not a valid duration.", param, ctx)
        return timedelta(seconds=known[value.strip()])
>>>>>>> REPLACE
'''

BREAKER_REPLY = f"""### CHEAT 1
STRATEGY: Support only hours, minutes and seconds; the tests never use days.
VIOLATES: R2
{solution_blocks(pattern=NO_DAYS_PATTERN)}

### CHEAT 2
STRATEGY: Hard-code the values used in the tests.
VIOLATES: R2, R4
{HARDCODE_CHEAT}

### CHEAT 3
STRATEGY: Parse correctly but ignore the min and max bounds.
VIOLATES: R4
{solution_blocks(bounds=False)}
"""


def make_responder():
    counters: dict[str, int] = {}

    def respond(stage: str, system: str, prompt: str) -> str:
        n = counters.get(stage, 0)
        counters[stage] = n + 1
        if stage == "propose":
            return json.dumps({"candidates": [
                {"slug": "duration-type", "title": "Duration parameter type", "area": "types",
                 "axis": "parsing", "summary": "Parse 90s / 1h30m into timedelta.",
                 "public_api": ["click.Duration(min=None, max=None)"],
                 "behaviours": ["units d h m s", "bare integer is seconds", "bounds"],
                 "novelty_terms": ["class Duration"], "files": ["src/click/types.py"],
                 "difficulty": "medium"},
                {"slug": "choice-type", "title": "Choice type", "area": "types.Choice",
                 "axis": "validation", "summary": "Already exists - must be rejected.",
                 "novelty_terms": ["class Choice"], "files": ["src/click/types.py"]},
            ]})
        if stage == "spec":
            return f"Here is the document.\n\n```markdown\n{INSTRUCTION}```"
        if stage == "solution":
            return solution_blocks(text="str(value)")          # bug: no strip()/lower()
        if stage == "tests":
            return f"```python\n{TESTS_V1}```"
        if stage == "fix":
            return FIX_REPLY
        if stage == "breaker":
            return BREAKER_REPLY
        if stage == "harden":
            return f"```python\n{TESTS_V2}```"
        if stage == "blind":
            if n == 1:   # second solver uses a different error message
                return solution_blocks(message='"Invalid duration: {value!r}"')
            return solution_blocks()
        if stage == "judge":
            return json.dumps({"findings": [{"test": "test_r3_rejects_garbage", "verdict": "spec_gap",
                                             "reason": "message wording", "missing_detail":
                                             "The error text must be exactly '<value>' is not a valid duration."}]})
        if stage == "clarify":
            return INSTRUCTION + "\nThe error text is exactly `'<value>' is not a valid duration.`\n"
        raise AssertionError(f"unexpected stage {stage}")

    return respond
