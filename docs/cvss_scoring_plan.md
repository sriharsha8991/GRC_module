# CVSS 3.1 Scoring — Implementation Plan

## Overview

Add a CVSS 3.1 base scoring module that takes a security finding text, uses a
dedicated Gemini LLM call to classify the finding into CVSS base metrics, then
computes the numeric score via the `cvss` Python library. The result (score,
vector, severity, confidence, remediation) attaches to the existing
`QueryResponse` at **finding level** — one CVSS assessment per query, not per
control mapping.

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| CVSS version | **3.1 only** | Industry standard, matches user example |
| Scoring scope | **Per-finding** | One CVSS per query, not per control mapping |
| LLM strategy | **Separate Gemini call** | Dedicated CVSS-expert system prompt, clean SoC |
| Input to classifier | **Finding text only** | CVSS metrics are intrinsic to the vulnerability, not compliance controls (see below) |
| CVE association | **Always `null`** | No CVE lookup for now — implement separately later |
| Score computation | **`cvss` PyPI library** | Avoids reimplementing the FIRST spec formulas |

### Why Mappings Are NOT Passed to the CVSS Classifier

CVSS 3.1 scores 8 base metrics that describe the **vulnerability itself**:

| Metric | Measures | Source |
|--------|----------|--------|
| Attack Vector (AV) | How exploitation happens | Finding text |
| Attack Complexity (AC) | Conditions beyond attacker control | Finding text |
| Privileges Required (PR) | Privilege level needed | Finding text |
| User Interaction (UI) | User participation needed | Finding text |
| Scope (S) | Impact beyond security boundary | Finding text |
| Confidentiality (C) | Loss of confidentiality | Finding text |
| Integrity (I) | Loss of integrity | Finding text |
| Availability (A) | Loss of availability | Finding text |

Control mappings provide `control_id`, `control_title`, `domain`,
`risk_mitigated` — all describing **regulatory compliance alignment**, not
vulnerability exploitation characteristics.

- **Token waste**: ~500-1000 extra tokens per request for zero accuracy gain
- **Confusion risk**: Mapping context could lead the LLM to conflate "what controls exist"
  with "how severe is the vulnerability"
- **FIRST spec guidance**: Scoring is "agnostic to the individual and their organization"

### Parallelization Opportunity

Since the CVSS classifier only needs the finding text (not mappings), it can run
**in parallel** with the mapper/critic framework processing, adding zero latency:

```
                    ┌─── Embed → Search → Map → Critic ───┐
Finding text ───────┤                                       ├──► Merge into QueryResponse
                    └─── CVSSClassifier.classify() ────────┘
```

---

## Architecture

### New Module: `src/scoring/`

```
src/scoring/
├── __init__.py          # Module init, public exports
├── models.py            # CVSSClassification (LLM schema) + CVSSResult (API response)
├── classifier.py        # CVSSClassifier — Gemini structured output call
└── engine.py            # compute_cvss() — vector assembly + score calculation
```

### Data Flow

```
Finding text
    │
    ▼
CVSSClassifier.classify(finding_text)
    │  ◄── Gemini call with CVSS-expert system prompt
    │      temperature=0.1, response_schema=CVSSClassification
    ▼
CVSSClassification
    │  (individual metrics: AV=N, AC=L, PR=N, ... + reasoning)
    │
    ▼
compute_cvss(classification)
    │  ◄── Builds vector string: "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N"
    │      Uses cvss.CVSS3(vector).base_score for computation
    │      Derives severity from FIRST thresholds
    ▼
CVSSResult
    │  {score=9.1, vector="CVSS:3.1/...", severity="Critical", ...}
    │
    ▼
QueryResponse.cvss = cvss_result
```

---

## Module Details

### 1. `src/scoring/models.py`

**`CVSSClassification`** — LLM structured output schema (Gemini generates this):

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Short finding name |
| `description` | `str` | Finding description |
| `potential_impact` | `str` | Impact description |
| `attack_vector` | `Literal["N","A","L","P"]` | AV metric |
| `attack_complexity` | `Literal["L","H"]` | AC metric |
| `privileges_required` | `Literal["N","L","H"]` | PR metric |
| `user_interaction` | `Literal["N","R"]` | UI metric |
| `scope` | `Literal["U","C"]` | S metric |
| `confidentiality_impact` | `Literal["N","L","H"]` | C metric |
| `integrity_impact` | `Literal["N","L","H"]` | I metric |
| `availability_impact` | `Literal["N","L","H"]` | A metric |
| `confidence` | `Literal["High","Medium","Low"]` | LLM self-assessment |
| `how_to_remediate` | `str` | Remediation guidance |
| `reasoning` | `str` | LLM's reasoning for metric choices |

**`CVSSResult`** — API response model (returned to client):

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Short finding name |
| `description` | `str` | Finding description |
| `potential_impact` | `str` | Impact description |
| `severity` | `str` | Derived: Critical/High/Medium/Low/None |
| `score` | `float` | CVSS 3.1 base score (0.0-10.0) |
| `cvss_vector` | `str` | Full vector string |
| `cve` | `str \| None` | Always `null` for now |
| `confidence` | `str` | High/Medium/Low |
| `how_to_remediate` | `str` | Remediation guidance |

### 2. `src/scoring/engine.py`

