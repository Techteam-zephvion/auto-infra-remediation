"""
LLM Router with Multi-Model Fallback Chain

Implements a resilient multi-LLM routing system with:
- Three-tier fallback: qwen2.5:3b → llama3.1:8b → OpenAI GPT-4
- Circuit breaker pattern to skip unhealthy models
- Cost tracking per invocation
- Performance metrics (latency, fallback rate, cost)

Author: AutoInfraRemediation Team
Date: April 2026
"""

import os
import time
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from langchain_ollama import ChatOllama
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.language_models import BaseChatModel

# Conditional imports for optional providers
try:
    from langchain_openai import ChatOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    ChatOpenAI = None

logger = logging.getLogger(__name__)


class ModelTier(Enum):
    """LLM model tiers in fallback chain"""
    PRIMARY = "qwen2.5:3b"      # Fast, local, low quality
    SECONDARY = "llama3.1:8b"   # Slower, local, high quality
    TERTIARY = "gpt-4"          # Expensive, cloud, highest quality


@dataclass
class ModelConfig:
    """Configuration for a single LLM model"""
    name: str
    provider: str  # "ollama" or "openai"
    cost_per_1k_tokens: float
    timeout_seconds: int = 30
    max_retries: int = 1
    
    # Circuit breaker settings
    failure_threshold: int = 3
    recovery_timeout: int = 60  # seconds
    
    # Runtime state
    consecutive_failures: int = 0
    circuit_open: bool = False
    circuit_open_until: Optional[datetime] = None


@dataclass
class RouterMetrics:
    """Metrics for LLM router performance"""
    total_requests: int = 0
    primary_invocations: int = 0
    secondary_invocations: int = 0
    tertiary_invocations: int = 0
    
    primary_failures: int = 0
    secondary_failures: int = 0
    tertiary_failures: int = 0
    
    total_cost_usd: float = 0.0
    total_latency_seconds: float = 0.0
    
    fallback_triggered: int = 0
    circuit_breaker_trips: int = 0


