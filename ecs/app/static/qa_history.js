// Guests retain their local transcript; signed-in transcripts come from SQLite.
// Account message text and images are never copied into browser storage.
let historyAccount = null;
let historyReady = false;
let historyLoading = false;
let accountConversationId = '';
let recentConversations = [];
let olderTurnId = null;
let hasOlderTurns = false;
let hasMoreConversations = false;
let historyNoticeKey = '';

function historyText(key) {
  return i18n[currentLang]?.[key] || i18n.en[key] || key;
}

function setHistoryNotice(key) {
  historyNoticeKey = key;
  const notice = document.getElementById('historyNotice');
  notice.textContent = key ? historyText(key) : '';
  notice.hidden = !key;
}

function updateHistoryUI() {
  const note = document.getElementById('historyStorageNote');
  note.textContent = historyText(historyAccount ? 'accountHistory' : 'localHistory');
  document.getElementById('importHistory').hidden = !historyAccount || !readLocalConversations().length;
  document.getElementById('moreConversations').hidden = !historyAccount || !hasMoreConversations;
  document.getElementById('olderMessages').hidden = !historyAccount || !hasOlderTurns;
  setHistoryNotice(historyNoticeKey);
}

function setHistoryLoading(loading) {
  historyLoading = loading;
  for (const control of [askButton, newConversationButton, mobileNewConversation, team, language,
    document.getElementById('importHistory'), document.getElementById('moreConversations'),
    document.getElementById('olderMessages')]) control.disabled = loading;
  renderRecentConversations();
}

function validMessages(messages) {
  return Array.isArray(messages) ? messages.filter(message => message &&
    ['user', 'bot'].includes(message.role) && typeof message.content === 'string'
  ).map(({role, content}) => ({role, content})) : [];
}

function readLocalConversations() {
  let conversations = [];
  try {
    const stored = JSON.parse(localStorage.getItem(recentKey) || '[]');
    if (Array.isArray(stored)) conversations = stored.filter(item => item &&
      typeof item.id === 'string' && typeof item.title === 'string' && Array.isArray(item.messages)
    ).slice(0, 30);
    const messages = validMessages(JSON.parse(localStorage.getItem(historyKey) || '[]'));
    const id = localStorage.getItem(conversationKey);
    if (id && messages.some(message => message.role === 'user') && !conversations.some(item => item.id === id)) {
      conversations.unshift({id, title: messages.find(message => message.role === 'user').content.slice(0, 100),
        team: localStorage.getItem(teamKey) || 'all', language: localStorage.getItem(languageKey) || 'zh-CN', messages});
    }
  } catch {}
  return conversations;
}

function activeChatKey() { return 'agent1_active_chat_' + historyAccount.user_id; }

function setConversationId(id) {
  if (historyAccount) {
    accountConversationId = id;
    try { sessionStorage.setItem(activeChatKey(), id); } catch {}
  } else {
    localStorage.setItem(conversationKey, id);
  }
}

function saveHistory() {
  const firstQuestion = currentChat.find(message => message.role === 'user');
  if (historyAccount) {
    // The /ask endpoint saves account history as it receives the answer.
    if (firstQuestion && accountConversationId) {
      recentConversations = [{id: accountConversationId, title: firstQuestion.content.slice(0, 100),
        team: team.value, language: language.value},
        ...recentConversations.filter(item => item.id !== accountConversationId)];
    }
  } else {
    try { localStorage.setItem(historyKey, JSON.stringify(currentChat)); } catch {}
    if (firstQuestion) {
      const id = getConversationId();
      recentConversations = [{id, title: firstQuestion.content.slice(0, 100),
        team: team.value, language: language.value, messages: validMessages(currentChat).slice(-60)},
        ...recentConversations.filter(item => item.id !== id)].slice(0, 30);
      try { localStorage.setItem(recentKey, JSON.stringify(recentConversations)); } catch {}
    }
  }
  renderRecentConversations();
}

function renderRecentConversations() {
  recentList.replaceChildren();
  document.getElementById('historyEmpty').hidden = recentConversations.length > 0;
  recentConversations.forEach(conversation => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'history-item';
    button.textContent = conversation.title;
    button.title = conversation.title;
    button.disabled = askButton.disabled || historyLoading;
    if (conversation.id === getConversationId()) {
      button.classList.add('active');
      button.setAttribute('aria-current', 'true');
    }
    button.addEventListener('click', () => openConversation(conversation));
    recentList.appendChild(button);
  });
}

function restoreSelectors(conversation) {
  team.value = [...team.options].some(option => option.value === conversation.team) ? conversation.team : 'all';
  language.value = i18n[conversation.language] ? conversation.language : currentLang;
  currentLang = language.value;
  localStorage.setItem(teamKey, team.value);
  localStorage.setItem(languageKey, currentLang);
  // Notify both the QA page and the shared account menu when a saved chat
  // restores a different language (including on a fresh device).
  language.dispatchEvent(new Event('change', {bubbles: true}));
}

