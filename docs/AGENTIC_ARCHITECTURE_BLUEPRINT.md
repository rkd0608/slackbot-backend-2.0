# Eunoia Agentic Architecture Blueprint

## Executive Summary

This blueprint outlines a greenfield agentic architecture for Eunoia, leveraging LangGraph for multi-agent orchestration and focusing on the core moat: a continuously learning, agentic knowledge graph that connects team knowledge across Slack, GitHub, Notion, and other tools.

**Key Advantages of Pre-Production Refactor**:
- No backward compatibility constraints
- Can build agent framework from day one
- Aggressive refactoring of orchestration layer
- Focus on autonomous decision-making from the start

**Timeline**: 12-16 weeks (reduced from 16-23 weeks due to no production constraints)
**Effort**: ~$162k-$216k

---

## 1. Architecture Vision

### 1.1 Core Principles

**Autonomy Over Orchestration**
- Agents make decisions based on context, not hardcoded steps
- Dynamic tool selection based on query characteristics
- Self-improving through continuous learning

**Knowledge Graph as Intelligence Layer**
- Not just storage - active reasoning component
- Autonomous entity linking and relationship discovery
- Temporal understanding (what was discussed when, by whom)

**Multi-Source Intelligence**
- Agents understand when to search Slack vs GitHub vs Notion
- Cross-source synthesis (connect GitHub PR to Slack discussion to Notion doc)
- Unified semantic understanding across sources

### 1.2 Agent Architecture Pattern

```
User Query
    ↓
Supervisor Agent (LangGraph StateGraph)
    ↓
├── Query Understanding Agent
│   ├── Intent Classification
│   ├── Entity Extraction
│   └── Temporal Context Detection
    ↓
├── Planning Agent
│   ├── Tool Selection (which sources to search)
│   ├── Search Strategy (vector, keyword, graph, hybrid)
│   └── Confidence Scoring
    ↓
├── Retrieval Agent Pool
│   ├── Slack Search Agent
│   ├── GitHub Search Agent
│   ├── File Search Agent
│   └── Knowledge Graph Agent
    ↓
├── Synthesis Agent
│   ├── Cross-Source Linking
│   ├── Entity Resolution
│   ├── Temporal Reasoning
│   └── Answer Generation
    ↓
├── Learning Agent (Background)
│   ├── Query Pattern Learning
│   ├── Entity Relationship Discovery
│   ├── Team Vocabulary Extraction
│   └── Knowledge Graph Expansion
    ↓
Formatted Response + Knowledge Graph Update
```

---

## 2. LangGraph State Machine Design

### 2.1 Main Query Processing State Machine

```python
# app/agents/state.py
from typing import TypedDict, Annotated, Sequence, List, Optional
from langchain_core.messages import BaseMessage
import operator

class AgentState(TypedDict):
    """State shared across all agents"""

    # Input
    query: str
    user_id: str
    team_id: str
    channel_id: str
    thread_ts: Optional[str]
    conversation_id: Optional[str]

    # Query Understanding
    intent: Optional[str]  # "search", "summarize", "connect", "explain"
    entities: List[dict]  # Extracted entities with types
    temporal_context: Optional[dict]  # Time references in query
    confidence: float

    # Planning
    selected_tools: List[str]  # Which tools/agents to use
    search_strategy: str  # "vector", "keyword", "graph", "hybrid"

    # Retrieval
    slack_results: List[dict]
    github_results: List[dict]
    file_results: List[dict]
    graph_results: List[dict]

    # Synthesis
    merged_results: List[dict]
    cross_source_links: List[dict]
    answer: Optional[str]
    citations: List[dict]

    # Learning (background)
    new_entities: List[dict]
    new_relationships: List[dict]
    query_patterns: List[dict]

    # Meta
    messages: Annotated[Sequence[BaseMessage], operator.add]
    next_agent: str
    iteration: int
    max_iterations: int
```

### 2.2 State Graph Flow

