"""Small runnable work-queue service; the baseline does not filter tags yet."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def select_tickets(tickets, *, status=None, tags=None, limit=None):
    """Return matching tickets in ascending ID order without mutating input."""
    if limit is not None and (
        not isinstance(limit, int) or isinstance(limit, bool) or limit < 0
    ):
        raise ValueError("limit must be a non-negative integer or None")

    requested_tags = {
        normalized
        for tag in (tags or [])
        if (normalized := tag.strip().casefold())
    }

    selected = []
    for ticket in tickets:
        if status is not None and ticket["status"] != status:
            continue
        if requested_tags:
            stored_tags = {
                normalized
                for tag in ticket.get("tags", [])
                if (normalized := tag.strip().casefold())
            }
            if not requested_tags.issubset(stored_tags):
                continue
        selected.append(ticket)

    selected.sort(key=lambda ticket: ticket["id"])
    return selected if limit is None else selected[:limit]


def main():
    parser = argparse.ArgumentParser(description="Query the sample work queue")
    parser.add_argument("--status")
    parser.add_argument("--tag", action="append", dest="tags")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    data = json.loads(Path(__file__).with_name("tickets.json").read_text(encoding="utf-8"))
    result = select_tickets(data, status=args.status, tags=args.tags, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
