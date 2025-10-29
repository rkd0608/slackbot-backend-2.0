"""
Migrate Pinecone data from global namespaces to team-specific namespaces

OLD STRUCTURE:
- __default__ namespace: All team messages mixed
- code namespace: All team code snippets mixed

NEW STRUCTURE:
- {team_id}:messages namespace: Team-specific messages
- {team_id}:code namespace: Team-specific code snippets
- {team_id}:files namespace: Team-specific files

Usage:
    python -m scripts.migrate_pinecone_to_team_namespaces --dry-run
    python -m scripts.migrate_pinecone_to_team_namespaces --execute
"""
import asyncio
import argparse
from typing import Dict, List
from pinecone import Pinecone
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import db_manager
from app.core.logging import get_logger
from app.core.vector_db import VectorDBManager
from app.models.message import Message
from app.models.file import File
from app.models.code_snippet import CodeSnippet

logger = get_logger(__name__)


class PineconeNamespaceMigration:
    """Handles migration from global to team-specific namespaces"""

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.pc = Pinecone(api_key=settings.pinecone_api_key)
        self.index = self.pc.Index(settings.pinecone_index_name)
        self.stats = {
            "messages": {"total": 0, "migrated": 0, "skipped": 0, "errors": 0},
            "code": {"total": 0, "migrated": 0, "skipped": 0, "errors": 0},
            "files": {"total": 0, "migrated": 0, "skipped": 0, "errors": 0}
        }

    def migrate_messages(self, db: Session, batch_size: int = 100):
        """Migrate message embeddings from default namespace to team namespaces"""
        print("\n" + "=" * 60)
        print("MIGRATING MESSAGE EMBEDDINGS")
        print("=" * 60)

        # Get all teams
        teams = db.query(Message.team_id).distinct().all()
        team_ids = [t[0] for t in teams if t[0]]

        print(f"Found {len(team_ids)} teams with messages")

        for team_id in team_ids:
            print(f"\n📦 Processing team: {team_id}")

            # Get all messages with vector_id for this team
            messages = db.query(Message).filter(
                Message.team_id == team_id,
                Message.vector_id.isnot(None)
            ).all()

            self.stats["messages"]["total"] += len(messages)
            print(f"  Found {len(messages)} messages to migrate")

            if self.dry_run:
                print(f"  [DRY RUN] Would migrate {len(messages)} vectors to namespace: {team_id}:messages")
                self.stats["messages"]["migrated"] += len(messages)
                continue

            # Process in batches
            for i in range(0, len(messages), batch_size):
                batch = messages[i:i + batch_size]
                vector_ids = [msg.vector_id for msg in batch]

                try:
                    # Fetch vectors from default namespace
                    fetch_response = self.index.fetch(
                        ids=vector_ids,
                        namespace=""  # Default namespace
                    )

                    vectors_to_upsert = []
                    for vec_id, vec_data in fetch_response.vectors.items():
                        vectors_to_upsert.append({
                            "id": vec_id,
                            "values": vec_data.values,
                            "metadata": vec_data.metadata
                        })

                    if vectors_to_upsert:
                        # Upsert to new namespace
                        new_namespace = f"{team_id}:messages"
                        self.index.upsert(
                            vectors=[(v["id"], v["values"], v["metadata"]) for v in vectors_to_upsert],
                            namespace=new_namespace
                        )

                        self.stats["messages"]["migrated"] += len(vectors_to_upsert)
                        print(f"  ✅ Migrated batch {i//batch_size + 1}: {len(vectors_to_upsert)} vectors")

                except Exception as e:
                    self.stats["messages"]["errors"] += len(batch)
                    print(f"  ❌ Error migrating batch: {str(e)}")

            print(f"  ✅ Completed team {team_id}")

    def migrate_code_snippets(self, db: Session, batch_size: int = 100):
        """Migrate code snippet embeddings from code namespace to team namespaces"""
        print("\n" + "=" * 60)
        print("MIGRATING CODE SNIPPET EMBEDDINGS")
        print("=" * 60)

        # Get all teams by joining with messages
        result = db.execute(text("""
            SELECT DISTINCT m.team_id
            FROM code_snippets cs
            JOIN messages m ON cs.message_id = m.message_id
            WHERE cs.vector_id IS NOT NULL
        """))
        team_ids = [row[0] for row in result if row[0]]

        print(f"Found {len(team_ids)} teams with code snippets")

        for team_id in team_ids:
            print(f"\n📦 Processing team: {team_id}")

            # Get all code snippets for this team
            snippets = db.execute(text("""
                SELECT cs.vector_id
                FROM code_snippets cs
                JOIN messages m ON cs.message_id = m.message_id
                WHERE m.team_id = :team_id
                AND cs.vector_id IS NOT NULL
            """), {"team_id": team_id}).fetchall()

            snippet_vector_ids = [s[0] for s in snippets]
            self.stats["code"]["total"] += len(snippet_vector_ids)
            print(f"  Found {len(snippet_vector_ids)} code snippets to migrate")

            if self.dry_run:
                print(f"  [DRY RUN] Would migrate {len(snippet_vector_ids)} vectors to namespace: {team_id}:code")
                self.stats["code"]["migrated"] += len(snippet_vector_ids)
                continue

            # Process in batches
            for i in range(0, len(snippet_vector_ids), batch_size):
                batch_ids = snippet_vector_ids[i:i + batch_size]

                try:
                    # Fetch vectors from code namespace
                    fetch_response = self.index.fetch(
                        ids=batch_ids,
                        namespace="code"  # Old code namespace
                    )

                    vectors_to_upsert = []
                    for vec_id, vec_data in fetch_response.vectors.items():
                        # Add team_id to metadata if not present
                        metadata = vec_data.metadata or {}
                        metadata["team_id"] = team_id

                        vectors_to_upsert.append({
                            "id": vec_id,
                            "values": vec_data.values,
                            "metadata": metadata
                        })

                    if vectors_to_upsert:
                        # Upsert to new namespace
                        new_namespace = f"{team_id}:code"
                        self.index.upsert(
                            vectors=[(v["id"], v["values"], v["metadata"]) for v in vectors_to_upsert],
                            namespace=new_namespace
                        )

                        self.stats["code"]["migrated"] += len(vectors_to_upsert)
                        print(f"  ✅ Migrated batch {i//batch_size + 1}: {len(vectors_to_upsert)} vectors")

                except Exception as e:
                    self.stats["code"]["errors"] += len(batch_ids)
                    print(f"  ❌ Error migrating batch: {str(e)}")

            print(f"  ✅ Completed team {team_id}")

    def migrate_files(self, db: Session, batch_size: int = 100):
        """Migrate file embeddings to team namespaces"""
        print("\n" + "=" * 60)
        print("MIGRATING FILE EMBEDDINGS")
        print("=" * 60)

        # Get all teams by joining with channels
        result = db.execute(text("""
            SELECT DISTINCT c.team_id
            FROM files f
            JOIN channels c ON f.channel_id = c.channel_id
            WHERE f.vector_id IS NOT NULL
        """))
        team_ids = [row[0] for row in result if row[0]]

        print(f"Found {len(team_ids)} teams with files")

        for team_id in team_ids:
            print(f"\n📦 Processing team: {team_id}")

            # Get all files for this team
            files = db.execute(text("""
                SELECT f.vector_id
                FROM files f
                JOIN channels c ON f.channel_id = c.channel_id
                WHERE c.team_id = :team_id
                AND f.vector_id IS NOT NULL
            """), {"team_id": team_id}).fetchall()

            file_vector_ids = [f[0] for f in files]
            self.stats["files"]["total"] += len(file_vector_ids)
            print(f"  Found {len(file_vector_ids)} files to migrate")

            if self.dry_run:
                print(f"  [DRY RUN] Would migrate {len(file_vector_ids)} vectors to namespace: {team_id}:files")
                self.stats["files"]["migrated"] += len(file_vector_ids)
                continue

            # Process in batches
            for i in range(0, len(file_vector_ids), batch_size):
                batch_ids = file_vector_ids[i:i + batch_size]

                try:
                    # Fetch vectors from default namespace
                    fetch_response = self.index.fetch(
                        ids=batch_ids,
                        namespace=""  # Default namespace
                    )

                    vectors_to_upsert = []
                    for vec_id, vec_data in fetch_response.vectors.items():
                        # Add team_id to metadata if not present
                        metadata = vec_data.metadata or {}
                        metadata["team_id"] = team_id

                        vectors_to_upsert.append({
                            "id": vec_id,
                            "values": vec_data.values,
                            "metadata": metadata
                        })

                    if vectors_to_upsert:
                        # Upsert to new namespace
                        new_namespace = f"{team_id}:files"
                        self.index.upsert(
                            vectors=[(v["id"], v["values"], v["metadata"]) for v in vectors_to_upsert],
                            namespace=new_namespace
                        )

                        self.stats["files"]["migrated"] += len(vectors_to_upsert)
                        print(f"  ✅ Migrated batch {i//batch_size + 1}: {len(vectors_to_upsert)} vectors")

                except Exception as e:
                    self.stats["files"]["errors"] += len(batch_ids)
                    print(f"  ❌ Error migrating batch: {str(e)}")

            print(f"  ✅ Completed team {team_id}")

    def print_summary(self):
        """Print migration summary"""
        print("\n" + "=" * 60)
        print("MIGRATION SUMMARY")
        print("=" * 60)

        for content_type, stats in self.stats.items():
            print(f"\n{content_type.upper()}:")
            print(f"  Total: {stats['total']}")
            print(f"  Migrated: {stats['migrated']}")
            print(f"  Skipped: {stats['skipped']}")
            print(f"  Errors: {stats['errors']}")

        total_migrated = sum(s["migrated"] for s in self.stats.values())
        total_errors = sum(s["errors"] for s in self.stats.values())

        print(f"\n{'='*60}")
        print(f"TOTAL MIGRATED: {total_migrated}")
        print(f"TOTAL ERRORS: {total_errors}")

        if self.dry_run:
            print("\n⚠️  THIS WAS A DRY RUN - No data was actually migrated")
            print("Run with --execute to perform the actual migration")