```python
# app/agents/orchestrator.py
from langgraph.graph import StateGraph, END
from app.agents.state import AgentState
from app.agents.query_understanding import QueryUnderstandingAgent
from app.agents.planning import PlanningAgent
from app.agents.retrieval import RetrievalAgentPool
from app.agents.synthesis import SynthesisAgent
from app.agents.learning import LearningAgent

def create_query_processing_graph():
    """Create main query processing state graph"""

    workflow = StateGraph(AgentState)

    # Add nodes (agents)
    workflow.add_node("understand", QueryUnderstandingAgent().process)
    workflow.add_node("plan", PlanningAgent().process)
    workflow.add_node("retrieve", RetrievalAgentPool().process)
    workflow.add_node("synthesize", SynthesisAgent().process)
    workflow.add_node("learn", LearningAgent().process)

    # Define edges (flow)
    workflow.set_entry_point("understand")

    workflow.add_edge("understand", "plan")
    workflow.add_edge("plan", "retrieve")
    workflow.add_edge("retrieve", "synthesize")

    # Conditional edge: if synthesis needs more data, loop back
    workflow.add_conditional_edges(
        "synthesize",
        should_continue_retrieval,
        {
            "continue": "plan",  # Need more data
            "finish": "learn"    # Answer is good
        }
    )

    workflow.add_edge("learn", END)

    return workflow.compile()

def should_continue_retrieval(state: AgentState) -> str:
    """Decide if we need more retrieval or can finish"""

    # If confidence too low and haven't hit max iterations, get more data
    if state["confidence"] < 0.7 and state["iteration"] < state["max_iterations"]:
        return "continue"

    return "finish"
```

---

## 3. Agent Definitions

### 3.1 Query Understanding Agent

**Purpose**: Understand what the user is asking for

**Responsibilities**:
- Intent classification (search, summarize, explain, connect)
- Entity extraction (people, projects, files, dates)
- Temporal context detection ("last week", "before the launch")
- Query expansion using team vocabulary

**Tools**:
- LLM for intent classification
- NER for entity extraction
- Team vocabulary service
- Conversation context service

**Implementation**:
```python
# app/agents/query_understanding.py
from langchain_core.prompts import ChatPromptTemplate
from app.services.team_vocabulary_service import team_vocabulary_service
from app.services.conversation_context_service import conversation_context_service

class QueryUnderstandingAgent:
    """Understands user query intent and context"""

    async def process(self, state: AgentState, db: AsyncSession) -> AgentState:
        """Process query to extract intent, entities, and context"""

        query = state["query"]
        user_id = state["user_id"]
        team_id = state["team_id"]
        conversation_id = state.get("conversation_id")

        # 1. Get conversation context if available
        context = None
        if conversation_id:
            context = await conversation_context_service.get_context(
                conversation_id, db
            )

        # 2. Expand query with team vocabulary
        expanded_query = await team_vocabulary_service.expand_query(
            query, team_id, db
        )

        # 3. Extract intent using LLM
        intent_prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a query understanding agent. Classify the user's intent.

            Possible intents:
            - search: User wants to find specific information
            - summarize: User wants a summary of discussions/activity
            - explain: User wants explanation of a concept/decision
            - connect: User wants to see relationships between entities

            Also extract:
            - Entities (people, projects, repos, files)
            - Temporal context (time references)
            - Confidence score (0-1)
            """),
            ("user", "Query: {query}\nExpanded: {expanded}\nContext: {context}")
        ])

        # LLM call with structured output
        result = await self.llm.invoke(
            intent_prompt.format_messages(
                query=query,
                expanded=expanded_query,
                context=context or "No prior context"
            )
        )

        # 4. Update state
        state["intent"] = result["intent"]
        state["entities"] = result["entities"]
        state["temporal_context"] = result["temporal_context"]
        state["confidence"] = result["confidence"]
        state["next_agent"] = "plan"

        return state
```

### 3.2 Planning Agent

**Purpose**: Decide which tools to use and how to search

**Responsibilities**:
- Select which sources to search (Slack, GitHub, files, graph)
- Determine search strategy (vector, keyword, graph traversal, hybrid)
- Estimate required depth (top-k, graph hops)
- Plan cross-source synthesis

