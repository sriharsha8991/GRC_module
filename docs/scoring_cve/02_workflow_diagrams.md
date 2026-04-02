# CVE ID Management — Workflow Diagrams

## 1. End-to-End Pipeline Flow

The complete flow from finding input to enriched response with CVE data.

```mermaid
flowchart TB
    subgraph INPUT["📥 Input"]
        FT["finding_text"]
    end

    FT --> CACHE{"Redis\nCache Hit?"}
    CACHE -->|HIT| RESP["Return cached\nQueryResponse"]
    CACHE -->|MISS| PAR

    subgraph PAR["⚡ Parallel Execution (ThreadPoolExecutor)"]
        direction TB

        subgraph TRACK1["Track 1: Compliance Mapping (existing)"]
            EMB["Embed finding\n(Gemini Embedding)"] --> SEARCH["Search Qdrant\n(per framework)"]
            SEARCH --> MAP["Map to controls\n(Gemini structured output)"]
            MAP --> CRITIC{"Confidence\n< threshold?"}
            CRITIC -->|Yes| CRIT["Adversarial Critic\n(Gemini validation)"]
            CRITIC -->|No| SKIP["Skip critic"]
            CRIT --> MAPS["ControlMapping[]"]
            SKIP --> MAPS
        end

        subgraph TRACK2["Track 2: CVSS Scoring (existing)"]
            CVSS_C["CVSS Classifier\n(Gemini structured output)"] --> CVSS_E["CVSS Engine\n(deterministic score)"]
            CVSS_E --> CVSS_R["CVSSResult"]
        end

        subgraph TRACK3["Track 3: CVE Enrichment (NEW)"]
            FC["Finding Classifier\n(Gemini structured output)"]
            FC --> DEC{"finding_type?"}
            DEC -->|MISCONFIGURATION| NULL["cve_ids = null\n(early return)"]
            DEC -->|VULNERABILITY| SEARCH_CVE["CVE Search\n(NVD + OSV.dev)"]
            SEARCH_CVE --> EVAL["CVE Evaluator\n(LLM judge)"]
            EVAL --> CVE_R["CveEnrichment"]
            NULL --> CVE_R
        end
    end

    MAPS --> MERGE["Merge Results"]
    CVSS_R --> MERGE
    CVE_R --> MERGE
    MERGE --> QR["QueryResponse"]
    QR --> CACHE_W["Write to Redis"]
    CACHE_W --> RESP2["Return Response"]

    style TRACK1 fill:#e8f5e9,stroke:#2e7d32
    style TRACK2 fill:#e3f2fd,stroke:#1565c0
    style TRACK3 fill:#fff3e0,stroke:#e65100
    style PAR fill:#fafafa,stroke:#424242
```

---

## 2. CVE Enrichment Track — Detailed Flow

The internal flow of Track 3 (CVE enrichment) in detail.

```mermaid
flowchart TD
    FT["finding_text"] --> FC

    subgraph CLASSIFY["Phase 1: Classification"]
        FC["FindingClassifier.classify()"]
        FC -->|Gemini structured output| FCR["FindingClassification"]
        FCR --> TYPE{"finding_type"}
    end

    TYPE -->|MISCONFIGURATION| EARLY["Return CveEnrichment\nfinding_type=MISCONFIGURATION\ncve_ids=null"]

    TYPE -->|VULNERABILITY| EXT["Extract:\n• software_component\n• vendor\n• version\n• ecosystem\n• explicit_cve_ids"]

    EXT --> EXPLICIT{"explicit CVE IDs\nin finding text?"}
    EXPLICIT -->|Yes| AUTO1["Auto-approve\n(skip search)"]

    EXPLICIT -->|No| SEARCH_PHASE

    subgraph SEARCH_PHASE["Phase 2: CVE ID Search"]
        direction TB
        ECO{"ecosystem\nknown?"}
        ECO -->|"npm/PyPI/Maven"| OSV["OSV.dev API\nPOST /v1/query\n{name, ecosystem, version}"]
        ECO -->|"unknown/OS"| NVD_ONLY["Skip OSV"]

        OSV --> NVD
        NVD_ONLY --> NVD

        NVD["NVD API\nvirtualMatchString\ncpe:2.3:a:*:{product}:{version}"]
        NVD --> KW{"CPE results\nfound?"}
        KW -->|No| KW_SEARCH["NVD keywordSearch\n{product} {version}"]
        KW -->|Yes| DEDUP
        KW_SEARCH --> DEDUP

        DEDUP["Deduplicate\ncandidate CVE IDs"]
    end

    AUTO1 --> DETAIL
    AUTO2 --> DETAIL
    DEDUP --> DETAIL

    subgraph DETAIL_PHASE["Phase 2b: CVE Detail Fetch"]
        DETAIL["For each CVE ID:"]
        DETAIL --> CVEORG["cve.org API\nGET /api/cve/{id}"]
        CVEORG --> PARSE["Parse:\n• CNA container (desc, CVSS, CWE)\n• CISA-ADP (SSVC, KEV)"]
        CVEORG -->|"404/timeout"| NVD_FALL["NVD API fallback\n?cveId={id}"]
        NVD_FALL --> PARSE
    end

    PARSE --> EVAL_CHECK{"CVE source?"}
    EVAL_CHECK -->|"explicit/lookup"| SKIP_EVAL["Skip evaluation\n(auto-approved)"]
    EVAL_CHECK -->|"search-discovered"| EVAL

    subgraph EVAL_PHASE["Phase 3: LLM Evaluation"]
        EVAL["CveEvaluator.evaluate()"]
        EVAL -->|"Gemini structured output"| JUDGE["Per CVE:\n• is_relevant: bool\n• relevance_score: 0-100\n• reasoning: str"]
        JUDGE --> FILTER["Filter:\nis_relevant=True AND\nrelevance_score ≥ threshold"]
    end

    SKIP_EVAL --> RESULT
    FILTER --> RESULT

    RESULT["CveEnrichment\n• finding_type\n• cve_ids\n• cve_details\n• evaluation_summary"]

    style CLASSIFY fill:#e8eaf6,stroke:#283593
    style SEARCH_PHASE fill:#fff3e0,stroke:#e65100
    style DETAIL_PHASE fill:#e0f2f1,stroke:#00695c
    style EVAL_PHASE fill:#fce4ec,stroke:#b71c1c
```