async function historyFetch(path, options = {}) {
  const response = await fetch(path, {cache: 'no-store', ...options});
  if (response.status === 401 || response.status === 403) {
    // Do not let an expired or changed account silently become a guest save.
    await verifyHistorySession();
    throw new Error('historySessionExpired');
  }
  if (!response.ok) {
    const error = new Error('historyLoadError');
    error.status = response.status;
    throw error;
  }
  return response.json();
}

async function verifyHistorySession() {
  const response = await fetch('/api/me', {cache: 'no-store'});
  if (!response.ok) throw new Error('historyLoadError');
  const account = await response.json();
  if ((account.logged_in ? account.user_id : null) !== (historyAccount?.user_id ?? null)) {
    // Clear the old account's rendered data before reloading the page/menu.
    currentChat = [];
    recentConversations = [];
    renderChat();
    renderRecentConversations();
    historyReady = false;
    window.location.reload();
    return false;
  }
  if (historyAccount) historyAccount = account;
  return true;
}

async function refreshAccountHistory(append = false) {
  const offset = append ? recentConversations.length : 0;
  const result = await historyFetch('/api/conversations?offset=' + offset);
  recentConversations = append ? [...recentConversations, ...result.conversations] : result.conversations;
  // Avoid duplicates if another device added a conversation between pages.
  recentConversations = [...new Map(recentConversations.map(item => [item.id, item])).values()];
  hasMoreConversations = result.has_more;
  renderRecentConversations();
  updateHistoryUI();
}

function messagesFromTurns(turns) {
  return turns.flatMap(turn => [
    {role: 'user', content: turn.question},
    {role: 'bot', content: turn.answer, images: turn.images || [], status: turn.status},
  ]);
}

async function loadAccountConversation(id, older = false) {
  const suffix = older && olderTurnId ? '?before=' + olderTurnId : '';
  const conversation = await historyFetch('/api/conversations/' + encodeURIComponent(id) + suffix);
  const messages = messagesFromTurns(conversation.turns);
  const previousHeight = chatHistory.scrollHeight;
  const previousTop = chatHistory.scrollTop;
  currentChat = older ? [...messages, ...currentChat] : messages;
  setConversationId(conversation.id);
  if (!older) restoreSelectors(conversation);
  olderTurnId = conversation.turns[0]?.id || null;
  hasOlderTurns = conversation.has_more;
  loadShownImageFingerprints();
  renderChat();
  if (older) chatHistory.scrollTop = previousTop + chatHistory.scrollHeight - previousHeight;
  updateHistoryUI();
  renderRecentConversations();
}

async function openConversation(conversation) {
  if (askButton.disabled || historyLoading) return;
  setHistoryLoading(true);
  try {
    if (!await verifyHistorySession()) return;
    if (historyAccount) await loadAccountConversation(conversation.id);
    else {
      saveHistory();
      setConversationId(conversation.id);
      currentChat = validMessages(conversation.messages);
      restoreSelectors(conversation);
      loadShownImageFingerprints();
      saveHistory();
      renderChat();
    }
    setHistoryNotice('');
    setMobileMenu(false);
  } catch { setHistoryNotice('historyLoadError'); }
  finally { setHistoryLoading(false); }
}

async function importBrowserConversations() {
  if (askButton.disabled || !historyAccount) return;
  setHistoryLoading(true);
  setHistoryNotice('historyImporting');
  try {
    if (!await verifyHistorySession()) return;
    for (const conversation of readLocalConversations().slice().reverse()) {
      const messages = validMessages(conversation.messages);
      if (!messages.length) continue;
      await historyFetch('/api/conversations/import', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': historyAccount.csrf_token},
        body: JSON.stringify({legacy_id: conversation.id, team: conversation.team || 'all',
          language: i18n[conversation.language] ? conversation.language : 'zh-CN', messages}),
      });
    }
    await refreshAccountHistory();
    setHistoryNotice('historyImported');
  } catch { setHistoryNotice('historyImportError'); }
  finally { setHistoryLoading(false); }
}

async function initializeHistory() {
  setHistoryLoading(true);
  setHistoryNotice('');
  try {
    const response = await fetch('/api/me', {cache: 'no-store'});
    if (!response.ok) throw new Error('Account unavailable');
    const account = await response.json();
    historyAccount = account.logged_in ? account : null;
    if (historyAccount) {
      await refreshAccountHistory();
      let active = null;
      try { active = sessionStorage.getItem(activeChatKey()); } catch {}
      if (active === null) active = recentConversations[0]?.id || '';
      if (active) {
        try { await loadAccountConversation(active); }
        catch (error) {
          if (error.status !== 404) throw error;
          setConversationId('');
          renderChat();
        }
      } else renderChat();
    } else {
      recentConversations = readLocalConversations();
      loadHistory();
      loadShownImageFingerprints();
      saveHistory();
    }
    historyReady = true;
    updateHistoryUI();
  } catch { setHistoryNotice('historyLoadError'); }
  finally { setHistoryLoading(false); }
}

async function loadMoreHistory(older) {
  if (!historyAccount || askButton.disabled) return;
  setHistoryLoading(true);
  try {
    if (!await verifyHistorySession()) return;
    if (older) await loadAccountConversation(accountConversationId, true);
    else await refreshAccountHistory(true);
  } catch { setHistoryNotice('historyLoadError'); }
  finally { setHistoryLoading(false); }
}
