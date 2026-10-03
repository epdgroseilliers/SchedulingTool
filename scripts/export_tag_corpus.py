#!/usr/bin/env python
"""Export the desk's West e-Tag history as the routes it repeats.

    python scripts/export_tag_corpus.py --start 2026-01-01

Writes a `tag_corpus/` directory: one CSV of recipes, most used first, and
one JSON file per recipe holding a few complete tags that followed it. The
point is to make the desk's own practice legible — what routes we run, how
often, with whom, over which transmission — as the ground truth for parsing
the path strings we swap on ICE chat and for prefilling a tag from the last
one like it.

**Read-only.** Everything here goes through `data.tags_history`, whose four
SQL files are all SELECTs against `MAG.dbo.OATI_*`.

**Local only.** These are MAG Energy Solutions e-Tag records. The output
directory ignores itself in git, a destination outside the repo on a shared
drive is called out, and nothing uploads anywhere.

A one-shot operator tool, outside the app's three layers — it imports `data`
and `domain` and is imported by nothing.
"""

import argparse
import csv
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.tags_history import assemble, fetch_frames  # noqa: E402
from domain.tag_recipes import SIGNATURE_VERSION, group_by_recipe  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "tag_corpus"

CSV_COLUMNS = [
    "rank", "recipe_id", "count", "first_flow_date", "last_flow_date",
    "gca", "lca", "n_market_steps", "n_physical_steps",
    "transmission_providers", "counterparties", "summary", "signature",
]

README = """# Tag corpus

Generated {generated} by `scripts/export_tag_corpus.py`.

**Internal MAG Energy Solutions e-Tag records. Keep them on this machine —
do not upload them to any external service.**

## What this is

Every West e-Tag the desk ran between {start} and {stop}, grouped by
*recipe*: the route a tag follows, stripped of everything that changes from
one day to the next. {tags:,} tags collapse into {recipes:,} recipes.

## Filters applied

- Both control areas are Western: neither is PJM, CPLE, ISNE, ERCO, NYIS or ONT.
- `TestTag = 0`.
- `LastAction` is one of IMPLEMENTED, ADJUSTED, CURTAILED, EXTENDED,
  RELOADED, CONFIRMED{unconfirmed}. Withdrawn, cancelled, denied, expired and
  terminated tags are routes the desk pulled back, and teach the wrong thing.
- Flow date is the tag's UTC start converted to Pacific — a full PPT day
  reads as 07:00 to 07:00 in OATI.

## The signature (version {version})

    <GCA>><LCA> || MS <pse>:<product> > ... || PS <segment> ; ...

A physical segment is `<ms>:G <point>(<CA>)`, `<ms>:L <point>(<CA>)`, or
`<ms>:T <provider> <POR>(<CA>)><POD>(<CA>) [<transmission products>]`, where
`<ms>` is the market-path step that owns it. `-` stands for an empty field
and is never dropped: a blank energy product marks a wheel-through, and
losing it would make a three-party chain read as two.

Deliberately absent: reservation numbers, times, MW, tag ids. Two tags a
month apart on the same route produce the same string.

## Files

- `recipes.csv` — one row per recipe, most used first. Columns: {columns}.
- `samples/<rank>_<recipe_id>.json` — up to {samples} complete tags per
  recipe, newest first, each self-contained.

## Reading it

`counterparties` is the set of PSEs MAG stands next to in the market path —
a set, not a name, because MAG sits in the middle of a wheel about a third of
the time and appears twice in one chain in another 15%.
"""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--start", type=date.fromisoformat, required=True,
                        help="First flow date to include (YYYY-MM-DD).")
    parser.add_argument("--stop", type=date.fromisoformat, default=date.today(),
                        help="Last flow date to include (default: today).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"Output directory (default: {DEFAULT_OUT}).")
    parser.add_argument("--samples", type=int, default=3,
                        help="Complete tags to keep per recipe (default: 3).")
    parser.add_argument("--min-count", type=int, default=1,
                        help="Skip recipes used fewer times than this.")
    parser.add_argument("--top", type=int, default=0,
                        help="Keep only the N most used recipes (0 = all).")
    parser.add_argument("--include-unconfirmed", action="store_true",
                        help="Also include withdrawn, cancelled and denied tags.")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def check_destination(path, quiet=False):
    r"""Say something if the corpus is being written somewhere shared.

    A warning rather than a refusal: this repository itself lives on the
    user's network home directory, so "the path is a UNC path" does not by
    itself mean "other people can read it". What is worth flagging is a
    destination *outside* the repo that is also on a share — the shape of
    an accidental write to Y:\ or Z:\, where the desk's own files live.
    """
    resolved = Path(path).resolve()
    repo = Path(__file__).resolve().parent.parent
    inside_repo = resolved == repo or repo in resolved.parents
    if not inside_repo and str(resolved).startswith("\\\\") and not quiet:
        print(
            f"warning: {resolved} is a shared network path. These are "
            "internal tagging records — keep them where only you can read "
            "them.",
            file=sys.stderr,
        )
    return path


def write_recipes_csv(path, recipes):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for rank, recipe in enumerate(recipes, start=1):
            sample = recipe["samples"][0] if recipe["samples"] else {}
            writer.writerow([
                rank,
                recipe["recipe_id"],
                recipe["count"],
                recipe["first_flow_date"] or "",
                recipe["last_flow_date"] or "",
                recipe["gca"],
                recipe["lca"],
                len(sample.get("market_path") or []),
                len(sample.get("physical_path") or []),
                "|".join(recipe["transmission_providers"]),
                "|".join(recipe["counterparties"]),
                recipe["summary"],
                recipe["signature"],
            ])


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, tuple)):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON-serializable")


