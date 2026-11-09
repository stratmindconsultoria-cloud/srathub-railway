// Portal do hub: lista as ferramentas, status em tempo real, iniciar/parar, logs e plugar por .zip.
const $ = s => document.querySelector(s)
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const TIPO = { static: 'Estático', python: 'Python', node: 'Node', comando: 'Comando' }
const STATUS = { ativo: 'Ativo', disponivel: 'Disponível', parado: 'Sob demanda', iniciando: 'Iniciando…', instalando: 'Instalando…', erro: 'Erro' }
let apps = [], filtroCat = 'Todas', menuAberto = null, info = {}

async function api(path, opts = {}) {
  const r = await fetch(path, opts)
  if (r.status === 401) { location.href = '/entrar'; throw new Error('sessão') }
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(d.detail || 'Falha')
  return d
}
function toast(m) { const t = document.createElement('div'); t.className = 'toast'; t.textContent = m; document.body.appendChild(t); setTimeout(() => t.remove(), 2600) }
const desde = ts => { if (!ts) return ''; const m = Math.round((Date.now() / 1000 - ts) / 60); return m < 1 ? 'agora' : m < 60 ? `há ${m} min` : `há ${Math.round(m / 60)} h` }

async function carregar() {
  try { apps = await api('/api/hub/apps') } catch { return }
  const ativos = apps.filter(a => a.processo && a.status === 'ativo').length
  $('#resumo').innerHTML = `<b>${apps.length}</b> ferramenta${apps.length === 1 ? '' : 's'} · <b>${ativos}</b> processo${ativos === 1 ? '' : 's'} ativo${ativos === 1 ? '' : 's'}`
  desenhar()
}

function desenhar() {
  const cats = ['Todas', ...new Set(apps.map(a => a.categoria))]
  if (!cats.includes(filtroCat)) filtroCat = 'Todas'
  $('#chips').innerHTML = cats.length > 2 ? cats.map(c => `<button class="chip ${c === filtroCat ? 'on' : ''}" data-cat="${esc(c)}">${esc(c)}</button>`).join('') : ''
  const q = $('#busca').value.trim().toLowerCase()
  const lista = apps.filter(a => (filtroCat === 'Todas' || a.categoria === filtroCat) && (!q || (a.nome + ' ' + a.descricao + ' ' + a.categoria + ' ' + a.id).toLowerCase().includes(q)))
  $('#vazio').hidden = apps.length > 0
  $('#grade').innerHTML = lista.map(a => {
    const st = a.status || 'parado'
    const proc = a.processo && st === 'ativo' ? `<div class="info-proc">porta ${a.porta} · ${a.memoria_mb ?? '?'} MB · iniciado ${desde(a.iniciado_em)}</div>` : ''
    const erro = a.erro || (st === 'erro' && a.mensagem) ? `<div class="erro-app">${esc(a.erro || a.mensagem)}</div>` : ''
    return `<div class="card" data-id="${esc(a.id)}">
      <div class="cab"><div class="icone">${esc(a.icone)}</div><div style="min-width:0;flex:1"><h3>${esc(a.nome)}</h3><div class="desc">${esc(a.descricao || 'Sem descrição')}</div></div></div>
      <div class="meta"><span class="tag">${esc(a.categoria)}</span><span class="tag">${TIPO[a.tipo] || a.tipo}</span>${a.publico ? '<span class="tag" title="Acessível sem login">público</span>' : ''}
        <span class="status ${st}"><i></i>${STATUS[st] || st}</span></div>
      ${erro}${proc}
      <div class="pe">
        <a class="btn primario abrir" href="${esc(a.url)}" target="_blank" rel="noopener" ${a.ativo ? '' : 'aria-disabled="true" style="pointer-events:none;opacity:.5"'}>Abrir →</a>
        <button class="btn" data-menu="${esc(a.id)}" title="Mais ações">⋯</button>
      </div>
      ${menuAberto === a.id ? `<div class="menu">
        ${a.processo && info.admin ? `<button data-acao="iniciar">▶ Iniciar agora</button><button data-acao="reiniciar">↻ Reiniciar</button><button data-acao="parar">■ Parar</button><button data-acao="logs">☰ Ver log</button>` : (info.admin ? `<button data-acao="logs">☰ Ver log</button>` : '')}
        <button data-acao="link">⧉ Copiar link</button>
      </div>` : ''}
    </div>`
  }).join('')
}