def main():
    parser = argparse.ArgumentParser(description="Migrate Pinecone to team-specific namespaces")
    parser.add_argument("--dry-run", action="store_true", help="Preview migration without making changes")
    parser.add_argument("--execute", action="store_true", help="Execute the migration")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for migration")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("Error: Must specify either --dry-run or --execute")
        return

    print("\n🚀 PINECONE NAMESPACE MIGRATION")
    print("=" * 60)
    print(f"Mode: {'DRY RUN' if args.dry_run else 'EXECUTE'}")
    print(f"Index: {settings.pinecone_index_name}")
    print(f"Batch Size: {args.batch_size}")
    print("=" * 60)

    if args.execute:
        confirm = input("\n⚠️  This will modify your Pinecone index. Continue? (yes/no): ")
        if confirm.lower() != "yes":
            print("Migration cancelled")
            return

    # Initialize database
    db_manager.initialize()
    db = db_manager.SessionLocal()

    try:
        migration = PineconeNamespaceMigration(dry_run=args.dry_run)

        # Run migrations
        migration.migrate_messages(db, batch_size=args.batch_size)
        migration.migrate_code_snippets(db, batch_size=args.batch_size)
        migration.migrate_files(db, batch_size=args.batch_size)

        # Print summary
        migration.print_summary()

        print("\n✅ Migration completed successfully!")

    except Exception as e:
        print(f"\n❌ Migration failed: {str(e)}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