def write_sample_files(directory, recipes):
    """One self-contained file per recipe, named by rank so a bare directory
    listing is already sorted by how often the desk runs the route."""
    directory.mkdir(parents=True, exist_ok=True)
    for rank, recipe in enumerate(recipes, start=1):
        payload = {
            "signature_version": SIGNATURE_VERSION,
            "recipe_id": recipe["recipe_id"],
            "signature": recipe["signature"],
            "summary": recipe["summary"],
            "count": recipe["count"],
            "first_flow_date": recipe["first_flow_date"],
            "last_flow_date": recipe["last_flow_date"],
            "counterparties": list(recipe["counterparties"]),
            "transmission_providers": list(recipe["transmission_providers"]),
            "samples": list(recipe["samples"]),
        }
        name = f"{rank:04d}_{recipe['recipe_id']}.json"
        (directory / name).write_text(
            json.dumps(payload, indent=2, default=_jsonable), encoding="utf-8"
        )


def write_readme(path, args, tags, recipes, generated):
    path.write_text(
        README.format(
            generated=generated.strftime("%Y-%m-%d %H:%M"),
            start=args.start,
            stop=args.stop,
            tags=tags,
            recipes=recipes,
            version=SIGNATURE_VERSION,
            samples=args.samples,
            columns=", ".join(f"`{c}`" for c in CSV_COLUMNS),
            unconfirmed=(
                " — **relaxed for this export (--include-unconfirmed)**"
                if args.include_unconfirmed else ""
            ),
        ),
        encoding="utf-8",
    )


def main(argv=None):
    args = parse_args(argv)
    # Importing data.tags_history pulls in Streamlit, which logs a warning
    # per cached call outside a runtime. fetch_frames is the uncached path,
    # so this only silences noise from the import itself.
    logging.getLogger("streamlit").setLevel(logging.ERROR)

    out = check_destination(args.out, args.quiet)
    frames = fetch_frames(args.start, args.stop, all_actions=args.include_unconfirmed)
    records = assemble(frames["tag"], frames["ms"], frames["ps"], frames["ta"])
    recipes = group_by_recipe(records, samples=args.samples)

    if args.min_count > 1:
        recipes = [r for r in recipes if r["count"] >= args.min_count]
    if args.top:
        recipes = recipes[: args.top]

    if not args.quiet:
        print(f"{len(records):,} tags -> {len(recipes):,} recipes")

    out.mkdir(parents=True, exist_ok=True)
    # The folder ignores itself, which survives someone moving it and needs
    # no change to the repo's own .gitignore to be safe.
    (out / ".gitignore").write_text("*\n", encoding="utf-8")
    write_recipes_csv(out / "recipes.csv", recipes)
    write_sample_files(out / "samples", recipes)
    write_readme(out / "README.md", args, len(records), len(recipes), datetime.now())

    if not args.quiet:
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