**Decision Logic**:
```python
# app/agents/planning.py

class PlanningAgent:
    """Plans retrieval strategy based on query understanding"""

    async def process(self, state: AgentState, db: AsyncSession) -> AgentState:
        """Plan which tools to use and how"""

        intent = state["intent"]
        entities = state["entities"]
        temporal_context = state.get("temporal_context")

        selected_tools = []
        search_strategy = "hybrid"  # Default

        # Decision logic based on intent
        if intent == "search":
            # Always use Slack for recent discussions
            selected_tools.append("slack_search")

            # If query mentions code/PRs/issues, use GitHub
            if self._mentions_code_artifacts(entities):
                selected_tools.append("github_search")

            # If query mentions documents/files, use file search
            if self._mentions_documents(entities):
                selected_tools.append("file_search")

            # Always use graph for entity connections
            selected_tools.append("graph_search")

            # Strategy: Hybrid (vector + keyword + graph)
            search_strategy = "hybrid"

        elif intent == "connect":
            # Focus on graph traversal
            selected_tools = ["graph_search", "slack_search"]
            search_strategy = "graph"

        elif intent == "summarize":
            # Broad search, then summarize
            selected_tools = ["slack_search", "github_search", "file_search"]
            search_strategy = "temporal_vector"  # Time-weighted vector search

        elif intent == "explain":
            # Deep search in specific source
            selected_tools = self._determine_primary_source(entities)
            search_strategy = "vector"

        # Update state
        state["selected_tools"] = selected_tools
        state["search_strategy"] = search_strategy
        state["next_agent"] = "retrieve"

        return state

    def _mentions_code_artifacts(self, entities: List[dict]) -> bool:
        """Check if query mentions code-related entities"""
        code_keywords = {"pr", "pull request", "issue", "commit", "repo", "code"}
        entity_texts = {e["text"].lower() for e in entities}
        return bool(code_keywords & entity_texts)

    def _mentions_documents(self, entities: List[dict]) -> bool:
        """Check if query mentions documents"""
        doc_keywords = {"doc", "document", "file", "pdf", "sheet", "slide"}
        entity_texts = {e["text"].lower() for e in entities}
        return bool(doc_keywords & entity_texts)

    def _determine_primary_source(self, entities: List[dict]) -> List[str]:
        """Determine primary source based on entities"""
        # Logic to determine which source is most relevant
        if self._mentions_code_artifacts(entities):
            return ["github_search", "graph_search"]
        elif self._mentions_documents(entities):
            return ["file_search", "graph_search"]
        else:
            return ["slack_search", "graph_search"]
```

### 3.3 Retrieval Agent Pool

**Purpose**: Execute parallel searches across selected sources

**Responsibilities**:
- Execute searches in parallel using selected tools
- Apply permissions filtering
- Normalize results from different sources
- Graph traversal for entity connections

**Implementation**:
```python
# app/agents/retrieval.py
import asyncio
from app.services.retrieval_service import retrieval_service
from app.services.graph_enhanced_retrieval import graph_enhanced_retrieval
from app.services.permission_service import permission_service

class RetrievalAgentPool:
    """Coordinates parallel retrieval across sources"""

    async def process(self, state: AgentState, db: AsyncSession) -> AgentState:
        """Execute retrieval in parallel"""

        query = state["query"]
        user_id = state["user_id"]
        team_id = state["team_id"]
        selected_tools = state["selected_tools"]
        search_strategy = state["search_strategy"]
        entities = state["entities"]

        # Create tasks for parallel execution
        tasks = []

        if "slack_search" in selected_tools:
            tasks.append(self._search_slack(
                query, team_id, user_id, search_strategy, db
            ))

        if "github_search" in selected_tools:
            tasks.append(self._search_github(
                query, team_id, user_id, search_strategy, db
            ))

        if "file_search" in selected_tools:
            tasks.append(self._search_files(
                query, team_id, user_id, search_strategy, db
            ))

        if "graph_search" in selected_tools:
            tasks.append(self._search_graph(
                entities, team_id, db
            ))

        # Execute in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Update state with results
        state["slack_results"] = results[0] if len(results) > 0 else []
        state["github_results"] = results[1] if len(results) > 1 else []
        state["file_results"] = results[2] if len(results) > 2 else []
        state["graph_results"] = results[3] if len(results) > 3 else []
        state["next_agent"] = "synthesize"

        return state

    async def _search_slack(
        self,
        query: str,
        team_id: str,
        user_id: str,
        strategy: str,
        db: AsyncSession
    ) -> List[dict]:
        """Search Slack messages"""

        # Use existing retrieval service
        results = await retrieval_service.search_messages(
            query=query,
            team_id=team_id,
            top_k=10,
            db=db
        )

        # Apply permission filtering
        filtered = await permission_service.filter_results_by_permissions(
            team_id, user_id, results, db
        )

        return filtered

    async def _search_github(
        self,
        query: str,
        team_id: str,
        user_id: str,
        strategy: str,
        db: AsyncSession
    ) -> List[dict]:
        """Search GitHub (placeholder - implement when GitHub integration ready)"""
        # TODO: Implement GitHub search
        return []

    async def _search_files(
        self,
        query: str,
        team_id: str,
        user_id: str,
        strategy: str,
        db: AsyncSession
    ) -> List[dict]:
        """Search files"""

        results = await retrieval_service.search_files(
            query=query,
            team_id=team_id,
            top_k=5,
            db=db
        )

        # Apply permission filtering
        filtered = await permission_service.filter_results_by_permissions(
            team_id, user_id, results, db
        )

        return filtered

    async def _search_graph(
        self,
        entities: List[dict],
        team_id: str,
        db: AsyncSession
    ) -> List[dict]:
        """Search knowledge graph for entity relationships"""

        # Use graph-enhanced retrieval
        graph_connections = await graph_enhanced_retrieval.find_entity_connections(
            entities=entities,
            team_id=team_id,
            max_hops=2,
            db=db
        )

        return graph_connections
```

