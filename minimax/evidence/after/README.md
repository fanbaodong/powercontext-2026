# Runnable ticket-filter baseline

Python 3.11+ and the standard library are sufficient. All records in
`tickets.json` are synthetic. Run commands from this directory:

```sh
python tickets.py --status open --tag security --limit 1
python -m unittest discover -s tests -p test_tags.py -v
python -m unittest discover -s tests -p test_regression.py -v
```

The delivered initial source intentionally accepts but ignores `tags`.
Its CLI currently returns ID `1` for the command above, while revision 2
requires ID `9`. The seven target tests therefore fail on concrete result
assertions. The twelve regression tests pass before the change.

`specs/ticket_filters.md` initially contains revision 1 (ANY). The SDD workflow
first imports that source, replaces it with `../fixtures/ticket_filters_v2.md`
at the same logical path, then imports again. The active revision 2 requires
ALL tags and is the version that must enter MiniMax's development context.
The two test files remain byte-for-byte identical before and after the change.

Only MiniMax's actual accepted edit to `tickets.py` produces the after version.
This baseline does not include a prewritten solution or success evidence.
`specs/payment_receipts.md` is unrelated engineering material for checking that
context selection does not return a hard-coded collection of all documents.

For a reset, copy this pristine baseline into a new working directory, use an
empty task-state namespace, and rerun the parent workflow. Do not overwrite
the previous run's evidence directory. The task and exact commands are defined
in `../fixtures/task.json`.
