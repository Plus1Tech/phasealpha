# Microsoft Foundry Agent Reference Card: QTO Engineering Schedule Table Extraction

Use this reference document as a retrieval/source document for Microsoft Foundry agents that extract quantity takeoff (QTO), engineering schedule, and construction drawing table data from PDF drawing sheets. The goal is **faithful table extraction**, not summarization.

## 1. Primary objective

Extract every visible schedule/table from the drawing page into structured records while preserving the drawing's original meaning.

A successful extraction:

1. Identifies each distinct schedule/table by title.
2. Keeps table-level notes, remarks, and sheet metadata separate from row data.
3. Preserves multi-row headers, grouped headers, units, symbols, abbreviations, and design-basis text.
4. Outputs one record per physical data row unless a table explicitly uses continuation rows.
5. Flags uncertain, blank, illegible, merged, or inferred values instead of silently guessing.

## 2. Page type and sample context

The sample page is a mechanical schedules sheet with many compact engineering tables. It appears to be sheet **M600, Mechanical Schedules**, for a school storm shelter addition/renovation project. The visible page contains a right-side title block and a main schedule area with multiple rectangular schedule boxes.

Expected schedule/table titles on this type of page include:

- Air Handling Unit Schedule
- Air Handling Unit with Energy Recovery Wheel Schedule
- Fin Tube Radiation Schedule - Hydronic
- ICC-500 Roof Housing Schedule Schedule
- VAV Box Schedule
- Fan Schedule
- Louver Penthouse Schedule
- Mechanical Pump Schedule
- Diffusers Registers and Grilles Schedule
- Electric Unit Heater Schedule
- Air Cooled Chiller Schedule
- Louver Schedule
- Air Separator Schedule
- Mechanical Piping Expansion Tank Schedule
- Fan Coil Unit Schedule

The agent should treat this list as a **sample inventory**, not a hardcoded exhaustive list. Always extract any additional visible table/schedule title present on the input page.

## 3. Non-negotiable extraction rules

### 3.1 Do not summarize table data

- Do not combine multiple rows into a narrative answer.
- Do not omit low-confidence rows.
- Do not simplify engineering abbreviations.
- Do not replace original design-basis/manufacturer text with generic equipment types.
- Do not infer missing numbers from nearby rows unless the output explicitly marks the value as inferred.

### 3.2 Preserve original text where it matters

Preserve these exactly when visible:

- Equipment marks, such as `AHU-9`, `VAV3-109`, `CHLR-1`, `EUH-1`, `FCU-1`.
- Schedule titles.
- Header labels and sublabels.
- Units, including `CFM`, `GPM`, `MBH`, `HP`, `RPM`, `MCA`, `V/PH`, `IN WC`, `FT`, `°F`, `LBS`, and `WATTS`.
- Dimensions, including inch marks, feet marks, fractions, and `x`/`X` separators.
- Hyphenated values and ranges.
- Abbreviations such as `N/A`, `TYP`, `ESP`, `EAT`, `LAT`, `EWT`, `LWT`, `APD`, `WPD`, `MERV`, `BHP`, `MCA`, `MOCP`, and `VFD`.

### 3.3 Never let the title block become a schedule row

The right-side title block, drawing border, grid letters/numbers, sheet number, revision block, seal/signature area, and project address are page metadata. Do not include them as rows in any schedule table.

Capture them only in a separate `sheet_metadata` object when requested.

### 3.4 Notes and remarks are table metadata

Notes and remarks above or inside a schedule box are not ordinary data rows. Capture them under the schedule's `notes` or `remarks` arrays.

If a table also has a final column named `REMARKS`, keep row-specific remarks in that row's `remarks` field or column value. Do not merge row remarks with table-level notes.

### 3.5 Blank cells must remain blank

Use `null` or an empty string according to the target schema. Do not fill blank cells with the value from the row above unless the table uses a visible ditto mark or the workflow explicitly requires fill-down. If fill-down is used, add `value_source: "fill_down"` and keep the original cell text separately.

## 4. Recommended output contract

Use this JSON shape when possible:

```json
{
  "document_type": "engineering_schedule_sheet",
  "sheet_metadata": {
    "sheet_number": "M600",
    "sheet_title": "Mechanical Schedules",
    "project_name": null,
    "project_number": null,
    "page_number": null,
    "source_file": null
  },
  "tables": [
    {
      "table_id": "T001",
      "title": "Air Handling Unit Schedule",
      "discipline": "mechanical",
      "bbox": { "page": 6, "x1": null, "y1": null, "x2": null, "y2": null },
      "notes": [],
      "headers": [
        {
          "raw": "SUPPLY FAN DATA PER FAN, 3 FANS PER ARRAY",
          "normalized": "supply_fan_data_per_fan_3_fans_per_array",
          "children": ["static_pressure", "electrical_data_total"]
        }
      ],
      "columns": [
        {
          "key": "mark",
          "raw_header": "MARK",
          "group_path": [],
          "unit": null,
          "data_type": "string"
        }
      ],
      "rows": [
        {
          "row_id": "T001-R001",
          "mark": "AHU-9",
          "values": {},
          "remarks": null,
          "confidence": 0.0,
          "warnings": []
        }
      ],
      "quality": {
        "row_count": 1,
        "column_count": null,
        "needs_human_review": false,
        "warnings": []
      }
    }
  ]
}
```