---

## 3. Module Dependency Graph

Shows which modules depend on which, following Dependency Inversion.

```mermaid
graph TD
    subgraph PIPELINE["src/retrieval/pipeline.py"]
        QF["query_finding()"]
    end

    subgraph SCORING["src/scoring/ (CVE modules)"]
        CP["cve_pipeline.py\n(orchestrator)"]
        FC["finding_classifier.py"]
        CS["cve_searcher.py"]
        CC["cve_client.py"]
        CE["cve_evaluator.py"]
        MD["models.py"]
    end

    subgraph EXISTING["src/scoring/ (existing)"]
        CL["classifier.py"]
        EN["engine.py"]
    end

    subgraph CONFIG["src/config/"]
        ST["settings.py"]
        GC["genai_client.py"]
    end

    subgraph EXTERNAL["External APIs"]
        NVD["NVD API v2.0"]
        CVE["cve.org API"]
        OSV["OSV.dev API"]
    end

    QF --> CP
    QF --> CL
    QF --> EN

    CP --> FC
    CP --> CS
    CP --> CE
    CP --> MD

    CS --> CC

    FC --> GC
    FC --> MD
    CE --> GC
    CE --> MD

    CC --> NVD
    CC --> CVE
    CC --> OSV

    CP --> ST
    FC --> ST
    CS --> ST
    CE --> ST

    style CP fill:#fff3e0,stroke:#e65100,stroke-width:3px
    style PIPELINE fill:#e8f5e9,stroke:#2e7d32
    style SCORING fill:#fff3e0,stroke:#e65100
    style EXISTING fill:#e3f2fd,stroke:#1565c0
    style EXTERNAL fill:#f3e5f5,stroke:#6a1b9a
```

---

## 4. Data Flow — Model Transformations

Shows how data models transform through each stage.

```mermaid
flowchart LR
    subgraph IN["Input"]
        FT["finding_text\n(str)"]
    end

    subgraph S1["Phase 1"]
        FCL["FindingClassification\n• finding_type\n• software_component\n• vendor / version\n• ecosystem\n• explicit_cve_ids"]
    end

    subgraph S2["Phase 2"]
        CSR["CveSearchResult[]\n• cve_id\n• source\n• description\n• affected_product\n• affected_versions"]
    end

    subgraph S2B["Phase 2b"]
        CD["CveDetail[]\n• cve_id\n• description\n• cvss_score / severity\n• cwe_id\n• kev / ssvc\n• references"]
    end

    subgraph S3["Phase 3"]
        CEV["CveEvaluation[]\n• cve_id\n• is_relevant\n• relevance_score\n• reasoning"]
    end

    subgraph OUT["Output"]
        CE["CveEnrichment\n• finding_type\n• cve_ids: list | null\n• cve_details\n• evaluation_summary\n• search_sources"]
    end

    FT --> FCL
    FCL -->|"VULNERABILITY\n+ component info"| CSR
    CSR -->|"candidate CVE IDs"| CD
    CD -->|"enriched details\n+ finding text"| CEV
    CEV -->|"filtered + assembled"| CE

    FCL -->|"MISCONFIGURATION"| CE

    style S1 fill:#e8eaf6,stroke:#283593
    style S2 fill:#fff3e0,stroke:#e65100
    style S2B fill:#e0f2f1,stroke:#00695c
    style S3 fill:#fce4ec,stroke:#b71c1c
    style OUT fill:#f1f8e9,stroke:#33691e
```

---

## 5. Short-Circuit Decision Tree

Shows when the system skips expensive operations.

```mermaid
flowchart TD
    START["Finding received"] --> CLASSIFY["LLM classifies:\nVulnerability or Misconfiguration?"]

    CLASSIFY -->|"MISCONFIGURATION"| MISCONFIG["Return cve_ids=null\n⚡ Skip all search + evaluation\n💰 0 API calls"]

    CLASSIFY -->|"VULNERABILITY"| CHECK1{"Explicit CVE IDs\nin finding text?"}

    CHECK1 -->|"Yes: CVE-2021-44228"| AUTO1["Auto-approve\n⚡ Skip search + evaluation\n💰 0 API calls"]

    CHECK1 -->|"No"| SEARCH["Full search:\nOSV.dev + NVD API\n💰 1-3 API calls"]

    SEARCH --> EVAL["LLM evaluates matches\n💰 1 Gemini call"]

    AUTO1 --> DETAIL["Fetch CVE details\n💰 1 API call per CVE"]
    EVAL --> DETAIL

    style MISCONFIG fill:#c8e6c9,stroke:#2e7d32
    style AUTO1 fill:#c8e6c9,stroke:#2e7d32
    style SEARCH fill:#fff9c4,stroke:#f9a825
    style EVAL fill:#ffcdd2,stroke:#c62828
```
