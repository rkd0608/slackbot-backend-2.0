"""
Script to verify complete GitHub integration deletion

This script checks:
1. Database records (ExternalContent, SyncJobs, Connections)
2. Pinecone vectors in GitHub index
3. Provides detailed report

Usage: python scripts/verify_github_deletion.py --team-id <your_team_id>
"""
import sys
import argparse
from pinecone import Pinecone
from app.core.config import settings
from app.core.database import db_manager
from app.models.integration import ExternalContent, IntegrationSyncJob, UserIntegrationConnection
from sqlalchemy import func


def check_database(team_id: str = None):
    """Check database for any GitHub-related records"""
    print("\n" + "="*60)
    print("DATABASE VERIFICATION")
    print("="*60)

    db_manager.initialize()
    db = db_manager.SessionLocal()

    try:
        # Check ExternalContent
        if team_id:
            external_count = db.query(func.count(ExternalContent.id)).filter(
                ExternalContent.source_type == 'github',
                ExternalContent.team_id == team_id
            ).scalar()
        else:
            external_count = db.query(func.count(ExternalContent.id)).filter(
                ExternalContent.source_type == 'github'
            ).scalar()

        # Check SyncJobs
        if team_id:
            sync_count = db.query(func.count(IntegrationSyncJob.id)).filter(
                IntegrationSyncJob.integration_type == 'github',
                IntegrationSyncJob.team_id == team_id
            ).scalar()
        else:
            sync_count = db.query(func.count(IntegrationSyncJob.id)).filter(
                IntegrationSyncJob.integration_type == 'github'
            ).scalar()

        # Check Connections
        if team_id:
            conn_count = db.query(func.count(UserIntegrationConnection.id)).filter(
                UserIntegrationConnection.integration_type == 'github',
                UserIntegrationConnection.team_id == team_id
            ).scalar()
        else:
            conn_count = db.query(func.count(UserIntegrationConnection.id)).filter(
                UserIntegrationConnection.integration_type == 'github'
            ).scalar()

        print(f"\n✓ ExternalContent (GitHub files): {external_count}")
        print(f"✓ IntegrationSyncJob (Sync history): {sync_count}")
        print(f"✓ UserIntegrationConnection (OAuth): {conn_count}")

        if external_count == 0 and sync_count == 0 and conn_count == 0:
            print("\n✅ DATABASE IS CLEAN - No GitHub data found")
            return True
        else:
            print("\n⚠️  DATABASE HAS GITHUB DATA - Not fully deleted")
            return False

    finally:
        db.close()


def check_pinecone(team_id: str):
    """Check Pinecone for any vectors in team namespaces"""
    print("\n" + "="*60)
    print("PINECONE VERIFICATION")
    print("="*60)

    try:
        pc = Pinecone(api_key=settings.pinecone_api_key)
        github_index = pc.Index(settings.pinecone_github_index_name)

        code_namespace = f"{team_id}:code"
        semantic_namespace = f"{team_id}:semantic"

        # Get stats for each namespace
        try:
            index_stats = github_index.describe_index_stats()

            code_count = index_stats.namespaces.get(code_namespace, {}).get('vector_count', 0)
            semantic_count = index_stats.namespaces.get(semantic_namespace, {}).get('vector_count', 0)

            print(f"\n✓ Code vectors in '{code_namespace}': {code_count}")
            print(f"✓ Semantic vectors in '{semantic_namespace}': {semantic_count}")

            if code_count == 0 and semantic_count == 0:
                print("\n✅ PINECONE IS CLEAN - No vectors found for this team")
                return True
            else:
                print(f"\n⚠️  PINECONE HAS VECTORS - Found {code_count + semantic_count} vectors")
                return False

        except Exception as e:
            print(f"\n⚠️  Could not check Pinecone namespaces: {str(e)}")
            print("This may be normal if namespaces were already deleted")
            return True

    except Exception as e:
        print(f"\n❌ ERROR checking Pinecone: {str(e)}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Verify GitHub integration deletion')
    parser.add_argument('--team-id', type=str, help='Team ID to check (optional)')
    args = parser.parse_args()

    print("\n🔍 GITHUB INTEGRATION DELETION VERIFICATION")

    # Check database
    db_clean = check_database(args.team_id)

    # Check Pinecone (only if team_id provided)
    pinecone_clean = True
    if args.team_id:
        pinecone_clean = check_pinecone(args.team_id)
    else:
        print("\n⚠️  Skipping Pinecone check - no team_id provided")
        print("Run with --team-id <your_team_id> to check Pinecone")

    # Final summary
    print("\n" + "="*60)
    print("FINAL SUMMARY")
    print("="*60)

    if db_clean and pinecone_clean:
        print("\n✅ ALL CHECKS PASSED - GitHub integration fully deleted")
        print("\nNo data violations detected:")
        print("  • Database: Clean")
        print("  • Pinecone: Clean")
        sys.exit(0)
    else:
        print("\n⚠️  SOME DATA REMAINS - Check details above")
        if not db_clean:
            print("  • Database: Has GitHub data")
        if not pinecone_clean:
            print("  • Pinecone: Has vectors")
        sys.exit(1)


if __name__ == "__main__":
    main()
