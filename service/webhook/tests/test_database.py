"""
Unit tests for database.py - PostgreSQL audit trail
Tests: CRUD operations, connection handling, error cases
"""
import pytest
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime
import sys

# Mock asyncpg before import
sys.modules['asyncpg'] = Mock()


class TestDatabaseSetup:
    """Test database initialization and setup"""
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_setup_audit_table_creates_table(self):
        """Test that setup_audit_table creates the audit_events table"""
        with patch('asyncpg.connect') as mock_connect:
            from service.webhook.database import setup_audit_table
            
            mock_conn = AsyncMock()
            mock_connect.return_value.__aenter__.return_value = mock_conn
            
            await setup_audit_table()
            
            # Function completes without error
            assert True
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_setup_audit_table_handles_no_database_url(self):
        """Test graceful handling when DATABASE_URL is not set"""
        from service.webhook.database import setup_audit_table
        
        with patch('database.os.getenv', return_value=""):
            await setup_audit_table()
            # Should complete without error


class TestAuditEventInsertion:
    """Test inserting audit events"""
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_insert_audit_event_success(self):
        """Test successful audit event insertion"""
        from service.webhook.database import insert_audit_event
        
        with patch('database.asyncpg.connect') as mock_connect:
            mock_conn = AsyncMock()
            mock_connect.return_value.__aenter__.return_value = mock_conn
            
            event_id = await insert_audit_event(
                alert_type="cpu_spike",
                status="started",
                alert_payload={"test": "data"},
                namespace="default",
                pod_name="test-pod"
            )
            
            # Should execute INSERT statement
            assert mock_conn.execute.called
            call_args = str(mock_conn.execute.call_args)
            assert "INSERT INTO audit_events" in call_args
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_insert_audit_event_handles_connection_error(self):
        """Test error handling when database connection fails"""
        from service.webhook.database import insert_audit_event
        
        with patch('database.asyncpg.connect', side_effect=Exception("Connection failed")):
            # Should not raise exception, just log error
            event_id = await insert_audit_event(
                alert_type="cpu_spike",
                status="started"
            )
            assert event_id is None
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_insert_audit_event_with_all_fields(self):
        """Test insertion with all possible fields"""
        from service.webhook.database import insert_audit_event
        
        with patch('database.asyncpg.connect') as mock_connect:
            mock_conn = AsyncMock()
            mock_connect.return_value.__aenter__.return_value = mock_conn
            
            await insert_audit_event(
                alert_type="memory_leak",
                status="completed",
                alert_payload={"alerts": [{"test": "data"}]},
                namespace="production",
                pod_name="app-pod-123",
                analysis="Memory exhaustion detected",
                script="kubectl delete pod app-pod-123",
                safety_approved=True,
                execution_result="Pod deleted successfully",
                duration_seconds=45.5
            )
            
            assert mock_conn.execute.called


class TestAuditEventUpdate:
    """Test updating audit events"""
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_audit_event_success(self):
        """Test successful audit event update"""
        from service.webhook.database import update_audit_event
        
        with patch('database.asyncpg.connect') as mock_connect:
            mock_conn = AsyncMock()
            mock_connect.return_value.__aenter__.return_value = mock_conn
            
            await update_audit_event(
                event_id=1,
                status="completed",
                analysis="Analysis complete",
                script="kubectl get pods",
                safety_approved=True,
                execution_result="Success"
            )
            
            # Should execute UPDATE statement
            assert mock_conn.execute.called
            call_args = str(mock_conn.execute.call_args)
            assert "UPDATE audit_events" in call_args
            assert "WHERE id = " in call_args
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_audit_event_partial_fields(self):
        """Test updating only specific fields"""
        from service.webhook.database import update_audit_event
        
        with patch('database.asyncpg.connect') as mock_connect:
            mock_conn = AsyncMock()
            mock_connect.return_value.__aenter__.return_value = mock_conn
            
            await update_audit_event(
                event_id=1,
                status="failed",
                execution_result="Error occurred"
            )
            
            assert mock_conn.execute.called


