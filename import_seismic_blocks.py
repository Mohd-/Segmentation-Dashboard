"""Import/merge a seismic block -> AR-number dictionary into seismic_blocks.json.

The app reads config.SEISMIC_BLOCKS_FILE (seismic_blocks.json, block name ->
list of AR-number strings) once at import time to build the Reservoir CoS
sheet's dependent Block/AR dropdowns and the Portfolio "Seismic Block" column
(config.SEISMIC_BLOCK_AR_MAP / AR_TO_SEISMIC_BLOCK). This tool validates an
incoming file of the same shape and writes it into place, so production data
swaps never involve hand-editing JSON.

Where config's loader is deliberately forgiving (a malformed file degrades to
{} so the app still boots), this importer is STRICT: anything that would be
silently dropped there -- a non-dict top level, a non-list AR value, a blank
block name or AR -- is an error here, because at import time a human is
present to fix the source data.

Input file: JSON, e.g.
    {
        "Block A": ["2525", "345346"],
        "Block B": ["1201", "88421"]
    }
(Numeric AR entries are accepted and normalized to strings.)

Usage:
    .venv/bin/python import_seismic_blocks.py new_blocks.json            # merge (default)
    .venv/bin/python import_seismic_blocks.py new_blocks.json --replace  # overwrite the file
    .venv/bin/python import_seismic_blocks.py new_blocks.json --dry-run  # validate + report only

Merge unions each block's AR list into the existing file (existing order kept,
new ARs appended, new blocks added); --replace discards the existing contents.
The app loads the file at process start, so restart it after importing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import config


def load_block_map(path: Path) -> dict:
    """Parse and strictly validate an incoming {block: [ar, ...]} JSON file.

    Returns the normalized map (str block -> [str ar, ...], deduplicated per
    block). Raises ValueError naming the offending entry on any problem.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be an object of block name -> list of AR numbers.")

    normalized: dict = {}
    for block, ars in raw.items():
        block_name = str(block).strip()
        if not block_name:
            raise ValueError(f"{path}: blank block name.")
        if block_name in normalized:
            raise ValueError(f"{path}: block {block_name!r} appears more than once after trimming.")
        if not isinstance(ars, list):
            raise ValueError(f"{path}: block {block_name!r} must map to a LIST of AR numbers, got {type(ars).__name__}.")
        cleaned = []
        for ar in ars:
            if isinstance(ar, (dict, list)):
                raise ValueError(f"{path}: block {block_name!r} contains a non-scalar AR entry: {ar!r}.")
            ar_text = str(ar).strip()
            if not ar_text:
                raise ValueError(f"{path}: block {block_name!r} contains a blank AR entry.")
            if ar_text not in cleaned:  # dedupe within the block, order kept
                cleaned.append(ar_text)
        normalized[block_name] = cleaned
    return normalized


def merge_block_maps(existing: dict, incoming: dict) -> dict:
    """Union ``incoming`` into ``existing``: block order and each block's AR
    order are preserved, new ARs append, new blocks land at the end."""
    merged = {block: list(ars) for block, ars in existing.items()}
    for block, ars in incoming.items():
        current = merged.setdefault(block, [])
        for ar in ars:
            if ar not in current:
                current.append(ar)
    return merged


def duplicate_ars_across_blocks(block_map: dict) -> dict:
    """{ar: [blocks...]} for every AR listed under more than one block.

    Not fatal (config's reverse index resolves these first-block-wins for the
    Portfolio label), but worth a loud warning at import time.
    """
    owners: dict = {}
    for block, ars in block_map.items():
        for ar in ars:
            owners.setdefault(ar, []).append(block)
    return {ar: blocks for ar, blocks in owners.items() if len(blocks) > 1}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", type=Path, help="JSON file of block name -> list of AR numbers.")
    parser.add_argument("--target", type=Path, default=config.SEISMIC_BLOCKS_FILE,
                        help=f"Destination file (default: {config.SEISMIC_BLOCKS_FILE}).")
    parser.add_argument("--replace", action="store_true",
                        help="Overwrite the destination instead of merging into it.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and report what would be written, without writing.")
    args = parser.parse_args()

    try:
        incoming = load_block_map(args.input)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    if not incoming:
        print(f"{args.input}: no blocks to import.", file=sys.stderr)
        sys.exit(1)

    if args.replace or not args.target.exists():
        result = incoming
    else:
        try:
            existing = load_block_map(args.target)
        except ValueError as exc:
            print(f"Existing target failed validation ({exc}); rerun with --replace to discard it.",
                  file=sys.stderr)
            sys.exit(1)
        result = merge_block_maps(existing, incoming)

    for ar, blocks in sorted(duplicate_ars_across_blocks(result).items()):
        print(f"WARNING: AR {ar} is listed under multiple blocks ({', '.join(blocks)}); "
              f"the Portfolio label uses the first.", file=sys.stderr)

    total_ars = sum(len(ars) for ars in result.values())
    mode = "replace" if (args.replace or not args.target.exists()) else "merge"
    print(f"{args.input} -> {args.target} [{mode}]: "
          f"{len(result)} block(s), {total_ars} AR number(s).")
    if args.dry_run:
        print("Dry run: nothing written.")
        return
    args.target.write_text(json.dumps(result, indent=4) + "\n", encoding="utf-8")
    print("Written. The app reads this file at startup -- restart it to pick up the change.")


if __name__ == "__main__":
    main()
