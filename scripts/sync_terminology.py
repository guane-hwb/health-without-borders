#!/usr/bin/env python3
"""
Download complete ICD-10 and ICD-11 catalogs from the WHO ICD API.

ICD-10: downloaded in English (Spanish not available in WHO API),
        enriched with Spanish names from the Vulcano fragment where available.
ICD-11: downloaded in Spanish directly.

Usage:
    python scripts/sync_terminology.py \
        --client-id "YOUR_ID" --client-secret "YOUR_SECRET"

    # Enrich ICD-10 with Spanish names from Vulcano fragment:
    python scripts/sync_terminology.py \
        --client-id "..." --client-secret "..." \
        --vulcano-icd10 datalake/catalogs-icd/CodeSystem-ICD10CO.json

    # Environment variables also work:
    export WHO_CLIENT_ID="..."
    export WHO_CLIENT_SECRET="..."
    python scripts/sync_terminology.py
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    import httpx
except ImportError:
    httpx = None

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "app" / "data"

TOKEN_URL = "https://icdaccessmanagement.who.int/connect/token"
ICD10_ROOT = "https://id.who.int/icd/release/10/2019"
ICD11_ROOT = "https://id.who.int/icd/release/11/2024-01/mms"

MAX_WORKERS = 10
REQUEST_TIMEOUT = 30


def get_who_token(client_id: str, client_secret: str) -> str:
    print("  Authenticating with WHO ICD API...")
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "icdapi_access",
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    print("  ✓ Authenticated")
    return token


def normalize_url(url: str) -> str:
    """Force https — the WHO API returns http:// child links that redirect."""
    if url.startswith("http://"):
        return "https://" + url[7:]
    return url


def fetch_entity(url: str, headers: dict, client: httpx.Client) -> dict:
    url = normalize_url(url)
    resp = client.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_title(entity: dict) -> str:
    title = entity.get("title", {})
    if isinstance(title, dict):
        return title.get("@value", "")
    return str(title) if title else ""


def traverse_who(
    root_url: str, token: str, label: str,
    lang: str = "en", remove_dots: bool = False,
) -> dict[str, str]:
    """Recursively traverse a WHO ICD hierarchy and collect all codes."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Accept-Language": lang,
        "API-Version": "v2",
    }

    codes: dict[str, str] = {}
    visited: set[str] = set()
    queue: list[str] = [normalize_url(root_url)]
    total_fetched = 0
    errors = 0

    with httpx.Client(follow_redirects=True) as client:
        while queue:
            batch: list[str] = []
            while queue and len(batch) < MAX_WORKERS:
                url = normalize_url(queue.pop(0))
                if url not in visited:
                    visited.add(url)
                    batch.append(url)

            if not batch:
                continue

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {
                    executor.submit(fetch_entity, url, headers, client): url
                    for url in batch
                }
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        entity = future.result()
                    except Exception as e:
                        errors += 1
                        if errors <= 3:
                            print(f"    ⚠ Error: {url}\n      {e}")
                        elif errors == 4:
                            print("    ⚠ Suppressing further errors...")
                        continue

                    total_fetched += 1

                    code = entity.get("code", "")
                    if code:
                        clean = code.replace(".", "").strip() if remove_dots else code.strip()
                        display = get_title(entity)
                        if clean and display:
                            codes[clean] = display

                    for child_url in entity.get("child", []):
                        n = normalize_url(child_url)
                        if n not in visited:
                            queue.append(n)

                    if total_fetched % 200 == 0:
                        print(
                            f"    {total_fetched} fetched, {len(codes)} codes, "
                            f"{len(queue)} queued"
                        )

    print(f"    Done: {total_fetched} fetched, {len(codes)} codes, {errors} errors")
    return codes


def load_vulcano_fragment(path: str) -> dict[str, str]:
    """Load Spanish display names from a Vulcano CodeSystem fragment."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    codes: dict[str, str] = {}
    for concept in data.get("concept", []):
        code = concept.get("code", "").strip()
        display = concept.get("display", "").strip()
        if code and display:
            codes[code] = display
    return codes


def enrich_with_spanish(who_codes: dict[str, str], vulcano: dict[str, str]) -> dict[str, str]:
    """Replace English display names with Spanish from Vulcano where available."""
    enriched = {}
    spanish_count = 0
    for code, en_display in who_codes.items():
        es_display = vulcano.get(code)
        if es_display:
            enriched[code] = es_display
            spanish_count += 1
        else:
            enriched[code] = en_display
    print(f"    Enriched {spanish_count}/{len(who_codes)} codes with Spanish names from Vulcano")
    return enriched


