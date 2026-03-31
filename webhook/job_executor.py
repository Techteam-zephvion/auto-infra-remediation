"""
Kubernetes Job Executor for Sandboxed Script Execution

This module provides secure, isolated execution of remediation scripts
in ephemeral Kubernetes Jobs with strict security controls.

Security Features:
- Network isolation via NetworkPolicy
- Resource limits (CPU, memory, timeout)
- Read-only root filesystem
- Non-root user execution
- No privilege escalation
- Automatic cleanup after completion

Usage:
    executor = JobExecutor()
    result = await executor.execute_script(
        script="kubectl scale deployment nginx --replicas=3",
        workflow_id="WF-123456",
        namespace="production",
        alert_type="cpu_spike",
        pod_name="nginx-abc123"
    )
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple
import yaml

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


class JobExecutor:
    """Executes remediation scripts in isolated Kubernetes Jobs"""
    
    def __init__(self):
        """Initialize Kubernetes client and load Job template"""
        self.template_path = Path(__file__).parent.parent / "infra" / "sandbox-job-template.yaml"
        self.job_template = self._load_template()
        self._init_k8s_client()
    
    def _init_k8s_client(self) -> None:
        """Initialize Kubernetes client"""
        try:
            # Try in-cluster config first
            config.load_incluster_config()
            logger.info("[JOB_EXECUTOR] Using in-cluster Kubernetes config")
        except config.ConfigException:
            # Fall back to kubeconfig
            config.load_kube_config()
            logger.info("[JOB_EXECUTOR] Using kubeconfig")
        
        self.batch_api = client.BatchV1Api()
        self.core_api = client.CoreV1Api()
    
    def _load_template(self) -> Dict:
        """Load Job template from YAML file"""
        try:
            with open(self.template_path, 'r') as f:
                template = yaml.safe_load(f)
            logger.info(f"[JOB_EXECUTOR] Loaded Job template from {self.template_path}")
            return template
        except Exception as e:
            logger.error(f"[JOB_EXECUTOR] Failed to load template: {e}")
            raise
    
    def _create_job_manifest(
        self,
        script: str,
        workflow_id: str,
        namespace: str,
        alert_type: str,
        pod_name: str
    ) -> Dict:
        """
        Create Job manifest from template with variable substitution
        
        Args:
            script: Shell script to execute
            workflow_id: Workflow identifier
            namespace: Target namespace
            alert_type: Type of alert triggering remediation
            pod_name: Target pod name
        
        Returns:
            Complete Job manifest ready for creation
        """
        # Deep copy template to avoid mutation
        import copy
        job_manifest = copy.deepcopy(self.job_template)
        
        # Replace placeholders in metadata
        job_manifest['metadata']['name'] = f"remediation-sandbox-{workflow_id.lower()}"
        job_manifest['metadata']['namespace'] = namespace
        job_manifest['metadata']['labels']['workflow-id'] = workflow_id
        
        # Replace placeholders in spec
        container = job_manifest['spec']['template']['spec']['containers'][0]
        
        # Escape script for shell execution
        escaped_script = script.replace('"', '\\"').replace('$', '\\$')
        container['args'] = ["-c", escaped_script]
        
        # Replace environment variables
        for env_var in container['env']:
            if env_var['name'] == 'WORKFLOW_ID':
                env_var['value'] = workflow_id
            elif env_var['name'] == 'ALERT_TYPE':
                env_var['value'] = alert_type
            elif env_var['name'] == 'NAMESPACE':
                env_var['value'] = namespace
            elif env_var['name'] == 'POD_NAME':
                env_var['value'] = pod_name
        
        logger.debug(f"[JOB_EXECUTOR] Created Job manifest for {workflow_id}")
        return job_manifest
    
    async def execute_script(
        self,
        script: str,
        workflow_id: str,
        namespace: str = "default",
        alert_type: str = "custom",
        pod_name: str = "unknown",
        timeout: int = 60
    ) -> Dict[str, any]:
        """
        Execute script in sandboxed Kubernetes Job
        
        Args:
            script: Shell script to execute
            workflow_id: Unique workflow identifier
            namespace: Kubernetes namespace
            alert_type: Alert type (for logging/metrics)
            pod_name: Target pod name
            timeout: Maximum execution time in seconds
        
        Returns:
            Dictionary with execution results:
            {
                'success': bool,
                'job_name': str,
                'stdout': str,
                'stderr': str,
                'exit_code': int,
                'duration_seconds': float,
                'error': Optional[str]
            }
        """
        start_time = time.time()
        job_name = f"remediation-sandbox-{workflow_id.lower()}"
        
        try:
            # Create Job manifest
            job_manifest = self._create_job_manifest(
                script=script,
                workflow_id=workflow_id,
                namespace=namespace,
                alert_type=alert_type,
                pod_name=pod_name
            )
            
            # Create Job
            logger.info(f"[JOB_EXECUTOR] Creating Job: {job_name} in namespace {namespace}")
            self.batch_api.create_namespaced_job(
                namespace=namespace,
                body=job_manifest
            )
            
            # Wait for Job completion
            job_status, logs = await self._wait_for_job_completion(
                job_name=job_name,
                namespace=namespace,
                timeout=timeout
            )
            
            duration = time.time() - start_time
            
            # Parse results
            success = job_status.get('succeeded', 0) > 0
            failed = job_status.get('failed', 0) > 0
            
            result = {
                'success': success,
                'job_name': job_name,
                'stdout': logs.get('stdout', ''),
                'stderr': logs.get('stderr', ''),
                'exit_code': 0 if success else (1 if failed else -1),
                'duration_seconds': round(duration, 2),
                'error': None if success else logs.get('error', 'Job failed')
            }
            
            logger.info(f"[JOB_EXECUTOR] Job {job_name} completed: success={success}, duration={duration:.2f}s")
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"[JOB_EXECUTOR] Job execution failed: {e}")
            
            return {
                'success': False,
                'job_name': job_name,
                'stdout': '',
                'stderr': '',
                'exit_code': -1,
                'duration_seconds': round(duration, 2),
                'error': str(e)
            }
        
        finally:
            # Cleanup is handled by ttlSecondsAfterFinished in Job spec
            # Jobs are automatically deleted 300s after completion
            pass
    
    async def _wait_for_job_completion(
        self,
        job_name: str,
        namespace: str,
        timeout: int = 60
    ) -> Tuple[Dict, Dict]:
        """
        Wait for Job to complete and retrieve logs
        
        Args:
            job_name: Name of the Job
            namespace: Namespace
            timeout: Maximum wait time in seconds
        
        Returns:
            Tuple of (job_status, logs)
        """
        start_time = time.time()
        pod_name = None
        
        while time.time() - start_time < timeout:
            try:
                # Get Job status
                job = self.batch_api.read_namespaced_job(
                    name=job_name,
                    namespace=namespace
                )
                
                status = job.status
                
                # Check if Job completed
                if status.succeeded:
                    logger.info(f"[JOB_EXECUTOR] Job {job_name} succeeded")
                    pod_name = await self._get_job_pod_name(job_name, namespace)
                    logs = await self._get_pod_logs(pod_name, namespace) if pod_name else {}
                    return {'succeeded': 1, 'failed': 0}, logs
                
                if status.failed:
                    logger.warning(f"[JOB_EXECUTOR] Job {job_name} failed")
                    pod_name = await self._get_job_pod_name(job_name, namespace)
                    logs = await self._get_pod_logs(pod_name, namespace) if pod_name else {}
                    return {'succeeded': 0, 'failed': 1}, logs
                
                # Job still running
                await asyncio.sleep(1)
                
            except ApiException as e:
                logger.error(f"[JOB_EXECUTOR] Error checking Job status: {e}")
                await asyncio.sleep(1)
        
        # Timeout
        logger.error(f"[JOB_EXECUTOR] Job {job_name} timed out after {timeout}s")
        pod_name = await self._get_job_pod_name(job_name, namespace)
        logs = await self._get_pod_logs(pod_name, namespace) if pod_name else {}
        return {
            'succeeded': 0,
            'failed': 0,
            'timeout': True
        }, {**logs, 'error': f'Job timed out after {timeout}s'}
    
    async def _get_job_pod_name(self, job_name: str, namespace: str) -> Optional[str]:
        """Get pod name for a Job"""
        try:
            pods = self.core_api.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={job_name}"
            )
            
            if pods.items:
                return pods.items[0].metadata.name
            
            return None
            
        except Exception as e:
            logger.error(f"[JOB_EXECUTOR] Error getting Job pod: {e}")
            return None
    
    async def _get_pod_logs(self, pod_name: str, namespace: str) -> Dict[str, str]:
        """Retrieve logs from pod"""
        try:
            logs = self.core_api.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace
            )
            
            return {
                'stdout': logs,
                'stderr': ''  # K8s combines stdout/stderr
            }
            
        except Exception as e:
            logger.error(f"[JOB_EXECUTOR] Error retrieving logs: {e}")
            return {
                'stdout': '',
                'stderr': str(e),
                'error': f'Failed to retrieve logs: {str(e)}'
            }


# Global singleton instance
_job_executor = None


def get_job_executor() -> JobExecutor:
    """Get or create JobExecutor singleton"""
    global _job_executor
    if _job_executor is None:
        _job_executor = JobExecutor()
    return _job_executor
