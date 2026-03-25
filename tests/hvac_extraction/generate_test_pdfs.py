#!/usr/bin/env python3
"""Generate synthetic HVAC schedule PDFs for pipeline structural testing."""

import fitz  # PyMuPDF
import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "test_pdfs")


def create_vector_schedule_pdf(path: str):
    """Simulate Bluestone_Engineering style: vector PDF with schedule on page 9 (0-indexed 8)."""
    doc = fitz.open()
    # Pages 0-7: filler
    for i in range(8):
        page = doc.new_page(width=612, height=792)
        tw = fitz.TextWriter(page.rect)
        tw.append((72, 100), f"Page {i+1} - General Notes", fontsize=14)
        tw.write_text(page)

    # Page 8: HVAC equipment schedule
    page = doc.new_page(width=612, height=792)
    tw = fitz.TextWriter(page.rect)
    tw.append((72, 50), "HVAC EQUIPMENT SCHEDULE", fontsize=16)

    # Table 1: AHU Schedule (top half)
    y = 80
    tw.append((72, y), "AIR HANDLING UNIT SCHEDULE", fontsize=11)
    y += 20
    headers = ["TAG", "AREA SERVED", "CFM", "ESP", "MFR/MODEL", "HP", "VOLTS"]
    col_w = 75
    for j, h in enumerate(headers):
        tw.append((72 + j * col_w, y), h, fontsize=8)
    rows = [
        ["AHU-1", "OFFICE", "12000", "2.5", "TRANE/?"  , "15", "460/3"],
        ["AHU-2", "LOBBY",  "8000",  "2.0", "TRANE/M2" , "10", "460/3"],
        ["AHU-3", "CONF",   "5000",  "1.5", "CARRIER/?" , "7.5", "460/3"],
    ]
    for row in rows:
        y += 15
        for j, val in enumerate(row):
            tw.append((72 + j * col_w, y), val, fontsize=7)

    # Table 2: Exhaust Fan Schedule (bottom half)
    y += 40
    tw.append((72, y), "EXHAUST FAN SCHEDULE", fontsize=11)
    y += 20
    headers2 = ["TAG", "AREA SERVED", "CFM", "ESP", "HP", "VOLTS"]
    for j, h in enumerate(headers2):
        tw.append((72 + j * col_w, y), h, fontsize=8)
    rows2 = [
        ["EF-1", "RESTROOM", "800",  "0.5", "0.5", "120/1"],
        ["EF-2", "KITCHEN",  "2000", "1.0", "1.0", "120/1"],
    ]
    for row in rows2:
        y += 15
        for j, val in enumerate(row):
            tw.append((72 + j * col_w, y), val, fontsize=7)

    tw.append((72, 750), "Note: All units to include VFDs. ? = TBD by contractor.", fontsize=7)
    tw.write_text(page)
    doc.save(path)
    doc.close()


