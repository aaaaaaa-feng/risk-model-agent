from __future__ import annotations

from dataclasses import dataclass

from app.agents.graph import RunEngine
from app.core.database import Database
from app.core.paths import AppPaths, get_paths
from app.evaluation.harness import EvaluationHarness
from app.notebooks.manager import NotebookManager
from app.services.archives import ArchiveService, BackupService
from app.services.artifacts import ArtifactService
from app.services.catalog import CatalogService
from app.services.conversations import ConversationService
from app.services.migration import LegacyMigrator
from app.services.pipeline import RunPipeline


@dataclass
class AppContext:
    paths: AppPaths
    database: Database
    catalog: CatalogService
    artifacts: ArtifactService
    notebooks: NotebookManager
    pipeline: RunPipeline
    engine: RunEngine
    archives: ArchiveService
    backups: BackupService
    conversations: ConversationService
    migration: LegacyMigrator
    evaluations: EvaluationHarness

    @classmethod
    def create(cls, paths: AppPaths | None = None) -> "AppContext":
        resolved = (paths or get_paths()).ensure()
        database = Database(paths=resolved)
        catalog = CatalogService(database, resolved)
        artifacts = ArtifactService(database, resolved, catalog)
        notebooks = NotebookManager(resolved)
        pipeline = RunPipeline(database, resolved, catalog, artifacts)
        engine = RunEngine(database, resolved, catalog, pipeline)
        archives = ArchiveService(database, resolved, catalog)
        backups = BackupService(database, resolved)
        conversations = ConversationService(database, resolved, catalog)
        migration = LegacyMigrator(database, resolved)
        evaluations = EvaluationHarness(resolved)
        return cls(
            resolved,
            database,
            catalog,
            artifacts,
            notebooks,
            pipeline,
            engine,
            archives,
            backups,
            conversations,
            migration,
            evaluations,
        )

    def shutdown(self) -> None:
        self.notebooks.shutdown_all()
        self.conversations.shutdown()
        self.engine.shutdown()
        self.evaluations.shutdown()
