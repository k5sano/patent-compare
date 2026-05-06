// ================================================================
// 壁打ち chat (Step 2 SUB 4 = 本願 chat / Step 4.5 末尾 = 検索 chat)
// ----------------------------------------------------------------
// 各 chat panel は data-panel-id (例: "hongan", "search") で識別。
// 状態 (現在選択中の thread_id 等) は panelId をキーにした map で持つ。
// ================================================================

window._chat = window._chat || { state: {} };

function _chatState(panelId) {
  if (!window._chat.state[panelId]) {
    window._chat.state[panelId] = { threadId: null, threads: [] };
  }
  return window._chat.state[panelId];
}

function _chatRoot(panelId) { return document.getElementById('chat-root-' + panelId); }
function _chatCaseId(panelId) {
  const r = _chatRoot(panelId);
  return r ? r.dataset.caseId : (window.CASE_ID || null);
}
function _chatTopic(panelId) {
  const r = _chatRoot(panelId);
  return r ? r.dataset.topic : 'free';
}

function _chatStatus(panelId, text, type) {
  const el = document.getElementById('chat-status-' + panelId);
  if (!el) return;
  el.textContent = text || '';
  el.style.color = type === 'error' ? '#fca5a5' :
                   type === 'success' ? '#86efac' : 'var(--text2)';
}