def create_image_schedule_pdf(path: str, variant: str = "MODUS"):
    """Simulate image-based PDF with schedule on page 6 (0-indexed 5)."""
    doc = fitz.open()
    # Pages 0-4: filler
    for i in range(5):
        page = doc.new_page(width=792, height=612)  # landscape
        tw = fitz.TextWriter(page.rect)
        tw.append((72, 100), f"Page {i+1} - {variant} Drawing Sheet", fontsize=14)
        tw.write_text(page)

    # Page 5: Equipment schedule - render as text then convert page to image
    page = doc.new_page(width=792, height=612)
    tw = fitz.TextWriter(page.rect)

    if variant == "MODUS":
        tw.append((72, 50), "MECHANICAL EQUIPMENT SCHEDULE", fontsize=14)
        y = 80
        tw.append((72, y), "ROOFTOP UNIT SCHEDULE", fontsize=10)
        y += 18
        headers = ["MARK", "LOCATION", "COOLING MBH", "HEATING MBH", "CFM", "MFR", "MODEL"]
        col_w = 95
        for j, h in enumerate(headers):
            tw.append((72 + j * col_w, y), h, fontsize=7)
        rows = [
            ["RTU-1", "ZONE 1", "180", "120", "6000", "LENNOX", "LGH060"],
            ["RTU-2", "ZONE 2", "240", "160", "8000", "LENNOX", "LGH080"],
            ["RTU-3", "ZONE 3", "120", "80",  "4000", "LENNOX", "?"],
        ]
        for row in rows:
            y += 14
            for j, val in enumerate(row):
                tw.append((72 + j * col_w, y), val, fontsize=7)
        tw.append((72, 560), "Notes: 1) All RTUs with economizer. 2) ? = selection pending.", fontsize=7)
    else:  # ShiveHattery
        tw.append((72, 50), "HVAC EQUIPMENT SCHEDULES", fontsize=14)
        y = 80
        tw.append((72, y), "SPLIT SYSTEM SCHEDULE", fontsize=10)
        y += 18
        headers = ["TAG", "SERVES", "TONS", "SEER", "HEATING", "VOLTS", "REMARKS"]
        col_w = 95
        for j, h in enumerate(headers):
            tw.append((72 + j * col_w, y), h, fontsize=7)
        rows = [
            ["SS-1", "OFFICE A", "3.0", "16", "GAS 60MBH", "208/1", ""],
            ["SS-2", "OFFICE B", "5.0", "16", "GAS 100MBH", "208/3", ""],
            ["SS-3", "STORAGE",  "2.0", "14", "ELEC ?",     "208/1", "VERIFY SIZE"],
        ]
        for row in rows:
            y += 14
            for j, val in enumerate(row):
                tw.append((72 + j * col_w, y), val, fontsize=7)

        y += 30
        tw.append((72, y), "VAV BOX SCHEDULE", fontsize=10)
        y += 18
        headers2 = ["TAG", "SERVES", "MAX CFM", "MIN CFM", "REHEAT", "VOLTS"]
        for j, h in enumerate(headers2):
            tw.append((72 + j * col_w, y), h, fontsize=7)
        rows2 = [
            ["VAV-1", "ROOM 101", "800",  "200", "HW 2-ROW", "24V"],
            ["VAV-2", "ROOM 102", "1200", "300", "HW 2-ROW", "24V"],
            ["VAV-3", "ROOM 103", "600",  "150", "ELEC ?",   "24V"],
        ]
        for row in rows2:
            y += 14
            for j, val in enumerate(row):
                tw.append((72 + j * col_w, y), val, fontsize=7)
        tw.append((72, 560), "General: All VAVs DDC controlled. ? = pending selection.", fontsize=7)

    tw.write_text(page)

    # Convert page to image and re-insert to simulate image-based PDF
    pix = page.get_pixmap(dpi=150)
    img_bytes = pix.tobytes("png")
    # Remove text, insert as image
    page.clean_contents()
    # Delete all text by redacting
    for block in page.get_text("dict")["blocks"]:
        if block["type"] == 0:  # text block
            page.add_redact_annot(fitz.Rect(block["bbox"]))
    page.apply_redactions()
    page.insert_image(page.rect, stream=img_bytes)

    doc.save(path)
    doc.close()


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    bluestone = os.path.join(OUTPUT_DIR, "Bluestone_Engineering_QTO_Example.pdf")
    modus = os.path.join(OUTPUT_DIR, "MODUS_engineering_QTO_Example.pdf")
    shive = os.path.join(OUTPUT_DIR, "ShiveHattery_QTO_Example.pdf")

    create_vector_schedule_pdf(bluestone)
    print(f"Created: {bluestone}")

    create_image_schedule_pdf(modus, variant="MODUS")
    print(f"Created: {modus}")

    create_image_schedule_pdf(shive, variant="ShiveHattery")
    print(f"Created: {shive}")

    print("\nAll test PDFs generated successfully.")


if __name__ == "__main__":
    main()