If the downstream system requires CSV, export one CSV per table and include sidecar metadata JSON for table notes, remarks, units, and header groups.

## 5. Step-by-step extraction workflow for Foundry agents

### Step 1: Preflight and page orientation

1. Confirm the page is upright.
2. If the title block text is vertical on the right, keep the page as-is for schedule extraction unless OCR quality improves with local table crops.
3. Ignore border coordinates, grid letters, and title block when detecting tables.
4. Use high-resolution rendering/cropping for dense schedules. Prefer 300 DPI or greater for OCR.

### Step 2: Detect table regions

Detect schedule boxes by looking for:

- A centered all-caps title in a bordered rectangle.
- Horizontal and vertical grid lines below a title.
- A notes/remarks band above the grid.
- Repeating equipment marks in the first data column.

Each bordered schedule box should become one `tables[]` entry. Do not combine adjacent schedule boxes just because they are on the same horizontal band.

### Step 3: Extract title and notes first

For each detected schedule region:

1. Extract the schedule title exactly.
2. Extract `NOTES:` and `REMARKS:` blocks in reading order.
3. Keep note numbering and punctuation where visible.
4. Do not treat note lines as headers or rows.

### Step 4: Parse header bands

Engineering schedules often use multi-row headers. Parse them as a hierarchy:

- Top row: major groups, such as `COOLING COIL`, `HEATING COIL`, `ELECTRICAL DATA`, `PRE-FILTER`, `FINAL FILTER`.
- Middle rows: subgroups, such as `STATIC PRESSURE`, `MOTOR DATA`, `ENTHALPY WHEEL`.
- Bottom row: leaf columns, such as `CFM`, `RPM`, `BHP`, `VOLTS`, `PHASE`, `MCA`.
- Units may be embedded in leaf headers, for example `EAT DB (°F)` or `APD (IN WC)`.

For every extracted cell, store:

- `raw_header`: the visible leaf header.
- `group_path`: all parent header labels from top to bottom.
- `unit`: the visible unit, if present.
- `normalized_key`: a stable snake_case key for downstream processing.

Example:

```json
{
  "raw_header": "EAT DB (°F)",
  "group_path": ["COOLING COIL"],
  "normalized_key": "cooling_coil_eat_db_f",
  "unit": "°F"
}
```

### Step 5: Parse data rows

1. Use the first visible identifier column as the row anchor. Common anchors are `MARK`, `TYPE MARK`, `UNIT`, or `NO.`.
2. Keep row order exactly as it appears top-to-bottom within the table.
3. If a row wraps onto multiple physical lines, join continuation text only when there is no new row anchor.
4. If two rows share a repeated mark, keep both rows and add sequence suffixes only to internal `row_id`, not to the visible mark.
5. Do not drop rows with mostly blank cells.

### Step 6: Normalize without destroying source text

For each value, keep both raw and normalized forms when possible:

```json
{
  "raw": "0.50",
  "normalized": 0.5,
  "unit": "IN WC",
  "confidence": 0.98
}
```

Recommended normalization:

- Numeric strings: parse as numbers only when unambiguous.
- `208`, `120`, `3`, `1`: keep as strings if the column is voltage/phase context and downstream consumers need exact electrical notation.
- Dimensions like `48x48x60`: keep as a string and optionally parse into component dimensions in a separate object.
- Values like `65°F` or `0.50 IN WC`: split into value and unit only if the unit is visibly associated with that cell or header.
- Manufacturer/model/design basis values: keep as strings.

### Step 7: Validate and flag

After extraction, run these checks:

- Every table has a non-empty title.
- Every data row has a row anchor/mark unless the original table visibly lacks one.
- All row objects for a table use the same column schema.
- Header groups are not lost when flattened.
- Notes are not mixed into rows.
- The right title block did not become a table.
- Low-confidence OCR characters are flagged, especially `0/O`, `1/I`, `5/S`, decimal points, fractions, and inch marks.

## 6. Table-specific guidance for the sample page

### 6.1 Air Handling Unit Schedule

Common groups/columns:

- `MARK`
- `AREA SERVED`
- `SUPPLY CFM`
- `MINIMUM OA CFM`
- `SUPPLY FAN CFM`
- `SUPPLY FAN DATA PER FAN, 3 FANS PER ARRAY`
- `RELIEF FAN DATA PER FAN, 3 FANS PER ARRAY`
- `COOLING COIL`
- `HEATING COIL`
- `PRE-FILTER`
- `FINAL FILTER`
- `MAX OPERATING WEIGHT`
- `DESIGN BASIS`

Important rules:

- The note block is part of the table metadata.
- Preserve `AHU-9` and equipment model/design-basis text exactly.
- Do not confuse `APD (IN WC)` with `WPD (FT HEAD)`.

### 6.2 Air Handling Unit with Energy Recovery Wheel Schedule

Common groups/columns:

- `MARK`
- `SUPPLY CFM`
- `MINIMUM OA CFM`
- `SUPPLY FAN CFM`
- `STATIC PRESSURE`
- `ELECTRICAL DATA`
- `RELIEF FAN CFM`
- `COOLING COIL`
- `HEATING COIL`
- `PRE-FILTER`
- `FINAL FILTER`
- `ENTHALPY WHEEL`
- `TOTAL UNIT OPERATING WEIGHT`
- `DESIGN BASIS`
- `REMARKS`

Important rules:

- Multiple fan sections may repeat similar leaf headers. Use the parent group to avoid key collisions.
- Preserve seasonal enthalpy wheel columns such as summer/winter EAT/LAT values under the `ENTHALPY WHEEL` group.

### 6.3 VAV Box Schedule

Common columns/groups:

- `MARK`
- `UNIT SIZE`
- `COOLING CFM` with `MAX`, `MIN`, or similar subcolumns.
- `MAX NC`
- `HEATING COIL DATA`
- `DESIGN BASIS (TITUS)`

Important rules:

- Extract every VAV row separately; do not aggregate by room or prefix.
- Marks such as `VAV3-109` and `VAV B-10` must retain spaces/hyphens exactly as visible.

### 6.4 Fan Schedule

Common columns:

- `PLAN MARK`
- `TYPE`
- `CFM`
- `ESP IN. WC`
- `FLOW BHP`
- `HP`
- `MOTOR DATA`
- `MAX RPM`
- `DRIVE`
- `DAMPER TYPE`
- `MAX SONES`
- `DESIGN BASIS`
- `REMARKS`

Important rules:

- Do not convert fan types such as `INLINE`, `LOUVERED PENTHOUSE`, or `CENTRIFUGAL` into generic categories unless adding a separate normalized field.
- Keep motor data grouped.

### 6.5 Mechanical Pump Schedule

Common columns:

- `MARK`
- `SYSTEM SERVED`
- `TYPE`
- `GPM`
- `HEAD (FT)`
- `SHUTOFF HEAD (FT)`
- `BHP`
- `HP`
- `VOLTS`
- `PHASE`
- `RPM`
- `DESIGN BASIS`
- `REMARKS`

Important rules:

- Marks like `CHWP-1` through `CHWP-4` are distinct rows.
- `HEAD (FT)` and `SHUTOFF HEAD (FT)` are different columns.

### 6.6 Diffusers, Registers, and Grilles Schedule

Common columns:

- `MARK`
- `MATERIAL`
- `DESCRIPTION`
- `BLOW PATTERN`
- `FACTORY FINISH`
- `DESIGN BASIS (TITUS)`
- `REMARKS`

Important rules:

- Description cells often contain sizes and neck dimensions. Keep the full raw description.
- Remarks may identify supply, return, exhaust, or transfer use; keep them row-specific.

### 6.7 Chiller, heater, louver, separator, expansion tank, and fan coil schedules

These smaller schedules may have only one or a few rows. Extract them even if they appear less prominent than larger tables.

Important rules:

- Do not skip single-row schedules.
- Preserve manufacturer/model/design-basis text.
- Preserve refrigerant, electrical, capacity, sound, and flow units.
- Keep hydronic and HVAC coil data grouped by parent header.

## 7. Header normalization rules

Use stable snake_case keys for downstream processing, but keep raw headers in metadata.

Recommended normalization:

| Raw text pattern | Normalized token |
| --- | --- |
| `MARK`, `PLAN MARK`, `TYPE MARK` | `mark`, `plan_mark`, `type_mark` |
| `CFM` | `cfm` |
| `GPM` | `gpm` |
| `MBH` | `mbh` |
| `EAT DB (°F)` | `eat_db_f` |
| `LAT DB (°F)` | `lat_db_f` |
| `APD (IN WC)` | `apd_in_wc` |
| `WPD (FT HEAD)` | `wpd_ft_head` |
| `VOLTS` | `volts` |
| `PHASE` | `phase` |
| `MCA` | `mca` |
| `RPM` | `rpm` |
| `DESIGN BASIS` | `design_basis` |
| `REMARKS` | `remarks` |