### 3.4 Synthesis Agent

**Purpose**: Combine results from multiple sources into coherent answer

**Responsibilities**:
- Merge results from different sources
- Resolve entities across sources (same person in Slack and GitHub)
- Temporal ordering (what happened when)
- Generate answer with citations
- Detect if more retrieval is needed

**Implementation**:
```python
# app/agents/synthesis.py
from app.services.cross_source_link_detector import cross_source_link_detector

class SynthesisAgent:
    """Synthesizes multi-source results into coherent answer"""

    async def process(self, state: AgentState, db: AsyncSession) -> AgentState:
        """Synthesize results and generate answer"""

        slack_results = state["slack_results"]
        github_results = state["github_results"]
        file_results = state["file_results"]
        graph_results = state["graph_results"]

        # 1. Merge and deduplicate results
        all_results = slack_results + github_results + file_results

        # 2. Detect cross-source links
        cross_links = await cross_source_link_detector.detect_links(
            all_results, state["team_id"], db
        )

        # 3. Entity resolution (same person across sources)
        resolved_entities = await self._resolve_entities(
            state["entities"], all_results, graph_results
        )

        # 4. Temporal ordering
        ordered_results = self._temporal_sort(all_results)

        # 5. Calculate confidence
        confidence = self._calculate_confidence(all_results, cross_links)

        # 6. Generate answer using LLM
        answer, citations = await self._generate_answer(
            query=state["query"],
            results=ordered_results,
            cross_links=cross_links,
            entities=resolved_entities,
            intent=state["intent"]
        )

        # 7. Update state
        state["merged_results"] = ordered_results
        state["cross_source_links"] = cross_links
        state["answer"] = answer
        state["citations"] = citations
        state["confidence"] = confidence
        state["iteration"] = state.get("iteration", 0) + 1

        # 8. Decide if we need more retrieval
        if confidence < 0.7 and state["iteration"] < state["max_iterations"]:
            state["next_agent"] = "plan"  # Loop back for more data
        else:
            state["next_agent"] = "learn"  # Finish and learn

        return state

    async def _resolve_entities(
        self,
        query_entities: List[dict],
        results: List[dict],
        graph_results: List[dict]
    ) -> List[dict]:
        """Resolve entities across sources"""

        # Use graph to find canonical entity representations
        # E.g., "rakshit" in Slack = "rkd0608" in GitHub

        resolved = []
        for entity in query_entities:
            canonical = await self._find_canonical_entity(
                entity, graph_results
            )
            resolved.append(canonical)

        return resolved

    def _temporal_sort(self, results: List[dict]) -> List[dict]:
        """Sort results by timestamp"""
        return sorted(
            results,
            key=lambda x: x.get("timestamp", ""),
            reverse=True
        )

    def _calculate_confidence(
        self,
        results: List[dict],
        cross_links: List[dict]
    ) -> float:
        """Calculate confidence in answer"""

        # Confidence based on:
        # - Number of results
        # - Cross-source links (higher confidence if multiple sources)
        # - Recency of results

        if not results:
            return 0.0

        base_confidence = min(len(results) / 10.0, 1.0)
        cross_source_boost = min(len(cross_links) * 0.1, 0.3)

        return min(base_confidence + cross_source_boost, 1.0)

    async def _generate_answer(
        self,
        query: str,
        results: List[dict],
        cross_links: List[dict],
        entities: List[dict],
        intent: str
    ) -> tuple[str, List[dict]]:
        """Generate answer using LLM"""

        # Format context for LLM
        context = self._format_context(results, cross_links)

        # Generate answer
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are Eunoia, an AI teammate that understands team knowledge.

            Generate a comprehensive answer based on the provided context.
            Include citations to sources.
            If information spans multiple sources, connect them.
            """),
            ("user", "Query: {query}\n\nContext: {context}\n\nIntent: {intent}")
        ])

        response = await self.llm.invoke(
            prompt.format_messages(
                query=query,
                context=context,
                intent=intent
            )
        )

        # Extract citations
        citations = self._extract_citations(results)

        return response.content, citations
```

### 3.5 Learning Agent (Background)

**Purpose**: Continuously improve from interactions

**Responsibilities**:
- Extract new entities and relationships
- Learn query patterns and rewrites
- Expand team vocabulary
- Update knowledge graph
- Store feedback signals

