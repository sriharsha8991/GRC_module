"""Streamlit UI for GRC Module — Ingestion & Query interfaces."""

import json

import httpx
import streamlit as st

API_BASE = "http://localhost:8080/api/v1"

# ── Framework registry (from frameworks.json) ──────────────────
FRAMEWORKS: dict[str, dict] = {}


@st.cache_data
def load_frameworks() -> dict[str, dict]:
    with open("src/config/frameworks.json") as f:
        return json.load(f)


FRAMEWORKS = load_frameworks()

# Display-label → key mapping for dropdown
FRAMEWORK_OPTIONS = {
    f"{v['display_name']} ({v['version']})": k for k, v in FRAMEWORKS.items()
}

# ── Page config ────────────────────────────────────────────────
st.set_page_config(page_title="GRC Module", page_icon="🛡️", layout="wide")

# ── Sidebar navigation ────────────────────────────────────────
page = st.sidebar.radio("Navigate", ["📄 Ingestion", "🔍 Query"], index=1)

# ═══════════════════════════════════════════════════════════════
#  INGESTION PAGE
# ═══════════════════════════════════════════════════════════════
if page == "📄 Ingestion":
    st.title("📄 Framework Ingestion")
    st.markdown(
        "Upload a GRC framework PDF and ingest it into the vector store. "
        "Select the matching framework from the dropdown."
    )

    col1, col2 = st.columns([2, 1])

    with col1:
        uploaded_file = st.file_uploader(
            "Upload PDF", type=["pdf"], accept_multiple_files=False
        )

    with col2:
        selected_label = st.selectbox(
            "Framework",
            options=list(FRAMEWORK_OPTIONS.keys()),
            index=None,
            placeholder="Select a framework…",
        )

    if st.button("🚀 Ingest", type="primary", disabled=not (uploaded_file and selected_label)):
        framework_key = FRAMEWORK_OPTIONS[selected_label]

        with st.spinner(f"Ingesting **{selected_label}** — this may take a few minutes…"):
            try:
                resp = httpx.post(
                    f"{API_BASE}/ingestion/ingest",
                    files={"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")},
                    data={"framework_key": framework_key},
                    timeout=600,
                )
                resp.raise_for_status()
                result = resp.json()

                st.success("Ingestion complete!")
                m1, m2, m3 = st.columns(3)
                m1.metric("Chunks Created", result.get("chunks_created", 0))
                m2.metric("Points Upserted", result.get("points_upserted", 0))
                m3.metric("Duration (s)", f"{result.get('duration_seconds', 0):.1f}")

                with st.expander("Full response"):
                    st.json(result)

            except httpx.HTTPStatusError as exc:
                detail = exc.response.json().get("detail", exc.response.text)
                st.error(f"**{exc.response.status_code}** — {detail}")
            except httpx.ConnectError:
                st.error("Cannot reach the API. Is the FastAPI server running on port 8080?")
            except Exception as exc:
                st.error(f"Unexpected error: {exc}")

# ═══════════════════════════════════════════════════════════════
#  QUERY PAGE
# ═══════════════════════════════════════════════════════════════
else:
    st.title("🔍 Security Finding Query")
    st.markdown(
        "Enter a security finding and select one or more frameworks to map it against."
    )

    finding_text = st.text_area(
        "Security Finding",
        height=120,
        placeholder="e.g. The application stores passwords in plaintext in the database without hashing or salting…",
    )

    selected_labels = st.multiselect(
        "Target Frameworks",
        options=list(FRAMEWORK_OPTIONS.keys()),
        default=None,
        placeholder="Select framework(s)…",
    )

    if st.button("🔎 Query", type="primary", disabled=not (finding_text and selected_labels)):
        target_keys = [FRAMEWORK_OPTIONS[lbl] for lbl in selected_labels]

        with st.spinner("Running retrieval pipeline…"):
            try:
                resp = httpx.post(
                    f"{API_BASE}/query",
                    json={
                        "finding_text": finding_text,
                        "target_frameworks": target_keys,
                    },
                    timeout=300,
                )
                resp.raise_for_status()
                data = resp.json()

                # ── Summary metrics ───────────────────────────
                st.divider()
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Mappings", len(data.get("mappings", [])))
                c2.metric("Chunks Retrieved", data.get("chunks_retrieved", 0))
                c3.metric("Duration (s)", f"{data.get('duration_seconds', 0):.1f}")
                total_tok = data.get("token_usage", {}).get("total_tokens", 0)
                c4.metric("Tokens Used", f"{total_tok:,}")

                # ── CVSS score ────────────────────────────────
                cvss = data.get("cvss")
                if cvss:
                    st.divider()
                    st.subheader("CVSS Assessment")
                    sc1, sc2, sc3 = st.columns(3)
                    severity = cvss.get("severity", "N/A")
                    score = cvss.get("score", 0)
                    severity_colors = {
                        "Critical": "🔴",
                        "High": "🟠",
                        "Medium": "🟡",
                        "Low": "🟢",
                        "None": "⚪",
                    }
                    sc1.metric("Score", f"{score}")
                    sc2.metric("Severity", f"{severity_colors.get(severity, '')} {severity}")
                    sc3.metric("Confidence", cvss.get("confidence", "N/A"))

                    with st.expander("CVSS Details"):
                        st.markdown(f"**Vector:** `{cvss.get('cvss_vector', '')}`")
                        st.markdown(f"**Description:** {cvss.get('description', '')}")
                        st.markdown(f"**Potential Impact:** {cvss.get('potential_impact', '')}")
                        st.markdown(f"**Remediation:**\n\n{cvss.get('how_to_remediate', '')}")
                        st.markdown(f"**Metric Rationale:**\n\n{cvss.get('metric_rationale', '')}")

                # ── Control mappings ──────────────────────────
                mappings = data.get("mappings", [])
                if mappings:
                    st.divider()
                    st.subheader(f"Control Mappings ({len(mappings)})")

                    for i, m in enumerate(mappings, 1):
                        status_icon = "✅" if m.get("status") == "APPROVED" else "❌"
                        with st.expander(
                            f"{status_icon} {m.get('control_id', '')} — "
                            f"{m.get('control_title', '')}  "
                            f"({m.get('confidence_score', 0)}%)",
                            expanded=(i <= 3),
                        ):
                            col_a, col_b = st.columns(2)
                            col_a.markdown(f"**Framework:** {m.get('framework', '')}")
                            col_b.markdown(f"**Version:** {m.get('framework_version', '')}")

                            st.markdown(f"**Domain:** {m.get('domain', '')}")
                            st.markdown(f"**Risk Mitigated:** {m.get('risk_mitigated', '')}")
                            st.markdown(f"**Citation Source:** {m.get('citation_source', '')}")
                            st.markdown(f"**Citation:**\n> {m.get('citation', '')}")

                            if m.get("status") == "FAILED":
                                st.warning(f"**Critic Reason:** {m.get('critic_reason', '')}")

                elif not data.get("mappings"):
                    st.info("No control mappings were found for this finding.")

                # ── Full JSON ─────────────────────────────────
                with st.expander("Full JSON Response"):
                    st.json(data)

            except httpx.HTTPStatusError as exc:
                detail = exc.response.json().get("detail", exc.response.text)
                st.error(f"**{exc.response.status_code}** — {detail}")
            except httpx.ConnectError:
                st.error("Cannot reach the API. Is the FastAPI server running on port 8080?")
            except Exception as exc:
                st.error(f"Unexpected error: {exc}")
