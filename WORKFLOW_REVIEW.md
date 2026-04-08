# PhaseAlpha v2.0 — Workflow Review & Corrections

## 1. CRITICAL BUGS

### C1. SECURITY: Hardcoded Anthropic API Key in Plaintext
**Node:** `Comparison Agent` (HTTP Request)  
**Severity:** CRITICAL — credential leak  
**Problem:** The node has the full Anthropic API key hardcoded in the `x-api-key` header:
```
REDACTED — rotate this key immediately
```
This key is exposed in every workflow export and version history. Additionally, the node references a `Serper` httpHeaderAuth credential (id: `L45zIzi5jL1hFDHZ`) which is a search API credential — completely wrong for this node.

**Fix applied:** Removed the hardcoded `x-api-key` header. Removed the `Serper` httpHeaderAuth credential. The node already has `authentication: "predefinedCredentialType"` with `nodeCredentialType: "anthropicApi"` pointing to credential `"PhaseAlpha Claude API"` (id: `je1vli3fFL5A38FY`), which automatically injects the API key header. **Rotate the exposed key immediately.**

---

### C2. Claude Direct Extract — PDF Binary Never Sent to Claude (THE CRITICAL FIX)
**Nodes:** `Extract from File`, `Prepare Claude Input`, `Claude Direct Extract` (AI Agent), `Anthropic Chat Model (Extract)`  
**Severity:** CRITICAL — Claude extracts from nothing, returns fabricated/empty schedules  

**Root cause analysis:**
The original approach used an AI Agent node with `passthroughBinaryImages: true`. This feature is designed for **images** (PNG, JPG, WebP) — it converts them to base64 image_url content blocks in the LangChain message. **It does not handle PDFs.** PDFs require the Anthropic-specific `document` content type, which the n8n LangChain abstraction does not support.

The data flow had additional problems:
- `Extract from File` used `binaryPropertyName: "file"` but the webhook binary key may be `"data"` (depends on the multipart form field name), causing a silent binary lookup failure
- `Prepare Claude Input` read binary from `$('Extract File Data')` by name, bypassing `Extract from File` entirely — meaning `Extract from File` was a no-op in the chain
- The AI Agent `text` parameter had a template syntax error: `{{ $json.chatInput }` (missing closing `}`)
- `Anthropic Chat Model (Extract)` had `maxTokensToSample: 16000` — half the required 32000

**Evaluation of options:**
| Approach | Works for PDF? | Reliability | Complexity |
|---|---|---|---|
| **HTTP Request to /v1/messages with base64 PDF document block** | YES | High — direct API, no abstraction layer | Low |
| AI Agent + passthroughBinaryImages | NO — images only | N/A | N/A |
| Basic LLM Chain + passthroughBinaryImages | NO — not supported | N/A | N/A |

**Fix applied — HTTP Request with base64 document content block (Option A):**

Removed 3 nodes: `Extract from File`, `Prepare Claude Input`, `Anthropic Chat Model (Extract)`

Replaced with 2 nodes:

**`Prepare Claude HTTP Request`** (Code node) — builds the Anthropic API request body:
```javascript
const fileData = $('Extract File Data').first().json;
const requestBody = {
  model: "claude-sonnet-4-6",
  max_tokens: 32000,
  system: "...", // full extraction system prompt
  messages: [{
    role: "user",
    content: [
      {
        type: "document",        // Anthropic document content type
        source: {
          type: "base64",
          media_type: fileData.mime_type,  // "application/pdf"
          data: fileData.base64_data       // from Extract File Data
        }
      },
      {
        type: "text",
        text: "Extract all mechanical equipment schedules..."
      }
    ]
  }]
};
```

**`Claude Direct Extract`** (HTTP Request node):
- POST `https://api.anthropic.com/v1/messages`
- Credential: `anthropicApi` → `PhaseAlpha Claude API` (auto-injects x-api-key)
- Headers: `anthropic-version: 2023-06-01`, `anthropic-beta: pdfs-2024-09-25`
- Body: `{{ JSON.stringify($json.request_body) }}`
- Timeout: 600000ms (10 minutes — large PDFs need time)

