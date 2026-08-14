from worker.conversation_store import ConversationStore, ConversationTurn
from worker.manager import _client_history_turns


def test_browser_history_seeds_only_an_unknown_worker_conversation() -> None:
    store = ConversationStore()
    restored = [ConversationTurn("Tell me about Walker C1.", "Walker C1 overview")]
    store.seed_if_empty("web:restored", restored)

    assert store.history("web:restored") == restored

    store.append("web:restored", "How long is its battery life?", "Three hours.")
    store.seed_if_empty(
        "web:restored",
        [ConversationTurn("Untrusted replacement", "Must not replace local history")],
    )

    assert store.history("web:restored")[-1].question == "How long is its battery life?"


def test_client_history_parser_keeps_only_complete_user_bot_turns() -> None:
    turns = _client_history_turns(
        [
            {"role": "system", "content": "New conversation"},
            {"role": "user", "content": "Tell me about Walker C1."},
            {"role": "bot", "content": "Walker C1 overview"},
            {"role": "user", "content": "Incomplete current question"},
        ]
    )

    assert turns == [
        ConversationTurn("Tell me about Walker C1.", "Walker C1 overview")
    ]