document.addEventListener('click', async e => {
  const chip = e.target.closest('[data-cat]'); if (chip) { filtroCat = chip.dataset.cat; desenhar(); return }
  const m = e.target.closest('[data-menu]'); if (m) { menuAberto = menuAberto === m.dataset.menu ? null : m.dataset.menu; desenhar(); return }
  const ac = e.target.closest('[data-acao]')
  if (ac) {
    const id = ac.closest('.card').dataset.id; const acao = ac.dataset.acao; menuAberto = null; desenhar()
    if (acao === 'logs') return abrirLog(id)
    if (acao === 'link') { const u = location.origin + `/apps/${id}/`; try { await navigator.clipboard.writeText(u); toast('Link copiado') } catch { prompt('Link:', u) } return }
    try { await api(`/api/hub/apps/${id}/${acao}`, { method: 'POST' }); toast({ iniciar: 'Iniciando…', parar: 'Parado', reiniciar: 'Reiniciando…' }[acao]); carregar() } catch (err) { toast(err.message) }
    return
  }
  if (menuAberto && !e.target.closest('.menu')) { menuAberto = null; desenhar() }
})
$('#busca').addEventListener('input', desenhar)
$('#bRecarregar').onclick = async () => { await api('/api/hub/recarregar', { method: 'POST' }); await carregar(); toast('Pasta de apps verificada') }
$('#bSair').onclick = async () => { await fetch('/api/hub/sair', { method: 'POST' }); location.href = '/entrar' }

// ── Log ──
function modal(html, largo) {
  const f = document.createElement('div'); f.className = 'fundo'
  f.innerHTML = `<div class="modal ${largo ? 'largo' : ''}">${html}</div>`
  f.addEventListener('click', ev => { if (ev.target === f) fechar() })
  document.body.appendChild(f)
  const fechar = () => { f.remove(); document.removeEventListener('keydown', esc_) }
  const esc_ = ev => { if (ev.key === 'Escape') fechar() }
  document.addEventListener('keydown', esc_)
  return { el: f, fechar }
}
function abrirLog(id) {
  const a = apps.find(x => x.id === id)
  const m = modal(`<h2>Log · ${esc(a?.nome || id)}</h2><p class="sub">Atualiza automaticamente · últimas 400 linhas</p><pre class="log" id="logtxt">carregando…</pre><div class="linha"><button class="btn" id="fecharLog">Fechar</button></div>`, true)
  let vivo = true
  const tick = async () => {
    if (!vivo) return
    try { const d = await api(`/api/hub/apps/${id}/logs?linhas=400`); const p = m.el.querySelector('#logtxt'); const fim = p.scrollTop + p.clientHeight >= p.scrollHeight - 30; p.textContent = d.log || '(log vazio — o app ainda não foi iniciado)'; if (fim) p.scrollTop = p.scrollHeight } catch {}
    setTimeout(tick, 2000)
  }
  tick()
  m.el.querySelector('#fecharLog').onclick = () => { vivo = false; m.fechar() }
  const obs = new MutationObserver(() => { if (!document.body.contains(m.el)) { vivo = false; obs.disconnect() } }); obs.observe(document.body, { childList: true })
}

