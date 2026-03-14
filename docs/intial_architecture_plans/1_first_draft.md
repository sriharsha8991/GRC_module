


Here is the final, comprehensive architectural blueprint and implementation master plan. This document compiles all our analysis, optimizations, and technical conclusions into a single source of truth for your engineering team to build the **Agentic GRC Compliance Mapping System**.

---

# 📄 Master Architecture Blueprint: Agentic GRC Compliance Mapping System

## 1. Executive Summary
An enterprise-grade, asynchronous AI system designed to automatically map technical security findings (e.g., "MySQL port 3306 open") to relevant GRC framework controls (ISO 27001, PCI-DSS, NIST, etc.). 
*   **Target Accuracy:** ~99% achieved via an adversarial Critic Agent (replacing Human-in-the-Loop) and strict citation grounding.
*   **Processing:** Supports both real-time single requests and bulk-batch processing.
*   **Storage:** Direct write-to-database output, bolstered by a historical persistent cache to eliminate redundant LLM costs.

---

## 2. Technology Stack & Infrastructure

### Core Infrastructure
*   **Vector Database (Retrieval Engine):** **Qdrant** (Rust-based, handles massive concurrent vector math, native Hybrid Search, and Batch Search APIs).
*   **Relational Database (State & Cache):** **PostgreSQL** (Stores persistent caching, batch job tracking, and final backend payloads).
*   **API & Task Queue:** **FastAPI** + **Celery** + **Redis/RabbitMQ** (Event-driven asynchronous processing).
*   **Containerization:** **Docker** (Crucial for baking in Hugging Face models and avoiding runtime downloads).

### AI & Orchestration
*   **Agent Framework:** **LangGraph** (Python-based stateful multi-agent workflows).
*   **Document Parsing:** **Docling** (Deep learning-based PDF layout/table extraction into Markdown).
*   **Primary LLMs:** **GPT-4o** or **Claude 3.5 Sonnet** (For Mapper & Critic, utilizing Prompt Caching).
*   **Micro-LLM:** **GPT-4o-mini** or **Claude 3.5 Haiku** (For metadata extraction and cheap YES/NO filtering).
*   **Embeddings:** **OpenAI `text-embedding-3-large`** (High dimension).
*   **Reranker:** **BAAI/bge-reranker-v2-m3** (Open-source, hosted locally via Hugging Face **TEI - Text Embeddings Inference** Docker container for zero API cost).

---

## 3. Database & Storage Strategy

### A. Qdrant (Knowledge Base)
A single collection (e.g., `grc_frameworks`) using **Payload Indexing** for ultra-fast filtering.
*   **Payload Schema:** `framework_name`, `framework_version`, `framework_category` (e.g., "Privacy & Data Protection"), `domain`, `control_id`, `is_latest: boolean`.

### B. PostgreSQL (State & Cache)
*   **Table:** `finding_mapping_cache`
*   **Columns:** 
    *   `finding_hash` (SHA-256 of Finding + Asset Type + Target Categories) - *Primary Key*
    *   `finding_text`
    *   `mapped_payload` (JSONB)
    *   `confidence_score` (Integer)
    *   `created_at` (Timestamp)

---

## 4. Phase 1: Ingestion & Knowledge Pipeline (Offline)

Executed only when adding or updating a GRC framework PDF.

1.  **Docling Extraction:** Run the PDF through the Dockerized Docling container. (Models are pre-cached during the `docker build` phase). Outputs clean Markdown, preserving complex tables.
2.  **Semantic Chunking:** A custom Python script parses the Markdown, splitting strictly by **Control Headers** (e.g., *Clause 8.20 Networks security* becomes exactly one chunk).
3.  **Metadata Enrichment:** Send each chunk to a Micro-LLM (`gpt-4o-mini`) to extract the Payload Schema JSON (categorizing it into "Financial", "Security & Trust", etc.).
4.  **Vectorization:** Embed the chunk and push it to Qdrant with `is_latest = true`. (If updating a framework, first update old vectors to `is_latest = false`).

---

## 5. Phase 2: Online API & Async Queue Pipeline

Handles production traffic without blocking HTTP requests.