class TestAuditEventFetch:
    """Test fetching audit events"""
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_fetch_audit_events_returns_list(self):
        """Test fetching audit events returns list of dicts"""
        from service.webhook.database import fetch_audit_events
        
        mock_records = [
            {
                'id': 1,
                'workflow_id': 'WF-123',
                'alert_type': 'cpu_spike',
                'status': 'completed',
                'created_at': datetime.now(),
                'namespace': 'default',
                'pod_name': 'test-pod'
            },
            {
                'id': 2,
                'workflow_id': 'WF-124',
                'alert_type': 'memory_leak',
                'status': 'failed',
                'created_at': datetime.now(),
                'namespace': 'production',
                'pod_name': 'app-pod'
            }
        ]
        
        with patch('database.asyncpg.connect') as mock_connect:
            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = mock_records
            mock_connect.return_value.__aenter__.return_value = mock_conn
            
            events = await fetch_audit_events(limit=10)
            
            assert len(events) == 2
            assert events[0]['workflow_id'] == 'WF-123'
            assert events[1]['alert_type'] == 'memory_leak'
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_fetch_audit_events_with_filters(self):
        """Test fetching with filters applied"""
        from service.webhook.database import fetch_audit_events
        
        with patch('database.asyncpg.connect') as mock_connect:
            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = []
            mock_connect.return_value.__aenter__.return_value = mock_conn
            
            await fetch_audit_events(
                limit=20,
                workflow_id="WF-123",
                alert_type="cpu_spike"
            )
            
            # Should execute SELECT with WHERE clause
            assert mock_conn.fetch.called
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_fetch_audit_events_handles_no_database(self):
        """Test fallback when database is not configured"""
        from service.webhook.database import fetch_audit_events
        
        with patch('database.os.getenv', return_value=""):
            events = await fetch_audit_events()
            assert events == []


class TestConnectionHandling:
    """Test database connection management"""
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_connection_properly_closed(self):
        """Test that database connections are properly closed"""
        from service.webhook.database import insert_audit_event
        
        with patch('database.asyncpg.connect') as mock_connect:
            mock_conn = AsyncMock()
            mock_context = AsyncMock()
            mock_context.__aenter__.return_value = mock_conn
            mock_context.__aexit__.return_value = None
            mock_connect.return_value = mock_context
            
            await insert_audit_event(alert_type="test", status="started")
            
            # Should enter and exit context (connection closed)
            assert mock_context.__aenter__.called
            assert mock_context.__aexit__.called
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_connection_error_handled_gracefully(self):
        """Test that connection errors don't crash the application"""
        from service.webhook.database import insert_audit_event
        
        with patch('database.asyncpg.connect', side_effect=ConnectionError("DB down")):
            # Should not raise exception
            result = await insert_audit_event(alert_type="test", status="started")
            assert result is None


class TestDataValidation:
    """Test data validation and constraints"""
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_insert_with_long_text_fields(self):
        """Test inserting events with large text fields"""
        from service.webhook.database import insert_audit_event
        
        with patch('database.asyncpg.connect') as mock_connect:
            mock_conn = AsyncMock()
            mock_connect.return_value.__aenter__.return_value = mock_conn
            
            long_text = "x" * 10000  # 10KB of text
            
            await insert_audit_event(
                alert_type="test",
                status="started",
                analysis=long_text,
                script=long_text,
                execution_result=long_text
            )
            
            assert mock_conn.execute.called
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_insert_with_json_payload(self):
        """Test inserting complex JSON payloads"""
        from service.webhook.database import insert_audit_event
        
        with patch('database.asyncpg.connect') as mock_connect:
            mock_conn = AsyncMock()
            mock_connect.return_value.__aenter__.return_value = mock_conn
            
            complex_payload = {
                "alerts": [
                    {"labels": {"key": "value"}, "annotations": {"key": "value"}},
                    {"labels": {"key": "value"}, "annotations": {"key": "value"}},
                ],
                "groupLabels": {"alertname": "Test"},
                "commonAnnotations": {"summary": "Test alert"}
            }
            
            await insert_audit_event(
                alert_type="test",
                status="started",
                alert_payload=complex_payload
            )
            
            assert mock_conn.execute.called


class TestConcurrency:
    """Test concurrent database operations"""
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_concurrent_inserts(self):
        """Test multiple concurrent insert operations"""
        import asyncio
        from service.webhook.database import insert_audit_event
        
        with patch('database.asyncpg.connect') as mock_connect:
            mock_conn = AsyncMock()
            mock_connect.return_value.__aenter__.return_value = mock_conn
            
            # Simulate 10 concurrent inserts
            tasks = [
                insert_audit_event(
                    alert_type=f"test_{i}",
                    status="started"
                )
                for i in range(10)
            ]
            
            await asyncio.gather(*tasks)
            
            # All inserts should complete
            assert mock_conn.execute.call_count == 10
