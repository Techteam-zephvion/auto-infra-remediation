"""
Knowledge Base for RAG (Retrieval-Augmented Generation)

Stores historical remediations in ChromaDB vector database.
Enables semantic search for similar past cases to enhance LLM prompts.

Author: AutoInfraRemediation Team
Date: April 2026
"""

import os
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RemediationCase:
    """A historical remediation case from the knowledge base."""
    id: str
    alert_type: str
    analysis: str
    script: str
    success: bool
    timestamp: str
    similarity_score: float = 0.0
    metadata: Dict[str, Any] = None


class KnowledgeBase:
    """
    Vector database knowledge base for remediation history.
    
    Uses ChromaDB for persistent storage and semantic search.
    Retrieves similar past cases to enhance LLM prompts with context.
    """
    
    def __init__(
        self,
        chromadb_url: str = "http://localhost:8000",
        collection_name: str = "remediations",
        enabled: bool = True
    ):
        """
        Initialize knowledge base connection.
        
        Args:
            chromadb_url: ChromaDB HTTP API URL
            collection_name: Name of the collection to store remediations
            enabled: Enable/disable knowledge base (fallback if disabled)
        """
        self.chromadb_url = chromadb_url
        self.collection_name = collection_name
        self.enabled = enabled
        self._client = None
        self._collection = None
        
        # Metrics
        self.total_queries = 0
        self.total_hits = 0  # Queries that returned results
        self.total_stored = 0
        
        if not self.enabled:
            logger.warning("[KB] Knowledge base disabled (KNOWLEDGE_BASE_ENABLED=false)")
            return
        
        logger.info(f"[KB] Initialized (chromadb_url={chromadb_url}, collection={collection_name})")
    
    def _get_client(self):
        """Lazy load ChromaDB client."""
        if self._client is None and self.enabled:
            try:
                import chromadb
                from chromadb.config import Settings
                
                logger.info(f"[KB] Connecting to ChromaDB at {self.chromadb_url}...")
                self._client = chromadb.HttpClient(
                    host=self.chromadb_url.replace("http://", "").split(":")[0],
                    port=int(self.chromadb_url.split(":")[-1]),
                    settings=Settings(anonymized_telemetry=False)
                )
                
                # Test connection
                self._client.heartbeat()
                logger.info("[KB] ✅ Connected to ChromaDB")
                
            except ImportError:
                logger.error("[KB] chromadb not installed. Install with: pip install chromadb")
                self.enabled = False
                return None
            except Exception as e:
                logger.error(f"[KB] Failed to connect to ChromaDB: {e}")
                self.enabled = False
                return None
        
        return self._client
    
    def _get_collection(self):
        """Get or create ChromaDB collection for remediations."""
        if self._collection is None and self.enabled:
            client = self._get_client()
            if client is None:
                return None
            
            try:
                # Get or create collection
                self._collection = client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"description": "Historical Kubernetes remediations for RAG"}
                )
                
                count = self._collection.count()
                logger.info(f"[KB] Collection '{self.collection_name}' ready ({count} documents)")
                
            except Exception as e:
                logger.error(f"[KB] Failed to get/create collection: {e}")
                self.enabled = False
                return None
        
        return self._collection
    
    def store_remediation(
        self,
        remediation_id: str,
        alert_type: str,
        analysis: str,
        script: str,
        success: bool,
        embedding: List[float],
        namespace: str = "default",
        additional_metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Store a remediation in the knowledge base.
        
        Args:
            remediation_id: Unique ID for this remediation (e.g., from audit_events)
            alert_type: Type of alert (e.g., "cpu_spike")
            analysis: LLM analysis of the issue
            script: Remediation script that was executed
            success: Whether the remediation succeeded
            embedding: Pre-computed embedding vector
            namespace: Kubernetes namespace
            additional_metadata: Optional extra metadata
            
        Returns:
            True if stored successfully, False otherwise
        """
        if not self.enabled:
            return False
        
        collection = self._get_collection()
        if collection is None:
            return False
        
        try:
            # Prepare metadata
            metadata = {
                "alert_type": alert_type,
                "success": success,
                "timestamp": datetime.now().isoformat(),
                "namespace": namespace
            }
            
            # Add additional metadata if provided
            if additional_metadata:
                metadata.update(additional_metadata)
            
            # Create document text (for display/debugging)
            document = f"Alert: {alert_type}\nAnalysis: {analysis[:200]}...\nScript: {script[:200]}..."
            
            # Store in ChromaDB
            collection.add(
                ids=[remediation_id],
                embeddings=[embedding],
                documents=[document],
                metadatas=[metadata]
            )
            
            self.total_stored += 1
            logger.info(f"[KB] ✅ Stored remediation {remediation_id} (alert_type={alert_type}, success={success})")
            return True
        
        except Exception as e:
            logger.error(f"[KB] Failed to store remediation: {e}")
            return False
    
    def search_similar(
        self,
        query_embedding: List[float],
        alert_type: Optional[str] = None,
        top_k: int = 3,
        success_only: bool = True
    ) -> List[RemediationCase]:
        """
        Search for similar historical remediations.
        
        Args:
            query_embedding: Embedding vector for the query
            alert_type: Optional filter by alert type
            top_k: Number of results to return
            success_only: Only return successful remediations
            
        Returns:
            List of similar remediation cases
        """
        self.total_queries += 1
        
        if not self.enabled:
            logger.debug("[KB] Knowledge base disabled, returning empty results")
            return []
        
        collection = self._get_collection()
        if collection is None:
            return []
        
        try:
            # Build where filter
            where_filter = None
            if success_only:
                where_filter = {"success": True}
            
            if alert_type and success_only:
                where_filter = {"$and": [{"success": True}, {"alert_type": alert_type}]}
            elif alert_type:
                where_filter = {"alert_type": alert_type}
            
            # Query ChromaDB
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where_filter
            )
            
            # Parse results
            cases = []
            if results['ids'] and len(results['ids'][0]) > 0:
                self.total_hits += 1
                
                for i in range(len(results['ids'][0])):
                    case_id = results['ids'][0][i]
                    metadata = results['metadatas'][0][i]
                    distance = results['distances'][0][i] if 'distances' in results else 0.0
                    
                    # Fetch full document from database (if needed)
                    # For now, reconstruct from metadata
                    case = RemediationCase(
                        id=case_id,
                        alert_type=metadata.get('alert_type', 'unknown'),
                        analysis=metadata.get('analysis', ''),  # May need to fetch from DB
                        script=metadata.get('script', ''),      # May need to fetch from DB
                        success=metadata.get('success', False),
                        timestamp=metadata.get('timestamp', ''),
                        similarity_score=1.0 - distance,  # Convert distance to similarity
                        metadata=metadata
                    )
                    cases.append(case)
                
                logger.info(f"[KB] Found {len(cases)} similar cases (alert_type={alert_type}, success_only={success_only})")
            else:
                logger.debug(f"[KB] No similar cases found (alert_type={alert_type})")
            
            return cases
        
        except Exception as e:
            logger.error(f"[KB] Failed to search knowledge base: {e}")
            return []
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get knowledge base statistics.
        
        Returns:
            Dictionary with metrics
        """
        collection = self._get_collection()
        
        total_documents = 0
        if collection is not None:
            try:
                total_documents = collection.count()
            except:
                pass
        
        hit_rate = 0.0
        if self.total_queries > 0:
            hit_rate = (self.total_hits / self.total_queries) * 100
        
        return {
            "enabled": self.enabled,
            "total_documents": total_documents,
            "total_queries": self.total_queries,
            "total_hits": self.total_hits,
            "hit_rate_percent": round(hit_rate, 2),
            "total_stored": self.total_stored,
            "collection_name": self.collection_name,
            "chromadb_url": self.chromadb_url if self.enabled else "disabled"
        }
    
    def health_check(self) -> bool:
        """
        Check if knowledge base is healthy.
        
        Returns:
            True if healthy, False otherwise
        """
        if not self.enabled:
            return False
        
        try:
            client = self._get_client()
            if client is None:
                return False
            
            client.heartbeat()
            return True
        except:
            return False
    
    def clear_collection(self) -> bool:
        """
        Clear all documents from the collection (for testing/debugging).
        
        Returns:
            True if cleared successfully
        """
        if not self.enabled:
            return False
        
        try:
            client = self._get_client()
            if client is None:
                return False
            
            # Delete and recreate collection
            client.delete_collection(self.collection_name)
            logger.info(f"[KB] Deleted collection '{self.collection_name}'")
            
            # Reset collection reference
            self._collection = None
            
            return True
        except Exception as e:
            logger.error(f"[KB] Failed to clear collection: {e}")
            return False


# Singleton instance for global access
_knowledge_base_instance: Optional[KnowledgeBase] = None


def get_knowledge_base() -> KnowledgeBase:
    """
    Get singleton knowledge base instance.
    
    Returns:
        Global KnowledgeBase instance
    """
    global _knowledge_base_instance
    
    if _knowledge_base_instance is None:
        chromadb_url = os.getenv("CHROMADB_URL", "http://localhost:8000")
        enabled = os.getenv("KNOWLEDGE_BASE_ENABLED", "true").lower() == "true"
        
        _knowledge_base_instance = KnowledgeBase(
            chromadb_url=chromadb_url,
            enabled=enabled
        )
    
    return _knowledge_base_instance


def format_rag_context(similar_cases: List[RemediationCase], max_cases: int = 3) -> str:
    """
    Format similar remediation cases for inclusion in LLM prompt.
    
    Args:
        similar_cases: List of similar cases from knowledge base
        max_cases: Maximum number of cases to include
        
    Returns:
        Formatted text for LLM prompt
    """
    if not similar_cases:
        return "No similar historical cases found."
    
    # Limit to max cases
    cases_to_show = similar_cases[:max_cases]
    
    context_parts = ["Here are similar issues from the knowledge base that may help:\n"]
    
    for i, case in enumerate(cases_to_show, 1):
        similarity_pct = case.similarity_score * 100
        context_parts.append(f"""
--- Case {i} (Similarity: {similarity_pct:.1f}%) ---
Alert Type: {case.alert_type}
Timestamp: {case.timestamp}
Success: {'✅ Yes' if case.success else '❌ No'}

Analysis:
{case.analysis[:300]}{'...' if len(case.analysis) > 300 else ''}

Script:
{case.script[:200]}{'...' if len(case.script) > 200 else ''}
""")
    
    context_parts.append("\nUse these historical cases as reference, but adapt to the current situation.")
    
    return "\n".join(context_parts)
