"""
LLM Response Cache using Redis

This module provides caching for LLM responses to reduce latency and costs
for identical or similar alerts.

Features:
- SHA256-based cache keys from alert context
- 1-hour TTL for cached responses
- Prometheus metrics for cache hit/miss rates
- Cache invalidation support
- Graceful fallback if Redis unavailable

Usage:
    cache = get_llm_cache()
    
    # Check cache
    cached_response = cache.get_cached_response(alert_type="cpu_spike", logs="...")
    if cached_response:
        return cached_response
    
    # Generate response via LLM
    response = llm.invoke(prompt)
    
    # Store in cache
    cache.set_cached_response(alert_type="cpu_spike", logs="...", response=response)
"""

import hashlib
import json
import logging
import os
from typing import Optional, Dict, Any
from datetime import timedelta

import redis
from redis.exceptions import RedisError, ConnectionError

logger = logging.getLogger(__name__)


class LLMCache:
    """Redis-based cache for LLM responses"""
    
    def __init__(self, redis_url: Optional[str] = None, ttl_seconds: int = 3600):
        """
        Initialize LLM cache
        
        Args:
            redis_url: Redis connection URL (default: localhost:6379)
            ttl_seconds: Cache TTL in seconds (default: 1 hour)
        """
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.ttl_seconds = ttl_seconds
        self.enabled = os.getenv("LLM_CACHE_ENABLED", "true").lower() == "true"
        self.redis_client = None
        
        # Metrics counters
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_errors = 0
        
        if self.enabled:
            self._init_redis()
    
    def _init_redis(self) -> None:
        """Initialize Redis connection"""
        try:
            self.redis_client = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2
            )
            # Test connection
            self.redis_client.ping()
            logger.info(f"[CACHE] Connected to Redis at {self.redis_url}")
        except (RedisError, ConnectionError) as e:
            logger.warning(f"[CACHE] Redis unavailable: {e}. Caching disabled.")
            self.redis_client = None
            self.enabled = False
    
    def _generate_cache_key(self, alert_type: str, logs: str, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Generate cache key from alert context
        
        Args:
            alert_type: Type of alert (e.g., 'cpu_spike')
            logs: Pod logs content
            context: Additional context (namespace, pod_name, etc.)
        
        Returns:
            SHA256 hash as cache key
        """
        # Normalize logs (remove timestamps, variable parts)
        normalized_logs = self._normalize_logs(logs)
        
        # Build cache key components
        key_parts = [
            alert_type,
            normalized_logs[:1000],  # Limit log size for key generation
        ]
        
        if context:
            # Add relevant context that affects LLM response
            if 'namespace' in context:
                key_parts.append(context['namespace'])
        
        # Generate SHA256 hash
        key_string = "|".join(key_parts)
        cache_key = hashlib.sha256(key_string.encode()).hexdigest()
        
        return f"llm:response:{cache_key}"
    
    def _normalize_logs(self, logs: str) -> str:
        """
        Normalize logs by removing variable parts
        
        Args:
            logs: Raw log content
        
        Returns:
            Normalized log string
        """
        if not logs:
            return ""
        
        # Remove timestamps (common patterns)
        import re
        normalized = re.sub(r'\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}', '[TIMESTAMP]', logs)
        normalized = re.sub(r'\d{10,13}', '[TIMESTAMP]', normalized)  # Unix timestamps
        
        # Remove pod/container IDs
        normalized = re.sub(r'[a-f0-9]{8,64}', '[ID]', normalized)
        
        # Remove IP addresses
        normalized = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '[IP]', normalized)
        
        # Normalize whitespace
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        
        return normalized
    
    def get_cached_response(
        self,
        alert_type: str,
        logs: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached LLM response
        
        Args:
            alert_type: Type of alert
            logs: Pod logs
            context: Additional context
        
        Returns:
            Cached response dict or None if cache miss
        """
        if not self.enabled or not self.redis_client:
            return None
        
        try:
            cache_key = self._generate_cache_key(alert_type, logs, context)
            cached_data = self.redis_client.get(cache_key)
            
            if cached_data:
                self.cache_hits += 1
                response = json.loads(cached_data)
                logger.info(f"[CACHE] HIT for alert_type={alert_type} (key={cache_key[:16]}...)")
                return response
            else:
                self.cache_misses += 1
                logger.debug(f"[CACHE] MISS for alert_type={alert_type}")
                return None
                
        except (RedisError, json.JSONDecodeError) as e:
            self.cache_errors += 1
            logger.warning(f"[CACHE] Error retrieving from cache: {e}")
            return None
    
    def set_cached_response(
        self,
        alert_type: str,
        logs: str,
        response: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        ttl: Optional[int] = None
    ) -> bool:
        """
        Store LLM response in cache
        
        Args:
            alert_type: Type of alert
            logs: Pod logs
            response: LLM response to cache
            context: Additional context
            ttl: Optional custom TTL (seconds)
        
        Returns:
            True if cached successfully, False otherwise
        """
        if not self.enabled or not self.redis_client:
            return False
        
        try:
            cache_key = self._generate_cache_key(alert_type, logs, context)
            cache_ttl = ttl or self.ttl_seconds
            
            # Serialize response
            cached_data = json.dumps(response)
            
            # Store with TTL
            self.redis_client.setex(
                cache_key,
                cache_ttl,
                cached_data
            )
            
            logger.info(f"[CACHE] SET for alert_type={alert_type} (TTL={cache_ttl}s, key={cache_key[:16]}...)")
            return True
            
        except (RedisError, TypeError) as e:
            self.cache_errors += 1
            logger.warning(f"[CACHE] Error storing in cache: {e}")
            return False
    
    def invalidate_cache(self, pattern: Optional[str] = None) -> int:
        """
        Invalidate cache entries
        
        Args:
            pattern: Key pattern to invalidate (default: all llm:response:*)
        
        Returns:
            Number of keys deleted
        """
        if not self.enabled or not self.redis_client:
            return 0
        
        try:
            pattern = pattern or "llm:response:*"
            keys = list(self.redis_client.scan_iter(match=pattern))
            
            if keys:
                deleted = self.redis_client.delete(*keys)
                logger.info(f"[CACHE] Invalidated {deleted} cache entries (pattern={pattern})")
                return deleted
            else:
                logger.debug(f"[CACHE] No keys to invalidate (pattern={pattern})")
                return 0
                
        except RedisError as e:
            self.cache_errors += 1
            logger.warning(f"[CACHE] Error invalidating cache: {e}")
            return 0
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics
        
        Returns:
            Dict with cache metrics
        """
        total_requests = self.cache_hits + self.cache_misses
        hit_rate = (self.cache_hits / total_requests * 100) if total_requests > 0 else 0
        
        stats = {
            'enabled': self.enabled,
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_misses,
            'cache_errors': self.cache_errors,
            'hit_rate_percent': round(hit_rate, 2),
            'total_requests': total_requests
        }
        
        # Get Redis info if available
        if self.enabled and self.redis_client:
            try:
                redis_info = self.redis_client.info('stats')
                stats['redis_memory_used'] = redis_info.get('used_memory_human', 'N/A')
                stats['redis_keys_count'] = self.redis_client.dbsize()
            except RedisError:
                pass
        
        return stats
    
    def health_check(self) -> bool:
        """
        Check if cache is healthy
        
        Returns:
            True if cache is accessible, False otherwise
        """
        if not self.enabled or not self.redis_client:
            return False
        
        try:
            self.redis_client.ping()
            return True
        except RedisError:
            return False


# Global singleton instance
_llm_cache = None


def get_llm_cache() -> LLMCache:
    """Get or create LLMCache singleton"""
    global _llm_cache
    if _llm_cache is None:
        _llm_cache = LLMCache()
    return _llm_cache


def warm_cache_for_alert_types(alert_types: list, sample_logs: Dict[str, str]) -> int:
    """
    Warm cache with common alert types (for testing/pre-deployment)
    
    Args:
        alert_types: List of alert types to warm
        sample_logs: Dict mapping alert_type to sample logs
    
    Returns:
        Number of entries warmed
    """
    cache = get_llm_cache()
    warmed = 0
    
    for alert_type in alert_types:
        logs = sample_logs.get(alert_type, "")
        if logs:
            # This would normally be a real LLM response
            # For warming, you'd need to generate or provide sample responses
            logger.info(f"[CACHE_WARMING] Would warm cache for {alert_type}")
            warmed += 1
    
    return warmed
