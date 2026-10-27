(() => {
  const box = document.getElementById('messages-area');
  if (!box) return;
  const endpoint = box.dataset.apiUrl;
  const placeholder = document.getElementById('chat-placeholder');
  let lastId = 0;
  let loading = false;

  function appendMessage(m) {
    if (placeholder) placeholder.hidden = true;
    const outer = document.createElement('div');
    outer.className = `chat-message ${m.is_mine ? 'is-mine' : 'is-other'} ${m.kind === 'delivery' ? 'is-delivery' : ''}`;
    const head = document.createElement('div');
    head.className = 'message-head';
    const author = document.createElement('b');
    author.textContent = m.is_admin ? `${m.author} · Desenvolvedor` : m.author;
    const when = document.createElement('span');
    when.textContent = m.created_at;
    head.append(author, when);
    outer.append(head);
    if (m.kind === 'delivery') {
      const tag = document.createElement('span');
      tag.className = 'delivery-tag';
      tag.textContent = '✦ Entrega do projeto';
      outer.append(tag);
    }
    const body = document.createElement('p');
    body.className = 'message-body';
    body.textContent = m.body || '📎 Arquivos anexados';
    outer.append(body);
    for (const file of m.files) {
      const anchor = document.createElement('a');
      anchor.className = 'message-file';
      anchor.href = file.url;
      anchor.textContent = `${file.is_delivery ? '↓ Arquivo entregue:' : '↓ Anexo:'} ${file.name} (${file.size} KB)`;
      anchor.setAttribute('download', '');
      outer.append(anchor);
    }
    box.append(outer);
  }

  async function refresh() {
    if (loading || document.hidden) return;
    loading = true;
    try {
      let more = true;
      let pages = 0;
      while (more && pages < 5) {
        const response = await fetch(`${endpoint}?after=${lastId}`, {
          headers: { Accept: 'application/json' }, credentials: 'same-origin', cache: 'no-store'
        });
        if (!response.ok) throw new Error(`Falha HTTP ${response.status}`);
        const data = await response.json();
        const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120 || lastId === 0;
        for (const m of data.messages) {
          if (m.id <= lastId) continue;
          appendMessage(m);
          lastId = m.id;
        }
        if (nearBottom) box.scrollTop = box.scrollHeight;
        more = data.has_more && data.messages.length > 0;
        pages++;
      }
    } catch (error) {
      console.warn('Não foi possível atualizar o chat agora:', error);
    } finally {
      loading = false;
    }
  }
  refresh();
  setInterval(refresh, 8000);
  window.addEventListener('focus', refresh);
})();