**Why this works:** `Extract File Data` already converts the webhook binary to base64 and stores it in `json.base64_data`. The new Code node reads this and constructs the exact Anthropic API request format with a `document` content block. The HTTP Request node sends it directly — no LangChain abstraction, no passthroughBinaryImages, no binary key mismatches.

**Response format:** The HTTP response is `{ content: [{ type: "text", text: "..." }], stop_reason: "end_turn"|"max_tokens", ... }`. The existing `Pre-Flight Check` code already handles this format via its `claudeRaw.content` path.

---

### C3. Submit Extract Job — PDF Binary Lost After Polling Loop
**Node:** `Submit Extract Job` (HTTP Request)  
**Severity:** CRITICAL — second LlamaParse upload silently fails  

**Problem:** The LlamaParse extract path requires uploading the PDF a second time:
```
Get Parse Markdown → Submit Extract Job (POST /upload with PDF)
```
But `Get Parse Markdown` is an HTTP Request node that fetches the markdown result from LlamaParse — it returns the API response, not the original PDF binary. The binary was lost much earlier in the chain:

```
Prepare LlamaParse Upload (has binary)
  → Submit Parse Job (HTTP — returns upload response, binary lost)
    → Wait Parse 2min (passes through)
      → Poll Counter Parse (Code — returns json only, NO binary)
        → Check Parse Status (HTTP — returns status response)
          → Parse Job Complete? (IF — no binary from upstream)
            → Get Parse Markdown (HTTP — fetches markdown, no PDF binary)
              → Submit Extract Job ← NO PDF BINARY AVAILABLE
```

The binary is definitively lost at `Poll Counter Parse` (Code node) which returns `[{ json: {...} }]` without a `binary` property.

**Fix applied:** Added `Restore Binary for Extract` Code node between `Get Parse Markdown` and `Submit Extract Job`:
```javascript
const parseMarkdownResult = $input.first().json;
const binary = $('Prepare LlamaParse Upload').first().binary || {};
return [{ json: parseMarkdownResult, binary: binary }];
```
This re-attaches the PDF binary from `Prepare LlamaParse Upload` (which still has it, accessible by node name) so `Submit Extract Job` can upload the file.

---

### C4. Template Syntax Error in Claude Direct Extract
**Node:** `Claude Direct Extract` (original AI Agent)  
**Severity:** CRITICAL — node fails or receives empty prompt  
**Problem:** Line 715: `"text": "{{ $json.chatInput }"` — missing closing `}`  
**Fix:** Resolved by replacing the entire node (see C2).

---

### C5. maxTokensToSample Too Low for Extraction
**Node:** `Anthropic Chat Model (Extract)`  
**Severity:** HIGH — output truncated on dense drawings  
**Problem:** Set to 16000 tokens. Dense mechanical schedule drawings with 20+ tables easily exceed this, causing `stop_reason: "max_tokens"` truncation.  
**Fix:** Set to 32000 in the new HTTP Request approach (see C2).

---

## 2. ARCHITECTURAL PROBLEMS

### A1. Orphaned Nodes — Comparison Agent (HTTP) and Prepare Comparison Input
**Nodes:** `Comparison Agent` (HTTP Request at position [2256, 5536]), `Prepare Comparison Input` (Code at position [2032, 5552])  
**Problem:** These two nodes are positioned far from the main pipeline (y≈5500 vs y≈10700-11400) and have **no incoming connections** and **empty output connections** (`[]`). They are remnants of a previous HTTP-based comparison approach that was replaced by the AI Agent comparison path.

They are harmless but confusing. The `Comparison Agent` node was the one with the hardcoded API key (fixed in C1). The `Prepare Comparison Input` node duplicates comparison logic now handled by `Prepare Agent Input`.