class LLMRouter:
    """
    Multi-model LLM router with fallback chain and circuit breaker.
    
    Tries models in order: qwen2.5:3b → llama3.1:8b → GPT-4
    Skips models with open circuit breakers (after 3 consecutive failures).
    Tracks cost and performance metrics per invocation.
    
    Usage:
        router = LLMRouter()
        response = router.invoke([HumanMessage(content="Hello")])
    """
    
    def __init__(
        self,
        ollama_base_url: str = "http://localhost:11434",
        openai_api_key: Optional[str] = None,
        enable_fallback: bool = True
    ):
        """
        Initialize LLM router with model configurations.
        
        Args:
            ollama_base_url: Base URL for Ollama API
            openai_api_key: OpenAI API key (optional, required for GPT-4 fallback)
            enable_fallback: Enable fallback to next model on failure
        """
        self.ollama_base_url = ollama_base_url
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        self.enable_fallback = enable_fallback
        
        # Model configurations
        self.models: Dict[ModelTier, ModelConfig] = {
            ModelTier.PRIMARY: ModelConfig(
                name="qwen2.5:3b",
                provider="ollama",
                cost_per_1k_tokens=0.0,
                timeout_seconds=20,
                failure_threshold=3,
                recovery_timeout=60
            ),
            ModelTier.SECONDARY: ModelConfig(
                name="llama3.1:8b",
                provider="ollama",
                cost_per_1k_tokens=0.0,
                timeout_seconds=30,
                failure_threshold=3,
                recovery_timeout=60
            ),
            ModelTier.TERTIARY: ModelConfig(
                name="gpt-4",
                provider="openai",
                cost_per_1k_tokens=0.03,  # $0.03 per 1K tokens (approx)
                timeout_seconds=45,
                failure_threshold=5,  # More lenient for paid service
                recovery_timeout=120
            )
        }
        
        # Metrics tracking
        self.metrics = RouterMetrics()
        
        # LangChain model instances (lazy initialization)
        self._model_instances: Dict[ModelTier, Optional[BaseChatModel]] = {
            ModelTier.PRIMARY: None,
            ModelTier.SECONDARY: None,
            ModelTier.TERTIARY: None
        }
        
        logger.info(
            f"[LLM ROUTER] Initialized with fallback={'enabled' if enable_fallback else 'disabled'}, "
            f"Ollama={ollama_base_url}, OpenAI={'configured' if self.openai_api_key else 'missing'}"
        )
    
    def _get_model_instance(self, tier: ModelTier) -> Optional[BaseChatModel]:
        """
        Get or create LangChain model instance for given tier.
        
        Args:
            tier: Model tier to instantiate
            
        Returns:
            LangChain chat model instance or None if unavailable
        """
        # Return cached instance if exists
        if self._model_instances[tier] is not None:
            return self._model_instances[tier]
        
        config = self.models[tier]
        
        try:
            if config.provider == "ollama":
                model = ChatOllama(
                    model=config.name,
                    base_url=self.ollama_base_url
                )
                self._model_instances[tier] = model
                logger.info(f"[LLM ROUTER] Initialized Ollama model: {config.name}")
                return model
            
            elif config.provider == "openai":
                if not self.openai_api_key:
                    logger.warning(f"[LLM ROUTER] Cannot initialize {config.name}: OPENAI_API_KEY missing")
                    return None
                
                # Check if OpenAI is available
                if not OPENAI_AVAILABLE or not ChatOpenAI:
                    logger.warning(f"[LLM ROUTER] OpenAI provider not available, skipping {config.name}")
                    return None
                
                # Set API key as environment variable for LangChain
                os.environ["OPENAI_API_KEY"] = self.openai_api_key
                
                model = ChatOpenAI(
                    model=config.name,
                    max_retries=config.max_retries
                )
                self._model_instances[tier] = model
                logger.info(f"[LLM ROUTER] Initialized OpenAI model: {config.name}")
                return model
            
            else:
                logger.error(f"[LLM ROUTER] Unknown provider: {config.provider}")
                return None
        
        except Exception as e:
            logger.error(f"[LLM ROUTER] Failed to initialize {tier.value}: {e}")
            return None
    
    def _is_circuit_open(self, tier: ModelTier) -> bool:
        """
        Check if circuit breaker is open for given model tier.
        
        Args:
            tier: Model tier to check
            
        Returns:
            True if circuit is open (model unavailable), False otherwise
        """
        config = self.models[tier]
        
        # Check if circuit is open and recovery timeout has passed
        if config.circuit_open and config.circuit_open_until:
            if datetime.now() >= config.circuit_open_until:
                # Recovery timeout passed, close circuit
                logger.info(f"[LLM ROUTER] Circuit breaker closed for {tier.value} after recovery timeout")
                config.circuit_open = False
                config.circuit_open_until = None
                config.consecutive_failures = 0
                return False
            else:
                # Circuit still open
                return True
        
        return config.circuit_open
    
    def _record_success(self, tier: ModelTier):
        """
        Record successful invocation for given model tier.
        
        Args:
            tier: Model tier that succeeded
        """
        config = self.models[tier]
        
        # Reset failure count on success
        if config.consecutive_failures > 0:
            logger.info(
                f"[LLM ROUTER] Model {tier.value} recovered (was {config.consecutive_failures} failures)"
            )
        config.consecutive_failures = 0
        
        # Close circuit if it was open
        if config.circuit_open:
            logger.info(f"[LLM ROUTER] Circuit breaker closed for {tier.value} after successful call")
            config.circuit_open = False
            config.circuit_open_until = None
    
    def _record_failure(self, tier: ModelTier):
        """
        Record failed invocation for given model tier.
        Updates circuit breaker state if threshold exceeded.
        
        Args:
            tier: Model tier that failed
        """
        config = self.models[tier]
        config.consecutive_failures += 1
        
        # Trip circuit breaker if failure threshold exceeded
        if config.consecutive_failures >= config.failure_threshold and not config.circuit_open:
            config.circuit_open = True
            config.circuit_open_until = datetime.now() + timedelta(seconds=config.recovery_timeout)
            self.metrics.circuit_breaker_trips += 1
            
            logger.warning(
                f"[LLM ROUTER] ⚠️  Circuit breaker OPENED for {tier.value} "
                f"({config.consecutive_failures} failures, recovery in {config.recovery_timeout}s)"
            )
        else:
            logger.warning(
                f"[LLM ROUTER] Model {tier.value} failed "
                f"({config.consecutive_failures}/{config.failure_threshold})"
            )
    
    def _estimate_cost(self, tier: ModelTier, response: str) -> float:
        """
        Estimate cost of LLM invocation based on response length.
        
        Args:
            tier: Model tier used
            response: LLM response text
            
        Returns:
            Estimated cost in USD
        """
        config = self.models[tier]
        
        # Rough token estimate: ~4 chars per token
        estimated_tokens = len(response) / 4
        cost = (estimated_tokens / 1000) * config.cost_per_1k_tokens
        
        return cost
    
    def invoke(self, messages: List[BaseMessage]) -> str:
        """
        Invoke LLM with fallback chain.
        
        Tries models in order: PRIMARY → SECONDARY → TERTIARY
        Skips models with open circuit breakers.
        Tracks metrics and costs.
        
        Args:
            messages: List of LangChain messages to send to LLM
            
        Returns:
            LLM response content as string
            
        Raises:
            RuntimeError: If all models fail
        """
        self.metrics.total_requests += 1
        start_time = time.time()
        
        # Try models in fallback chain order
        fallback_chain = [ModelTier.PRIMARY, ModelTier.SECONDARY, ModelTier.TERTIARY]
        last_error = None
        
        for i, tier in enumerate(fallback_chain):
            config = self.models[tier]
            
            # Skip if circuit breaker is open
            if self._is_circuit_open(tier):
                logger.info(f"[LLM ROUTER] Skipping {tier.value} (circuit breaker open)")
                continue
            
            # Get model instance
            model = self._get_model_instance(tier)
            if model is None:
                logger.warning(f"[LLM ROUTER] Model {tier.value} not available (initialization failed)")
                self._record_failure(tier)
                continue
            
            # Track fallback if not primary
            if i > 0:
                self.metrics.fallback_triggered += 1
                logger.info(f"[LLM ROUTER] ⚠️  Fallback to {tier.value} (tier {i+1}/{len(fallback_chain)})")
            
            try:
                # Invoke model
                logger.info(f"[LLM ROUTER] Invoking {tier.value}...")
                model_start = time.time()
                
                response = model.invoke(messages)
                # Ensure response_content is always a string
                if hasattr(response, 'content'):
                    content = response.content
                    response_content = str(content) if not isinstance(content, str) else content
                else:
                    response_content = str(response)
                
                elapsed = time.time() - model_start
                
                # Record success
                self._record_success(tier)
                
                # Update metrics
                if tier == ModelTier.PRIMARY:
                    self.metrics.primary_invocations += 1
                elif tier == ModelTier.SECONDARY:
                    self.metrics.secondary_invocations += 1
                elif tier == ModelTier.TERTIARY:
                    self.metrics.tertiary_invocations += 1
                
                # Estimate cost
                cost = self._estimate_cost(tier, response_content)
                self.metrics.total_cost_usd += cost
                self.metrics.total_latency_seconds += elapsed
                
                logger.info(
                    f"[LLM ROUTER] ✅ Success with {tier.value} "
                    f"(latency={elapsed:.2f}s, cost=${cost:.4f}, tokens~{len(response_content)//4})"
                )
                
                return response_content
            
            except Exception as e:
                last_error = e
                self._record_failure(tier)
                
                # Update failure metrics
                if tier == ModelTier.PRIMARY:
                    self.metrics.primary_failures += 1
                elif tier == ModelTier.SECONDARY:
                    self.metrics.secondary_failures += 1
                elif tier == ModelTier.TERTIARY:
                    self.metrics.tertiary_failures += 1
                
                logger.error(f"[LLM ROUTER] ❌ {tier.value} failed: {e}")
                
                # If fallback disabled, raise immediately
                if not self.enable_fallback:
                    raise RuntimeError(f"LLM invocation failed (fallback disabled): {e}") from e
                
                # If last model in chain, raise
                if i == len(fallback_chain) - 1:
                    total_elapsed = time.time() - start_time
                    self.metrics.total_latency_seconds += total_elapsed
                    raise RuntimeError(
                        f"All LLM models failed in fallback chain. Last error: {e}"
                    ) from e
                
                # Otherwise continue to next model
                continue
        
        # Should never reach here, but just in case
        raise RuntimeError(f"LLM routing failed: {last_error}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Get current router metrics.
        
        Returns:
            Dictionary with performance and cost metrics
        """
        avg_latency = (
            self.metrics.total_latency_seconds / self.metrics.total_requests
            if self.metrics.total_requests > 0
            else 0.0
        )
        
        return {
            "total_requests": self.metrics.total_requests,
            "primary_invocations": self.metrics.primary_invocations,
            "secondary_invocations": self.metrics.secondary_invocations,
            "tertiary_invocations": self.metrics.tertiary_invocations,
            "primary_failures": self.metrics.primary_failures,
            "secondary_failures": self.metrics.secondary_failures,
            "tertiary_failures": self.metrics.tertiary_failures,
            "total_cost_usd": round(self.metrics.total_cost_usd, 4),
            "avg_latency_seconds": round(avg_latency, 2),
            "fallback_triggered": self.metrics.fallback_triggered,
            "circuit_breaker_trips": self.metrics.circuit_breaker_trips,
            "circuit_states": {
                "primary": self.models[ModelTier.PRIMARY].circuit_open,
                "secondary": self.models[ModelTier.SECONDARY].circuit_open,
                "tertiary": self.models[ModelTier.TERTIARY].circuit_open
            }
        }
    
    def reset_metrics(self):
        """Reset all metrics to zero."""
        self.metrics = RouterMetrics()
        logger.info("[LLM ROUTER] Metrics reset")
    
    def reset_circuit_breakers(self):
        """Force reset all circuit breakers (for testing/debugging)."""
        for tier, config in self.models.items():
            config.circuit_open = False
            config.circuit_open_until = None
            config.consecutive_failures = 0
        logger.info("[LLM ROUTER] All circuit breakers reset")


# Singleton instance for global access
_llm_router_instance: Optional[LLMRouter] = None


def get_llm_router() -> LLMRouter:
    """
    Get singleton LLM router instance.

    Returns:
        Global LLMRouter instance
    """
    global _llm_router_instance

    if _llm_router_instance is None:
        _llm_router_instance = LLMRouter(
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        )

    return _llm_router_instance
