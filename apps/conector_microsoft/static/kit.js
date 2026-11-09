/* kit.js — base comum das interfaces: sessão (hub ou demonstração), organização/vocabulário, tema, API e componentes. */
'use strict'
const K = (() => {
  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)]
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const base = () => { const m = location.pathname.match(/^(\/apps\/[^/]+)/); return m ? m[1] : '' }
  const st = { meta: null, sessao: null, usuarios: [], user: { id: '', nome: '', perfil: '' }, rot: [], chave: 'kit:' + location.pathname }
  async function api(method, url, body, isForm) {
    const h = st.user && st.user.id ? { 'X-Usuario': st.user.id } : {}
    if (body !== undefined && !isForm) h['Content-Type'] = 'application/json'
    const r = await fetch(base() + url, { method, headers: h, body: body === undefined ? undefined : (isForm ? body : JSON.stringify(body)) })
    let d = null; try { d = await r.json() } catch (e) { }
    if (!r.ok) { const det = d && d.detail; const e = new Error(typeof det === 'string' ? det : (det && det.mensagem) || ('Erro ' + r.status)); e.status = r.status; e.data = det; throw e }
    return d
  }
  const V = k => ((st.meta && st.meta.organizacao && st.meta.organizacao.vocabulario) || {})[k] || k
  const moeda = () => (st.meta && st.meta.organizacao && st.meta.organizacao.moeda) || 'BRL'
  const nf = (v, d = 0) => (Number(v) || 0).toLocaleString('pt-BR', { minimumFractionDigits: d, maximumFractionDigits: d })
  const din = v => { v = Number(v) || 0; const a = Math.abs(v), s = moeda() === 'BRL' ? 'R$ ' : moeda() + ' '; if (a >= 1e9) return s + nf(v / 1e9, 1) + ' bi'; if (a >= 1e6) return s + nf(v / 1e6, 1) + ' mi'; if (a >= 1e3) return s + nf(v / 1e3, 1) + ' mil'; return s + nf(v, 0) }
  const pct = (v, d = 1) => v == null || isNaN(v) ? '—' : nf(v * 100, d) + '%'
  const data = s => s ? new Date(String(s).length === 10 ? s + 'T12:00:00' : s).toLocaleDateString('pt-BR') : '—'
  const dh = s => s ? new Date(s).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : '—'
  function toast(m) { let t = $('#toast'); if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast'; t.setAttribute('role', 'status'); document.body.appendChild(t) } t.textContent = m; t.classList.add('on'); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove('on'), 3400) }
  function modal(html, aoAbrir) {
    const d = document.createElement('dialog'); d.innerHTML = html; document.body.appendChild(d)
    // toda janela fecha com ×, Esc (nativo do <dialog>) ou clique fora
    const x = document.createElement('button'); x.type = 'button'; x.className = 'kx'; x.setAttribute('aria-label', 'Fechar'); x.textContent = '×'
    x.style.cssText = 'position:absolute;top:6px;right:8px;border:0;background:none;font-size:22px;line-height:1;cursor:pointer;color:var(--muted,#6b7280)'
    d.style.position = d.style.position || 'fixed'; d.prepend(x); x.onclick = () => { d.dispatchEvent(new Event('cancel')); d.close() }
    d.addEventListener('click', e => { if (e.target !== d) return; const r = d.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) { d.dispatchEvent(new Event('cancel')); d.close() } })
    d.addEventListener('close', () => d.remove()); d.showModal(); if (aoAbrir) aoAbrir(d); return d
  }
  function pedir(titulo, texto, obrigatorio, valor = '') {
    return new Promise(res => {
      const d = modal(`<h2>${esc(titulo)}</h2><p class="muted">${esc(texto)}</p><textarea class="in" rows="4" style="width:100%" id="kp">${esc(valor)}</textarea><p id="ke" style="color:var(--bad);min-height:18px;font-size:13px"></p><div class="row" style="justify-content:flex-end"><button class="btn" id="kc">Cancelar</button><button class="btn pri" id="ko">Confirmar</button></div>`)
      d.querySelector('#kc').onclick = () => { d.close(); res(null) }
      d.querySelector('#ko').onclick = () => { const v = d.querySelector('#kp').value.trim(); if (obrigatorio && !v) { d.querySelector('#ke').textContent = 'Preencha para continuar.'; return } d.close(); res(v) }
      d.addEventListener('cancel', () => res(null))
    })
  }
  // vocabulário configurável (troca de termos exibidos)
  const _rx = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  function rotular(t) { if (!t || !st.rot.length) return t; let r = String(t); for (const [rx, s] of st.rot) r = r.replace(rx, (_, p) => p + s); return r }
  const vistos = new WeakMap()
  function rotNo(n) {
    if (n.nodeType === 3) { if (vistos.get(n) === n.nodeValue) return; const v = rotular(n.nodeValue); if (v !== n.nodeValue) n.nodeValue = v; vistos.set(n, n.nodeValue); return }
    if (n.nodeType !== 1 || /^(SCRIPT|STYLE|TEXTAREA)$/.test(n.tagName)) return
    for (const a of ['placeholder', 'title']) { const v = n.getAttribute(a); if (v) { const r = rotular(v); if (r !== v) n.setAttribute(a, r) } }
    n.childNodes.forEach(rotNo)
  }
  const mix = (hex, alvo, p) => { const h = hex.replace('#', ''); return '#' + [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16)).map(v => Math.round(v + ((alvo === 'w' ? 255 : 0) - v) * p).toString(16).padStart(2, '0')).join('') }
  function tema(o) {
    const r = document.documentElement.style, c = /^#[0-9a-f]{6}$/i.test(o.cor_primaria || '') ? o.cor_primaria : null
    if (c) { r.setProperty('--pri', c); r.setProperty('--pri-deep', mix(c, 'b', .35)); r.setProperty('--pri-soft', mix(c, 'w', .88)); r.setProperty('--side-muted', mix(c, 'w', .7)) }
    if (/^#[0-9a-f]{6}$/i.test(o.cor_destaque || '')) r.setProperty('--acc', o.cor_destaque)
  }
  // shell
  let cfgShell = null
  function shell(rota) {
    const meta = st.meta || {}, hub = meta.modo === 'hub', admin = meta.perfil_admin
    const itens = cfgShell.nav().filter(Boolean)
    const notif = hub && window.STRATHUB && window.STRATHUB.temNotificacoes
    $('#nav').innerHTML = itens.map(([k, l, n]) => `<a href="#/${k}"${rota === k ? ' aria-current="page"' : ''}>${esc(l)}${n ? `<span class="count">${n}</span>` : ''}</a>`).join('')
      + (notif ? `<a href="#" id="kNotif">🔔 Notificações${st.notif ? `<span class="count">${st.notif}</span>` : ''}</a>` : '')
    const kn = $('#kNotif'); if (kn) kn.onclick = ev => { ev.preventDefault(); window.STRATHUB.abrirNotificacoes() }
    // no hub a pessoa é sempre quem o hub disse; a escolha de usuário existe só no modo isolado (demonstração)
    const lista = hub ? [] : st.usuarios.filter(u => u.origem !== 'hub')
    const sel = $('#userSel'); sel.hidden = !lista.length
    $('#userLbl').textContent = hub ? '' : 'Entrando como (demonstração)'
    const master = hub && st.sessao && st.sessao.master
    sel.innerHTML = lista.map(u => `<option value="${esc(u.id)}"${u.id === st.user.id ? ' selected' : ''}>${esc(u.nome)}${u.origem === 'demo' ? ' (demo)' : ''} · ${esc((meta.perfis || {})[u.perfil] || u.perfil)}</option>`).join('')
    $('#userInfo').textContent = (lista.length ? '' : st.user.nome + ' · ' + (master ? 'MASTER TESTE (qualquer papel)' : ((meta.perfis || {})[st.user.perfil] || st.user.perfil)) + ' · ') + (st.user.unidade ? V('unidade') + ': ' + st.user.unidade : 'Todas as unidades')
    faixaTeste(hub && st.sessao && st.sessao.ambiente === 'teste', master)
    $('#modeInfo').innerHTML = hub ? '<b>Conectado ao hub</b> · ' + esc((meta.organizacao || {}).nome || '') : '<b>Modo isolado</b> · ' + esc(location.host)
  }
  function faixaTeste(ligada, master) {
    let f = document.getElementById('faixaTeste')
    if (!ligada) { if (f) f.remove(); return }
    if (!f) { f = document.createElement('div'); f.id = 'faixaTeste'; f.style.cssText = 'position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:9999;pointer-events:none;max-width:92vw;background:#b45309;color:#fff;font:600 11.5px/1.3 system-ui,sans-serif;text-align:center;padding:4px 14px;border-radius:0 0 8px 8px;letter-spacing:.03em;box-shadow:0 2px 6px rgba(0,0,0,.18)'; document.body.insertBefore(f, document.body.firstChild) }
    f.textContent = 'AMBIENTE DE TESTE' + (master ? ' · você é MASTER TESTE: recebe todas as pendências e pode executar qualquer papel' : '')
  }
  async function render() {
    const partes = (location.hash || '#/' + cfgShell.inicio).slice(2).split('/').map(decodeURIComponent)
    const rota = partes[0] || cfgShell.inicio
    shell(rota)
    const fn = cfgShell.rotas[rota] || cfgShell.rotas[cfgShell.inicio]
    try { await fn($('#main'), partes.slice(1)) } catch (e) {
      console.error(e)
      $('#main').innerHTML = `<h1>Algo deu errado</h1><div class="note bad">${esc(e.message)}</div><div class="row" style="margin-top:12px"><a class="btn pri" href="#/${esc(cfgShell.inicio)}">← Voltar ao início</a>${st.meta && st.meta.modo === 'hub' ? '<a class="btn" href="/">Portal STRAT·HUB</a>' : ''}</div>`
    }
  }
  async function carregarSessao() { st.sessao = await api('GET', '/api/sessao'); if (st.sessao.modo === 'hub') st.user = st.sessao.usuario || st.sessao.real }
  async function iniciar(cfg) {
    cfgShell = cfg
    document.body.insertAdjacentHTML('afterbegin', `<div class="app"><aside><div class="brand">${esc(cfg.nome)}<small id="brandSub"></small></div><nav id="nav"></nav>
      <div class="who"><label id="userLbl" for="userSel"></label><select id="userSel"></select><p id="userInfo"></p><p id="modeInfo"></p><div id="syncBox" class="sync" hidden></div></div></aside><main id="main"><p class="muted">Carregando…</p></main></div>`)
    try { st.meta = await api('GET', '/api/meta') } catch (e) { $('#main').innerHTML = `<h1>Servidor indisponível</h1><p class="lede">${esc(e.message)}</p>`; return }
    const o = st.meta.organizacao || {}; tema(o)
    $('#brandSub').textContent = (o.sigla || o.nome || '') + ' · ' + (cfg.subtitulo || V('pmo'))
    st.rot = Object.entries(st.meta.rotulos || {}).filter(([k, t]) => k && t && k !== t).sort((a, b) => b[0].length - a[0].length).map(([k, t]) => [new RegExp('(^|[^\\wÀ-ÿ])' + _rx(k) + '(?![\\wÀ-ÿ])', 'g'), t])
    if (st.rot.length) { rotNo(document.body); new MutationObserver(ms => { for (const x of ms) { if (x.type === 'characterData') rotNo(x.target); else x.addedNodes.forEach(rotNo) } }).observe(document.body, { childList: true, subtree: true, characterData: true }) }
    try { st.usuarios = await api('GET', '/api/usuarios') } catch (e) { }
    let salvo = ''; try { salvo = localStorage.getItem(st.chave) || '' } catch (e) { }
    if (st.meta.modo === 'hub') {
      try { localStorage.removeItem(st.chave) } catch (e) { }
      try { await carregarSessao() } catch (e) { $('#main').innerHTML = `<h1>Acesso pelo hub</h1><p class="lede">${esc(e.message)}</p>`; return }
    } else {
      const lista = st.usuarios.filter(u => u.origem !== 'hub')
      st.user = lista.find(u => u.id === salvo) || lista.find(u => u.perfil === st.meta.perfil_admin) || lista[0] || st.user
    }
    $('#userSel').onchange = async e => {
      const id = e.target.value
      st.user = (st.sessao && st.sessao.real && st.sessao.real.id === id) ? st.sessao.real : (st.usuarios.find(u => u.id === id) || st.user)
      try { localStorage.setItem(st.chave, st.user.id) } catch (x) { }
      if (st.meta.modo === 'hub') { try { await carregarSessao() } catch (x) { } }
      render()
    }
    window.addEventListener('hashchange', () => { render(); window.scrollTo(0, 0) })
    window.addEventListener('strathub:notificacoes', e => { st.notif = e.detail.pendentes; shell((location.hash || '#/' + cfgShell.inicio).slice(2).split('/')[0] || cfgShell.inicio) })
    if (cfg.aoIniciar) await cfg.aoIniciar()
    render()
    if (st.meta.modo === 'hub') { sincronizar(true); setInterval(() => sincronizar(false), 60000); setInterval(desenharSync, 20000) }
  }
  // fluxo automático: ao abrir sincroniza; depois acompanha o laço do servidor
  st.sync = null
  const ha = t => { if (!t) return 'nunca'; const s = Math.max(0, Date.now() / 1000 - t); return s < 60 ? 'agora há pouco' : s < 3600 ? 'há ' + Math.round(s / 60) + ' min' : 'há ' + Math.round(s / 3600) + ' h' }
  function desenharSync() {
    const b = $('#syncBox'), s = st.sync; if (!b || !s || !s.automatico) return
    b.hidden = false; b.className = 'sync ' + (s.ok === false ? 'bad' : 'ok')
    b.innerHTML = `<span>${s.ok === false ? '⚠ Falha na integração' : '⟳ Sincronizado ' + ha(s.ultimo)}</span><button type="button" id="syncBtn" title="${esc(s.erro || 'Recebe agora o que as outras ferramentas publicaram')}">Sincronizar</button>`
    $('#syncBtn').onclick = () => sincronizar(true, true)
  }
  async function sincronizar(agora, manual) {
    const antes = st.sync && st.sync.ultimo
    try { st.sync = await api(agora ? 'POST' : 'GET', agora ? '/api/sync/agora' : '/api/sync/status') } catch (e) { st.sync = { ...(st.sync || { automatico: true }), ok: false, erro: e.message } }
    desenharSync()
    const r = (st.sync && st.sync.resultado) || {}
    const novos = Object.entries(r).filter(([k, v]) => k !== 'pendencias' && typeof v === 'number' && v > 0)
    if (manual) toast(st.sync.ok === false ? 'Falha: ' + (st.sync.erro || '') : novos.length ? 'Recebido: ' + novos.map(([k, v]) => v + ' ' + k.replace(/_/g, ' ')).join(', ') : 'Tudo em dia.')
    if (novos.length && st.sync.ultimo !== antes && cfgShell.aoSincronizar) { await cfgShell.aoSincronizar(r); render() }
  }
  // componentes
  function tabela(cols, linhas, opt = {}) {
    const th = cols.map(c => `<th class="${c.n ? 'n' : ''}">${esc(c.t)}</th>`).join('')
    const tr = linhas.map((l, i) => `<tr${opt.attr ? ' ' + opt.attr(l, i) : ''}>${cols.map(c => `<td class="${c.n ? 'n' : ''}${c.wrap ? ' wrap' : ''}">${c.h ? c.h(l, i) : esc(l[c.k])}</td>`).join('')}</tr>`).join('')
    const tf = opt.total ? `<tfoot><tr>${cols.map(c => `<td class="${c.n ? 'n' : ''}">${opt.total[c.k ?? c.t] ?? ''}</td>`).join('')}</tr></tfoot>` : ''
    return `<div class="tw"${opt.max ? ` style="max-height:${opt.max}px"` : ''}><table class="t"><thead><tr>${th}</tr></thead><tbody>${tr || `<tr><td colspan="${cols.length}" class="muted">${esc(opt.vazio || 'Nada por aqui.')}</td></tr>`}</tbody>${tf}</table></div>`
  }
  const CORES = ['var(--pri)', '#C2410C', '#0f766e', '#7c3aed', '#64748b', '#b91c1c']
  // gráfico de linhas/barras em SVG: series [{nome, valores[], tipo:'linha'|'barra', cor, tracejado}], rotulos[]
  function grafico(series, rotulos, opt = {}) {
    const W = opt.w || 760, H = opt.h || 240, pl = 64, pr = 12, pt = 12, pb = 28
    const todos = series.flatMap(s => s.valores.filter(v => v != null && !isNaN(v)))
    let max = Math.max(0, ...todos), min = Math.min(0, ...todos); if (max === min) max = min + 1
    const x = i => pl + (rotulos.length <= 1 ? 0 : i * (W - pl - pr) / (rotulos.length - 1)), y = v => pt + (max - v) * (H - pt - pb) / (max - min)
    const bw = (W - pl - pr) / Math.max(rotulos.length, 1) * .6, xb = i => pl + (i + .5) * (W - pl - pr) / rotulos.length
    const fmt = opt.fmt || din
    let g = ''
    for (let k = 0; k <= 4; k++) { const v = min + (max - min) * k / 4; g += `<line x1="${pl}" x2="${W - pr}" y1="${y(v)}" y2="${y(v)}" stroke="#E3E7EE"/><text x="${pl - 6}" y="${y(v) + 4}" text-anchor="end" font-size="10.5" fill="#667085">${esc(fmt(v))}</text>` }
    const passo = Math.ceil(rotulos.length / 14)
    rotulos.forEach((r, i) => { if (i % passo === 0) g += `<text x="${opt.barras ? xb(i) : x(i)}" y="${H - 8}" text-anchor="middle" font-size="10.5" fill="#667085">${esc(r)}</text>` })
    const nb = series.filter(s => s.tipo === 'barra').length; let ib = 0
    series.forEach((s, si) => {
      const cor = s.cor || CORES[si % CORES.length]
      if (s.tipo === 'barra') { const w = bw / nb, off = -bw / 2 + ib * w; ib++; s.valores.forEach((v, i) => { if (v == null) return; g += `<rect x="${xb(i) + off}" y="${Math.min(y(v), y(0))}" width="${w - 1}" height="${Math.abs(y(v) - y(0))}" fill="${cor}" opacity=".85"><title>${esc(s.nome)} · ${esc(rotulos[i])}: ${esc(fmt(v))}</title></rect>` }) }
      else { const px = opt.barras ? xb : x; const pts = s.valores.map((v, i) => v == null || isNaN(v) ? null : `${px(i)},${y(v)}`); let d = '', on = false; pts.forEach(p => { if (!p) { on = false; return } d += (on ? 'L' : 'M') + p; on = true }); g += `<path d="${d}" fill="none" stroke="${cor}" stroke-width="2.2"${s.tracejado ? ' stroke-dasharray="5 4"' : ''}/>`; s.valores.forEach((v, i) => { if (v != null && !isNaN(v)) g += `<circle cx="${px(i)}" cy="${y(v)}" r="2.6" fill="${cor}"><title>${esc(s.nome)} · ${esc(rotulos[i])}: ${esc(fmt(v))}</title></circle>` }) }
    })
    const leg = series.map((s, si) => `<span><i style="background:${s.cor || CORES[si % CORES.length]}"></i>${esc(s.nome)}</span>`).join('')
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">${g}</svg><div class="legend">${leg}</div>`
  }
  function baixar(blob, nome) { const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = nome; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000) }
  async function baixarUrl(url, nome) { const h = st.user && st.user.id ? { 'X-Usuario': st.user.id } : {}; const r = await fetch(base() + url, { headers: h }); if (!r.ok) { let d = {}; try { d = await r.json() } catch (e) { } throw new Error(d.detail || 'Falha no download') } baixar(await r.blob(), nome) }
  const opcoes = (lista, sel, vazio) => (vazio != null ? `<option value="">${esc(vazio)}</option>` : '') + lista.map(o => { const v = typeof o === 'object' ? o.v : o, l = typeof o === 'object' ? o.l : o; return `<option value="${esc(v)}"${String(v) === String(sel ?? '') ? ' selected' : ''}>${esc(l)}</option>` }).join('')
  const unidades = () => ((st.meta && st.meta.organizacao && st.meta.organizacao.unidades) || []).map(u => u.nome || u.codigo)
  const areas = () => (st.meta && st.meta.organizacao && st.meta.organizacao.areas) || []
  const perfil = () => st.user.perfil, eAdmin = () => st.meta && st.user.perfil === st.meta.perfil_admin
  async function telaUsuarios(m) {
    if (!eAdmin()) { m.innerHTML = '<h1>Usuários</h1><div class="note">Área restrita ao administrador da ferramenta.</div>'; return }
    const us = await api('GET', '/api/usuarios'); st.usuarios = us; const hub = st.meta.modo === 'hub'
    m.innerHTML = `<h1>Usuários e perfis</h1><p class="lede">${hub ? 'O hub informa <b>quem</b> é cada pessoa (login único). Aqui você define <b>o que</b> ela faz nesta ferramenta.' : 'Modo isolado: usuários de demonstração, um por perfil.'}</p>` +
      tabela([{ t: 'Nome', h: u => esc(u.nome) + (u.origem === 'demo' ? ' <span class="muted">(demo)</span>' : '') }, { t: 'E-mail', k: 'email' },
      { t: 'Perfil', h: u => `<select class="in" data-perf="${esc(u.id)}">${opcoes(Object.entries(st.meta.perfis).map(([v, l]) => ({ v, l })), u.perfil)}</select>` },
      { t: V('unidade'), h: u => `<select class="in" data-uni="${esc(u.id)}">${opcoes(unidades(), u.unidade, 'Todas')}</select>` },
      { t: '', h: u => `<button class="btn mini" data-salva="${esc(u.id)}">Salvar</button>` }], us)
    m.querySelectorAll('[data-salva]').forEach(b => b.onclick = async () => {
      const id = b.dataset.salva
      try { await api('PUT', '/api/usuarios/' + encodeURIComponent(id), { perfil: m.querySelector(`[data-perf="${CSS.escape(id)}"]`).value, unidade: m.querySelector(`[data-uni="${CSS.escape(id)}"]`).value || null }); toast('Perfil atualizado.'); if (st.sessao && st.sessao.real && st.sessao.real.id === id) await carregarSessao(); render() } catch (e) { toast(e.message) }
    })
  }
  return { sincronizar, $, $$, esc, api, V, din, nf, pct, data, dh, toast, modal, pedir, iniciar, render, tabela, grafico, baixar, baixarUrl, opcoes, unidades, areas, perfil, eAdmin, telaUsuarios, st, rotular, carregarSessao }
})()