def write_lookup(codes: dict[str, str], output_path: Path, label: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(codes, f, ensure_ascii=False, indent=None, separators=(",", ":"))
    size_kb = output_path.stat().st_size / 1024
    print(f"  ✓ {label}: {len(codes)} codes → {output_path.name} ({size_kb:.0f} KB)")


def main():
    parser = argparse.ArgumentParser(description="Download ICD catalogs from WHO API")
    parser.add_argument("--client-id", help="WHO ICD API Client ID")
    parser.add_argument("--client-secret", help="WHO ICD API Client Secret")
    parser.add_argument(
        "--vulcano-icd10", metavar="FILE",
        help="Vulcano CodeSystem-ICD10CO.json for Spanish name enrichment",
    )
    parser.add_argument("--icd10-only", action="store_true")
    parser.add_argument("--icd11-only", action="store_true")
    parser.add_argument(
        "--local", nargs=2, metavar=("ICD10", "ICD11"),
        help="Use local Vulcano fragments instead of WHO API (limited ~395 codes)",
    )
    args = parser.parse_args()

    if httpx is None and not args.local:
        print("ERROR: httpx required. Install: pip install httpx")
        sys.exit(1)

    print("=" * 60)
    print("  HWB Terminology Sync")
    print("=" * 60)

    if args.local:
        print("\n⚠ Local mode: Vulcano fragments only (~395 ICD-10 codes)")
        for i, (path, label) in enumerate([(args.local[0], "ICD-10"), (args.local[1], "ICD-11")]):
            print(f"\n[{i+1}/2] {label}")
            codes = load_vulcano_fragment(path) if Path(path).exists() else {}
            fname = "icd10_codes.json" if i == 0 else "icd11_codes.json"
            write_lookup(codes, OUTPUT_DIR / fname, label)
    else:
        client_id = args.client_id or os.environ.get("WHO_CLIENT_ID")
        client_secret = args.client_secret or os.environ.get("WHO_CLIENT_SECRET")
        if not client_id or not client_secret:
            print("\nERROR: Credentials required. Use --client-id/--client-secret or env vars.")
            print("  Register at: https://icd.who.int/icdapi")
            sys.exit(1)

        token = get_who_token(client_id, client_secret)

        # --- ICD-10 (English + Vulcano Spanish enrichment) ---
        if not args.icd11_only:
            print("\n[1/2] ICD-10 (WHO 2019 in English, ~12,000 codes)")
            print("  This may take 5-15 minutes...")
            t0 = time.time()
            icd10 = traverse_who(
                ICD10_ROOT, token, label="ICD-10",
                lang="en", remove_dots=True,
            )
            print(f"  Downloaded in {time.time() - t0:.0f}s")

            # Enrich with Spanish from Vulcano fragment
            vulcano_path = args.vulcano_icd10
            if not vulcano_path:
                # Try default location
                default = Path(__file__).resolve().parent.parent / "datalake" / "catalogs-icd" / "CodeSystem-ICD10CO.json"
                if default.exists():
                    vulcano_path = str(default)
            if vulcano_path and Path(vulcano_path).exists():
                print(f"  Enriching with Spanish names from {vulcano_path}...")
                vulcano = load_vulcano_fragment(vulcano_path)
                icd10 = enrich_with_spanish(icd10, vulcano)
            else:
                print("  ℹ No Vulcano fragment found — using English names.")
                print("    Pass --vulcano-icd10 <file> for Spanish enrichment.")

            write_lookup(icd10, OUTPUT_DIR / "icd10_codes.json", "ICD-10")
        else:
            print("\n[1/2] ICD-10 — skipped")

        # --- ICD-11 (Spanish directly) ---
        if not args.icd10_only:
            print("\n[2/2] ICD-11 (WHO 2024-01 MMS in Spanish)")
            print("  This may take 15-30 minutes...")
            t0 = time.time()
            icd11 = traverse_who(
                ICD11_ROOT, token, label="ICD-11",
                lang="es", remove_dots=False,
            )
            print(f"  Downloaded in {time.time() - t0:.0f}s")
            write_lookup(icd11, OUTPUT_DIR / "icd11_codes.json", "ICD-11")
        else:
            print("\n[2/2] ICD-11 — skipped")

    print(f"\n{'=' * 60}")
    print(f"  ✓ Done. Files in {OUTPUT_DIR}/")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()