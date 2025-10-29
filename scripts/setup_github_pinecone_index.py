"""Script to create and configure Pinecone index for GitHub code embeddings"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pinecone import Pinecone, ServerlessSpec
from app.core.config import settings
from app.core.logging import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)


def setup_github_index():
    """
    Create Pinecone index for GitHub code embeddings

    Index Configuration:
    - Name: github-code-embeddings (configurable via PINECONE_GITHUB_INDEX_NAME)
    - Dimension: 1536 (matches voyage-code-2 and text-embedding-3-large)
    - Metric: cosine (standard for code similarity)
    - Serverless: AWS us-east-1

    Namespaces Strategy:
    - {team_id}:code - Code structure embeddings (voyage-code-2)
    - {team_id}:semantic - Semantic context embeddings (OpenAI)
    """
    try:
        logger.info("github_index_setup_started", index_name=settings.pinecone_github_index_name)

        # Initialize Pinecone
        pc = Pinecone(api_key=settings.pinecone_api_key)

        # Check if index already exists
        existing_indexes = pc.list_indexes()
        index_names = [idx.name for idx in existing_indexes]

        if settings.pinecone_github_index_name in index_names:
            logger.info(
                "github_index_already_exists",
                index_name=settings.pinecone_github_index_name
            )
            print(f"✅ Index '{settings.pinecone_github_index_name}' already exists")

            # Get index info
            index = pc.Index(settings.pinecone_github_index_name)
            stats = index.describe_index_stats()

            print(f"\nIndex Stats:")
            print(f"  - Total vectors: {stats.total_vector_count}")
            print(f"  - Dimension: {stats.dimension}")
            print(f"  - Namespaces: {len(stats.namespaces)}")

            if stats.namespaces:
                print(f"\n  Namespace breakdown:")
                for ns, ns_stats in stats.namespaces.items():
                    print(f"    - {ns}: {ns_stats.vector_count} vectors")

            return

        # Create new index
        logger.info(
            "creating_github_index",
            index_name=settings.pinecone_github_index_name,
            dimension=1536
        )

        print(f"\nCreating Pinecone index: {settings.pinecone_github_index_name}")
        print(f"  - Dimension: 1536 (voyage-code-2 / text-embedding-3-large)")
        print(f"  - Metric: cosine")
        print(f"  - Cloud: AWS")
        print(f"  - Region: {settings.pinecone_environment}")

        pc.create_index(
            name=settings.pinecone_github_index_name,
            dimension=1536,
            metric="cosine",
            spec=ServerlessSpec(
                cloud="aws",
                region=settings.pinecone_environment
            )
        )

        logger.info(
            "github_index_created",
            index_name=settings.pinecone_github_index_name
        )

        print(f"\n✅ Successfully created index: {settings.pinecone_github_index_name}")
        print(f"\nNamespace Strategy:")
        print(f"  - {{team_id}}:code      → Code structure embeddings (voyage-code-2)")
        print(f"  - {{team_id}}:semantic  → Semantic context embeddings (OpenAI)")
        print(f"\nExample namespace: T0123456789:code")

        # Wait for index to be ready
        print(f"\n⏳ Waiting for index to be ready...")
        import time
        while not pc.describe_index(settings.pinecone_github_index_name).status['ready']:
            time.sleep(1)

        print(f"✅ Index is ready for use!")

        # Verify index
        index = pc.Index(settings.pinecone_github_index_name)
        stats = index.describe_index_stats()

        print(f"\nIndex Stats:")
        print(f"  - Total vectors: {stats.total_vector_count}")
        print(f"  - Dimension: {stats.dimension}")

    except Exception as e:
        logger.error("github_index_setup_failed", error=str(e))
        print(f"\n❌ Error setting up GitHub index: {str(e)}")
        raise


def delete_github_index():
    """Delete GitHub Pinecone index (use with caution!)"""
    try:
        pc = Pinecone(api_key=settings.pinecone_api_key)

        existing_indexes = pc.list_indexes()
        index_names = [idx.name for idx in existing_indexes]

        if settings.pinecone_github_index_name not in index_names:
            print(f"Index '{settings.pinecone_github_index_name}' does not exist")
            return

        # Confirm deletion
        confirm = input(f"\n⚠️  Delete index '{settings.pinecone_github_index_name}'? This cannot be undone! (yes/no): ")

        if confirm.lower() != "yes":
            print("Deletion cancelled")
            return

        print(f"Deleting index: {settings.pinecone_github_index_name}")
        pc.delete_index(settings.pinecone_github_index_name)

        logger.info("github_index_deleted", index_name=settings.pinecone_github_index_name)
        print(f"✅ Index deleted successfully")

    except Exception as e:
        logger.error("github_index_deletion_failed", error=str(e))
        print(f"❌ Error deleting index: {str(e)}")
        raise


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Manage GitHub Pinecone index")
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the GitHub index (use with caution!)"
    )

    args = parser.parse_args()

    if args.delete:
        delete_github_index()
    else:
        setup_github_index()
