"""
Base Agent class - Template for all specialized agents
"""
from typing import Dict, List, Optional, AsyncGenerator
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for all specialized agents"""

    def __init__(self, agent_name: str):
        """
        Initialize base agent

        Args:
            agent_name: Name of the agent
        """
        self.agent_name = agent_name
        self.logger = logging.getLogger(f"Agent.{agent_name}")

    @abstractmethod
    async def process(
        self,
        user_message: str,
        conversation_history: List[Dict],
        uploaded_files: Optional[List[Dict]] = None,
        session_context: Optional[Dict] = None
    ) -> AsyncGenerator[str, None]:
        """
        Process user request

        Args:
            user_message: User's message
            conversation_history: Previous conversation messages
            uploaded_files: List of uploaded files with metadata
            session_context: Session-specific context

        Yields:
            Streaming response chunks
        """
        pass

    def get_system_prompt(self) -> str:
        """
        Get system prompt for this agent

        Returns:
            System prompt string
        """
        return ""

    async def stream_response(self, text: str) -> AsyncGenerator[str, None]:
        """
        Helper to stream a text response

        Args:
            text: Text to stream

        Yields:
            Text chunks
        """
        # Stream in chunks for better UX
        chunk_size = 50
        for i in range(0, len(text), chunk_size):
            yield text[i:i + chunk_size]