- `compute_cvss(classification: CVSSClassification) -> CVSSResult`
- Builds vector: `CVSS:3.1/AV:{av}/AC:{ac}/PR:{pr}/UI:{ui}/S:{s}/C:{c}/I:{i}/A:{a}`
- Score via `cvss.CVSS3(vector).base_score`
- Severity thresholds (FIRST spec §5):
  - None: 0.0
  - Low: 0.1 – 3.9
  - Medium: 4.0 – 6.9
  - High: 7.0 – 8.9
  - Critical: 9.0 – 10.0

### 3. `src/scoring/classifier.py`

- `CVSSClassifier(settings: AppSettings)`
- `classify(finding: str) -> tuple[CVSSClassification, dict]`
- System prompt embeds full FIRST spec metric definitions
- Gemini structured output, temperature=0.1
- Returns `(classification, token_usage_dict)`

---

## Integration Points

### Modified Files

| File | Change |
|------|--------|
| `src/retrieval/models.py` | Add `cvss: CVSSResult \| None` to `QueryResponse`; add `cvss_prompt_tokens`, `cvss_total_tokens` to `TokenUsage` |
| `src/retrieval/pipeline.py` | Add CVSS step (parallel with mapper/critic); aggregate CVSS tokens |
| `src/api/schemas.py` | Re-export `CVSSResult` |
| `requirements.txt` | Add `cvss>=3.2,<4.0` |

### Pipeline Flow (Updated)

```
0. Cache lookup
1. Embed finding
2. Search Qdrant (parallel per-framework)
3. Per-framework: Map + conditional Critic (parallel) ─┐
   ┌── CVSS: Classify + Score (parallel) ──────────────┤
4. Merge mappings + CVSS + tokens                      │
5. Build QueryResponse ◄──────────────────────────────┘
6. Cache write
7. Return
```

---

## Example Response

```json
{
  "finding_text": "DNS Zone Transfer Enabled (AXFR)",
  "cvss": {
    "name": "DNS Zone Transfer Enabled (AXFR)",
    "description": "The DNS server allows unauthenticated zone transfers.",
    "potential_impact": "Attackers can enumerate all DNS records and internal infrastructure.",
    "severity": "Critical",
    "score": 9.1,
    "cvss_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N",
    "cve": null,
    "confidence": "High",
    "how_to_remediate": "Restrict AXFR to authorized secondary DNS servers only."
  },
  "mappings": [
    {
      "framework": "ISO/IEC 27001:2022",
      "control_id": "A.8.20",
      "control_title": "Networks security",
      "confidence_score": 92,
      "status": "APPROVED"
    }
  ],
  "token_usage": {
    "mapper_prompt_tokens": 3250,
    "mapper_total_tokens": 4100,
    "critic_prompt_tokens": 1800,
    "critic_total_tokens": 2200,
    "cvss_prompt_tokens": 850,
    "cvss_total_tokens": 1100,
    "critic_skipped": false,
    "total_tokens": 7400
  }
}
```

---

## CVSS 3.1 Scoring Rubrics Reference

Based on FIRST.Org CVSS v3.1 Specification Document.

### Base Metrics

#### Exploitability Metrics

**Attack Vector (AV):**
- **Network (N):** Vulnerable component bound to network stack; exploitable
  remotely across routers/Internet
- **Adjacent (A):** Limited to logically adjacent topology (same LAN, Bluetooth,
  MPLS, VPN)
- **Local (L):** Attacker exploits via read/write/execute capabilities (keyboard,
  SSH, social engineering)
- **Physical (P):** Requires physical touch/manipulation (evil maid, cold boot,
  FireWire/USB DMA)

**Attack Complexity (AC):**
- **Low (L):** No specialized conditions; repeatable success expected
- **High (H):** Depends on conditions beyond attacker's control (race conditions,
  MITM positioning, environment reconnaissance)

**Privileges Required (PR):**
- **None (N):** Attacker is unauthorized prior to attack
- **Low (L):** Basic user capabilities (settings/files owned by user)
- **High (H):** Significant (admin) control over vulnerable component

**User Interaction (UI):**
- **None (N):** Exploitable without any user participation
- **Required (R):** User must take action (open file, click link, install)

#### Scope (S)
- **Unchanged (U):** Impact limited to same security authority
- **Changed (C):** Impact crosses security boundary to different authority

#### Impact Metrics

**Confidentiality / Integrity / Availability (C/I/A):**
- **High (H):** Total loss (all resources divulged / all files modifiable / full
  denial of service)
- **Low (L):** Limited loss (some restricted info / limited modification / reduced
  performance)
- **None (N):** No impact to this dimension

### Severity Scale

| Rating | Score Range |
|--------|------------|
| None | 0.0 |
| Low | 0.1 – 3.9 |
| Medium | 4.0 – 6.9 |
| High | 7.0 – 8.9 |
| Critical | 9.0 – 10.0 |

---

## Verification Checklist

- [ ] Unit test `engine.py`: known vectors produce expected scores
  - `CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N` → 9.1, Critical
  - `CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N` → 1.8, Low
- [ ] LLM-generated vectors parse with `cvss.CVSS3()` without exceptions
- [ ] `POST /api/v1/query` response includes `cvss` with all fields
- [ ] `token_usage` includes `cvss_prompt_tokens` and `cvss_total_tokens`
- [ ] CVSS step runs in parallel (check timing logs)