**Implementation**:
```python
# app/agents/learning.py
from app.services.query_rewrite_learner import query_rewrite_learner
from app.services.team_vocabulary_service import team_vocabulary_service
from app.models.cross_source_node import CrossSourceNode
from app.models.cross_source_edge import CrossSourceEdge

class LearningAgent:
    """Learns from interactions to improve future queries"""

    async def process(self, state: AgentState, db: AsyncSession) -> AgentState:
        """Learn from this interaction (runs in background)"""

        query = state["query"]
        team_id = state["team_id"]
        entities = state["entities"]
        merged_results = state["merged_results"]
        cross_links = state["cross_source_links"]

        # 1. Learn query patterns
        await query_rewrite_learner.learn_from_interaction(
            original_query=query,
            entities=entities,
            results=merged_results,
            team_id=team_id,
            db=db
        )

        # 2. Extract and store new team vocabulary
        new_terms = await self._extract_team_terms(
            query, merged_results
        )

        for term in new_terms:
            await team_vocabulary_service.add_term(
                term=term["text"],
                category=term["category"],
                team_id=team_id,
                db=db
            )

        # 3. Update knowledge graph with new entities
        new_nodes = await self._create_graph_nodes(
            entities, merged_results, team_id, db
        )

        # 4. Create new relationships
        new_edges = await self._create_graph_edges(
            cross_links, new_nodes, team_id, db
        )

        # 5. Update state
        state["new_entities"] = new_nodes
        state["new_relationships"] = new_edges
        state["next_agent"] = "END"

        return state

    async def _extract_team_terms(
        self,
        query: str,
        results: List[dict]
    ) -> List[dict]:
        """Extract team-specific terminology"""

        # Use NER and pattern matching to find team-specific terms
        # E.g., project names, internal tools, acronyms

        # Placeholder implementation
        return []

    async def _create_graph_nodes(
        self,
        entities: List[dict],
        results: List[dict],
        team_id: str,
        db: AsyncSession
    ) -> List[dict]:
        """Create knowledge graph nodes for entities"""

        new_nodes = []

        for entity in entities:
            # Check if node already exists
            existing = await db.execute(
                select(CrossSourceNode).where(
                    CrossSourceNode.entity_id == entity["id"],
                    CrossSourceNode.team_id == team_id
                )
            )

            if not existing.scalar_one_or_none():
                # Create new node
                node = CrossSourceNode(
                    entity_id=entity["id"],
                    entity_type=entity["type"],
                    name=entity["text"],
                    team_id=team_id,
                    metadata={"source": "query_extraction"}
                )
                db.add(node)
                new_nodes.append(entity)

        await db.commit()
        return new_nodes

    async def _create_graph_edges(
        self,
        cross_links: List[dict],
        new_nodes: List[dict],
        team_id: str,
        db: AsyncSession
    ) -> List[dict]:
        """Create relationships between entities"""

        new_edges = []

        for link in cross_links:
            # Create edge between linked entities
            edge = CrossSourceEdge(
                source_node_id=link["source_id"],
                target_node_id=link["target_id"],
                relationship_type=link["relationship"],
                team_id=team_id,
                confidence=link.get("confidence", 0.8),
                metadata={"detected_from": "cross_source_link"}
            )
            db.add(edge)
            new_edges.append(link)

        await db.commit()
        return new_edges
```

---

## 4. Tool Wrappers

### 4.1 Wrapping Existing Services

All existing services become tools that agents can use:

```python
# app/agents/tools.py
from langchain_core.tools import tool
from app.services.retrieval_service import retrieval_service
from app.services.query_service import query_service
from app.services.team_vocabulary_service import team_vocabulary_service
from app.services.graph_enhanced_retrieval import graph_enhanced_retrieval

@tool
async def search_slack_messages(query: str, team_id: str, top_k: int = 10) -> List[dict]:
    """Search Slack messages for relevant information"""
    async for db in db_manager.get_session():
        results = await retrieval_service.search_messages(
            query=query,
            team_id=team_id,
            top_k=top_k,
            db=db
        )
        return results

@tool
async def search_files(query: str, team_id: str, top_k: int = 5) -> List[dict]:
    """Search uploaded files for relevant information"""
    async for db in db_manager.get_session():
        results = await retrieval_service.search_files(
            query=query,
            team_id=team_id,
            top_k=top_k,
            db=db
        )
        return results

@tool
async def expand_query_with_vocabulary(query: str, team_id: str) -> str:
    """Expand query using team-specific vocabulary"""
    async for db in db_manager.get_session():
        expanded = await team_vocabulary_service.expand_query(
            query, team_id, db
        )
        return expanded

@tool
async def find_entity_connections(
    entities: List[dict],
    team_id: str,
    max_hops: int = 2
) -> List[dict]:
    """Find connections between entities in knowledge graph"""
    async for db in db_manager.get_session():
        connections = await graph_enhanced_retrieval.find_entity_connections(
            entities=entities,
            team_id=team_id,
            max_hops=max_hops,
            db=db
        )
        return connections

@tool
async def detect_cross_source_links(results: List[dict], team_id: str) -> List[dict]:
    """Detect links between different data sources"""
    async for db in db_manager.get_session():
        from app.services.cross_source_link_detector import cross_source_link_detector
        links = await cross_source_link_detector.detect_links(
            results, team_id, db
        )
        return links
```