function _chatEsc(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

function _chatRenderMarkdown(text) {
  // 軽量: エスケープ後に **bold**、改行 -> <br>、suggestion マーカーを抜き取る
  let s = _chatEsc(text || '');
  // suggestion マーカー [[suggest kind=... target=... value="..."]] を削除 (UI にカードで別途表示)
  s = s.replace(/\[\[\s*suggest\s+kind=[A-Za-z_]+\s+target=[^\s\]]+\s+value=&quot;(?:[^&]|&[^q])*&quot;\s*\]\]/g, '');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/`([^`]+)`/g, '<code style="background:#1e293b; padding:1px 4px; border-radius:3px;">$1</code>');
  s = s.replace(/\n/g, '<br>');
  return s;
}

// ----------------------------------------------------------------
// スレッド一覧
// ----------------------------------------------------------------

async function chatLoadThreads(panelId) {
  const caseId = _chatCaseId(panelId);
  const topic = _chatTopic(panelId);
  const wrap = document.getElementById('chat-thread-list-' + panelId);
  if (!caseId || !wrap) return;
  try {
    const resp = await fetch(`/case/${caseId}/chat/threads?topic=${encodeURIComponent(topic)}`);
    const d = await resp.json();
    if (!resp.ok || d.error) {
      wrap.innerHTML = `<p style="color:#fca5a5; font-size:0.78rem; padding:0.4rem;">${_chatEsc(d.error || 'HTTP ' + resp.status)}</p>`;
      return;
    }
    _chatState(panelId).threads = d.threads || [];
    _chatRenderThreadList(panelId);
  } catch (e) {
    wrap.innerHTML = `<p style="color:#fca5a5; font-size:0.78rem; padding:0.4rem;">${_chatEsc(e.message)}</p>`;
  }
}

function _chatRenderThreadList(panelId) {
  const wrap = document.getElementById('chat-thread-list-' + panelId);
  if (!wrap) return;
  const st = _chatState(panelId);
  if (!st.threads.length) {
    wrap.innerHTML = '<p style="color:var(--text2); font-size:0.78rem; padding:0.4rem;">スレッドなし</p>';
    return;
  }
  wrap.innerHTML = st.threads.map(t => {
    const isActive = t.id === st.threadId;
    const updatedShort = (t.updated_at || '').slice(5, 16).replace('T', ' ');
    return `
      <div class="chat-thread-row" data-tid="${_chatEsc(t.id)}"
           onclick="chatSelectThread('${_chatEsc(panelId)}', '${_chatEsc(t.id)}')"
           style="padding:0.4rem 0.5rem; background:${isActive ? '#1e3a8a' : '#1e293b'}; border:1px solid ${isActive ? '#3b82f6' : 'var(--border)'}; border-radius:5px; cursor:pointer; display:flex; gap:0.4rem; align-items:flex-start;">
        <div style="flex:1; min-width:0;">
          <div style="font-size:0.82rem; color:#e2e8f0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${_chatEsc(t.title || '(無題)')}</div>
          <div style="font-size:0.7rem; color:#64748b;">${updatedShort} / ${t.message_count || 0} 件</div>
        </div>
        <button onclick="event.stopPropagation(); chatDeleteThread('${_chatEsc(panelId)}', '${_chatEsc(t.id)}')"
                style="background:none; border:none; color:#94a3b8; cursor:pointer; padding:0.1rem 0.3rem; font-size:0.85rem;"
                title="削除">🗑</button>
      </div>
    `;
  }).join('');
}

async function chatCreateThread(panelId) {
  const caseId = _chatCaseId(panelId);
  const topic = _chatTopic(panelId);
  if (!caseId) return;
  const title = prompt('スレッドのタイトル (例: 実施例の必須構成について)');
  if (title == null) return; // cancel
  try {
    const resp = await fetch(`/case/${caseId}/chat/threads`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({topic, title: title.trim()}),
    });
    const d = await resp.json();
    if (!resp.ok || d.error) {
      alert(d.error || 'HTTP ' + resp.status);
      return;
    }
    await chatLoadThreads(panelId);
    chatSelectThread(panelId, d.thread.id);
  } catch (e) {
    alert(e.message);
  }
}

async function chatDeleteThread(panelId, threadId) {
  if (!confirm('このスレッドを削除しますか?')) return;
  const caseId = _chatCaseId(panelId);
  try {
    const resp = await fetch(`/case/${caseId}/chat/threads/${encodeURIComponent(threadId)}`, {
      method: 'DELETE',
    });
    const d = await resp.json();
    if (!resp.ok || d.error) { alert(d.error || 'HTTP ' + resp.status); return; }
    const st = _chatState(panelId);
    if (st.threadId === threadId) {
      st.threadId = null;
      _chatRenderMessages(panelId, []);
      document.getElementById('chat-thread-header-' + panelId).innerHTML = '<span style="color:#64748b;">スレッド未選択</span>';
      document.getElementById('chat-input-' + panelId).disabled = true;
      document.getElementById('chat-send-' + panelId).disabled = true;
    }
    await chatLoadThreads(panelId);
  } catch (e) {
    alert(e.message);
  }
}

// ----------------------------------------------------------------
// スレッド選択 + メッセージ表示
// ----------------------------------------------------------------

async function chatSelectThread(panelId, threadId) {
  const caseId = _chatCaseId(panelId);
  if (!caseId) return;
  const st = _chatState(panelId);
  st.threadId = threadId;
  _chatRenderThreadList(panelId);
  // 入力欄有効化
  document.getElementById('chat-input-' + panelId).disabled = false;
  document.getElementById('chat-send-' + panelId).disabled = false;
  _chatStatus(panelId, '読込中...');
  try {
    const resp = await fetch(`/case/${caseId}/chat/threads/${encodeURIComponent(threadId)}`);
    const d = await resp.json();
    if (!resp.ok || d.error) { _chatStatus(panelId, d.error || 'HTTP ' + resp.status, 'error'); return; }
    const t = d.thread;
    document.getElementById('chat-thread-header-' + panelId).innerHTML =
      `<strong>${_chatEsc(t.title || '(無題)')}</strong> <span style="color:#64748b; font-size:0.78rem; margin-left:0.5rem;">${_chatEsc(t.id)}</span>`;
    _chatRenderMessages(panelId, t.messages || []);
    _chatStatus(panelId, '');
  } catch (e) {
    _chatStatus(panelId, e.message, 'error');
  }
}

function _chatRenderMessages(panelId, messages) {
  const wrap = document.getElementById('chat-messages-' + panelId);
  if (!wrap) return;
  if (!messages.length) {
    wrap.innerHTML = '<p style="color:var(--text2); text-align:center; margin-top:1rem;">最初のメッセージを送信してください</p>';
    return;
  }
  wrap.innerHTML = messages.map(m => _chatRenderOneMessage(panelId, m)).join('');
  wrap.scrollTop = wrap.scrollHeight;
}

function _chatRenderOneMessage(panelId, m) {
  const isUser = m.role === 'user';
  const align = isUser ? 'flex-end' : 'flex-start';
  const bg = isUser ? '#1e40af' : '#1e293b';
  const fg = isUser ? '#dbeafe' : '#e2e8f0';
  const label = isUser ? 'あなた' : 'アシスタント';
  const ts = (m.timestamp || '').slice(11, 16);
  const body = _chatRenderMarkdown(m.content || '');
  // Suggestion カード
  let suggBlock = '';
  const suggs = m.suggestions || [];
  if (suggs.length) {
    suggBlock = '<div style="margin-top:0.5rem; display:flex; flex-direction:column; gap:0.35rem;">' +
      suggs.map(s => _chatRenderSuggestion(panelId, s)).join('') +
      '</div>';
  }
  const errStyle = m._error ? 'border:1px solid #f87171;' : '';
  return `
    <div style="display:flex; flex-direction:column; align-items:${align};">
      <div style="font-size:0.7rem; color:#64748b; margin-bottom:0.15rem;">${label} ${ts}</div>
      <div style="max-width:80%; padding:0.55rem 0.75rem; background:${bg}; color:${fg}; border-radius:8px; ${errStyle}">
        ${body}
        ${suggBlock}
      </div>
    </div>
  `;
}

function _chatRenderSuggestion(panelId, s) {
  const kindLabel = s.kind === 'update_analysis_item' ? '本願分析項目を更新'
                  : s.kind === 'append_understanding_note' ? '予備調査メモに追記'
                  : s.kind === 'add_citation' ? '引用文献に追加'
                  : s.kind;
  const applied = s.applied;
  const valuePreview = (s.value || '').replace(/\n/g, ' ').slice(0, 140);
  return `
    <div style="background:#0f172a; border:1px solid ${applied ? '#22c55e' : '#f59e0b'}; border-radius:6px; padding:0.45rem 0.55rem; font-size:0.78rem;">
      <div style="display:flex; gap:0.4rem; align-items:center;">
        <span style="font-size:0.7rem; padding:0.1rem 0.4rem; background:${applied ? '#14532d' : '#78350f'}; color:${applied ? '#86efac' : '#fde68a'}; border-radius:3px;">${applied ? '✅ 適用済' : '💡 提案'}</span>
        <strong style="color:#cbd5e1;">${_chatEsc(kindLabel)}</strong>
        <code style="font-size:0.72rem; color:#94a3b8;">${_chatEsc(s.target || '')}</code>
        <span style="flex:1;"></span>
        ${applied ? '' : `<button class="btn btn-success" style="font-size:0.72rem; padding:0.1rem 0.5rem;"
            onclick="chatApplySuggestion('${_chatEsc(panelId)}', '${_chatEsc(s.id)}')">適用</button>`}
      </div>
      <div style="margin-top:0.3rem; color:#cbd5e1; word-break:break-word;">${_chatEsc(valuePreview)}${(s.value || '').length > 140 ? '…' : ''}</div>
    </div>
  `;
}

// ----------------------------------------------------------------
// メッセージ送信
// ----------------------------------------------------------------

async function chatSendMessage(panelId) {
  const st = _chatState(panelId);
  if (!st.threadId) { alert('スレッドを選択してください'); return; }
  const ta = document.getElementById('chat-input-' + panelId);
  const content = (ta.value || '').trim();
  if (!content) return;
  const sendBtn = document.getElementById('chat-send-' + panelId);
  sendBtn.disabled = true;
  ta.disabled = true;
  const model = (typeof getPickerModel === 'function')
    ? getPickerModel('chat-' + panelId, 'sonnet')
    : 'sonnet';
  _chatStatus(panelId, `⏳ LLM(${model}) 応答待ち... (5〜30 秒)`);
  try {
    const caseId = _chatCaseId(panelId);
    const resp = await fetch(`/case/${caseId}/chat/threads/${encodeURIComponent(st.threadId)}/message`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content, model}),
    });
    const d = await resp.json();
    if (d.thread) _chatRenderMessages(panelId, d.thread.messages || []);
    if (!resp.ok || d.error) {
      _chatStatus(panelId, d.error || 'HTTP ' + resp.status, 'error');
    } else {
      ta.value = '';
      _chatStatus(panelId, '応答受信', 'success');
      // スレッド一覧の更新時刻を反映
      chatLoadThreads(panelId);
    }
  } catch (e) {
    _chatStatus(panelId, e.message, 'error');
  } finally {
    sendBtn.disabled = false;
    ta.disabled = false;
    ta.focus();
  }
}

// ----------------------------------------------------------------
// 提案適用
// ----------------------------------------------------------------

async function chatApplySuggestion(panelId, suggestionId) {
  if (!confirm('この提案を適用しますか? 既存データが書き換わります。')) return;
  const caseId = _chatCaseId(panelId);
  const st = _chatState(panelId);
  if (!st.threadId) return;
  _chatStatus(panelId, '適用中...');
  try {
    const resp = await fetch(`/case/${caseId}/chat/threads/${encodeURIComponent(st.threadId)}/apply`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({suggestion_id: suggestionId}),
    });
    const d = await resp.json();
    if (!resp.ok || d.error) {
      _chatStatus(panelId, d.error || 'HTTP ' + resp.status, 'error');
      // 引用文献 DL 失敗時の手動 DL 案内
      if (d.suggestion && d.suggestion.dl_hint) {
        const h = d.suggestion.dl_hint;
        const lines = [d.error || '引用文献の自動 DL に失敗しました'];
        if (h.jplatpat_url) lines.push('J-PlatPat: ' + h.jplatpat_url);
        if (h.google_patents_url) lines.push('Google Patents: ' + h.google_patents_url);
        if (h.hint) lines.push(h.hint);
        alert(lines.join('\n\n'));
      }
      return;
    }
    if (d.thread) _chatRenderMessages(panelId, d.thread.messages || []);
    _chatStatus(panelId, '✅ 適用しました', 'success');
  } catch (e) {
    _chatStatus(panelId, e.message, 'error');
  }
}

// ----------------------------------------------------------------
// 初期化
// ----------------------------------------------------------------
// 各 chat-root に対応する SUB タブが初めて開かれた時にロード
document.addEventListener('DOMContentLoaded', () => {
  const roots = document.querySelectorAll('.chat-root');
  roots.forEach(root => {
    const panelId = root.dataset.panelId;
    let loaded = false;
    const loadOnce = () => { if (!loaded) { loaded = true; chatLoadThreads(panelId); } };
    // SUB 4 タブをクリックで初回ロード (本願 chat 用)
    const subInd = document.getElementById('step2-sub-ind-4');
    if (subInd && root.closest('#step2-sub-panel-4')) {
      subInd.addEventListener('click', loadOnce);
    } else {
      // それ以外 (検索 chat) は表示時にロードする手段が無いので可視性を IntersectionObserver で監視
      const io = new IntersectionObserver((entries) => {
        for (const en of entries) {
          if (en.isIntersecting) { loadOnce(); io.disconnect(); break; }
        }
      });
      io.observe(root);
    }
  });
});