**Recommendation:** Delete both orphaned nodes to reduce confusion, or move them to a separate "Archive" sticky note area.

---

### A2. Merge Results — Synchronization-Only Role
**Node:** `Merge Results` (Merge v3)  
**Problem:** With empty parameters, Merge v3 defaults to "mergeByPosition" which combines items from both inputs by index position. Since `Pre-Flight Check` reads sources by node name (`$('Prepare LlamaParse Output')` and `$('Claude Direct Extract')`), the Merge output structure doesn't matter — it only serves as a synchronization barrier to ensure both branches complete.

**Fix applied:** Set explicit `mode: "append"` so the Merge node clearly waits for both inputs and appends all items. This is the most reliable mode for a sync-only node.

---

### A3. Claude Direct Extract (AI Agent) Had No Tool
**Node:** `Claude Direct Extract` (original AI Agent)  
**Problem:** The AI Agent node at typeVersion 3 in "conversational" mode had no tool connected. While this doesn't crash, it means Claude can't output structured data via tool use — it can only return free text. For extraction, this means the JSON output has to be parsed from free text, introducing fragility.

**Fix:** Resolved by replacing with HTTP Request (C2). The direct API call returns Claude's text response, which the Pre-Flight Check already parses as JSON.

---

### A4. Error Response Node — Disconnected
**Node:** `Error Response` (Respond to Webhook at position [1136, 11568])  
**Problem:** This node has no incoming connections in the connections map. It's designed to return a 500 error response to the webhook, but nothing routes to it. If any node fails, the webhook caller gets no response (timeout).

**Recommendation:** Connect this to the workflow's error handling. In n8n, you can set a workflow-level error trigger, or use the `onError` setting on individual nodes to route to `Error Response`.

---

## 3. DATA FLOW VERIFICATION

### Complete Trace: Webhook Trigger → Respond to Webhook

```
Webhook Trigger (POST /phasealpha-extract, multipart/form-data with PDF)
  │
  ▼
Extract File Data (Code)
  Reads: $input.first() — webhook JSON + binary
  Outputs: { has_file, file_url, base64_data, mime_type, file_name, project_name, drawing_number }
  Binary: passes through webhook binary
  │
  ├───────────────────────────────────────────┐
  ▼                                           ▼
Prepare Claude HTTP Request (Code)     Prepare LlamaParse Upload (Code)
  Reads: $('Extract File Data').json         Reads: $input.first().json + binary
  ✓ base64_data ← fileData.base64_data       ✓ Renames binary key to "data"
  ✓ mime_type ← fileData.mime_type            Outputs: metadata JSON + binary {data: pdf}
  Outputs: { request_body }                   │
  │                                           ▼
  ▼                                    Submit Parse Job (HTTP POST /upload)
Claude Direct Extract (HTTP)             Reads: binary.data (formBinaryData)
  Reads: $json.request_body                   ✓ Credential: Llama APIHeader ✓
  ✓ Credential: PhaseAlpha Claude API         Outputs: { id, status, ... }
  ✓ Headers: anthropic-version,               │
    anthropic-beta, content-type              ▼
  Outputs: { content, stop_reason }    Wait Parse 2min → Poll Counter Parse
  │                                      → Check Parse Status → Parse Job Complete?
  │                                           │true              │false
  │                                           ▼                  ▼ (loop to Wait)
  │                                    Get Parse Markdown (GET /result/markdown)
  │                                      ✓ job_id from Poll Counter Parse
  │                                      ✓ Credential: Llama APIHeader ✓
  │                                      Outputs: { markdown, ... }
  │                                           │
  │                                           ▼
  │                                    Restore Binary for Extract (Code) ← NEW
  │                                      Reads: $input (markdown result)
  │                                      + $('Prepare LlamaParse Upload').binary
  │                                      Outputs: markdown JSON + PDF binary
  │                                           │
  │                                           ▼
  │                                    Submit Extract Job (HTTP POST /upload)
  │                                      ✓ binary.data available ← FIXED
  │                                      ✓ Credential: Llama APIHeader ✓
  │                                           │
  │                                    [Extract polling loop same as parse]
  │                                           │
  │                                           ▼
  │                                    Get Extract JSON (GET /result/json)
  │                                           │
  │                                           ▼
  │                                    Prepare LlamaParse Output (Code)
  │                                      Reads: $input (extract JSON)
  │                                      + $('Get Parse Markdown').first().json
  │                                      + $('Extract File Data').first().json
  │                                      ✓ markdown field: parseResult.markdown ✓
  │                                      Outputs: { source, llamaparse_markdown,
  │                                        llamaparse_extract, file_url, ... }
  │                                           │
  ├───────── Merge Results (input 0) ◄────────┘ (input 1)
  │            mode: append, waits for both
  │
  ▼
Pre-Flight Check (Code)
  Reads by node name:
    ✓ $('Prepare LlamaParse Output').first().json → SOURCE_A
    ✓ $('Claude Direct Extract').first().json → SOURCE_B
    ✓ $('Extract File Data').first().json → file_info
  Checks: empty, parse errors, truncation (stop_reason), completeness
  Outputs: { preflight_status, flags, source_a, source_b_parsed, ... }
  │
  ▼
IF Preflight Pass? ($json.preflight_status === "PASS")
  │true                     │false
  ▼                         ▼
Prepare Agent Input    Stop and Error
  Reads from preflight      Returns error message
  output fields
  Passes webhook binary
  │
  ▼
AI Agent (Comparison)
  ✓ LLM: Anthropic Chat Model (claude-sonnet-4-6, 32000 tokens, thinking: true) ✓
  ✓ Tool: submit_extraction_result (validates schema) ✓
  ✓ passthroughBinaryImages: true (for PDF source of truth — best effort)
  │
  ▼
Respond to Webhook
  Returns: $json (AI Agent tool output = final merged extraction)
```

