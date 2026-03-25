#!/usr/bin/env python3
"""
Two-pass HVAC equipment schedule extraction pipeline.

Pass 1: Rasterize full page at 150 DPI -> detect table locations via Claude Vision.
Pass 2: Crop each detected table at 300 DPI -> extract structured data via Claude Vision.

Tracks: API calls, estimated tokens, wall-clock time per PDF.
"""

import argparse
import base64
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field

import fitz  # PyMuPDF
from PIL import Image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MODEL = "claude-opus-4-5-20250514"
PASS1_DPI = 150
PASS2_DPI = 300

PASS1_SYSTEM = (
    "You are an HVAC schedule detector. Identify every distinct equipment schedule "
    "table on this page. For each table return: table_name, equipment_type, and "
    "approximate bounding box as percentage of page (x0_pct, y0_pct, x1_pct, y1_pct). "
    "Return JSON only, no markdown."
)

PASS2_SYSTEM = (
    "You are an HVAC equipment schedule extractor. Extract ALL rows and columns from "
    "this table exactly as shown. Normalize multi-line column headers into a single "
    "string. Concatenate all footnotes into a single remarks field. Assign a confidence "
    'score 0.0-1.0. Return JSON only: {"table_name", "equipment_type", "confidence", '
    '"columns": [], "rows": [{"col": "val"}], "remarks"}'
)

# PDF manifest: (filename, 0-indexed page number)
PDF_MANIFEST = [
    ("Bluestone_Engineering_QTO_Example.pdf", 8),
    ("MODUS_engineering_QTO_Example.pdf", 5),
    ("ShiveHattery_QTO_Example.pdf", 5),
]


# ---------------------------------------------------------------------------
# Metrics tracking
# ---------------------------------------------------------------------------
@dataclass
class DocumentMetrics:
    filename: str
    api_calls: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    wall_clock_seconds: float = 0.0
    tables_found: int = 0


@dataclass
class RunMetrics:
    documents: list = field(default_factory=list)
    total_api_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def page_to_png_bytes(doc: fitz.Document, page_idx: int, dpi: int) -> bytes:
    """Rasterize a PDF page to PNG bytes."""
    page = doc[page_idx]
    pix = page.get_pixmap(dpi=dpi)
    return pix.tobytes("png")


def crop_region(png_bytes: bytes, x0_pct: float, y0_pct: float,
                x1_pct: float, y1_pct: float) -> bytes:
    """Crop a percentage-based bounding box from a PNG image."""
    img = Image.open(io.BytesIO(png_bytes))
    w, h = img.size
    box = (
        int(x0_pct / 100 * w),
        int(y0_pct / 100 * h),
        int(x1_pct / 100 * w),
        int(y1_pct / 100 * h),
    )
    cropped = img.crop(box)
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()


