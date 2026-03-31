"""
Notification System - Multi-channel alerting (Slack, PagerDuty)
Sends notifications for medium/high severity alerts that need human attention
"""
import logging
import os
import json
from typing import Dict, Any, Optional
from datetime import datetime
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Configuration
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
PAGERDUTY_API_KEY = os.getenv("PAGERDUTY_API_KEY", "")
PAGERDUTY_INTEGRATION_KEY = os.getenv("PAGERDUTY_INTEGRATION_KEY", "")
NOTIFICATIONS_ENABLED = os.getenv("NOTIFICATIONS_ENABLED", "false").lower() == "true"


class NotificationService:
    """Multi-channel notification service"""
    
    def __init__(self):
        self.slack_enabled = bool(SLACK_WEBHOOK_URL) and NOTIFICATIONS_ENABLED
        self.pagerduty_enabled = bool(PAGERDUTY_INTEGRATION_KEY) and NOTIFICATIONS_ENABLED
        
        if self.slack_enabled:
            logger.info("[NOTIFICATIONS] Slack notifications enabled")
        if self.pagerduty_enabled:
            logger.info("[NOTIFICATIONS] PagerDuty notifications enabled")
        
        if not NOTIFICATIONS_ENABLED:
            logger.info("[NOTIFICATIONS] Notifications disabled via NOTIFICATIONS_ENABLED=false")
    
    async def send_slack_notification(
        self,
        alert_type: str,
        severity: str,
        message: str,
        workflow_id: str,
        namespace: str = "default",
        pod_name: str = "",
        analysis: str = "",
        action_taken: str = ""
    ) -> bool:
        """
        Send Slack notification
        
        Args:
            alert_type: Type of alert
            severity: Severity level
            message: Alert message
            workflow_id: Workflow identifier
            namespace: Kubernetes namespace
            pod_name: Affected pod name
            analysis: LLM analysis
            action_taken: Remediation action taken
        
        Returns:
            True if notification sent successfully
        """
        if not self.slack_enabled:
            logger.debug("[SLACK] Slack notifications disabled")
            return False
        
        try:
            # Choose color based on severity
            color_map = {
                'low': '#36a64f',      # Green
                'medium': '#ff9900',   # Orange
                'high': '#ff6600',     # Red-Orange
                'critical': '#ff0000'  # Red
            }
            color = color_map.get(severity.lower(), '#808080')
            
            # Build Slack message with blocks
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"🚨 {alert_type.upper().replace('_', ' ')} - {severity.upper()}",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Namespace:*\n{namespace}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Pod:*\n{pod_name or 'N/A'}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Workflow ID:*\n`{workflow_id}`"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Time:*\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        }
                    ]
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Message:*\n{message}"
                    }
                }
            ]
            
            if analysis:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Analysis:*\n```{analysis[:500]}```"
                    }
                })
            
            if action_taken:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Action Taken:*\n{action_taken}"
                    }
                })
            
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "🤖 AutoInfraRemediation System"
                    }
                ]
            })
            
            payload = {
                "attachments": [{
                    "color": color,
                    "blocks": blocks
                }]
            }
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    SLACK_WEBHOOK_URL,
                    json=payload
                )
                
                if response.status_code == 200:
                    logger.info(f"[SLACK] Notification sent for workflow {workflow_id}")
                    return True
                else:
                    logger.error(f"[SLACK] Failed to send notification: {response.status_code}")
                    return False
        
        except Exception as e:
            logger.error(f"[SLACK] Error sending notification: {e}")
            return False
    
    async def create_pagerduty_incident(
        self,
        alert_type: str,
        severity: str,
        message: str,
        workflow_id: str,
        namespace: str = "default",
        pod_name: str = "",
        analysis: str = ""
    ) -> Optional[str]:
        """
        Create PagerDuty incident for critical alerts
        
        Args:
            alert_type: Type of alert
            severity: Severity level
            message: Alert message
            workflow_id: Workflow identifier
            namespace: Kubernetes namespace
            pod_name: Affected pod name
            analysis: LLM analysis
        
        Returns:
            Incident ID if created successfully, None otherwise
        """
        if not self.pagerduty_enabled:
            logger.debug("[PAGERDUTY] PagerDuty integration disabled")
            return None
        
        try:
            # Map severity to PagerDuty severity
            pd_severity_map = {
                'low': 'info',
                'medium': 'warning',
                'high': 'error',
                'critical': 'critical'
            }
            pd_severity = pd_severity_map.get(severity.lower(), 'error')
            
            # Build incident payload
            payload = {
                "routing_key": PAGERDUTY_INTEGRATION_KEY,
                "event_action": "trigger",
                "dedup_key": workflow_id,
                "payload": {
                    "summary": f"{alert_type.upper().replace('_', ' ')}: {message}",
                    "severity": pd_severity,
                    "source": "AutoInfraRemediation",
                    "timestamp": datetime.now().isoformat(),
                    "component": namespace,
                    "group": pod_name or "unknown",
                    "class": alert_type,
                    "custom_details": {
                        "workflow_id": workflow_id,
                        "namespace": namespace,
                        "pod_name": pod_name,
                        "analysis": analysis[:1000] if analysis else "No analysis available",
                        "alert_type": alert_type
                    }
                },
                "links": [{
                    "href": f"http://localhost:8080/namespaces/default/workflows/{workflow_id}",
                    "text": "View Workflow in Temporal UI"
                }]
            }
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://events.pagerduty.com/v2/enqueue",
                    json=payload
                )
                
                if response.status_code == 202:
                    result = response.json()
                    incident_id = result.get('dedup_key')
                    logger.info(f"[PAGERDUTY] Incident created: {incident_id}")
                    return incident_id
                else:
                    logger.error(f"[PAGERDUTY] Failed to create incident: {response.status_code}")
                    return None
        
        except Exception as e:
            logger.error(f"[PAGERDUTY] Error creating incident: {e}")
            return None
    
    async def notify(
        self,
        escalation_actions: list,
        alert_type: str,
        severity: str,
        message: str,
        workflow_id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute notification escalation actions
        
        Args:
            escalation_actions: List of actions to take
            alert_type: Type of alert
            severity: Severity level
            message: Alert message
            workflow_id: Workflow identifier
            **kwargs: Additional context (namespace, pod_name, analysis, etc.)
        
        Returns:
            Dictionary with notification results
        """
        results = {
            "slack_sent": False,
            "pagerduty_incident_id": None,
            "notifications_attempted": True
        }
        
        if not NOTIFICATIONS_ENABLED:
            logger.info("[NOTIFICATIONS] Notifications disabled, skipping")
            results["notifications_attempted"] = False
            return results
        
        # Execute each escalation action
        for action in escalation_actions:
            if action == 'notify_slack':
                results["slack_sent"] = await self.send_slack_notification(
                    alert_type=alert_type,
                    severity=severity,
                    message=message,
                    workflow_id=workflow_id,
                    **kwargs
                )
            
            elif action == 'create_pagerduty':
                results["pagerduty_incident_id"] = await self.create_pagerduty_incident(
                    alert_type=alert_type,
                    severity=severity,
                    message=message,
                    workflow_id=workflow_id,
                    **kwargs
                )
        
        return results


# Global instance
_notification_service = None


def get_notification_service() -> NotificationService:
    """Get or create notification service instance"""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service