1.  **API Gateway (FastAPI):** Receives the JSON payload (single finding or an array of 1,000+).
2.  **Queue Dispatch:** Immediately returns `202 Accepted` with a `Batch_Job_ID`. Pushes items to Redis/RabbitMQ.
3.  **Celery Workers:** Pull items from the queue.
4.  **Cache Check (Zero-Cost Bypass):**
    *   Worker hashes the input.
    *   Queries Postgres `finding_mapping_cache`.
    *   If a match is found AND `confidence_score >= 60`, it returns the cached JSON to the backend immediately.
    *   If no match, it passes the finding to LangGraph.

---

## 6. Phase 3: The LangGraph Multi-Agent Workflow (Core Engine)

The intelligent heart of the system.

**Agent 1: Atomic Contextualizer**
*   *Action:* Analyzes the finding and breaks it into **Atomic Sub-queries** (e.g., "MySQL port open" $\rightarrow$ `["Network firewall rules", "Database exposure", "Secure configuration"]`). It also identifies the relevant `framework_category`.

**Agent 2: Qdrant Batch Retriever (Fan-Out)**
*   *Action:* Takes the atomic sub-queries and makes a **single Batch Search API call** to Qdrant.
*   *Filter:* Applies payload filter `is_latest = true AND framework_category IN (...)`.
*   *Output:* Retrieves the Top 10 chunks per atomic query using Qdrant’s native Hybrid Search.

**Agent 3: Local Reranker**
*   *Action:* Sends the finding and retrieved chunks to the local Hugging Face TEI microservice.
*   *Dynamic Thresholding:* Instead of limiting to "Top 2", it drops any chunk with a relevance score `< 0.85`. This ensures all valid controls across different frameworks survive.

**Agent 3.5: The Micro-Filter (Cost Optimizer)**
*   *Action:* Uses `gpt-4o-mini` to do a ruthless, lightning-fast YES/NO check on surviving chunks. *"Does this text directly mitigate the finding?"* Discards the "NOs".

**Agent 4: Compliance Mapper (The Writer)**
*   *Action:* Ingests the finding and the highly filtered chunks. Utilizes **Prompt Caching** to save 50-90% on input token costs.
*   *Output:* Generates structured JSON grouped by framework: `Framework`, `Mapped_Domain`, `Exact_Control_ID`, `Risk_Mitigated`, `Citation` (exact quote), and `Confidence_Score`.

**Agent 5: The Critic (The Automated QA / Safety Net)**
*   *Action:* Conducts a strict adversarial audit against Agent 4’s JSON.
*   *Checks:* 
    1. Is the Citation perfectly grounded in the source text (No hallucination)? 
    2. Is the mapping logically sound? 
    3. Is `Confidence_Score >= 60`?
*   *One-Pass Pruning:* Does **not** loop. If a framework mapping fails any check, the Critic overwrites that specific framework's output to a standard fallback: `{"status": "Failed", "Message": "Explicitly mapped as not able to have proper information for this finding."}`

---

## 7. Phase 4: Storage & Handoff

1.  **Backend Commit:** The Celery worker takes the final, Critic-approved JSON array and writes it to your main backend database.
2.  **Cache Update:** The worker inserts the successful finding and its hash into the PostgreSQL `finding_mapping_cache` table for future requests.
3.  **Task Complete:** The job is marked as successful in the task queue.

---

## 8. Critical Development & Deployment Dependencies

To build this successfully, the dev team must adhere to the following:

1.  **Docling Dockerization:** The `Dockerfile` must include a build step that executes a dummy Docling run. This ensures the Hugging Face OCR/Layout weights are baked into the image, preventing runtime downloading timeouts (as seen in your logs).
2.  **Hugging Face TEI Provisioning:** The `bge-reranker-v2-m3` model via TEI requires adequate RAM (allocate ~4GB to this specific container). It must be exposed as an internal microservice, not a public endpoint.
3.  **Qdrant Indexing:** Ensure Qdrant collections are set up with **Payload Indexes** on `framework_category`, `framework_name`, and `is_latest` to guarantee sub-millisecond filtering before vector searches.
4.  **Prompt Caching API Implementation:** Ensure the LangChain/API calls to OpenAI/Anthropic for Agent 4 are configured with the specific headers required to enable Prompt Caching for the system instructions and framework contexts.