def send_image_to_claude(client, system: str,
                         png_bytes: bytes, user_prompt: str,
                         dry_run: bool = False) -> tuple:
    """Send an image to Claude and return (parsed_json, usage_dict).
    In dry_run mode, returns mock data to validate pipeline structure."""
    if dry_run:
        # Estimate tokens: ~1 token per 750 bytes of image + text overhead
        est_input = len(png_bytes) // 750 + 200
        usage = {"input_tokens": est_input, "output_tokens": 350}
        if "detector" in system.lower() or "detect" in system.lower():
            mock = [
                {"table_name": "MOCK EQUIPMENT SCHEDULE",
                 "equipment_type": "AHU",
                 "x0_pct": 5, "y0_pct": 8, "x1_pct": 95, "y1_pct": 50},
                {"table_name": "MOCK FAN SCHEDULE",
                 "equipment_type": "Exhaust Fan",
                 "x0_pct": 5, "y0_pct": 55, "x1_pct": 95, "y1_pct": 95},
            ]
        else:
            mock = {
                "table_name": "MOCK SCHEDULE",
                "equipment_type": "AHU",
                "confidence": 0.85,
                "columns": ["TAG", "AREA SERVED", "CFM", "HP", "VOLTS"],
                "rows": [
                    {"TAG": "AHU-1", "AREA SERVED": "OFFICE", "CFM": "12000",
                     "HP": "15", "VOLTS": "460/3"},
                    {"TAG": "AHU-2", "AREA SERVED": "LOBBY", "CFM": "8000",
                     "HP": "10", "VOLTS": "460/3"},
                    {"TAG": "AHU-3", "AREA SERVED": "CONF", "CFM": "?",
                     "HP": "7.5", "VOLTS": "460/3"},
                ],
                "remarks": "All units include VFDs. ? = TBD by contractor."
            }
        print(f"  [DRY-RUN] Returning mock data (est. {est_input} input tokens)")
        return mock, usage

    import anthropic as _anthropic  # noqa: deferred import
    b64 = base64.standard_b64encode(png_bytes).decode("utf-8")
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=system,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                },
                {"type": "text", "text": user_prompt},
            ],
        }],
    )

    raw_text = response.content[0].text
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }

    # Strip markdown code fences if present
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        print(f"  [WARN] Could not parse JSON response. Raw:\n{raw_text[:500]}")
        parsed = {"error": "JSON parse failed", "raw": raw_text[:1000]}

    return parsed, usage


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def process_pdf(client, pdf_path: str,
                page_idx: int, metrics: DocumentMetrics,
                dry_run: bool = False) -> list:
    """Run two-pass extraction on a single PDF page. Returns list of table results."""
    print(f"\n{'='*60}")
    print(f"Processing: {os.path.basename(pdf_path)} (page {page_idx})")
    print(f"{'='*60}")

    doc = fitz.open(pdf_path)
    if page_idx >= len(doc):
        print(f"  [ERROR] Page {page_idx} does not exist (doc has {len(doc)} pages)")
        doc.close()
        return []

    # --- Pass 1: Full-page table detection at 150 DPI ---
    print(f"\n  Pass 1: Rasterizing page at {PASS1_DPI} DPI...")
    full_page_png = page_to_png_bytes(doc, page_idx, PASS1_DPI)
    print(f"  Pass 1: Image size = {len(full_page_png):,} bytes")

    print(f"  Pass 1: Sending to {MODEL} for table detection...")
    tables_detected, usage1 = send_image_to_claude(
        client, PASS1_SYSTEM, full_page_png,
        "Detect all HVAC equipment schedule tables on this page. Return JSON array.",
        dry_run=dry_run,
    )
    metrics.api_calls += 1
    metrics.estimated_input_tokens += usage1["input_tokens"]
    metrics.estimated_output_tokens += usage1["output_tokens"]

    # Normalize to list
    if isinstance(tables_detected, dict):
        if "tables" in tables_detected:
            tables_detected = tables_detected["tables"]
        elif "error" not in tables_detected:
            tables_detected = [tables_detected]
        else:
            tables_detected = []

    metrics.tables_found = len(tables_detected)
    print(f"  Pass 1: Detected {len(tables_detected)} table(s)")
    for t in tables_detected:
        print(f"    - {t.get('table_name', 'unknown')}: "
              f"({t.get('x0_pct', '?')}, {t.get('y0_pct', '?')}) -> "
              f"({t.get('x1_pct', '?')}, {t.get('y1_pct', '?')})")

    # --- Pass 2: Crop and extract each table at 300 DPI ---
    print(f"\n  Pass 2: Rasterizing page at {PASS2_DPI} DPI for cropping...")
    hires_png = page_to_png_bytes(doc, page_idx, PASS2_DPI)
    print(f"  Pass 2: Hi-res image size = {len(hires_png):,} bytes")

    results = []
    for i, table_info in enumerate(tables_detected):
        x0 = table_info.get("x0_pct", 0)
        y0 = table_info.get("y0_pct", 0)
        x1 = table_info.get("x1_pct", 100)
        y1 = table_info.get("y1_pct", 100)

        print(f"\n  Pass 2 [{i+1}/{len(tables_detected)}]: Cropping "
              f"{table_info.get('table_name', 'table')} "
              f"({x0:.0f}%,{y0:.0f}%) -> ({x1:.0f}%,{y1:.0f}%)")

        cropped_png = crop_region(hires_png, x0, y0, x1, y1)
        print(f"  Pass 2: Cropped image = {len(cropped_png):,} bytes")

        print(f"  Pass 2: Sending cropped table to {MODEL} for data extraction...")
        extracted, usage2 = send_image_to_claude(
            client, PASS2_SYSTEM, cropped_png,
            "Extract all rows and columns from this HVAC equipment schedule table.",
            dry_run=dry_run,
        )
        metrics.api_calls += 1
        metrics.estimated_input_tokens += usage2["input_tokens"]
        metrics.estimated_output_tokens += usage2["output_tokens"]

        # Attach detection metadata
        extracted["_detection"] = {
            "bbox_pct": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
            "source_page": page_idx,
        }
        results.append(extracted)

        row_count = len(extracted.get("rows", []))
        col_count = len(extracted.get("columns", []))
        confidence = extracted.get("confidence", "N/A")
        print(f"  Pass 2: Extracted {row_count} rows, {col_count} columns, "
              f"confidence={confidence}")

    doc.close()
    return results


