#!/usr/bin/env python3
"""
Download ICD-10 and ICD-11 CodeSystem resources from the Vulcano IHCE IG
and transform them into compact {code: display} lookup files.

Usage:
    python scripts/sync_terminology.py

    # Or from local files if you already downloaded the JSONs:
    python scripts/sync_terminology.py --local path/to/CodeSystem-ICD10CO.json path/to/CodeSystem-ICD11CO.json

Output:
    app/data/icd10_codes.json  — {"A000": "Colera debido a Vibrio cholerae 01...", ...}
    app/data/icd11_codes.json  — {"1A00": "Cholera", ...}

These files are loaded at startup by app/services/terminology.py for O(1) code validation.
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import httpx
except ImportError:
    httpx = None  # Will fail gracefully if --local is used


VULCANO_BASE = "https://vulcano.ihcecol.gov.co"
ICD10_URL = f"{VULCANO_BASE}/CodeSystem-ICD10CO.json"
ICD11_URL = f"{VULCANO_BASE}/CodeSystem-ICD11CO.json"

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "app" / "data"


def fetch_codesystem(url: str) -> dict:
    """Download a FHIR CodeSystem JSON from Vulcano."""
    if httpx is None:
        print("ERROR: httpx is required for remote download. Install with: pip install httpx")
        print("       Or use --local with pre-downloaded files.")
        sys.exit(1)

    print(f"  Downloading {url} ...")
    resp = httpx.get(url, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def load_local(path: str) -> dict:
    """Load a FHIR CodeSystem JSON from a local file."""
    print(f"  Loading {path} ...")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_codes(codesystem: dict) -> dict[str, str]:
    """
    Extract {code: display} from a FHIR CodeSystem resource.

    FHIR CodeSystem structure:
    {
        "resourceType": "CodeSystem",
        "concept": [
            {"code": "A000", "display": "Colera debido a Vibrio cholerae 01..."},
            ...
        ]
    }
    """
    concepts = codesystem.get("concept", [])
    codes = {}
    for concept in concepts:
        code = concept.get("code", "").strip()
        display = concept.get("display", "").strip()
        if code:
            codes[code] = display
    return codes


def write_lookup(codes: dict[str, str], output_path: Path, label: str) -> None:
    """Write compact JSON lookup file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(codes, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  ✓ {label}: {len(codes)} codes → {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Sync ICD-10/11 terminology from Vulcano IHCE")
    parser.add_argument(
        "--local", nargs=2, metavar=("ICD10_FILE", "ICD11_FILE"),
        help="Use local CodeSystem JSON files instead of downloading from Vulcano",
    )
    args = parser.parse_args()

    print("=== HWB Terminology Sync ===\n")

    # --- ICD-10 ---
    print("[1/2] ICD-10 (CodeSystem-ICD10CO)")
    if args.local:
        cs10 = load_local(args.local[0])
    else:
        cs10 = fetch_codesystem(ICD10_URL)

    icd10_codes = extract_codes(cs10)
    if not icd10_codes:
        print("  WARNING: No codes extracted from ICD-10 CodeSystem!")
        print("  Check that the JSON has a 'concept' array.")
    write_lookup(icd10_codes, OUTPUT_DIR / "icd10_codes.json", "ICD-10")

    # --- ICD-11 ---
    print("\n[2/2] ICD-11 (CodeSystem-ICD11CO)")
    if args.local:
        cs11 = load_local(args.local[1])
    else:
        cs11 = fetch_codesystem(ICD11_URL)

    icd11_codes = extract_codes(cs11)
    if not icd11_codes:
        print("  WARNING: No codes extracted from ICD-11 CodeSystem!")
        print("  Check that the JSON has a 'concept' array.")
    write_lookup(icd11_codes, OUTPUT_DIR / "icd11_codes.json", "ICD-11")

    print(f"\n✓ Done. Files written to {OUTPUT_DIR}/")
    print("  These files are loaded at startup by app/services/terminology.py")


if __name__ == "__main__":
    main()