### 4.2 Reusable Services (60-70% of codebase)

These services can be used as-is or with minimal modifications:

**Direct Reuse**:
- `retrieval_service.py` - Vector/keyword search
- `embedding_service.py` - Text embeddings
- `team_vocabulary_service.py` - Team terminology
- `cross_source_link_detector.py` - Link detection
- `graph_enhanced_retrieval.py` - Graph traversal
- `conversation_context_service.py` - Context tracking
- `permission_service.py` - Access control
- `file_processor.py` - File processing
- `message_processor.py` - Message storage
- `workspace_service.py` - Multi-tenancy

**Needs Refactoring**:
- `bot_interaction.py` - Replace with LangGraph orchestration
- `query_service.py` - Extract components into agent tools
- `query_analyzer.py` - Becomes part of QueryUnderstandingAgent
- `query_rewriter.py` - Used by PlanningAgent

---

## 5. Knowledge Graph Intelligence

### 5.1 Autonomous Entity Linking

The knowledge graph isn't just storage - it's an active reasoning component:

```python
# app/agents/knowledge_graph_agent.py

class KnowledgeGraphAgent:
    """Autonomous agent for knowledge graph operations"""

    async def auto_link_entities(
        self,
        team_id: str,
        db: AsyncSession
    ):
        """Automatically discover and create entity relationships"""

        # 1. Find unlinked entities
        unlinked = await self._find_unlinked_entities(team_id, db)

        # 2. Use LLM to infer relationships
        for entity_pair in unlinked:
            relationship = await self._infer_relationship(
                entity_pair[0], entity_pair[1], team_id, db
            )

            if relationship["confidence"] > 0.7:
                # Create edge
                edge = CrossSourceEdge(
                    source_node_id=entity_pair[0].id,
                    target_node_id=entity_pair[1].id,
                    relationship_type=relationship["type"],
                    confidence=relationship["confidence"],
                    team_id=team_id,
                    metadata={"auto_discovered": True}
                )
                db.add(edge)

        await db.commit()

    async def _infer_relationship(
        self,
        entity1: CrossSourceNode,
        entity2: CrossSourceNode,
        team_id: str,
        db: AsyncSession
    ) -> dict:
        """Use LLM to infer relationship between entities"""

        # Get context where both entities appear
        context = await self._get_co_occurrence_context(
            entity1, entity2, team_id, db
        )

        # Use LLM to infer relationship
        prompt = f"""Given these two entities and their co-occurrence context,
        infer their relationship.

        Entity 1: {entity1.name} ({entity1.entity_type})
        Entity 2: {entity2.name} ({entity2.entity_type})

        Context: {context}

        Return relationship type and confidence (0-1).
        """

        # LLM inference
        result = await self.llm.invoke(prompt)

        return {
            "type": result["relationship_type"],
            "confidence": result["confidence"]
        }
```

### 5.2 Temporal Understanding

Knowledge graph tracks temporal context:

```python
# app/models/cross_source_edge.py

class CrossSourceEdge(Base):
    """Extended with temporal tracking"""
    __tablename__ = "cross_source_edges"

    # ... existing fields ...

    # Temporal fields
    first_seen: datetime  # When relationship first appeared
    last_seen: datetime   # Last time relationship was observed
    frequency: int = 0    # How often this relationship appears

    # Temporal metadata
    temporal_context: dict = {}  # {"before_launch": True, "sprint_3": True}
```

This enables queries like:
- "What did we discuss about the API **before the launch**?"
- "Show me how our approach to authentication **evolved over time**"

---

## 6. Implementation Phases

### Phase 1: Foundation (Weeks 1-3)

**Goal**: Set up LangGraph infrastructure and first agent

**Tasks**:
1. Install LangGraph and dependencies
2. Create state definitions (`AgentState`)
3. Implement QueryUnderstandingAgent
4. Wrap existing services as tools
5. Create basic state graph (understand → plan → retrieve → synthesize)

**Deliverable**: Working single-agent prototype

**Files to Create**:
- `app/agents/__init__.py`
- `app/agents/state.py`
- `app/agents/orchestrator.py`
- `app/agents/query_understanding.py`
- `app/agents/tools.py`

**Files to Refactor**:
- `app/services/bot_interaction.py` (extract components)

### Phase 2: Multi-Agent Retrieval (Weeks 4-6)

