"""
Alert Tuning Module - Dynamic threshold management and escalation logic
Provides configurable thresholds per alert type and multi-level escalation
"""
import logging
import yaml
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class SeverityLevel(Enum):
    """Alert severity levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EscalationAction(Enum):
    """Escalation action types"""
    AUTO_REMEDIATE = "auto_remediate"
    NOTIFY_SLACK = "notify_slack"
    CREATE_PAGERDUTY = "create_pagerduty"
    SUPPRESS = "suppress"


class AlertTuningConfig:
    """Alert tuning configuration manager"""
    
    def __init__(self, config_path: str = "infra/alert-thresholds.yaml"):
        self.config_path = Path(config_path)
        self.config: Dict[str, Any] = {}
        self.maintenance_windows: List[Dict[str, Any]] = []
        self.load_config()
    
    def load_config(self) -> None:
        """Load alert thresholds from YAML configuration"""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r') as f:
                    self.config = yaml.safe_load(f) or {}
                logger.info(f"[ALERT_TUNING] Loaded config from {self.config_path}")
                
                # Load maintenance windows
                self.maintenance_windows = self.config.get('maintenance_windows', [])
                if self.maintenance_windows:
                    logger.info(f"[ALERT_TUNING] Loaded {len(self.maintenance_windows)} maintenance windows")
            else:
                logger.warning(f"[ALERT_TUNING] Config file not found: {self.config_path}, using defaults")
                self._load_defaults()
        except Exception as e:
            logger.error(f"[ALERT_TUNING] Failed to load config: {e}")
            self._load_defaults()
    
    def _load_defaults(self) -> None:
        """Load default alert thresholds"""
        self.config = {
            'alert_types': {
                'cpu_spike': {
                    'threshold': 80,
                    'unit': 'percent',
                    'duration': '5m',
                    'severity': 'high',
                    'auto_remediate': True,
                    'escalation': ['auto_remediate', 'notify_slack']
                },
                'memory_leak': {
                    'threshold': 85,
                    'unit': 'percent',
                    'duration': '10m',
                    'severity': 'critical',
                    'auto_remediate': True,
                    'escalation': ['auto_remediate', 'notify_slack', 'create_pagerduty']
                },
                'pod_crash_loop': {
                    'threshold': 3,
                    'unit': 'count',
                    'duration': '5m',
                    'severity': 'high',
                    'auto_remediate': True,
                    'escalation': ['auto_remediate', 'notify_slack']
                },
                'disk_space_low': {
                    'threshold': 90,
                    'unit': 'percent',
                    'duration': '10m',
                    'severity': 'medium',
                    'auto_remediate': False,
                    'escalation': ['notify_slack']
                },
                'service_unavailable': {
                    'threshold': 1,
                    'unit': 'count',
                    'duration': '1m',
                    'severity': 'critical',
                    'auto_remediate': True,
                    'escalation': ['auto_remediate', 'notify_slack', 'create_pagerduty']
                }
            },
            'maintenance_windows': []
        }
        logger.info("[ALERT_TUNING] Loaded default configuration")
    
    def get_threshold(self, alert_type: str) -> Dict[str, Any]:
        """
        Get threshold configuration for alert type
        
        Args:
            alert_type: Type of alert (e.g., 'cpu_spike')
        
        Returns:
            Threshold configuration dict
        """
        alert_types = self.config.get('alert_types', {})
        
        if alert_type in alert_types:
            return alert_types[alert_type]
        
        # Return default if not found
        logger.warning(f"[ALERT_TUNING] No config for {alert_type}, using defaults")
        return {
            'threshold': 80,
            'unit': 'percent',
            'duration': '5m',
            'severity': 'medium',
            'auto_remediate': True,
            'escalation': ['auto_remediate', 'notify_slack']
        }
    
    def should_auto_remediate(self, alert_type: str, severity: Optional[str] = None) -> bool:
        """
        Determine if alert should trigger auto-remediation
        
        Args:
            alert_type: Type of alert
            severity: Alert severity level
        
        Returns:
            True if auto-remediation should proceed
        """
        # Check maintenance window
        if self.is_maintenance_window():
            logger.info(f"[ALERT_TUNING] Suppressing {alert_type} - maintenance window active")
            return False
        
        threshold_config = self.get_threshold(alert_type)
        
        # Check if auto-remediation is enabled
        if not threshold_config.get('auto_remediate', False):
            logger.info(f"[ALERT_TUNING] Auto-remediation disabled for {alert_type}")
            return False
        
        # Check severity override
        if severity:
            severity_level = severity.lower()
            if severity_level == 'low':
                logger.info(f"[ALERT_TUNING] Suppressing low severity {alert_type}")
                return False
        
        return True
    
    def get_escalation_actions(self, alert_type: str, severity: Optional[str] = None) -> List[str]:
        """
        Get escalation actions for alert type
        
        Args:
            alert_type: Type of alert
            severity: Alert severity level
        
        Returns:
            List of escalation action strings
        """
        threshold_config = self.get_threshold(alert_type)
        escalation = threshold_config.get('escalation', ['notify_slack'])
        
        # Upgrade escalation for critical severity
        if severity and severity.lower() == 'critical':
            if 'create_pagerduty' not in escalation:
                escalation = escalation + ['create_pagerduty']
        
        return escalation
    
    def is_maintenance_window(self) -> bool:
        """
        Check if current time is within a maintenance window
        
        Returns:
            True if in maintenance window
        """
        now = datetime.now()
        
        for window in self.maintenance_windows:
            try:
                start = datetime.fromisoformat(window['start'])
                end = datetime.fromisoformat(window['end'])
                
                if start <= now <= end:
                    logger.info(f"[ALERT_TUNING] Maintenance window active: {window.get('name', 'unnamed')}")
                    return True
            except Exception as e:
                logger.error(f"[ALERT_TUNING] Invalid maintenance window: {e}")
                continue
        
        return False
    
    def add_maintenance_window(self, name: str, start: str, end: str, reason: str = "") -> None:
        """
        Add a maintenance window
        
        Args:
            name: Window name/identifier
            start: Start time (ISO format)
            end: End time (ISO format)
            reason: Reason for maintenance
        """
        window = {
            'name': name,
            'start': start,
            'end': end,
            'reason': reason
        }
        self.maintenance_windows.append(window)
        logger.info(f"[ALERT_TUNING] Added maintenance window: {name} ({start} to {end})")
    
    def reload_config(self) -> None:
        """Reload configuration from file"""
        logger.info("[ALERT_TUNING] Reloading configuration")
        self.load_config()


# Global instance
_alert_tuning = None


def get_alert_tuning() -> AlertTuningConfig:
    """Get or create alert tuning config instance"""
    global _alert_tuning
    if _alert_tuning is None:
        _alert_tuning = AlertTuningConfig()
    return _alert_tuning
