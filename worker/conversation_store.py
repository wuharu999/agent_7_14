from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass

from worker.config import CONVERSATION_MAX_SESSIONS, CONVERSATION_MAX_TURNS


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    question: str
    answer: str


class ConversationStore:
    """Small in-memory conversation history used by the stateless Claude CLI calls.

    The browser owns a random conversation ID. The Worker keeps only the latest
    few turns for that ID so follow-up questions retain context without keeping a
    permanent Claude process alive.
    """

    def __init__(self) -> None:
        self._sessions: OrderedDict[str, deque[ConversationTurn]] = OrderedDict()

    def history(self, conversation_id: str) -> list[ConversationTurn]:
        turns = self._sessions.get(conversation_id)
        if turns is None:
            return []
        self._sessions.move_to_end(conversation_id)
        return list(turns)

    def append(self, conversation_id: str, question: str, answer: str) -> None:
        turns = self._sessions.get(conversation_id)
        if turns is None:
            turns = deque(maxlen=max(1, CONVERSATION_MAX_TURNS))
            self._sessions[conversation_id] = turns
        turns.append(ConversationTurn(question=question, answer=answer))
        self._sessions.move_to_end(conversation_id)
        while len(self._sessions) > max(1, CONVERSATION_MAX_SESSIONS):
            self._sessions.popitem(last=False)

    def clear(self, conversation_id: str) -> None:
        self._sessions.pop(conversation_id, None)
