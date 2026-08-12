**How to maintain queryable relationships between AI pipeline steps without sacrificing the simplicity of file-based traces?**  

BuildLog v0.1’s file-only traces worked for basic validation but lacked structured relationships between projects, iterations, and artifacts. This made comparing runs or tracking evaluation history cumbersome. To address this, we adopted a hybrid persistence model: SQLite for metadata and relationships, while retaining JSON/Markdown files for human readability.  

The key was avoiding over-engineering. We used SQLite via SQLAlchemy 2.0 to store project, iteration, run, artifact, evaluation, and prompt version data. Domain records were decoupled from SQLAlchemy models to keep pipeline logic independent of ORM specifics. A minimal `RunRepository` protocol ensured persistence boundaries without speculative abstractions like async access or migrations.  

We prioritized consistency: filesystem artifacts (plans, drafts, evaluations) remained untouched, while SQLite tracked relationships (e.g., artifact paths, SHA-256 hashes, evaluation scores). Tests validated database creation, persistence, and integration with the pipeline. A real run using Ollama and Qwen3:8b confirmed the model’s viability.  

**Lessons learned:**  
- A database justifies itself by preserving product relationships, not by making a project look more engineered.  
- Hybrid persistence works when each storage layer has a clear responsibility: metadata for queries, files for debugging.  
- Repository boundaries protect domain logic from persistence details without overcomplicating the architecture.  
- Documenting decisions early avoids architectural drift.  

What trade-offs would you make to balance structured querying with maintainable traceability in your AI workflows? 🤔  
#AIEngineering #SoftwareDesign #LLMWorkflows #DataPersistence #EngineeringPractices

---

Human review required before publishing: check for secrets, API keys, employer-confidential information, customer data, private repository details, and unpublished business information.
