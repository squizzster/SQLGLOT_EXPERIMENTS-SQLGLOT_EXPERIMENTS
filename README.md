# SQLGlot Experiments

An experimental Python workspace for learning and evaluating
[SQLGlot](https://github.com/tobymao/sqlglot) against representative SQL
parsing and transformation cases.

## Development mode

`EXP` — run a representative case, observe real behaviour, make the smallest
useful adaptation, and repeat.

## Current state

The Python 3.12 project baseline and SQLGlot dependency are installed and
locked. The first experiment exercises SQLGlot's native parser, lineage, and
transpiler APIs directly against the verified SQLite source query.

## Run

```bash
uv sync
uv run python experiments/native_api_showcase.py
```
