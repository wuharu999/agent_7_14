from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Sequence
from dataclasses import dataclass

from worker.config import CONVERSATION_MAX_SESSIONS, CONVERSATION_MAX_TURNS


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    question: str
    answer: str


class ConversationStore:
    """Small in-memory conversation history used by stateless provider API calls.

    The browser owns a random conversation ID. The Worker keeps only the latest
    few turns for that ID so follow-up questions retain context without keeping a
    permanent provider process alive.
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

    def seed_if_empty(
        self,
        conversation_id: str,
        turns: Sequence[ConversationTurn],
    ) -> None:
        """Restore bounded browser-visible history after a Worker restart.

        Existing Worker history remains authoritative. Browser history is used
        only when the conversation ID is unknown to this Worker process and is
        still treated as untrusted prompt context by the QA layer.
        """
        if not turns or conversation_id in self._sessions:
            return
        bounded = deque(turns, maxlen=max(1, CONVERSATION_MAX_TURNS))
        self._sessions[conversation_id] = bounded
        self._sessions.move_to_end(conversation_id)
        while len(self._sessions) > max(1, CONVERSATION_MAX_SESSIONS):
            self._sessions.popitem(last=False)

    def clear(self, conversation_id: str) -> None:
        self._sessions.pop(conversation_id, None)