### Field Name Verification

| Node | Field Read | Source | Correct? |
|---|---|---|---|
| Poll Counter Parse | `item.id \|\| item.job_id \|\| $('Submit Parse Job').first().json.id` | LlamaParse upload returns `{ id: "..." }` | ✓ `.id` is correct |
| Check Parse Status URL | `$('Poll Counter Parse').first().json.job_id` | Poll Counter sets `job_id: jobId` | ✓ |
| Get Parse Markdown URL | `$('Poll Counter Parse').first().json.job_id` | Same as above | ✓ |
| Prepare LlamaParse Output | `parseResult.markdown` | LlamaParse `/result/markdown` returns `{ markdown: "..." }` | ✓ |
| Prepare LlamaParse Output | `$('Get Parse Markdown').first().json` | Reads by node name | ✓ |
| Prepare LlamaParse Output | `$('Extract File Data').first().json` | Reads by node name | ✓ |
| Pre-Flight Check | `$('Claude Direct Extract').first().json.content` | HTTP response has `.content` array | ✓ |
| Pre-Flight Check | `claudeRaw.stop_reason` | HTTP response has `.stop_reason` | ✓ |

---

## 4. CREDENTIAL MAPPING

| Node | API | Credential Type | Credential Name | ID | Correct? |
|---|---|---|---|---|---|
| Submit Parse Job | LlamaParse | httpHeaderAuth | Llama APIHeader | zHcdy6Tmcs7XIdS8 | ✓ |
| Check Parse Status | LlamaParse | httpHeaderAuth | Llama APIHeader | zHcdy6Tmcs7XIdS8 | ✓ |
| Get Parse Markdown | LlamaParse | httpHeaderAuth | Llama APIHeader | zHcdy6Tmcs7XIdS8 | ✓ |
| Submit Extract Job | LlamaParse | httpHeaderAuth | Llama APIHeader | zHcdy6Tmcs7XIdS8 | ✓ |
| Check Extract Status | LlamaParse | httpHeaderAuth | Llama APIHeader | zHcdy6Tmcs7XIdS8 | ✓ |
| Get Extract JSON | LlamaParse | httpHeaderAuth | Llama APIHeader | zHcdy6Tmcs7XIdS8 | ✓ |
| Claude Direct Extract | Anthropic | anthropicApi | PhaseAlpha Claude API | je1vli3fFL5A38FY | ✓ (FIXED) |
| Anthropic Chat Model | Anthropic | anthropicApi | PhaseAlpha Claude API | je1vli3fFL5A38FY | ✓ |
| Comparison Agent | Anthropic | anthropicApi | PhaseAlpha Claude API | je1vli3fFL5A38FY | ✓ (FIXED — removed Serper + hardcoded key) |