**Goal**: Implement planning and retrieval agents

**Tasks**:
1. Implement PlanningAgent with decision logic
2. Implement RetrievalAgentPool for parallel search
3. Add conditional edges for adaptive retrieval
4. Integrate permission filtering
5. Test multi-source retrieval

**Deliverable**: Multi-source intelligent retrieval

**Files to Create**:
- `app/agents/planning.py`
- `app/agents/retrieval.py`

**Files to Modify**:
- `app/agents/orchestrator.py` (add conditional edges)

### Phase 3: Synthesis & Learning (Weeks 7-9)

**Goal**: Implement synthesis and continuous learning

**Tasks**:
1. Implement SynthesisAgent
2. Implement LearningAgent (background)
3. Add cross-source entity resolution
4. Integrate feedback loop
5. Test end-to-end flow

**Deliverable**: Complete agentic query system

**Files to Create**:
- `app/agents/synthesis.py`
- `app/agents/learning.py`

**Files to Modify**:
- `app/services/query_rewrite_learner.py` (integrate with learning agent)

### Phase 4: Knowledge Graph Intelligence (Weeks 10-12)

**Goal**: Make knowledge graph autonomous

**Tasks**:
1. Implement KnowledgeGraphAgent
2. Add autonomous entity linking
3. Implement temporal reasoning
4. Add relationship confidence scoring
5. Background graph expansion worker

**Deliverable**: Self-improving knowledge graph

**Files to Create**:
- `app/agents/knowledge_graph_agent.py`
- `app/workers/graph_expansion_worker.py`

**Files to Modify**:
- `app/models/cross_source_edge.py` (add temporal fields)
- `app/models/cross_source_node.py` (add confidence fields)

### Phase 5: Optimization & Testing (Weeks 13-16)

**Goal**: Production-ready system

**Tasks**:
1. Performance optimization (caching, batching)
2. Cost optimization (model selection, prompt engineering)
3. Comprehensive testing (unit, integration, e2e)
4. Monitoring and observability
5. Documentation

**Deliverable**: Production-ready agentic system

**Testing Focus**:
- Agent decision quality
- Retrieval accuracy
- Learning effectiveness
- Latency targets (<3s p95)
- Cost per query (<$0.10)

---

## 7. Migration Strategy

### 7.1 Parallel Development Approach

Since you're pre-production, you can develop the agentic system in parallel:

```
Current System (bot_interaction.py)    New System (LangGraph agents)
         ↓                                        ↓
   /api/events.py                           /api/events.py
         ↓                                        ↓
   Feature flag: USE_AGENTIC = False        USE_AGENTIC = True
```

**Implementation**:
```python
# app/core/config.py
class Settings(BaseSettings):
    # ... existing settings ...

    use_agentic_system: bool = False  # Feature flag

    # Agent-specific settings
    agent_max_iterations: int = 3
    agent_confidence_threshold: float = 0.7
    enable_learning_agent: bool = True

# app/api/events.py
@router.post("/slack/events")
async def handle_slack_events(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle Slack events with agentic system"""

    # ... existing validation ...

    if event_type == "message":
        if settings.use_agentic_system:
            # New agentic system
            from app.agents.orchestrator import query_processing_graph

            result = await query_processing_graph.ainvoke({
                "query": event.get("text"),
                "user_id": event.get("user"),
                "team_id": team_id,
                "channel_id": event.get("channel"),
                "thread_ts": event.get("thread_ts"),
                "max_iterations": settings.agent_max_iterations
            })
        else:
            # Old hardcoded system
            from app.services.bot_interaction import bot_interaction_service
            await bot_interaction_service.handle_message_event(event, db)
```

### 7.2 Testing Strategy

**Comparison Testing**:
Run both systems in parallel and compare:
- Response quality (human eval)
- Latency
- Cost
- Learning effectiveness

**A/B Testing**:
Once agentic system is stable, A/B test with internal users:
- 50% get agentic system
- 50% get hardcoded system
- Measure satisfaction, task completion, retention

**Gradual Rollout**:
1. Week 1-3: Internal testing only
2. Week 4-6: 10% of users
3. Week 7-9: 50% of users
4. Week 10+: 100% rollout
5. Deprecate old system

---

## 8. Key Advantages

### 8.1 Autonomy

**Before (Hardcoded)**:
```python
# Always does these steps in order
1. Expand query
2. Entity extraction
3. Rewrite query
4. Vector search
5. Keyword search
6. Graph search
7. Merge results
8. Generate answer
```

**After (Agentic)**:
```python
# Agent decides dynamically
if intent == "search" and mentions_code:
    use_github_search()
elif intent == "connect":
    focus_on_graph_traversal()
elif low_confidence:
    loop_back_for_more_data()
```

