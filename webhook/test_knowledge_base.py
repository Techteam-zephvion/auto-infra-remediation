"""
Test script for Phase 6.3 - Knowledge Base Integration (RAG)

Tests ChromaDB integration, embeddings generation, and semantic search.

Requirements:
    - ChromaDB running (docker-compose up -d chromadb)
    - Python packages: chromadb, sentence-transformers

Usage:
    python test_knowledge_base.py
"""

import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def test_embeddings_initialization():
    """Test 1: Embeddings generator initialization"""
    print("\n" + "="*70)
    print("TEST 1: Embeddings Generator Initialization")
    print("="*70)
    
    from embeddings import get_embedding_generator
    
    try:
        print("\nInitializing embedding generator...")
        gen = get_embedding_generator()
        
        dim = gen.get_embedding_dimension()
        print(f"✅ Embedding generator initialized")
        print(f"Model: {gen.model_name}")
        print(f"Device: {gen.device}")
        print(f"Embedding dimension: {dim}")
        
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        logger.exception("Full traceback:")
        return False


def test_embedding_generation():
    """Test 2: Generate embeddings for text"""
    print("\n" + "="*70)
    print("TEST 2: Embedding Generation")
    print("="*70)
    
    from embeddings import get_embedding_generator
    
    try:
        gen = get_embedding_generator()
        
        # Test single embedding
        test_text = "CPU usage is very high on the nginx pod"
        print(f"\nGenerating embedding for: '{test_text}'")
        
        embedding = gen.generate_embedding(test_text)
        
        print(f"✅ Embedding generated")
        print(f"Embedding dimension: {len(embedding)}")
        print(f"First 5 values: {embedding[:5]}")
        
        # Test batch embedding
        texts = [
            "Memory leak detected in application",
            "Disk space running low",
            "High CPU usage alert"
        ]
        
        print(f"\nGenerating batch embeddings for {len(texts)} texts...")
        embeddings = gen.generate_embeddings_batch(texts)
        
        print(f"✅ Batch embeddings generated")
        print(f"Number of embeddings: {len(embeddings)}")
        
        return len(embedding) > 0 and len(embeddings) == 3
    
    except Exception as e:
        print(f"❌ FAIL: {e}")
        logger.exception("Full traceback:")
        return False


def test_knowledge_base_initialization():
    """Test 3: Knowledge base initialization"""
    print("\n" + "="*70)
    print("TEST 3: Knowledge Base Initialization")
    print("="*70)
    
    from knowledge_base import get_knowledge_base
    
    try:
        print("\nInitializing knowledge base...")
        kb = get_knowledge_base()
        
        print(f"Enabled: {kb.enabled}")
        print(f"ChromaDB URL: {kb.chromadb_url}")
        print(f"Collection: {kb.collection_name}")
        
        # Health check
        print("\nPerforming health check...")
        healthy = kb.health_check()
        
        if healthy:
            print("✅ Knowledge base healthy (ChromaDB connected)")
            return True
        else:
            print("⚠️  Knowledge base unhealthy (ChromaDB not connected)")
            print("   Make sure ChromaDB is running: docker-compose up -d chromadb")
            return False
    
    except Exception as e:
        print(f"❌ FAIL: {e}")
        logger.exception("Full traceback:")
        return False


def test_store_and_search():
    """Test 4: Store and search remediations"""
    print("\n" + "="*70)
    print("TEST 4: Store and Search Remediations")
    print("="*70)
    
    from knowledge_base import get_knowledge_base
    from embeddings import get_embedding_generator, create_remediation_text
    
    try:
        kb = get_knowledge_base()
        gen = get_embedding_generator()
        
        if not kb.health_check():
            print("⚠️  Skipping test - ChromaDB not available")
            return True  # Skip but don't fail
        
        # Clear collection for clean test
        print("\nClearing collection for clean test...")
        kb.clear_collection()
        
        # Store some test remediations
        test_cases = [
            {
                "id": "test-cpu-1",
                "alert_type": "cpu_spike",
                "analysis": "High CPU usage detected on nginx pod. Root cause: insufficient resource limits causing throttling.",
                "script": "kubectl set resources deployment nginx --limits=cpu=2000m",
                "success": True
            },
            {
                "id": "test-memory-1",
                "alert_type": "memory_leak",
                "analysis": "Memory leak in Java application. Heap size exceeded.",
                "script": "kubectl set env deployment myapp JAVA_OPTS='-Xmx2048m'",
                "success": True
            },
            {
                "id": "test-cpu-2",
                "alert_type": "cpu_spike",
                "analysis": "CPU spike due to runaway process. Scaling horizontally.",
                "script": "kubectl scale deployment nginx --replicas=5",
                "success": True
            }
        ]
        
        print(f"\nStoring {len(test_cases)} test remediations...")
        for case in test_cases:
            # Create text and embedding
            text = create_remediation_text(
                case["alert_type"],
                case["analysis"],
                case["script"],
                case["success"]
            )
            embedding = gen.generate_embedding(text)
            
            # Store in KB
            success = kb.store_remediation(
                remediation_id=case["id"],
                alert_type=case["alert_type"],
                analysis=case["analysis"],
                script=case["script"],
                success=case["success"],
                embedding=embedding
            )
            
            if success:
                print(f"  ✅ Stored: {case['id']} ({case['alert_type']})")
            else:
                print(f"  ❌ Failed: {case['id']}")
        
        # Search for similar cases
        print("\n--- Searching for Similar Cases ---")
        
        # Query: CPU-related issue
        from embeddings import create_query_text
        query_text = create_query_text(
            "cpu_spike",
            "CPU usage at 95% on nginx deployment, throttling requests"
        )
        query_embedding = gen.generate_embedding(query_text)
        
        print(f"\nQuery: CPU spike issue")
        results = kb.search_similar(
            query_embedding=query_embedding,
            alert_type="cpu_spike",
            top_k=2,
            success_only=True
        )
        
        print(f"Found {len(results)} similar cases:")
        for i, case in enumerate(results, 1):
            print(f"\n  Case {i}:")
            print(f"    ID: {case.id}")
            print(f"    Alert Type: {case.alert_type}")
            print(f"    Similarity: {case.similarity_score:.2%}")
            print(f"    Script: {case.script[:60]}...")
        
        # Verify we got CPU-related results
        if len(results) > 0 and results[0].alert_type == "cpu_spike":
            print("\n✅ PASS: Search returned relevant results")
            return True
        else:
            print("\n❌ FAIL: Search did not return expected results")
            return False
    
    except Exception as e:
        print(f"❌ FAIL: {e}")
        logger.exception("Full traceback:")
        return False