---

## 5. SUMMARY OF ALL CHANGES

| # | Type | What Changed | File Location |
|---|---|---|---|
| C1 | Security | Removed hardcoded API key + wrong Serper credential from Comparison Agent | Node: Comparison Agent |
| C2 | Critical Fix | Replaced AI Agent approach with HTTP Request + base64 document block for PDF | Nodes: Prepare Claude HTTP Request (new), Claude Direct Extract (replaced) |
| C3 | Critical Fix | Added Restore Binary for Extract node to fix lost PDF binary in LlamaParse extract path | Node: Restore Binary for Extract (new) |
| C4 | Bug Fix | Fixed template syntax error `{{ $json.chatInput }` → resolved by node replacement | Node: Claude Direct Extract |
| C5 | Bug Fix | maxTokensToSample 16000 → 32000 | Node: Prepare Claude HTTP Request |
| C6 | Cleanup | Removed unused Extract from File, Prepare Claude Input, Anthropic Chat Model (Extract) | Nodes removed |
| C7 | Reliability | Set Merge Results to explicit `mode: "append"` | Node: Merge Results |

### Nodes Added
- `Prepare Claude HTTP Request` — Code node building Anthropic API request with base64 PDF
- `Restore Binary for Extract` — Code node re-attaching PDF binary for second LlamaParse upload

### Nodes Removed  
- `Extract from File` — was looking for wrong binary key, not needed with direct API approach
- `Prepare Claude Input` — replaced by Prepare Claude HTTP Request  
- `Anthropic Chat Model (Extract)` — LLM sub-node no longer needed with HTTP Request

### Nodes Modified
- `Comparison Agent` — removed hardcoded API key and Serper credential
- `Claude Direct Extract` — replaced AI Agent with HTTP Request
- `Merge Results` — added explicit `mode: "append"`

---

## 6. REMAINING RECOMMENDATIONS (Non-blocking)

1. **Rotate the exposed API key** (`REDACTED...`) immediately — it's in the workflow export JSON and likely in n8n version history.

2. **Delete orphaned nodes** (`Comparison Agent` HTTP Request and `Prepare Comparison Input`) — they duplicate the AI Agent comparison path and add confusion.

3. **Connect Error Response node** — currently has no incoming connection. Wire it to n8n's workflow error handler so webhook callers get a 500 response on failure instead of a timeout.

4. **Comparison AI Agent PDF attachment** — The `Prepare Agent Input` node passes webhook binary via `passthroughBinaryImages`, but this won't work for PDFs. The comparison agent uses LlamaParse markdown as SOURCE_OF_TRUTH, so this is non-critical. For full PDF comparison, consider a similar HTTP Request approach for the comparison step.

5. **LlamaParse dual-upload efficiency** — The workflow uploads the same PDF to LlamaParse twice (Submit Parse Job + Submit Extract Job). Consider using LlamaParse's job reuse API if available, or store the uploaded file ID from the first job to reference in the second.

6. **Webhook binary key** — `Extract File Data` handles both `binary.data` and `binary.file`, and `Prepare LlamaParse Upload` renames to `data`. The HTTP Request nodes use `inputDataFieldName: "data"`. This is consistent as long as the webhook form field is named "data" or "file". Document the expected field name for API consumers.