def print_summary(all_results: dict, run_metrics: RunMetrics):
    """Print human-readable summary."""
    print(f"\n{'='*70}")
    print("EXTRACTION SUMMARY")
    print(f"{'='*70}")

    for doc_name, tables in all_results.items():
        print(f"\n--- {doc_name} ---")
        if not tables:
            print("  No tables extracted.")
            continue

        for table in tables:
            name = table.get("table_name", "Unknown")
            rows = table.get("rows", [])
            cols = table.get("columns", [])
            conf = table.get("confidence", "N/A")
            remarks = table.get("remarks", "")

            print(f"  Table: {name}")
            print(f"    Equipment type: {table.get('equipment_type', 'N/A')}")
            print(f"    Rows: {len(rows)}, Columns: {len(cols)}, Confidence: {conf}")
            if remarks:
                print(f"    Remarks: {remarks[:100]}")

            # Flag cells containing "?"
            question_cells = []
            for ri, row in enumerate(rows):
                for col_name, val in row.items():
                    if isinstance(val, str) and "?" in val:
                        question_cells.append(f"Row {ri+1}/{col_name}: '{val}'")
            if question_cells:
                print(f"    ⚠ Cells with '?': {', '.join(question_cells)}")

    # Cost/performance metrics
    print(f"\n{'='*70}")
    print("COST & PERFORMANCE METRICS")
    print(f"{'='*70}")
    print(f"{'Document':<45} {'Calls':>5} {'In Tok':>8} {'Out Tok':>8} {'Time(s)':>8}")
    print("-" * 78)

    for dm in run_metrics.documents:
        print(f"{dm.filename:<45} {dm.api_calls:>5} "
              f"{dm.estimated_input_tokens:>8,} {dm.estimated_output_tokens:>8,} "
              f"{dm.wall_clock_seconds:>8.1f}")

    print("-" * 78)
    print(f"{'TOTAL':<45} {run_metrics.total_api_calls:>5} "
          f"{run_metrics.total_input_tokens:>8,} {run_metrics.total_output_tokens:>8,} "
          f"{sum(d.wall_clock_seconds for d in run_metrics.documents):>8.1f}")

    # Rough cost estimate (Opus pricing: $15/M input, $75/M output)
    est_input_cost = run_metrics.total_input_tokens / 1_000_000 * 15
    est_output_cost = run_metrics.total_output_tokens / 1_000_000 * 75
    print(f"\nEstimated API cost: ${est_input_cost + est_output_cost:.2f} "
          f"(input: ${est_input_cost:.2f}, output: ${est_output_cost:.2f})")


def main():
    parser = argparse.ArgumentParser(description="HVAC Equipment Schedule Extraction Test")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run pipeline with mock API responses (no API key needed)")
    parser.add_argument("--pdf-dir", default=None,
                        help="Directory containing the test PDFs (or set HVAC_PDF_DIR)")
    args = parser.parse_args()

    dry_run = args.dry_run

    # Resolve PDF directory
    pdf_dir = (args.pdf_dir
               or os.environ.get("HVAC_PDF_DIR")
               or os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_pdfs"))

    if not os.path.isdir(pdf_dir):
        print(f"ERROR: PDF directory not found: {pdf_dir}")
        print("Set HVAC_PDF_DIR environment variable or place PDFs in test_pdfs/")
        sys.exit(1)

    # Validate all PDFs exist
    missing = []
    for filename, _ in PDF_MANIFEST:
        path = os.path.join(pdf_dir, filename)
        if not os.path.isfile(path):
            missing.append(filename)

    if missing:
        print(f"ERROR: Missing PDF files in {pdf_dir}:")
        for m in missing:
            print(f"  - {m}")
        print("\nRun generate_test_pdfs.py first, or set HVAC_PDF_DIR to your PDF folder.")
        sys.exit(1)

    # Initialize Anthropic client (skip in dry-run mode)
    client = None
    if not dry_run:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
            print("Export your key: export ANTHROPIC_API_KEY='sk-ant-...'")
            print("Or use --dry-run to test pipeline structure without API calls.")
            sys.exit(1)
        client = anthropic.Anthropic(api_key=api_key)
    else:
        print("[DRY-RUN MODE] Using mock API responses.\n")

    run_metrics = RunMetrics()
    all_results = {}

    for filename, page_idx in PDF_MANIFEST:
        pdf_path = os.path.join(pdf_dir, filename)
        dm = DocumentMetrics(filename=filename)
        start = time.time()

        tables = process_pdf(client, pdf_path, page_idx, dm, dry_run=dry_run)

        dm.wall_clock_seconds = time.time() - start
        run_metrics.documents.append(dm)
        run_metrics.total_api_calls += dm.api_calls
        run_metrics.total_input_tokens += dm.estimated_input_tokens
        run_metrics.total_output_tokens += dm.estimated_output_tokens
        all_results[filename] = tables

    # Save full results
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "extraction_results.json")
    output = {
        "dry_run": dry_run,
        "results": all_results,
        "metrics": {
            "total_api_calls": run_metrics.total_api_calls,
            "total_input_tokens": run_metrics.total_input_tokens,
            "total_output_tokens": run_metrics.total_output_tokens,
            "per_document": [
                {
                    "filename": d.filename,
                    "api_calls": d.api_calls,
                    "input_tokens": d.estimated_input_tokens,
                    "output_tokens": d.estimated_output_tokens,
                    "wall_clock_seconds": round(d.wall_clock_seconds, 2),
                    "tables_found": d.tables_found,
                }
                for d in run_metrics.documents
            ],
        },
    }
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nFull results saved to: {output_path}")

    # Print summary
    print_summary(all_results, run_metrics)


if __name__ == "__main__":
    main()
