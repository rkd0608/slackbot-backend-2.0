"""Code extraction service orchestrating detection, parsing, and storage"""
from typing import List, Optional, Dict, Any
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.code_snippet import CodeSnippet
from app.models.message import Message
from app.services.code_detector import code_detector
from app.services.code_parser import code_parser
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class CodeExtractionService:
    """Orchestrates code detection, parsing, and storage"""

    async def extract_and_store_code(
        self,
        message_id: str,
        text: str,
        db: AsyncSession
    ) -> List[str]:
        """
        Main entry point: extract code from message and store snippets

        Flow:
        1. Detect code blocks
        2. For each block:
           - Identify language
           - Parse with AST (if supported)
           - Extract metadata
           - Store in code_snippets table
        3. Update message.code_snippet_count
        4. Return list of snippet IDs

        Returns:
            List of snippet_ids that were created
        """
        if not settings.enable_code_intelligence:
            logger.debug("code_intelligence_disabled")
            return []

        try:
            # Step 1: Detect code blocks
            code_blocks = code_detector.detect_code_blocks(text)

            if not code_blocks:
                logger.debug("no_code_blocks_found", message_id=message_id)
                return []

            # Filter blocks based on length
            valid_blocks = []
            for block in code_blocks:
                code = block["code"]
                line_count = len(code.split('\n'))

                # Skip tiny snippets
                if line_count < settings.min_code_snippet_lines:
                    continue

                # Skip overly long snippets
                if len(code) > settings.max_code_snippet_length:
                    logger.warning(
                        "code_snippet_too_long",
                        message_id=message_id,
                        length=len(code)
                    )
                    continue

                valid_blocks.append(block)

            if not valid_blocks:
                logger.debug("no_valid_code_blocks", message_id=message_id)
                return []

            # Step 2: Process each code block
            snippet_ids = []

            for idx, block in enumerate(valid_blocks):
                snippet_id = await self._process_code_block(
                    message_id=message_id,
                    block=block,
                    snippet_index=idx,
                    db=db
                )

                if snippet_id:
                    snippet_ids.append(snippet_id)

            # Step 3: Update message snippet count
            if snippet_ids:
                await self._update_message_snippet_count(
                    message_id,
                    len(snippet_ids),
                    db
                )

            logger.info(
                "code_extraction_completed",
                message_id=message_id,
                snippet_count=len(snippet_ids)
            )

            return snippet_ids

        except Exception as e:
            logger.error(
                "code_extraction_error",
                message_id=message_id,
                error=str(e)
            )
            return []

    async def _process_code_block(
        self,
        message_id: str,
        block: Dict,
        snippet_index: int,
        db: AsyncSession
    ) -> Optional[str]:
        """
        Process a single code block: parse and store

        Returns:
            snippet_id if successful, None otherwise
        """
        try:
            code = block["code"]
            language = block["language"]

            # Step 1: Parse code to extract metadata
            metadata = code_parser.parse(code, language)

            # Step 2: Determine code type
            code_type = self._determine_code_type(metadata, block)

            # Step 3: Enrich metadata with block info
            enriched_metadata = {
                **metadata,
                "is_inline": block["is_inline"],
                "fence_type": block["fence_type"],
                "language_hint": block.get("language_hint")
            }

            # Step 4: Store in database
            snippet_id = await self._store_snippet(
                message_id=message_id,
                snippet_index=snippet_index,
                code=code,
                language=language,
                code_type=code_type,
                metadata=enriched_metadata,
                db=db
            )

            return snippet_id

        except Exception as e:
            await db.rollback()
            logger.error(
                "code_block_processing_error",
                message_id=message_id,
                snippet_index=snippet_index,
                error=str(e)
            )
            return None

    def _determine_code_type(self, metadata: Dict, block: Dict) -> str:
        """
        Determine the type of code snippet

        Types:
        - function: Contains function definitions
        - class: Contains class definitions
        - import: Only import statements
        - inline: Small inline code snippet
        - snippet: General code snippet
        """
        # Check for inline
        if block.get("is_inline"):
            return "inline"

        # Check for class
        if metadata.get("classes") and len(metadata["classes"]) > 0:
            return "class"

        # Check for function
        if metadata.get("functions") and len(metadata["functions"]) > 0:
            return "function"

        # Check for imports only
        imports = metadata.get("imports", [])
        functions = metadata.get("functions", [])
        classes = metadata.get("classes", [])

        if imports and not functions and not classes:
            return "import"

        # Default
        return "snippet"

    async def _store_snippet(
        self,
        message_id: str,
        snippet_index: int,
        code: str,
        language: str,
        code_type: str,
        metadata: Dict,
        db: AsyncSession
    ) -> str:
        """Store code snippet in database"""
        # Generate unique snippet ID
        snippet_id = f"code_{message_id}_{snippet_index}_{uuid.uuid4().hex[:8]}"

        # Create snippet record
        snippet = CodeSnippet(
            snippet_id=snippet_id,
            message_id=message_id,
            snippet_index=snippet_index,
            language=language,
            code_type=code_type,
            raw_code=code,
            extracted_metadata=metadata
        )

        db.add(snippet)
        await db.commit()
        await db.refresh(snippet)

        logger.info(
            "code_snippet_stored",
            snippet_id=snippet_id,
            message_id=message_id,
            language=language,
            code_type=code_type
        )

        return snippet_id

    async def _update_message_snippet_count(
        self,
        message_id: str,
        count: int,
        db: AsyncSession
    ):
        """Update message's code_snippet_count field"""
        try:
            result = await db.execute(
                select(Message).where(Message.message_id == message_id)
            )
            message = result.scalar_one_or_none()

            if message:
                message.code_snippet_count = count
                await db.commit()

                logger.debug(
                    "message_snippet_count_updated",
                    message_id=message_id,
                    count=count
                )

        except Exception as e:
            logger.error(
                "update_snippet_count_error",
                message_id=message_id,
                error=str(e)
            )
            await db.rollback()

    async def get_code_snippets_for_message(
        self,
        message_id: str,
        db: AsyncSession
    ) -> List[CodeSnippet]:
        """Get all code snippets for a message"""
        result = await db.execute(
            select(CodeSnippet)
            .where(CodeSnippet.message_id == message_id)
            .order_by(CodeSnippet.snippet_index)
        )
        return result.scalars().all()


# Global instance
code_extraction_service = CodeExtractionService()