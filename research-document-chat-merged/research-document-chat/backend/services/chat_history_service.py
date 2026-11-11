"""
Chat History Service for managing chat sessions and messages
"""
import logging
import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_
from models.models import ChatSession, ChatMessage

logger = logging.getLogger(__name__)


class ChatHistoryService:
    """Service for managing chat history in PostgreSQL"""

    def __init__(self, db_session: Session):
        """
        Initialize chat history service

        Args:
            db_session: SQLAlchemy database session
        """
        self.db = db_session

    def create_session(self, session_id: str, user_id: str, title: Optional[str] = None) -> ChatSession:
        """
        Create a new chat session

        Args:
            session_id: Unique session identifier
            user_id: User ID who owns this session
            title: Optional title for the session

        Returns:
            Created ChatSession object
        """
        try:
            session = ChatSession(
                session_id=session_id,
                user_id=user_id,
                title=title or "New Chat",
                is_active=True
            )
            self.db.add(session)
            self.db.commit()
            self.db.refresh(session)
            logger.info(f"Created new chat session: {session_id} for user: {user_id}")
            return session
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error creating chat session: {str(e)}")
            raise

    def get_session(self, session_id: str, user_id: Optional[str] = None) -> Optional[ChatSession]:
        """
        Get a chat session by session_id

        Args:
            session_id: Session identifier
            user_id: Optional user ID to filter by (for security)

        Returns:
            ChatSession object or None if not found
        """
        try:
            query = self.db.query(ChatSession).filter(
                ChatSession.session_id == session_id
            )
            if user_id:
                query = query.filter(ChatSession.user_id == user_id)
            return query.first()
        except Exception as e:
            logger.error(f"Error getting chat session: {str(e)}")
            raise

    def get_or_create_session(self, session_id: str, user_id: str, title: Optional[str] = None) -> ChatSession:
        """
        Get existing session or create new one

        Args:
            session_id: Session identifier
            user_id: User ID who owns this session
            title: Optional title for new session

        Returns:
            ChatSession object
        """
        session = self.get_session(session_id, user_id)
        if session is None:
            logger.info(
                f"get_or_create_session: No existing session found for session_id={session_id}, "
                f"creating new one for user={user_id}")
            session = self.create_session(session_id, user_id, title)
        else:
            logger.info(
                f"get_or_create_session: Found existing session with id={session.id} for session_id={session_id}")
        return session

    def get_drafting_state(self, session_id: str) -> Optional[Dict]:
        """
        Get the drafting state for a session, deserialized from JSON

        Args:
            session_id: Session identifier

        Returns:
            Drafting state as dictionary or None
        """
        try:
            session = self.get_session(session_id)
            if session is None or session.drafting_state is None:
                return None

            # Deserialize from JSON if it's a string
            if isinstance(session.drafting_state, str):
                try:
                    return json.loads(session.drafting_state)
                except json.JSONDecodeError:
                    # If it's not valid JSON, return as simple state
                    return {"phase": session.drafting_state}
            return session.drafting_state
        except Exception as e:
            logger.error(f"Error getting drafting state: {str(e)}")
            return None

    def get_uploaded_file_ids(self, session_id: str) -> List[str]:
        """
        Get list of uploaded file IDs for a session

        Args:
            session_id: Session identifier

        Returns:
            List of file IDs or empty list
        """
        try:
            session = self.get_session(session_id)
            if session is None:
                logger.warning(f"Session {session_id} not found when getting uploaded file IDs")
                return []

            # Return empty list if no files uploaded yet (None is expected for new sessions)
            if session.uploaded_file_ids is None:
                logger.info(f"Session {session_id} has no uploaded files yet (uploaded_file_ids is None)")
                return []

            logger.info(f"Getting uploaded file IDs for session {session_id}: raw value = {session.uploaded_file_ids}")

            # Deserialize from JSON if it's a string
            if isinstance(session.uploaded_file_ids, str):
                try:
                    result = json.loads(session.uploaded_file_ids)
                    logger.info(f"Deserialized uploaded_file_ids: {result}")
                    return result
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse uploaded_file_ids for session {session_id}")
                    return []
            return session.uploaded_file_ids if isinstance(session.uploaded_file_ids, list) else []
        except Exception as e:
            logger.error(f"Error getting uploaded file IDs: {str(e)}")
            return []

    def add_uploaded_file_id(self, session_id: str, file_id: str) -> bool:
        """
        Add a file ID to the session's uploaded files list

        Args:
            session_id: Session identifier
            file_id: File ID to add

        Returns:
            True if added successfully
        """
        try:
            logger.info(f"add_uploaded_file_id called: session_id={session_id}, file_id={file_id}")
            session = self.get_session(session_id)
            if session is None:
                logger.warning(f"Session {session_id} not found when adding file ID")
                return False

            # Get current file IDs
            current_ids = self.get_uploaded_file_ids(session_id)

            # Add new file ID if not already present
            if file_id not in current_ids:
                current_ids.append(file_id)
                session.uploaded_file_ids = json.dumps(current_ids)
                self.db.commit()
                logger.info(f"Added file {file_id} to session {session_id}. Session now has {len(current_ids)} files.")
                logger.info(f"Session {session_id} uploaded_file_ids after commit: {session.uploaded_file_ids}")
            else:
                logger.info(f"File {file_id} already in session {session_id}")

            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error adding uploaded file ID: {str(e)}")
            return False

    def update_session(
        self,
        session_id: str,
        active_agent: Optional[str] = None,
        drafting_state: Optional[Any] = None,
        title: Optional[str] = None,
        is_active: Optional[bool] = None,
        uploaded_file_ids: Optional[List[str]] = None
    ) -> Optional[ChatSession]:
        """
        Update session metadata

        Args:
            session_id: Session identifier
            active_agent: Active agent name
            drafting_state: Drafting state (dict or string, will be serialized to JSON)
            title: Session title
            is_active: Whether session is active
            uploaded_file_ids: List of file IDs uploaded during this session

        Returns:
            Updated ChatSession object or None if not found
        """
        try:
            session = self.get_session(session_id)
            if session is None:
                return None

            if active_agent is not None:
                session.active_agent = active_agent
            if drafting_state is not None:
                # Serialize to JSON if it's a dict
                if isinstance(drafting_state, dict):
                    session.drafting_state = json.dumps(drafting_state)
                else:
                    session.drafting_state = drafting_state
            if title is not None:
                session.title = title
            if is_active is not None:
                session.is_active = is_active
            if uploaded_file_ids is not None:
                # Serialize to JSON array
                session.uploaded_file_ids = json.dumps(uploaded_file_ids)

            self.db.commit()
            self.db.refresh(session)
            return session
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error updating chat session: {str(e)}")
            raise

    def _generate_title_from_message(self, content: str) -> str:
        """
        Generate a meaningful title from message content (4-5 words)
        Extracts key topics and nouns, skipping request phrases

        Args:
            content: Message content

        Returns:
            Generated title string
        """
        try:
            # Try LLM-based title generation first for better relevance
            from services.llm_service import LLMService
            from utils.prompt_lib import get_title_generation_prompt

            llm = LLMService()
            prompt = get_title_generation_prompt(content)

            # Use synchronous generation with low temperature for consistent titles
            title = llm.generate(
                messages=prompt,
                temperature=0.3,
                max_tokens=20
            ).strip()

            # Clean up the title
            title = title.strip('"\'')  # Remove quotes if LLM added them

            # Ensure it's not too long
            if len(title) > 50:
                title = title[:47] + "..."

            if title and len(title.split()) <= 7:  # Reasonable title length
                return title

        except Exception as e:
            logger.warning(f"LLM title generation failed, using fallback: {str(e)}")

        # Fallback to improved heuristic method
        return self._generate_title_heuristic(content)

    def _generate_title_heuristic(self, content: str) -> str:
        """
        Fallback heuristic method for title generation
        Focuses on extracting key nouns and topics

        Args:
            content: Message content

        Returns:
            Generated title string
        """
        # Remove extra whitespace and newlines
        clean_content = " ".join(content.split())

        # Expanded list of words to skip - focus on extracting the actual topic
        skip_phrases = {
            'i', 'need', 'want', 'can', 'you', 'please', 'help', 'me', 'with',
            'a', 'an', 'the', 'my', 'how', 'what', 'when', 'where', 'why',
            'do', 'does', 'is', 'are', 'am', 'be', 'have', 'has', 'had',
            'guide', 'assist', 'show', 'tell', 'create', 'make', 'write',
            'creating', 'making', 'writing', 'drafting', 'draft',
            'for', 'to', 'in', 'on', 'at', 'of', 'from', 'by'
        }

        # Question words that should always be skipped
        question_words = {'how', 'what', 'when', 'where', 'why', 'who', 'which', 'can', 'could', 'would', 'should'}

        # Split into words
        words = clean_content.split()

        # Extract meaningful words (nouns, domain terms, capitalized words)
        meaningful_words = []

        for i, word in enumerate(words):
            clean_word = word.strip('.,!?;:()')

            if not clean_word:
                continue

            word_lower = clean_word.lower()

            # Skip question words at the start
            if word_lower in question_words and len(meaningful_words) == 0:
                continue

            # Keep if:
            # 1. Word is all caps (acronym like SOP, ISO, FDA)
            # 2. Word contains number (like ISO13485, 13485)
            # 3. Word is capitalized and not a question word
            # 4. Word is longer than 4 chars and not in skip list
            is_acronym = clean_word.isupper() and len(clean_word) >= 2
            has_number = any(c.isdigit() for c in clean_word)
            is_capitalized = clean_word[0].isupper() and word_lower not in question_words
            is_long_meaningful = len(clean_word) > 4 and word_lower not in skip_phrases

            if is_acronym or has_number or (is_capitalized and len(clean_word) > 2) or is_long_meaningful:
                # Preserve acronyms and mixed case
                if is_acronym or has_number or any(c.isupper() for c in clean_word[1:]):
                    meaningful_words.append(clean_word)
                else:
                    meaningful_words.append(clean_word.capitalize())

                if len(meaningful_words) >= 5:
                    break

        # If we didn't find enough meaningful words, be less strict
        if len(meaningful_words) < 3:
            for word in words:
                clean_word = word.strip('.,!?;:()')
                word_lower = clean_word.lower()

                if clean_word and len(clean_word) > 3 \
                    and word_lower not in skip_phrases \
                        and word_lower not in question_words:

                    meaningful_words.append(clean_word.capitalize())
                    if len(meaningful_words) >= 5:
                        break

        # Create title
        title_words = meaningful_words[:5]

        # Final cleanup: remove any remaining generic action words from start
        while title_words and title_words[0].lower() in {'help', 'need', 'want', 'guide', 'assist'}:
            title_words.pop(0)

        title = " ".join(title_words)

        # If title is too long, truncate
        if len(title) > 50:
            title = title[:47] + "..."

        return title if title else "New Chat"

    def add_message(
        self,
        session_id: str,
        user_id: str,
        role: str,
        content: str,
        agent_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ChatMessage:
        """
        Add a message to a chat session

        Args:
            session_id: Session identifier
            user_id: User ID who owns this session
            role: Message role ('user' or 'assistant')
            content: Message content
            agent_type: Type of agent (if assistant)
            metadata: Additional metadata as dict

        Returns:
            Created ChatMessage object
        """
        try:
            # Get or create session
            session = self.get_or_create_session(session_id, user_id)

            # Create message
            message = ChatMessage(
                session_id=session.id,
                role=role,
                content=content,
                agent_type=agent_type,
                message_metadata=json.dumps(metadata) if metadata else None
            )
            self.db.add(message)

            # Generate meaningful title from first user message
            if role == "user" and session.title == "New Chat":
                session.title = self._generate_title_from_message(content)

            self.db.commit()
            self.db.refresh(message)
            logger.debug(f"Added message to session {session_id}")
            return message
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error adding message: {str(e)}")
            raise

    @staticmethod
    def _parse_message_metadata(raw: Any) -> Dict[str, Any]:
        if not raw:
            return {}
        if isinstance(raw, dict):
            return dict(raw)
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def update_message_metadata(
        self,
        message_id: int,
        session_id: str,
        user_id: str,
        metadata_patch: Dict[str, Any],
    ) -> Optional[ChatMessage]:
        """Merge metadata onto an existing message in a user-owned session."""
        try:
            session = self.get_session(session_id, user_id)
            if session is None:
                return None

            message = (
                self.db.query(ChatMessage)
                .filter(
                    ChatMessage.id == message_id,
                    ChatMessage.session_id == session.id,
                )
                .first()
            )
            if message is None:
                return None

            metadata = self._parse_message_metadata(message.message_metadata)
            metadata.update(metadata_patch or {})
            message.message_metadata = json.dumps(metadata)
            self.db.commit()
            self.db.refresh(message)
            return message
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error updating message metadata: {str(e)}")
            raise

    def set_message_feedback(
        self,
        message_id: int,
        session_id: str,
        user_id: str,
        rating: str,
        comment: Optional[str] = None,
    ) -> Optional[ChatMessage]:
        from datetime import datetime, timezone

        normalized_rating = str(rating or "").strip().lower()
        if normalized_rating not in {"up", "down"}:
            raise ValueError("rating must be 'up' or 'down'")

        feedback = {
            "rating": normalized_rating,
            "comment": str(comment or "").strip(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return self.update_message_metadata(
            message_id=message_id,
            session_id=session_id,
            user_id=user_id,
            metadata_patch={"feedback": feedback},
        )

    def get_session_feedback_context(
        self,
        session_id: str,
        user_id: str,
        limit: int = 8,
    ) -> str:
        """Build prompt-ready feedback notes from prior assistant-message ratings."""
        from utils.chat_engagement import format_session_feedback_for_prompt

        messages = self.get_messages(session_id, user_id)
        notes: list[str] = []
        for msg in messages:
            if msg.role != "assistant":
                continue
            metadata = self._parse_message_metadata(msg.message_metadata)
            feedback = metadata.get("feedback")
            if not isinstance(feedback, dict):
                continue
            rating = str(feedback.get("rating") or "").strip().lower()
            if rating not in {"up", "down"}:
                continue
            preview = " ".join(str(msg.content or "").split())[:120]
            note = f"The answer beginning '{preview}' received a thumbs-{rating}."
            comment = str(feedback.get("comment") or "").strip()
            if comment:
                note += f" User comment: {comment}"
            notes.append(note)
        return format_session_feedback_for_prompt(notes[-limit:])

    def get_messages(
        self,
        session_id: str,
        user_id: str,
        limit: Optional[int] = None,
        agent_type: Optional[str] = None
    ) -> List[ChatMessage]:
        """
        Get messages for a session

        Args:
            session_id: Session identifier
            user_id: User ID (for authorization)
            limit: Optional limit on number of messages to return
            agent_type: Optional filter by agent type (e.g., 'general_chat', 'sop_drafting')

        Returns:
            List of ChatMessage objects ordered by creation time
        """
        try:
            session = self.get_session(session_id, user_id)
            if session is None:
                logger.warning(f"get_messages: Session not found for session_id={session_id}, user_id={user_id}")
                return []

            logger.debug(f"get_messages: Found session with id={session.id}, session_id={session_id}")

            query = self.db.query(ChatMessage).filter(
                ChatMessage.session_id == session.id
            )

            # Filter by agent type if specified
            if agent_type:
                # Include user messages (no agent_type) and messages from specified agent
                query = query.filter(
                    (ChatMessage.agent_type == agent_type) | (ChatMessage.agent_type.is_(None))
                )
                logger.debug(f"get_messages: Filtering by agent_type={agent_type}")

            query = query.order_by(ChatMessage.created_at)

            if limit:
                query = query.limit(limit)

            messages = query.all()
            logger.debug(f"get_messages: Found {len(messages)} messages for session_id={session_id}")
            return messages
        except Exception as e:
            logger.error(f"Error getting messages: {str(e)}")
            raise

    def get_conversation_history(
        self,
        session_id: str,
        user_id: str,
        agent_type: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """
        Get conversation history in format suitable for AI agents

        Args:
            session_id: Session identifier
            user_id: User ID (for authorization)
            agent_type: Optional filter by agent type

        Returns:
            List of dicts with 'role' and 'content' keys
        """
        messages = self.get_messages(session_id, user_id, agent_type=agent_type)
        agent_filter = f" filtered by agent_type={agent_type}" if agent_type else ""
        logger.info(
            f"get_conversation_history: Retrieved {len(messages)} messages "
            f"for session_id={session_id}{agent_filter}"
        )
        return [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]

    def list_sessions(
        self,
        user_id: str,
        limit: int = 50,
        include_inactive: bool = False,
        agent_type: Optional[str] = None
    ) -> List[ChatSession]:
        """
        List chat sessions for a specific user

        Args:
            user_id: User ID to filter sessions by
            limit: Maximum number of sessions to return
            include_inactive: Whether to include inactive sessions
            agent_type: Optional filter by agent type (e.g., 'general_chat', 'sop_drafting')

        Returns:
            List of ChatSession objects ordered by most recent
        """
        try:
            query = self.db.query(ChatSession).filter(
                ChatSession.user_id == user_id
            )

            if not include_inactive:
                query = query.filter(ChatSession.is_active.is_(True))

            # Filter by agent type if specified
            if agent_type:
                query = query.filter(ChatSession.active_agent == agent_type)
                logger.debug(f"Filtering sessions by agent_type={agent_type}")

            query = query.order_by(desc(ChatSession.updated_at)).limit(limit)
            sessions = query.all()

            # Document drafting history is shared across GQS / SOP / URS. Older SOP/URS
            # sessions can fall outside the recent limit — pin them without dropping
            # the current recent list.
            if (agent_type or "").strip() == "document_drafting":
                sessions = self._merge_pinned_sop_urs_drafting_sessions(
                    user_id=user_id,
                    sessions=sessions,
                    include_inactive=include_inactive,
                )

            agent_filter = f" (agent: {agent_type})" if agent_type else ""
            logger.info(f"Found {len(sessions)} sessions for user {user_id}{agent_filter}")
            return sessions
        except Exception as e:
            logger.error(f"Error listing sessions: {str(e)}")
            raise

    def _merge_pinned_sop_urs_drafting_sessions(
        self,
        *,
        user_id: str,
        sessions: List[ChatSession],
        include_inactive: bool = False,
        pin_limit: int = 25,
    ) -> List[ChatSession]:
        """
        Ensure SOP/URS document-drafting chats remain visible in History.

        Keeps the incoming recent list intact and appends any matching SOP/URS
        sessions that were truncated by the recent-session limit.
        """
        try:
            existing_ids = {s.id for s in sessions if getattr(s, "id", None) is not None}
            title_match = or_(
                ChatSession.title.ilike("%SOP%"),
                ChatSession.title.ilike("%URS%"),
                ChatSession.title.ilike("%Standard Operating%"),
                ChatSession.title.ilike("%User Requirement%"),
            )
            pin_query = self.db.query(ChatSession).filter(
                ChatSession.user_id == user_id,
                ChatSession.active_agent == "document_drafting",
                title_match,
            )
            if not include_inactive:
                pin_query = pin_query.filter(ChatSession.is_active.is_(True))
            if existing_ids:
                pin_query = pin_query.filter(~ChatSession.id.in_(existing_ids))

            extras = (
                pin_query.order_by(desc(ChatSession.updated_at))
                .limit(max(1, int(pin_limit or 25)))
                .all()
            )
            if not extras:
                return sessions

            merged = list(sessions) + list(extras)
            merged.sort(
                key=lambda s: s.updated_at or s.created_at or datetime.min,
                reverse=True,
            )
            logger.info(
                "Pinned %d SOP/URS drafting session(s) into history for user %s",
                len(extras),
                user_id,
            )
            return merged
        except Exception as exc:
            logger.warning(
                "Could not pin SOP/URS drafting sessions for user %s: %s",
                user_id,
                exc,
            )
            return sessions

    def delete_session(self, session_id: str, user_id: str) -> bool:
        """
        Delete a chat session and all its messages

        Args:
            session_id: Session identifier
            user_id: User ID (for authorization)

        Returns:
            True if deleted, False if not found
        """
        try:
            session = self.get_session(session_id, user_id)
            if session is None:
                return False

            self.db.delete(session)
            self.db.commit()
            logger.info(f"Deleted chat session: {session_id}")
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error deleting session: {str(e)}")
            raise

    def clear_old_sessions(self, days: int = 30) -> int:
        """
        Clear sessions older than specified days

        Args:
            days: Number of days to keep

        Returns:
            Number of sessions deleted
        """
        try:
            from datetime import datetime, timedelta
            cutoff_date = datetime.utcnow() - timedelta(days=days)

            deleted = self.db.query(ChatSession).filter(
                ChatSession.updated_at < cutoff_date
            ).delete()

            self.db.commit()
            logger.info(f"Cleared {deleted} old chat sessions")
            return deleted
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error clearing old sessions: {str(e)}")
            raise
