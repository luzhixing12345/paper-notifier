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
    print(f"[{index}/{total}] {prefix} [{short_title}]")


def parse_conference_filters(module, value: str) -> list[str]:
    return module.parse_conference_filters([value])


def parse_year_filters(value: str) -> list[int]:
    years: list[int] = []
    for part in value.split(","):
        text = part.strip()
        if not text:
            continue
        try:
            year = int(text)
        except ValueError as exc:
            raise SystemExit(f"Invalid year: {text}") from exc
        if year not in years:
            years.append(year)
    return years


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fill missing abstracts and Chinese translations in paper_cache/<year>/<conference>/info.json"
    )
    parser.add_argument("conference", help="Conference key(s), e.g. asplos or dac,isca")
    parser.add_argument("year", help="Year(s), e.g. 2026 or 2025,2024")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N missing abstracts")
    parser.add_argument("--debug", action="store_true", help="Enable build-cache debug logs during fetch")
    args = parser.parse_args()

    build_cache = load_build_cache_module()
    repository = build_cache.REPOSITORY
    conferences = parse_conference_filters(build_cache, args.conference)
    years = parse_year_filters(args.year)
    repository.configure_debug(args.debug, [*conferences, *(str(year) for year in years)] if args.debug else [])

    total_updated_abstracts = 0
    total_failed_abstracts = 0
    total_updated_translations = 0
    started_at = time.perf_counter()

    for conference in conferences:
        for year in years:
            info_path = CACHE_DIR / str(year) / conference / "info.json"
            if not info_path.exists():
                print(f"Skip missing file: {info_path}")
                continue

            payload = load_payload(info_path)
            items = payload.get("items", [])
            targets = [
                item
                for item in items
                if not build_cache.is_excluded_paper_type(item.get("type", ""))
                if (not item.get("abstract")) or (item.get("abstract") and not item.get("abstract_zh"))
            ]
            if args.limit > 0:
                targets = targets[:args.limit]

            if not targets:
                print(f"No missing abstracts or translations found in {info_path}")
                continue

            print(f"Processing {len(targets)} missing abstract/translation item(s) in {info_path}")

            updated_abstracts = 0
            failed_abstracts = 0
            updated_translations = 0

            for index, paper in enumerate(targets, start=1):
                title = paper.get("title", "<untitled>")
                if not paper.get("abstract"):
                    print_status("fetching", index, len(targets), title)
                    abstract_info = repository._find_best_abstract(paper)
                    if not abstract_info or not abstract_info.get("abstract"):
                        failed_abstracts += 1
                        print("  -> \033[91mfailed\033[0m: no abstract found")
                        continue
                    paper.update(abstract_info)
                    updated_abstracts += 1
                    abstract_chars = len(paper.get("abstract", ""))
                    source = paper.get("abstract_source", "")
                    print(f"  -> \033[92mabstract ok\033[0m: source={source or '<unknown>'}, chars={abstract_chars}")
                else:
                    print_status("translating", index, len(targets), title)
                    print(f"  -> \033[93mreuse abstract\033[0m: chars={len(paper.get('abstract', ''))}")

                abstract_zh = repository._translate_paper_abstract(paper)
                if abstract_zh:
                    paper["abstract_zh"] = abstract_zh
                    updated_translations += 1
                    print(f"  -> \033[92mtranslation ok\033[0m: chars={len(abstract_zh)}")
                else:
                    paper["abstract_zh"] = ""
                    print("  -> \033[91mtranslation skipped/failed\033[0m")

                save_payload(info_path, payload)
                print("  -> saved")

            print("")
            print("Done.")
            print(f"Updated abstracts: {updated_abstracts}")
            print(f"Updated translations: {updated_translations}")
            print(f"Failed abstracts: {failed_abstracts}")

            total_updated_abstracts += updated_abstracts
            total_updated_translations += updated_translations
            total_failed_abstracts += failed_abstracts

    elapsed = time.perf_counter() - started_at
    print("")
    print("All done.")
    print(f"Updated abstracts: {total_updated_abstracts}")
    print(f"Updated translations: {total_updated_translations}")
    print(f"Failed abstracts: {total_failed_abstracts}")
    print(f"Elapsed: {elapsed:.2f}s")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