// ── Plugar ──
$('#bPlugar').onclick = () => {
  const m = modal(`<h2>Plugar ferramenta</h2><p class="sub">Envie um .zip com a pasta do app (precisa ter um <b>hub.json</b>).</p>
    <label class="soltar" id="zona"><input type="file" id="arq" accept=".zip" hidden><div style="font-size:26px">📦</div><div><b>Escolha o .zip</b> ou arraste aqui</div><div id="nomeArq" style="font-size:12px;margin-top:6px"></div></label>
    <div style="display:grid;grid-template-columns:1fr auto;gap:10px;margin-top:14px;align-items:end">
      <div><label class="rot">Identificador (opcional)</label><input class="campo" id="pid" placeholder="ex.: meu-app" pattern="[a-z0-9_-]+"></div>
      <label style="display:flex;gap:6px;align-items:center;font-size:12.5px;color:var(--text2);padding-bottom:10px"><input type="checkbox" id="subst"> substituir se existir</label>
    </div>
    <div id="res"></div>
    <div class="dica">Também dá para plugar sem enviar nada: copie a pasta do app para <code>${esc(info.pasta_apps || 'apps/')}</code>; o hub detecta sozinho em segundos. Atualizações preservam <code>.venv</code>, <code>node_modules</code> e <code>dados/</code>.</div>
    <div class="linha"><button class="btn" id="cancelar">Fechar</button><button class="btn primario" id="enviar" disabled>Plugar</button></div>`)
  const el = m.el, arq = el.querySelector('#arq'), zona = el.querySelector('#zona'), enviar = el.querySelector('#enviar')
  let file = null
  const set = f => { file = f; el.querySelector('#nomeArq').textContent = f ? `${f.name} · ${(f.size / 1048576).toFixed(1)} MB` : ''; enviar.disabled = !f }
  arq.onchange = () => set(arq.files[0])
  zona.addEventListener('dragover', ev => { ev.preventDefault(); zona.classList.add('sobre') })
  zona.addEventListener('dragleave', () => zona.classList.remove('sobre'))
  zona.addEventListener('drop', ev => { ev.preventDefault(); zona.classList.remove('sobre'); set(ev.dataTransfer.files[0]) })
  el.querySelector('#cancelar').onclick = m.fechar
  enviar.onclick = async () => {
    const fd = new FormData(); fd.append('arquivo', file); fd.append('app_id', el.querySelector('#pid').value.trim()); fd.append('substituir', el.querySelector('#subst').checked ? 'true' : 'false')
    enviar.disabled = true; enviar.textContent = 'Enviando…'
    try {
      const d = await api('/api/hub/plugar', { method: 'POST', body: fd })
      el.querySelector('#res').innerHTML = d.erro ? `<div class="aviso err">Plugado como <b>${esc(d.id)}</b>, mas: ${esc(d.erro)}</div>` : `<div class="aviso ok">✓ <b>${esc(d.id)}</b> plugado. <a href="${esc(d.url)}" target="_blank">Abrir</a></div>`
      carregar()
    } catch (err) { el.querySelector('#res').innerHTML = `<div class="aviso err">${esc(err.message)}</div>` }
    enviar.disabled = false; enviar.textContent = 'Plugar'
  }
}

;(async () => {
  try { info = await api('/api/hub/info') } catch { return }
  $('#subtitulo').textContent = info.subtitulo || ''
  $('#quem').textContent = info.usuario ? info.usuario.nome : ''
  document.querySelectorAll('.so-admin').forEach(b => { b.hidden = !info.admin })
  $('#pastaApps').textContent = info.pasta_apps
  $('#rodape').innerHTML = `<span>${esc(info.nome)} v${esc(info.versao)}</span><span>Pasta de apps: ${esc(info.pasta_apps)}</span><span>Acesso: ${esc(location.origin)}</span>`
  await carregar()
  setInterval(() => { if (!document.hidden && !menuAberto) carregar() }, 3000)
})()

// ── Administração: tela própria (Admin técnico)
$('#bAdmin').onclick = () => { location.href = '/admin' }
