# Ticket selection specification

Revision: 2

This document governs `tickets.py::select_tickets`, the work-queue filtering API.
Valid input is an iterable of ticket dictionaries with integer `id`, string
`status`, and a `tags` list containing strings. Requested tags are a list of
strings or `None`. Fields such as title and priority must be preserved.

## REQ-TAG-001 — Requested tag matching

tag_match_mode: ALL

For a nonempty normalized request, select a ticket only when **all** requested
tags are present. Normalize requested and stored tags with `strip().casefold()`.
Ignore blank requested tags and duplicates. `None`, an empty request, or a
request containing only whitespace means no tag restriction.

With `tickets.json`, tags `backend` and `bug` match ID `[9]` in this revision.
This revision replaces the earlier ANY rule at the same source path
`specs/ticket_filters.md`; do not combine the old and new rules. The baseline
still ignores requested tags, so it must be changed to meet this specification.

## REQ-COMPAT-001 — Existing status behavior

When status is not `None`, require exact equality with the stored status;
status matching remains case-sensitive. Unknown status returns an empty list.
Status and tag restrictions must both hold.

## REQ-COMPAT-002 — Sorting and pagination

Return a new list sorted by ascending integer ID. Apply `limit` only after
all filtering and sorting. `None` has no limit; zero returns an empty list.
Negative, noninteger, and boolean limits raise `ValueError` as before.

## REQ-COMPAT-003 — Data preservation

Do not mutate input ordering, dictionaries, stored tag lists, or the requested
tag list. Preserve all original fields. Empty input and iterator input remain
valid. Related regression checks are in `tests/test_regression.py`.

## Verification sources

- Code: `tickets.py::select_tickets`
- Target functionality: `tests/test_tags.py`
- Existing behavior: `tests/test_regression.py`
- Initial data: `tickets.json`