When the same leaf header appears under multiple groups, prefix with the group path:

- `supply_fan_electrical_data_volts`
- `relief_fan_electrical_data_volts`
- `cooling_coil_eat_db_f`
- `heating_coil_eat_db_f`

## 8. OCR risk rules

Dense engineering schedules create predictable OCR errors. Apply these safeguards:

- Treat `O` vs `0` carefully in model numbers and equipment marks.
- Treat `I`, `l`, and `1` carefully in units and marks.
- Verify decimal points in values like `0.50`, `0.06`, and `1.25`.
- Verify minus signs and hyphens in equipment marks.
- Verify inch marks and feet marks in dimensions.
- Do not auto-correct manufacturer names unless a controlled vocabulary is provided.
- If a value is unreadable, set the raw value to the best visible text and add a warning such as `"low_confidence_ocr"`.

## 9. Confidence and review rules

Set `needs_human_review: true` when any of these occur:

- A table title cannot be read confidently.
- A row anchor/mark is missing or unreadable.
- Header and data cell alignment is ambiguous.
- OCR confidence is low for quantities, capacities, voltages, dimensions, or model numbers.
- The number of extracted columns changes from row to row.
- A note or remark may have been mixed into a row.
- A table is partially cut off by the crop.

Use warning codes that downstream workflows can filter:

- `low_confidence_ocr`
- `ambiguous_header_group`
- `possible_wrapped_row`
- `blank_cell_preserved`
- `inferred_value`
- `table_crop_cutoff`
- `title_block_excluded`
- `single_row_schedule`

## 10. Foundry system prompt template

Use or adapt this prompt as the Foundry agent's system/developer instruction:

```text
You are a construction drawing schedule extraction agent. Extract tables exactly as shown. Do not summarize, infer, or omit data. Treat notes and remarks as metadata unless they are row-specific values in a REMARKS column. Preserve equipment marks, units, abbreviations, punctuation, hyphens, dimensions, and manufacturer/model text exactly. Parse multi-row headers into group_path + raw_header + unit metadata. Output one structured table object per visible schedule box. Exclude drawing borders, grid labels, title blocks, seals, revision blocks, and sheet metadata from schedule rows. If any cell is uncertain, keep the best raw text, add a warning, and set needs_human_review when appropriate.
```

## 11. Foundry user prompt template

Use this for each page or cropped schedule image:

```text
Extract every schedule/table from this construction drawing page using the QTO Engineering Schedule Table Extraction reference card.

Required output:
1. JSON only.
2. Include sheet_metadata if visible.
3. Include one tables[] item per distinct schedule box.
4. For each table include title, notes, remarks, columns with raw_header/group_path/unit, rows, row_count, column_count, confidence, and warnings.
5. Preserve original visible text in raw values.
6. Do not include title block, drawing border, or grid labels as rows.
7. Mark uncertain cells with warnings instead of guessing.
```

## 12. Optional deterministic post-processing pseudocode

```python
def post_process_schedule_extraction(result):
    for table in result["tables"]:
        assert table["title"], "Every table must have a title"

        # Keep notes separate from data rows.
        table["notes"] = table.get("notes", [])
        table["remarks"] = table.get("remarks", [])

        # Build unique column keys from group path + raw header.
        seen = set()
        for col in table["columns"]:
            key_parts = [normalize(part) for part in col.get("group_path", [])]
            key_parts.append(normalize(col["raw_header"]))
            col["key"] = dedupe("_".join(filter(None, key_parts)), seen)
            seen.add(col["key"])

        # Validate rows against columns.
        expected = {col["key"] for col in table["columns"]}
        for row in table["rows"]:
            row_values = set(row.get("values", {}).keys())
            missing = expected - row_values
            extra = row_values - expected
            if missing or extra:
                table.setdefault("quality", {}).setdefault("warnings", []).append(
                    {"code": "row_column_mismatch", "row_id": row.get("row_id")}
                )
                table.setdefault("quality", {})["needs_human_review"] = True

    return result
```

## 13. Acceptance checklist

Before considering extraction complete, verify:

- [ ] All visible schedule titles are listed.
- [ ] Every schedule has its own table object.
- [ ] Single-row schedules are included.
- [ ] Table-level notes/remarks are separated from rows.
- [ ] Row-level remarks remain in the row.
- [ ] Parent header groups are retained.
- [ ] Units are retained.
- [ ] Equipment marks are exact.
- [ ] Blank cells are not guessed.
- [ ] Title block data is not mixed with schedule rows.
- [ ] Low-confidence cells are flagged.
- [ ] Output is valid JSON when JSON is requested.
