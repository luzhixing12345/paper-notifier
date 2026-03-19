import argparse
import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "paper_cache"


def parse_csv_values(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_years(value: str) -> list[int]:
    years: list[int] = []
    for part in parse_csv_values(value):
        try:
            year = int(part)
        except ValueError as exc:
            raise SystemExit(f"Invalid year: {part}") from exc
        if year not in years:
            years.append(year)
    return years


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clear abstract and abstract_zh fields in paper_cache/<year>/<conference>/info.json"
    )
    parser.add_argument("conference", help="Conference key(s), e.g. eurosys or osdi,nsdi")
    parser.add_argument("year", help="Year(s), e.g. 2025 or 2025,2024")
    args = parser.parse_args()

    conferences = parse_csv_values(args.conference)
    years = parse_years(args.year)

    total_files = 0
    total_items = 0
    total_cleared_abstracts = 0
    total_cleared_translations = 0

    for conference in conferences:
        for year in years:
            info_path = CACHE_DIR / str(year) / conference / "info.json"
            if not info_path.exists():
                print(f"Skip missing file: {info_path}")
                continue

            payload = json.loads(info_path.read_text(encoding="utf-8"))
            items = payload.get("items", [])

            cleared_abstracts = 0
            cleared_translations = 0
            for item in items:
                if item.get("abstract"):
                    cleared_abstracts += 1
                if item.get("abstract_zh"):
                    cleared_translations += 1
                item["abstract"] = ""
                item["abstract_zh"] = ""

            info_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            total_files += 1
            total_items += len(items)
            total_cleared_abstracts += cleared_abstracts
            total_cleared_translations += cleared_translations

            print(f"Cleared file: {info_path}")
            print(f"Items: {len(items)}")
            print(f"Cleared abstracts: {cleared_abstracts}")
            print(f"Cleared abstract_zh: {cleared_translations}")
            print("")

    print("Done.")
    print(f"Processed files: {total_files}")
    print(f"Items: {total_items}")
    print(f"Cleared abstracts: {total_cleared_abstracts}")
    print(f"Cleared abstract_zh: {total_cleared_translations}")


if __name__ == "__main__":
    main()
