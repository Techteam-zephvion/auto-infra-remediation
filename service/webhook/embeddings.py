"""
Text Embedding Generation for RAG

Generates embeddings from remediation text using sentence-transformers.
Supports multiple embedding models with caching.

Author: AutoInfraRemediation Team
Date: April 2026
"""

import os
import logging
from typing import List, Optional
from functools import lru_cache

logger = logging.getLogger(__name__)


class EmbeddingGenerator:
    """
    Generate text embeddings for semantic search in knowledge base.
    
    Uses sentence-transformers for high-quality embeddings.
    Default model: all-MiniLM-L6-v2 (384 dimensions, fast, good quality)
    """
    
    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        device: str = "cpu"
    ):
        """
        Initialize embedding generator.
        
        Args:
            model_name: HuggingFace model name for embeddings
            device: Device to run model on ('cpu' or 'cuda')
        """
        self.model_name = model_name
        self.device = device
        self._model = None
        
        logger.info(f"[EMBEDDINGS] Initialized with model={model_name}, device={device}")
    
    def _get_model(self):
        """Lazy load the sentence-transformers model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                
                logger.info(f"[EMBEDDINGS] Loading model {self.model_name}...")
                self._model = SentenceTransformer(self.model_name, device=self.device)
                logger.info(f"[EMBEDDINGS] Model loaded successfully (dim={self._model.get_sentence_embedding_dimension()})")
            except ImportError:
                logger.error("[EMBEDDINGS] sentence-transformers not installed. Install with: pip install sentence-transformers")
                raise
            except Exception as e:
                logger.error(f"[EMBEDDINGS] Failed to load model: {e}")
                raise
        
        return self._model
    
    def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for a single text.
        
        Args:
            text: Input text to embed
            
        Returns:
            List of floats representing the embedding vector
        """
        if not text or not text.strip():
            logger.warning("[EMBEDDINGS] Empty text provided, returning zero vector")
            # Return zero vector with correct dimensions
            model = self._get_model()
            dim = model.get_sentence_embedding_dimension()
            return [0.0] * dim
        
        try:
            model = self._get_model()
            embedding = model.encode(text, convert_to_numpy=True)
            
            # Convert numpy array to list
            embedding_list = embedding.tolist()
            
            logger.debug(f"[EMBEDDINGS] Generated embedding (dim={len(embedding_list)}) for text: {text[:50]}...")
            return embedding_list
        
        except Exception as e:
            logger.error(f"[EMBEDDINGS] Failed to generate embedding: {e}")
            raise
    
    def generate_embeddings_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """
        Generate embeddings for multiple texts (more efficient than individual calls).
        
        Args:
            texts: List of input texts to embed
            batch_size: Batch size for encoding (larger = faster but more memory)
            
        Returns:
            List of embedding vectors
        """
        if not texts:
            return []
        
        try:
            model = self._get_model()
            embeddings = model.encode(
                texts,
                batch_size=batch_size,
                convert_to_numpy=True,
                show_progress_bar=len(texts) > 10  # Show progress for large batches
            )
            
            # Convert numpy array to list of lists
            embeddings_list = embeddings.tolist()
            
            logger.info(f"[EMBEDDINGS] Generated {len(embeddings_list)} embeddings (batch_size={batch_size})")
            return embeddings_list
        
        except Exception as e:
            logger.error(f"[EMBEDDINGS] Failed to generate batch embeddings: {e}")
            raise
    
    def get_embedding_dimension(self) -> int:
        """
        Get the dimension of embedding vectors.
        
        Returns:
            Embedding dimension (e.g., 384 for all-MiniLM-L6-v2)
        """
        model = self._get_model()
        return model.get_sentence_embedding_dimension()


# Singleton instance for global access
_embedding_generator_instance: Optional[EmbeddingGenerator] = None


def get_embedding_generator() -> EmbeddingGenerator:
    """
    Get singleton embedding generator instance.
    
    Returns:
        Global EmbeddingGenerator instance
    """
    global _embedding_generator_instance
    
    if _embedding_generator_instance is None:
        # Use environment variable to allow custom model
        model_name = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
        device = os.getenv("EMBEDDING_DEVICE", "cpu")
        
        _embedding_generator_instance = EmbeddingGenerator(
            model_name=model_name,
            device=device
        )
    
    return _embedding_generator_instance


def create_remediation_text(
    alert_type: str,
    analysis: str,
    script: str,
    success: bool
) -> str:
    """
    Create combined text for embedding from remediation data.
    
    Combines alert type, analysis, and script into a single text
    that captures the context of the remediation.
    
    Args:
        alert_type: Type of alert (e.g., "cpu_spike", "memory_leak")
        analysis: LLM analysis of the issue
        script: Remediation script that was executed
        success: Whether the remediation succeeded
        
    Returns:
        Combined text suitable for embedding
    """
    # Format: AlertType | Analysis | Script | Result
    result_text = "successful" if success else "failed"
    
    # Combine with special tokens for structure
    combined = f"""
Alert Type: {alert_type}

Analysis:
{analysis}

Script:
{script}

Result: {result_text}
""".strip()
    
    return combined


def create_query_text(alert_type: str, logs: str) -> str:
    """
    Create query text for searching similar remediations.
    
    Args:
        alert_type: Type of current alert
        logs: Current pod logs
        
    Returns:
        Query text suitable for embedding and similarity search
    """
    # Format query similar to stored remediations for better matching
    query = f"""
Alert Type: {alert_type}

Logs:
{logs[:1000]}
""".strip()
    
    return query
