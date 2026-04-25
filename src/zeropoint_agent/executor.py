"""Executor for DAG reconciliation."""

import logging
from typing import Dict, Any, Optional
from enum import Enum

from zeropoint_agent.dag import DAG
from zeropoint_agent.entities import DAGNode, NodeStatus
from zeropoint_agent.nodes import DiskNode, PartitionNode, FormatNode, MountNode

logger = logging.getLogger(__name__)


class ReconcileResult(Enum):
    """Result of reconciliation."""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class Executor:
    """
    Executor for DAG reconciliation.
    
    Loads DAG, executes nodes in topological order, updates statuses, saves DAG.
    """
    
    # Mapping of node type to implementation class
    NODE_TYPES = {
        'disk': DiskNode,
        'partition': PartitionNode,
        'format': FormatNode,
        'mount': MountNode,
    }
    
    def __init__(self, dag: DAG):
        """
        Initialize executor.
        
        Args:
            dag: DAG instance to execute
        """
        self.dag = dag
    
    def execute(self) -> ReconcileResult:
        """
        Execute full DAG in topological order.
        
        Returns:
            ReconcileResult indicating overall success/failure
        """
        logger.info("Starting DAG execution")
        
        try:
            self.dag.validate()
        except ValueError as e:
            logger.error(f"DAG validation failed: {e}")
            return ReconcileResult.FAILED
        
        # Get execution order
        node_ids = self.dag.topological_sort()
        logger.info(f"Execution order: {' -> '.join(node_ids)}")
        
        # Track results and statuses
        node_results: Dict[str, Any] = {}
        failed_nodes = []
        blocked_nodes = []
        
        # Execute each node
        for node_id in node_ids:
            node = self.dag.get_node(node_id)
            
            try:
                # Check if parents succeeded
                parent_ids = self.dag.get_parents(node_id)
                parent_results = {}
                
                for parent_id in parent_ids:
                    if parent_id in blocked_nodes or parent_id in failed_nodes:
                        logger.info(f"Blocking {node_id}: parent {parent_id} failed")
                        node.status = NodeStatus.BLOCKED.value
                        blocked_nodes.append(node_id)
                        self.dag.add_node(node)
                        continue
                    
                    parent_results[parent_id] = node_results.get(parent_id)
                
                if node.status == NodeStatus.BLOCKED.value:
                    continue
                
                # Get node implementation
                node_impl = self._get_node_impl(node.type)
                
                # Execute operation
                logger.info(f"Executing {node.operation} on {node.id}")
                node.status = NodeStatus.RUNNING.value
                
                if node.operation == 'add':
                    result = node_impl.add(node.desired, parent_results)
                elif node.operation == 'remove':
                    result = node_impl.remove(node.desired)
                else:
                    raise NotImplementedError(f"Operation not implemented: {node.operation}")
                
                node.result = result
                node.status = NodeStatus.SUCCESS.value
                node_results[node_id] = result
                
                logger.info(f"✓ {node.id} {node.operation} succeeded")
                self.dag.add_node(node)
            
            except Exception as e:
                logger.error(f"✗ {node.id} failed: {e}")
                node.status = NodeStatus.ERROR.value
                node.error = str(e)
                failed_nodes.append(node_id)
                self.dag.add_node(node)
        
        # Determine result
        self.dag.save()
        
        if failed_nodes:
            logger.error(f"Execution failed: {len(failed_nodes)} nodes failed, {len(blocked_nodes)} blocked")
            return ReconcileResult.FAILED
        elif blocked_nodes:
            logger.warning(f"Execution partial: {len(blocked_nodes)} nodes blocked")
            return ReconcileResult.PARTIAL
        else:
            logger.info("Execution succeeded")
            return ReconcileResult.SUCCESS
    
    def _get_node_impl(self, node_type: str) -> Any:
        """
        Get node implementation instance.
        
        Args:
            node_type: Type string (e.g., "disk", "partition")
            
        Returns:
            Node implementation instance
            
        Raises:
            ValueError if type not found
        """
        if node_type not in self.NODE_TYPES:
            raise ValueError(f"Unknown node type: {node_type}")
        
        return self.NODE_TYPES[node_type]()
