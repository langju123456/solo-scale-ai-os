from pathlib import Path

from buildlog.config import load_settings
from buildlog.external_artifact import stage_soloscale_artifact
from buildlog.publication_content import FinalArtifactResolver
from buildlog.sqlalchemy_repository import SQLAlchemyRunRepository


def test_stage_soloscale_artifact_creates_one_publishable_immutable_copy(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "02_linkedin.md"
    source.write_text("Grounded external content.\n", encoding="utf-8")
    monkeypatch.setenv("BUILDLOG_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("BUILDLOG_DATABASE_URL", f"sqlite:///{tmp_path / 'buildlog.db'}")
    settings = load_settings(tmp_path)
    repository = SQLAlchemyRunRepository(settings.database_url)
    repository.initialize()

    run_id = stage_soloscale_artifact(
        settings=settings,
        repository=repository,
        source_path=source,
        source_run_id="content-20260812T010203Z-abcdef1234",
        channel="linkedin",
    )

    assert stage_soloscale_artifact(
        settings=settings,
        repository=repository,
        source_path=source,
        source_run_id="content-20260812T010203Z-abcdef1234",
        channel="linkedin",
    ) == run_id
    artifact = FinalArtifactResolver(repository, allowed_root=settings.runs_dir).resolve(run_id)
    assert artifact.content == "Grounded external content."
    assert (settings.runs_dir / run_id / "00_soloscale_source.json").is_file()
