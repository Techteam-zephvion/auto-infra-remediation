"""
HashiCorp Vault Client for Secret Management
Provides secure secret retrieval with fallback to environment variables
"""

import os
import logging
from typing import Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Vault configuration
VAULT_ENABLED = os.getenv("VAULT_ENABLED", "true").lower() == "true"
VAULT_ADDR = os.getenv("VAULT_ADDR", "http://localhost:8200")
VAULT_TOKEN = os.getenv("VAULT_TOKEN", "dev-root-token")
VAULT_MOUNT_POINT = os.getenv("VAULT_MOUNT_POINT", "secret")
VAULT_SECRET_PATH = os.getenv("VAULT_SECRET_PATH", "auto-remediation")

# Global Vault client
_vault_client: Optional[Any] = None


def get_vault_client() -> Optional[Any]:
    """
    Get or create Vault client connection
    
    Returns:
        hvac.Client instance or None if Vault is disabled/unavailable
    """
    global _vault_client
    
    if not VAULT_ENABLED:
        logger.info("[VAULT] Disabled via VAULT_ENABLED=false")
        return None
    
    if _vault_client is None:
        try:
            import hvac
            
            logger.info(f"[VAULT] Connecting to {VAULT_ADDR}...")
            _vault_client = hvac.Client(url=VAULT_ADDR, token=VAULT_TOKEN)
            
            # Verify connection
            if _vault_client.is_authenticated():
                logger.info("[VAULT] Connected and authenticated successfully")
            else:
                logger.error("[VAULT] Authentication failed")
                _vault_client = None
                
        except ImportError:
            logger.error("[VAULT] hvac library not installed. Run: pip install hvac")
            _vault_client = None
        except Exception as e:
            logger.error(f"[VAULT] Connection failed: {e}")
            logger.warning("[VAULT] Will fall back to environment variables")
            _vault_client = None
    
    return _vault_client


def get_secret(secret_name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Retrieve a secret from Vault with fallback to environment variable
    
    Args:
        secret_name: Name of the secret (e.g., "DATABASE_URL")
        default: Default value if secret not found
    
    Returns:
        Secret value or None
    """
    # Try environment variable first (for backward compatibility)
    env_value = os.getenv(secret_name)
    if env_value:
        logger.debug(f"[VAULT] Using {secret_name} from environment")
        return env_value
    
    # Try Vault
    client = get_vault_client()
    if client is None:
        logger.warning(f"[VAULT] Client unavailable, cannot retrieve {secret_name}")
        return default
    
    try:
        # Read from KV v2 secrets engine
        secret_path = f"{VAULT_SECRET_PATH}/data/{secret_name.lower()}"
        logger.debug(f"[VAULT] Reading {secret_path}...")
        
        response = client.secrets.kv.v2.read_secret_version(
            path=secret_name.lower(),
            mount_point=VAULT_MOUNT_POINT
        )
        
        if response and 'data' in response and 'data' in response['data']:
            value = response['data']['data'].get('value')
            logger.info(f"[VAULT] Retrieved {secret_name} from Vault")
            return value
        else:
            logger.warning(f"[VAULT] Secret {secret_name} not found in Vault")
            return default
            
    except Exception as e:
        logger.error(f"[VAULT] Error retrieving {secret_name}: {e}")
        return default


def get_all_secrets() -> Dict[str, str]:
    """
    Retrieve all application secrets from Vault
    
    Returns:
        Dictionary of secret_name -> secret_value
    """
    secrets = {}
    
    secret_names = [
        "DATABASE_URL",
        "OLLAMA_BASE_URL",
        "TEMPORAL_HOST",
        "TEMPORAL_NAMESPACE",
    ]
    
    for name in secret_names:
        value = get_secret(name)
        if value:
            secrets[name] = value
    
    return secrets


def initialize_vault_secrets() -> bool:
    """
    Initialize Vault with default secrets from environment
    Should be run once during setup to migrate secrets to Vault
    
    Returns:
        True if successful, False otherwise
    """
    client = get_vault_client()
    if client is None:
        logger.error("[VAULT] Cannot initialize - client unavailable")
        return False
    
    try:
        # List of secrets to migrate from .env to Vault
        secrets_to_migrate = {
            "database_url": os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/auto_remediation"),
            "ollama_base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            "temporal_host": os.getenv("TEMPORAL_HOST", "localhost:7233"),
            "temporal_namespace": os.getenv("TEMPORAL_NAMESPACE", "default"),
        }
        
        logger.info("[VAULT] Migrating secrets to Vault...")
        
        for key, value in secrets_to_migrate.items():
            try:
                client.secrets.kv.v2.create_or_update_secret(
                    path=key,
                    secret={'value': value},
                    mount_point=VAULT_MOUNT_POINT
                )
                logger.info(f"[VAULT] Stored {key}")
            except Exception as e:
                logger.error(f"[VAULT] Failed to store {key}: {e}")
        
        logger.info("[VAULT] Secret migration complete")
        return True
        
    except Exception as e:
        logger.error(f"[VAULT] Initialization failed: {e}")
        return False


def check_vault_health() -> Dict[str, Any]:
    """
    Check if Vault is accessible and healthy
    
    Returns:
        {
            "status": "healthy" | "unhealthy" | "unavailable",
            "authenticated": bool,
            "address": str,
            "error": str (if failed)
        }
    """
    if not VAULT_ENABLED:
        return {
            "status": "unavailable",
            "authenticated": False,
            "message": "Vault is disabled",
        }
    
    client = get_vault_client()
    if client is None:
        return {
            "status": "unavailable",
            "authenticated": False,
            "error": "Failed to connect to Vault",
        }
    
    try:
        is_authenticated = client.is_authenticated()
        is_sealed = client.seal_status.get('sealed', True)
        
        if is_authenticated and not is_sealed:
            return {
                "status": "healthy",
                "authenticated": True,
                "address": VAULT_ADDR,
                "sealed": False,
            }
        elif is_sealed:
            return {
                "status": "unhealthy",
                "authenticated": is_authenticated,
                "error": "Vault is sealed",
            }
        else:
            return {
                "status": "unhealthy",
                "authenticated": False,
                "error": "Authentication failed",
            }
            
    except Exception as e:
        return {
            "status": "unhealthy",
            "authenticated": False,
            "error": str(e),
        }
