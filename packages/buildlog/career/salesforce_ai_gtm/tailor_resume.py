from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape


SOURCE = Path("/Users/ju.l/Downloads/Lang_Ju_Resume_Salesforce_AI_GTM_Developer.docx")
OUTPUT = Path("/Users/ju.l/Documents/AI TEAM/career/salesforce_ai_gtm/Lang_Ju_Resume_Salesforce_AI_GTM_Developer_Screening_Optimized.docx")
EXPECTED_SHA256 = "5713ef79b9faa515f10befdffb743f2e4ce2a352470562b544b9122566bb0461"
CORE_NAMESPACES = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcmitype": "http://purl.org/dc/dcmitype/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

REPLACEMENTS = {
    "AI Product Engineer | Agentic Workflows | Internal Tools & Cloud Applications":
        "AI Product Engineer | Full-Stack AI Products | GTM Automation",
    "AI product engineer with an M.S. in Information Systems Management and hands-on experience building LLM and agentic applications, internal workflow automation, API integrations, and cloud-deployment prototypes. Designed evidence-grounded systems with structured outputs, evaluation, observability, and human review. Combines AI-native development with stakeholder-facing experience translating ambiguous business needs into working software.":
        "AI product engineer with an M.S. in Information Systems Management building full-stack internal AI applications, GTM workflows, and SaaS integrations. Uses Python, FastAPI, PostgreSQL, JavaScript, Azure, Docker, and LLM evaluation to turn ambiguous stakeholder needs into reliable software.",
    "BuildLog | Evidence-to-Artifact AI Engineering Workflow":
        "BuildLog | Full-Stack Internal AI Product for GTM Workflows",
    "Built a typed Python workflow that transforms reviewed engineering evidence into validated content artifacts using LiteLLM, Pydantic, versioned prompts, and deterministic review policy.":
        "Independently built a FastAPI/JavaScript full-stack internal AI product with Pydantic and SQLAlchemy/PostgreSQL, automating a five-stage evidence-to-GTM workflow; persisted 60+ versioned artifacts and averaged 8.97/10 across 8 evaluated runs.",
    "Designed SQLite + filesystem persistence, prompt/model/artifact lineage, and step-level observability across planning, drafting, evaluation, revision, and visual package generation.":
        "Designed an idempotent database-backed job workflow with transactional claims, bounded retries, Alembic migrations, request IDs, health checks, Prometheus metrics, and Blob artifact mirroring; validated 500 ASGI requests at 0% errors and 34.6 ms p95.",
    "Integrated LinkedIn and X OAuth/API adapters with exact preview, human approval, duplicate suppression, no automatic retry, and durable receipts; validated real smoke publications with 237 automated tests.":
        "Integrated LinkedIn and X with OAuth 2.0/PKCE and live HTTP 201 publications; containerized the service and implemented Azure Container Apps, PostgreSQL/Blob, and GitHub Actions CI/CD architecture, backed by 270 tests.",
    "Led a six-person team through 14 stakeholder meetings to assess supply-chain data, integration, and governance challenges for a food manufacturing enterprise.":
        "Led a six-person team and partnered with client leaders through 14 stakeholder meetings to define supply-chain data, integration, and governance problems for a food manufacturing enterprise.",
    "Compared EDI, API, supplier collaboration, and master-data approaches, translating findings into an iterative transformation roadmap; directed 100+ pages of presentation material and 100+ pages of research for client management.":
        "Translated EDI, API, supplier collaboration, and master-data findings into an iterative transformation roadmap; directed 100+ pages each of research and presentation material for client management.",
    "Built a ReAct research agent for multi-step retrieval, tool/API use, memory, and Markdown/PDF reporting with LangChain and ChromaDB; earned the Hugging Face Agents Course Certificate of Excellence through smolagents, LlamaIndex, LangGraph, agentic RAG, GAIA evaluation, and observability.":
        "Prototyped a ReAct research agent for multi-step retrieval, tool/API use, memory, and Markdown/PDF reporting with LangChain and ChromaDB; earned the Hugging Face Agents Course Certificate of Excellence.",
    "Programming & Applications: Python, SQL, JavaScript, Java, HTML/CSS, Streamlit, REST APIs":
        "Full-Stack & Data: Python, FastAPI, SQL/PostgreSQL, JavaScript, HTML/CSS, REST APIs, SQLAlchemy, Alembic",
    "AI & Agents: LiteLLM, OpenAI APIs, Ollama/Qwen, ReAct, tool/function calling, agentic RAG, memory, LangGraph, LlamaIndex, smolagents":
        "AI & AI-Native Development: LiteLLM, OpenAI APIs, Claude Code, Codex, Cursor, ReAct, tool/function calling, agentic RAG",
    "Reliability & Data: Pydantic, structured outputs, prompt/version management, evaluation, observability, human review, SQLAlchemy, SQLite, ChromaDB":
        "Reliability: Pydantic, idempotency, durable jobs, structured outputs, evaluation, Prometheus, structured logging, pytest",
    "Cloud & Delivery: Microsoft Azure, Docker, Kubernetes, containerized deployment, OAuth 2.0/PKCE, Git/GitHub, pytest, Codex, Claude Code, Cursor":
        "Cloud & Delivery: Azure Container Apps, Blob Storage, Managed Identity, Docker, Kubernetes, GitHub Actions CI/CD, OAuth 2.0/PKCE",
    "Supported 2-3 overseas rail-transit bids and technical projects, coordinating requirements and delivery across customers, engineering, production, and sales.":
        "Partnered with sales, engineering, production, and customer teams on 2-3 overseas rail-transit bids and technical projects, translating requirements into proposals and coordinated delivery plans.",
    "Analyzed CRM/ERP workflows with Python and translated customer and operational needs into proposals, technical documentation, project updates, and cross-functional action items.":
        "Analyzed CRM/ERP workflows with Python, surfaced process and data gaps, and converted ambiguous customer and operational needs into technical documentation, project updates, and cross-functional actions.",
    "Used Python and JMP to analyze user behavior, survey data, and customer segments, translating findings into product and marketing recommendations.":
        "Analyzed user behavior, survey data, and customer segments with Python and JMP, translating findings into product and marketing recommendations for non-technical stakeholders.",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_core_properties(payload: bytes) -> bytes:
    root = ElementTree.fromstring(payload)
    for prefix, uri in CORE_NAMESPACES.items():
        ElementTree.register_namespace(prefix, uri)
    creator = root.find("dc:creator", CORE_NAMESPACES)
    modified_by = root.find("cp:lastModifiedBy", CORE_NAMESPACES)
    revision = root.find("cp:revision", CORE_NAMESPACES)
    if creator is not None:
        creator.text = "Lang Ju"
    if modified_by is not None:
        modified_by.text = "Lang Ju"
    if revision is not None:
        revision.text = "1"
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def main() -> None:
    actual_sha256 = sha256(SOURCE)
    if actual_sha256 != EXPECTED_SHA256:
        raise SystemExit(f"Reference changed: expected {EXPECTED_SHA256}, got {actual_sha256}")

    with zipfile.ZipFile(SOURCE, "r") as source_zip:
        document_xml = source_zip.read("word/document.xml")
        for old_text, new_text in REPLACEMENTS.items():
            old_bytes = escape(old_text).encode("utf-8")
            new_bytes = escape(new_text).encode("utf-8")
            count = document_xml.count(old_bytes)
            if count != 1:
                raise SystemExit(f"Expected exactly one source slot, found {count}: {old_text[:80]}")
            document_xml = document_xml.replace(old_bytes, new_bytes, 1)

        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(OUTPUT, "w") as output_zip:
            for item in source_zip.infolist():
                payload = document_xml if item.filename == "word/document.xml" else source_zip.read(item.filename)
                if item.filename == "docProps/core.xml":
                    payload = sanitize_core_properties(payload)
                output_zip.writestr(item, payload)

    print(OUTPUT)


if __name__ == "__main__":
    main()
