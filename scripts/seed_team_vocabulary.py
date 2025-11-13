#!/usr/bin/env python3
"""
Seed Team Vocabulary with Common Abbreviations

This script seeds all active workspaces with common tech abbreviations.
Run this once after deploying the vocabulary learning feature.

Usage:
    python scripts/seed_team_vocabulary.py
    python scripts/seed_team_vocabulary.py --team-id T12345  # Seed specific team
"""
import asyncio
import argparse
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import db_manager
from app.services.team_vocabulary_service import TeamVocabularyService
from app.models.workspace import Workspace
from sqlalchemy import select

# Initialize database
db_manager.initialize()


async def seed_all_workspaces():
    """Seed vocabulary for all active workspaces"""
    print("=" * 80)
    print("SEEDING TEAM VOCABULARY FOR ALL WORKSPACES")
    print("=" * 80)

    async with db_manager.AsyncSessionLocal() as db:
        # Get all active workspaces
        result = await db.execute(
            select(Workspace).where(Workspace.is_active == "true")
        )
        workspaces = result.scalars().all()

        print(f"\nFound {len(workspaces)} active workspaces")

        total_seeded = 0

        for workspace in workspaces:
            print(f"\n{'-' * 80}")
            print(f"Workspace: {workspace.team_name} (ID: {workspace.team_id})")

            vocab_service = TeamVocabularyService(db)

            # Seed common abbreviations
            seeded_count = await vocab_service.seed_common_abbreviations(workspace.team_id)

            print(f"  ✓ Seeded {seeded_count} common abbreviations")
            total_seeded += seeded_count

        print(f"\n{'=' * 80}")
        print(f"SEEDING COMPLETE")
        print(f"{'=' * 80}")
        print(f"Total terms seeded: {total_seeded}")
        print(f"Workspaces processed: {len(workspaces)}")


async def seed_specific_team(team_id: str):
    """Seed vocabulary for a specific team"""
    print("=" * 80)
    print(f"SEEDING TEAM VOCABULARY FOR TEAM: {team_id}")
    print("=" * 80)

    async with db_manager.AsyncSessionLocal() as db:
        vocab_service = TeamVocabularyService(db)

        # Seed common abbreviations
        seeded_count = await vocab_service.seed_common_abbreviations(team_id)

        print(f"\n✓ Seeded {seeded_count} common abbreviations")

        # Show vocabulary
        vocab = await vocab_service.get_team_vocabulary(team_id, limit=30)
        print(f"\nTeam vocabulary ({len(vocab)} terms):")
        print(f"{'Term':<15} {'Canonical Form':<40} {'Confidence':<12} {'Type'}")
        print("-" * 80)

        for item in vocab:
            print(f"{item['term']:<15} {item['canonical_form']:<40} "
                  f"{item['confidence_score']:<12.2f} {item['term_type']}")


async def show_vocabulary_stats():
    """Show vocabulary learning statistics across all teams"""
    print("=" * 80)
    print("VOCABULARY LEARNING STATISTICS")
    print("=" * 80)

    async with db_manager.AsyncSessionLocal() as db:
        from app.models.team_vocabulary import TeamVocabulary

        # Get all teams with vocabulary
        result = await db.execute(
            select(TeamVocabulary.team_id).distinct()
        )
        team_ids = result.scalars().all()

        print(f"\nTeams with learned vocabulary: {len(team_ids)}")

        for team_id in team_ids:
            vocab_service = TeamVocabularyService(db)
            vocab = await vocab_service.get_team_vocabulary(team_id, limit=1000)

            print(f"\n{'-' * 80}")
            print(f"Team: {team_id}")
            print(f"  Total terms: {len(vocab)}")

            # Count by type
            by_type = {}
            total_usage = 0
            for item in vocab:
                term_type = item['term_type']
                by_type[term_type] = by_type.get(term_type, 0) + 1
                total_usage += item['occurrence_count']

            print(f"  By type:")
            for term_type, count in by_type.items():
                print(f"    {term_type}: {count}")

            print(f"  Total usage: {total_usage}")

            # Show top 5 most used
            top_terms = sorted(vocab, key=lambda x: x['occurrence_count'], reverse=True)[:5]
            print(f"  Top 5 most used:")
            for item in top_terms:
                print(f"    {item['term']} -> {item['canonical_form']} "
                      f"(used {item['occurrence_count']}x)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed team vocabulary with common abbreviations")
    parser.add_argument("--team-id", type=str, help="Seed specific team ID")
    parser.add_argument("--stats", action="store_true", help="Show vocabulary statistics")

    args = parser.parse_args()

    if args.stats:
        asyncio.run(show_vocabulary_stats())
    elif args.team_id:
        asyncio.run(seed_specific_team(args.team_id))
    else:
        asyncio.run(seed_all_workspaces())