### 8.2 Continuous Learning

**Before**: Static system, no improvement over time

**After**:
- Learns query patterns
- Expands team vocabulary automatically
- Discovers entity relationships
- Improves from feedback

### 8.3 Cross-Source Intelligence

**Before**: Separate searches per source

**After**:
- Unified entity understanding (same person across Slack/GitHub)
- Automatic relationship detection
- Temporal reasoning across sources

### 8.4 Scalability

**Before**: Adding new source = rewrite orchestration

**After**: Adding new source = add new tool, agents automatically learn to use it

---

## 9. Cost & Performance Targets

### 9.1 Latency Targets

- **Query Understanding**: <500ms
- **Planning**: <200ms
- **Retrieval (parallel)**: <1s
- **Synthesis**: <1s
- **Total**: <3s p95 (same as current system)

### 9.2 Cost Targets

**Per Query**:
- Query Understanding LLM: ~$0.001 (haiku-3.5)
- Synthesis LLM: ~$0.05 (sonnet-3.5)
- Embeddings: ~$0.001
- **Total**: ~$0.052 per query

**At Scale (10K queries/day)**:
- Daily: $520
- Monthly: $15,600

**Optimization Strategies**:
- Cache query understanding for similar queries
- Use smaller models for classification
- Batch embeddings
- Target: <$0.03 per query

### 9.3 Quality Targets

- **Answer Accuracy**: >90% (human eval)
- **Citation Accuracy**: >95%
- **Cross-Source Links**: >70% precision
- **Learning Rate**: 5% improvement per week

---

## 10. Risks & Mitigations

### 10.1 Latency Risk

**Risk**: Multiple LLM calls increase latency

**Mitigation**:
- Parallel execution where possible
- Cache query understanding
- Use faster models (haiku) for classification
- Streaming responses

### 10.2 Cost Risk

**Risk**: Agent autonomy could lead to runaway costs

**Mitigation**:
- Hard limit on max_iterations (default 3)
- Cost tracking per query
- Alerts on anomalous cost patterns
- Budget caps per workspace

### 10.3 Infinite Loop Risk

**Risk**: Agents could loop forever if confidence never reached

**Mitigation**:
- Hard limit on max_iterations
- Confidence threshold fallback (if can't reach 0.7, return best effort at iteration 3)
- Circuit breakers

### 10.4 Learning Quality Risk

**Risk**: Learning agent could learn wrong patterns

**Mitigation**:
- Confidence thresholds for auto-learning
- Human review for low-confidence learnings
- Rollback mechanism for bad learnings
- Feedback loop integration

---

## 11. Success Metrics

### 11.1 Technical Metrics

- Agent decision accuracy
- Retrieval precision/recall
- Learning effectiveness (improvement over time)
- Latency p50/p95/p99
- Cost per query
- Knowledge graph growth rate

### 11.2 Product Metrics

- User satisfaction (CSAT)
- Query success rate
- Time to answer
- Cross-source connection rate
- Repeat usage rate

### 11.3 Moat Metrics

- Unique entity connections discovered
- Team vocabulary size
- Knowledge graph density
- Temporal relationships tracked
- Cross-source synthesis rate

---

## 12. Next Steps

### Immediate (This Week)

1. Review this blueprint with team
2. Validate approach with stakeholders
3. Set up development environment
4. Install LangGraph dependencies
5. Create project structure

### Short Term (Weeks 1-3)

1. Implement Phase 1 (Foundation)
2. Create first working agent prototype
3. Test basic query flow
4. Validate state management
5. Benchmark performance

### Medium Term (Weeks 4-12)

1. Implement Phases 2-4
2. Complete multi-agent system
3. Integrate knowledge graph intelligence
4. Run comparison tests
5. Prepare for rollout

### Long Term (Weeks 13-16)

1. Production optimization
2. Comprehensive testing
3. Documentation
4. Internal rollout
5. Monitor and iterate

---

## Conclusion

This agentic architecture transforms Eunoia from a hardcoded RAG system into an intelligent, continuously learning AI teammate. The key advantages:

1. **Autonomous Decision-Making**: Agents adapt to each query
2. **Continuous Learning**: System improves from every interaction
3. **Cross-Source Intelligence**: Unified understanding across tools
4. **Scalable Architecture**: Easy to add new sources and capabilities

The pre-production status is a **major advantage** - you can build this architecture from the ground up without backward compatibility constraints. This 12-16 week investment will establish a defensible moat that competitors cannot replicate without access to the same team data and interaction history.

The continuously learning knowledge graph is your differentiator. As teams use Eunoia, it becomes smarter about their specific context, terminology, and relationships - creating a compounding advantage over time.