def test_rag_context_formatting():
    """Test 5: RAG context formatting"""
    print("\n" + "="*70)
    print("TEST 5: RAG Context Formatting")
    print("="*70)
    
    from knowledge_base import format_rag_context, RemediationCase
    
    try:
        # Create mock similar cases
        cases = [
            RemediationCase(
                id="case-1",
                alert_type="cpu_spike",
                analysis="High CPU usage due to traffic spike",
                script="kubectl scale deployment app --replicas=5",
                success=True,
                timestamp="2024-04-01T10:00:00",
                similarity_score=0.85
            ),
            RemediationCase(
                id="case-2",
                alert_type="cpu_spike",
                analysis="CPU throttling from resource limits",
                script="kubectl set resources deployment app --limits=cpu=2000m",
                success=True,
                timestamp="2024-03-30T15:30:00",
                similarity_score=0.78
            )
        ]
        
        print(f"\nFormatting {len(cases)} similar cases for LLM prompt...")
        context = format_rag_context(cases, max_cases=2)
        
        print("\n--- Formatted Context ---")
        print(context)
        print("--- End Context ---")
        
        # Verify formatting
        if "Case 1" in context and "85.0%" in context and "kubectl" in context:
            print("\n✅ PASS: Context formatted correctly")
            return True
        else:
            print("\n❌ FAIL: Context formatting incomplete")
            return False
    
    except Exception as e:
        print(f"❌ FAIL: {e}")
        logger.exception("Full traceback:")
        return False


def test_knowledge_base_stats():
    """Test 6: Knowledge base statistics"""
    print("\n" + "="*70)
    print("TEST 6: Knowledge Base Statistics")
    print("="*70)
    
    from knowledge_base import get_knowledge_base
    
    try:
        kb = get_knowledge_base()
        
        print("\nFetching knowledge base statistics...")
        stats = kb.get_stats()
        
        print("\nKnowledge Base Stats:")
        for key, value in stats.items():
            print(f"  {key}: {value}")
        
        return True
    
    except Exception as e:
        print(f"❌ FAIL: {e}")
        logger.exception("Full traceback:")
        return False


def main():
    """Run all knowledge base tests"""
    print("\n" + "="*70)
    print("PHASE 6.3 - KNOWLEDGE BASE (RAG) TESTS")
    print("="*70)
    print("Testing: ChromaDB, embeddings, semantic search, RAG context")
    print("="*70)
    
    results = {}
    
    try:
        results['embeddings_init'] = test_embeddings_initialization()
    except Exception as e:
        logger.error(f"Test 1 failed: {e}")
        results['embeddings_init'] = False
    
    try:
        results['embedding_generation'] = test_embedding_generation()
    except Exception as e:
        logger.error(f"Test 2 failed: {e}")
        results['embedding_generation'] = False
    
    try:
        results['kb_init'] = test_knowledge_base_initialization()
    except Exception as e:
        logger.error(f"Test 3 failed: {e}")
        results['kb_init'] = False
    
    try:
        results['store_search'] = test_store_and_search()
    except Exception as e:
        logger.error(f"Test 4 failed: {e}")
        results['store_search'] = False
    
    try:
        results['rag_formatting'] = test_rag_context_formatting()
    except Exception as e:
        logger.error(f"Test 5 failed: {e}")
        results['rag_formatting'] = False
    
    try:
        results['stats'] = test_knowledge_base_stats()
    except Exception as e:
        logger.error(f"Test 6 failed: {e}")
        results['stats'] = False
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test_name:25} {status}")
    
    total = len(results)
    passed = sum(results.values())
    print(f"\nTotal: {passed}/{total} tests passed ({passed/total*100:.0f}%)")
    print("="*70)
    
    return passed == total


if __name__ == "__main__":
    try:
        success = main()
        exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user")
        exit(1)
    except Exception as e:
        logger.error(f"Test suite failed: {e}")
        logger.exception("Full traceback:")
        exit(1)
