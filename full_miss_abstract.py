import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "paper_cache"
BUILD_CACHE_PATH = BASE_DIR / "build-cache.py"


def load_build_cache_module():
    spec = importlib.util.spec_from_file_location("build_cache_module", BUILD_CACHE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {BUILD_CACHE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_payload(info_path: Path) -> dict:
    return json.loads(info_path.read_text(encoding="utf-8"))


def save_payload(info_path: Path, payload: dict) -> None:
    info_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def print_status(prefix: str, index: int, total: int, title: str) -> None:
    short_title = title if len(title) <= 110 else title[:107] + "..."
    print(f"[{index}/{total}] {prefix} {short_title}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fill missing abstracts and Chinese translations in paper_cache/<year>/<conference>/info.json"
    )
    parser.add_argument("conference", help="Conference key, e.g. asplos")
    parser.add_argument("year", type=int, help="Year, e.g. 2026")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N missing abstracts")
    parser.add_argument("--debug", action="store_true", help="Enable build-cache debug logs during fetch")
    args = parser.parse_args()

    info_path = CACHE_DIR / str(args.year) / args.conference / "info.json"
    if not info_path.exists():
        raise SystemExit(f"Cache file not found: {info_path}")

    build_cache = load_build_cache_module()
    repository = build_cache.REPOSITORY
    repository.configure_debug(args.debug, [args.conference, str(args.year)] if args.debug else [])

    payload = load_payload(info_path)
    items = payload.get("items", [])
    targets = [item for item in items if not item.get("abstract")]
    if args.limit > 0:
        targets = targets[:args.limit]

    if not targets:
        print(f"No missing abstracts found in {info_path}")
        return

    print(f"Processing {len(targets)} missing abstract(s) in {info_path}")

    updated = 0
    failed = 0
    translated = 0
    started_at = time.perf_counter()

    for index, paper in enumerate(targets, start=1):
        title = paper.get("title", "<untitled>")
        print_status("fetching", index, len(targets), title)
        abstract_info = repository._find_best_abstract(paper)

        if not abstract_info or not abstract_info.get("abstract"):
            failed += 1
            print(f"  -> failed: no abstract found")
            continue

        paper.update(abstract_info)
        updated += 1
        abstract_chars = len(paper.get("abstract", ""))
        source = paper.get("abstract_source", "")
        print(f"  -> abstract ok: source={source or '<unknown>'}, chars={abstract_chars}")

        print_status("translating", index, len(targets), title)
        abstract_zh = repository._translate_paper_abstract(paper)
        if abstract_zh:
            paper["abstract_zh"] = abstract_zh
            translated += 1
            print(f"  -> translation ok: chars={len(abstract_zh)}")
        else:
            paper["abstract_zh"] = ""
            print("  -> translation skipped/failed")

        save_payload(info_path, payload)
        print("  -> saved")

    elapsed = time.perf_counter() - started_at
    print("")
    print("Done.")
    print(f"Updated abstracts: {updated}")
    print(f"Updated translations: {translated}")
    print(f"Failed abstracts: {failed}")
    print(f"Elapsed: {elapsed:.2f}s")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
