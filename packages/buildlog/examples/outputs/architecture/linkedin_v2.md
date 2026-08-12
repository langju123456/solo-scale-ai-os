When I realized the filesystem traces couldn’t track relationships between runs, I faced a choice: over-engineer v0.1 with a full backend or find a way to preserve both queryability and readability. The initial state was file-only traces that were easy to inspect but lacked structural relationships. Keeping everything file-only would make later trace comparison impossible, while storing only artifact content in SQLite would lose metadata about relationships. Instead, I chose hybrid persistence—keeping original files for human inspection while using SQLite to store hashes, evaluation scores, and relationships.  

SQLite provided ACID-compliant metadata storage for relationships, while JSON/Markdown artifacts remained human-readable. Separating domain records from SQLAlchemy models ensured business logic stayed decoupled from ORM implementation details. For example, I defined persistence-facing domain records without SQLAlchemy imports, then mapped them to tables via SQLAlchemy 2.0. This avoided polluting pipeline logic with ORM-specific classes or session behavior.  

A minimal synchronous repository protocol avoided unnecessary complexity for a single-backend implementation. The SQLite schema includes projects, iterations, runs, artifacts, evaluations, and prompt_versions tables. Run rows reference exact prompt version records, and SHA-256 hashes of artifacts and prompts matched their files.  

The trade-off was ensuring filesystem writes and database entries stayed consistent without overcomplicating the architecture for v0.1. Creating tables on startup works for now but lacks long-term migration strategies. The repository interface remains narrow, avoiding speculative abstractions like dashboards or analytics.  

The lesson is that architecture constraints are preserved when decisions are documented before implementation, and hybrid systems can serve both human and machine needs when each storage mechanism has a clear responsibility.  

#Python #LLM #SoftwareEngineering

---

Human review required before publishing: check for secrets, API keys, employer-confidential information, customer data, private repository details, and unpublished business information.
