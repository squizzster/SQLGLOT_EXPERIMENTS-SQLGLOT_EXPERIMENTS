# INSERT static-assessment experiment

Status: **folded into the public preparation envelope**

This retained experiment proves that the existing SQLGlot source/target pipeline
owns INSERT target and supplied-column evidence. It exercises the public
`prepare_statement()` API only.

The cases deliberately mix quoted dotted identifiers, multi-row UPSERT with
`RETURNING`, injection-shaped data, MySQL SET, a CTE INSERT SELECT, absent column
ownership, qualification, and REPLACE exclusion.

The result is intentionally not an auto-increment/unique boolean. SQLGlot has no
database schema. It supplies the authoritative static SQL half; the SQL Agent must
combine that with normalized schema evidence and return `TRUE` or `FALSE`.

Run:

```bash
uv run python -m experiments.insert_static_assessment.run_experiment
uv run python -m unittest experiments.insert_static_assessment.test_experiment
```
