"""Command line entry point for fetching and searching official UMD nutrition data."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .umd import HALLS, retrieve, save_json, search


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m macromellow", description="UMD Dining nutrition prototype")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="Retrieve official menu and nutrition labels")
    fetch.add_argument("--date", default=datetime.now(ZoneInfo("America/New_York")).date().isoformat(), help="Menu date, YYYY-MM-DD; defaults to today in Maryland")
    fetch.add_argument("--hall", choices=["all", *HALLS], default="all", help="Dining hall; defaults to all three")
    fetch.add_argument("--output", type=Path, help="Output JSON path; defaults to data/umd-YYYY-MM-DD.json")
    fetch.add_argument("--delay", type=float, default=0.25, help="Minimum seconds between requests; defaults to 0.25")
    fetch.add_argument("--max-labels", type=int, help="Development smoke test: fetch at most N unique labels")
    find = sub.add_parser("search", help="Search a previously saved JSON file")
    find.add_argument("query", help="Food name substring")
    find.add_argument("--file", type=Path, required=True, help="Saved JSON file")
    find.add_argument("--hall", choices=list(HALLS), help="Restrict results to one hall")
    web = sub.add_parser("web", help="Launch the local nutrition data inspector")
    web.add_argument("--port", type=int, default=8000, help="Local port; defaults to 8000")
    web.add_argument("--data-dir", type=Path, help="Directory of retrieved JSON files; defaults to this repository's data directory")
    web.add_argument("--no-browser", action="store_true", help="Start the server without opening a browser tab")

    args = parser.parse_args()
    if args.command == "web":
        if not 1 <= args.port <= 65535:
            parser.error("--port must be between 1 and 65535")
        from .web import serve
        return serve(args.port, args.data_dir, open_browser=not args.no_browser)
    if args.command == "fetch":
        try:
            menu_date = date.fromisoformat(args.date)
        except ValueError:
            parser.error("--date must be a valid YYYY-MM-DD date")
        if args.delay < 0.25:
            parser.error("--delay must be at least 0.25 seconds")
        if args.max_labels is not None and args.max_labels < 1:
            parser.error("--max-labels must be positive")
        output = args.output or Path("data") / f"umd-{menu_date.isoformat()}.json"
        try:
            result = retrieve(menu_date, list(HALLS) if args.hall == "all" else [args.hall], args.delay, args.max_labels)
        except RuntimeError as exc:
            print(f"Retrieval stopped: {exc}", file=sys.stderr)
            return 1
        save_json(result, output)
        print(f"Saved {len(result['foods'])} menu entries to {output} ({len(result['errors'])} errors)")
        return 0 if not result["errors"] else 2

    try:
        matches = search(args.file, args.query, args.hall)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Cannot search file: {exc}", file=sys.stderr)
        return 1
    for food in matches:
        def show(value: object, unit: str = "") -> str:
            return "unknown" if value is None else f"{value}{unit}"
        print(f"{food['name']} | {food['dining_hall']} | {food['menu_date']} | {food.get('meal') or 'meal unknown'}")
        print(f"  Serving: {show(food.get('serving_size'))} | Calories: {show(food.get('calories'))} | Protein: {show(food.get('protein_g'), ' g')} | Carbs: {show(food.get('carbohydrates_g'), ' g')} | Fat: {show(food.get('fat_g'), ' g')}")
        print(f"  Source: {food['source_url']}")
    print(f"{len(matches)} match(es)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
