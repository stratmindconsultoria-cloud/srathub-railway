/* STRAT·HUB — botão do hub em toda ferramenta (o hub acrescenta este script nas páginas que passam por ele).
   Garante sempre um caminho de volta (início da ferramenta e portal) e a tela de notificações da ferramenta,
   com os mesmos botões de ação da agenda. window.STRATHUB.abrirNotificacoes() abre a tela a partir do menu da ferramenta. */
(function () {
  if (window.STRATHUB) return
  var me = document.currentScript || document.querySelector('script[src*="navhub.js"]')
  var APP = me.dataset.app, NOME = me.dataset.nome || APP, BASE = me.dataset.base || ('/apps/' + APP), TEM = me.dataset.notif === '1'
  var esc = function (v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] }) }
  var st = { estado: 'pendentes', soEsta: !!APP, itens: [], pend: 0, aberto: false }  // sem data-app (portal): todas as ferramentas
  var css = document.createElement('style')
  css.textContent = '#shBtn{position:fixed;right:14px;bottom:14px;z-index:2147483000;width:44px;height:44px;border-radius:50%;border:0;background:#1e3a6e;color:#fff;font:700 12px system-ui,sans-serif;box-shadow:0 3px 10px rgba(0,0,0,.25);cursor:pointer;opacity:.9}' +
    '#shBtn:hover{opacity:1}#shBtn .b{position:absolute;top:-4px;right:-4px;background:#c0392b;color:#fff;border-radius:10px;font-size:10px;padding:1px 5px;min-width:16px}' +
    '#shMenu{position:fixed;right:14px;bottom:66px;z-index:2147483000;background:#fff;border:1px solid #d8dde6;border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.18);min-width:230px;font:14px system-ui,sans-serif;overflow:hidden}' +
    '#shMenu a,#shMenu button{display:block;width:100%;text-align:left;padding:10px 14px;border:0;background:none;color:#1f2937;text-decoration:none;font:inherit;cursor:pointer}#shMenu a:hover,#shMenu button:hover{background:#f1f4f9}' +
    '#shMenu .t{padding:8px 14px;font-size:11px;color:#6b7280;border-bottom:1px solid #eef0f4}' +
    '#shGav{position:fixed;top:0;right:0;bottom:0;width:min(440px,100vw);z-index:2147483001;background:#fff;box-shadow:-6px 0 24px rgba(0,0,0,.18);display:flex;flex-direction:column;font:14px system-ui,sans-serif;color:#1f2937}' +
    '#shGav header{padding:14px 16px;border-bottom:1px solid #eef0f4;display:flex;align-items:center;gap:8px}#shGav h2{font-size:16px;margin:0;flex:1}' +
    '#shGav .x{border:0;background:none;font-size:22px;cursor:pointer;color:#6b7280}#shGav .f{display:flex;gap:6px;padding:8px 16px;flex-wrap:wrap;border-bottom:1px solid #eef0f4}' +
    '#shGav .f button{border:1px solid #d8dde6;background:#fff;border-radius:14px;padding:3px 10px;font:12px system-ui;cursor:pointer}#shGav .f button.on{background:#1e3a6e;color:#fff;border-color:#1e3a6e}' +
    '#shGav .l{overflow:auto;flex:1;padding:6px 0}#shGav .i{padding:10px 16px;border-bottom:1px solid #f2f4f7}#shGav .i.lida{opacity:.65}' +
    '#shGav .i b{display:block}#shGav .m{font-size:12px;color:#6b7280;margin:2px 0 6px}#shGav .d{font-size:13px;color:#374151;margin-bottom:6px}' +
    '#shGav .ac{display:flex;gap:6px;flex-wrap:wrap}#shGav .ac button,#shGav .ac a{border:1px solid #d8dde6;background:#fff;border-radius:6px;padding:4px 10px;font:12px system-ui;cursor:pointer;color:#1f2937;text-decoration:none}' +
    '#shGav .ac .ok{background:#1f7a4d;color:#fff;border-color:#1f7a4d}#shGav .ac .bad{background:#b42318;color:#fff;border-color:#b42318}' +
    '#shGav .crit{color:#b42318;font-weight:600}#shGav footer{padding:10px 16px;border-top:1px solid #eef0f4;font-size:12px}#shGav footer a{color:#1e3a6e}' +
    '#shToast{position:fixed;left:50%;bottom:70px;transform:translateX(-50%);background:#111827;color:#fff;padding:8px 14px;border-radius:8px;font:13px system-ui;z-index:2147483002}'
  document.head.appendChild(css)

  function toast(t) { var d = document.createElement('div'); d.id = 'shToast'; d.textContent = t; document.body.appendChild(d); setTimeout(function () { d.remove() }, 3500) }
  function api(m, u, b) {
    return fetch(u, { method: m, credentials: 'same-origin', headers: b ? { 'Content-Type': 'application/json' } : {}, body: b ? JSON.stringify(b) : undefined })
      .then(function (r) { return r.json().catch(function () { return {} }).then(function (d) { if (!r.ok) { var e = d.detail; throw new Error(typeof e === 'string' ? e : (e && e.mensagem) || ('Erro ' + r.status)) } return d }) })
  }
  var btn = document.createElement('button'); btn.id = 'shBtn'; btn.type = 'button'; btn.title = 'STRAT·HUB: início, portal e notificações'; btn.setAttribute('aria-label', btn.title)
  btn.innerHTML = 'S·H<span class="b" hidden></span>'
  document.body.appendChild(btn)

  function contar() {
    if (!TEM) return Promise.resolve(0)
    return api('GET', '/api/hub/notificacoes' + (APP ? '?app=' + encodeURIComponent(APP) : '')).then(function (d) {
      st.pend = d.pendentes || 0
      var b = btn.querySelector('.b'); b.hidden = !st.pend; b.textContent = st.pend > 99 ? '99+' : st.pend
      try { window.dispatchEvent(new CustomEvent('strathub:notificacoes', { detail: { pendentes: st.pend } })) } catch (e) { }
      return st.pend
    }).catch(function () { return 0 })
  }
  function fecharMenu() { var m = document.getElementById('shMenu'); if (m) m.remove() }
  btn.onclick = function (e) {
    e.stopPropagation()
    if (document.getElementById('shMenu')) return fecharMenu()
    var m = document.createElement('div'); m.id = 'shMenu'
    m.innerHTML = '<div class="t">' + esc(NOME) + '</div>' + (APP ? '<a href="' + esc(BASE) + '/">⟲ Início da ferramenta</a>' : '') + '<a href="/">⌂ Portal STRAT·HUB</a>' +
      (TEM ? '<button type="button" data-n>🔔 Notificações' + (st.pend ? ' (' + st.pend + ')' : '') + '</button>' : '') + '<a href="/notificacoes">⚙ Preferências de notificação</a>'
    document.body.appendChild(m)
    var n = m.querySelector('[data-n]'); if (n) n.onclick = function () { fecharMenu(); abrir() }
  }
  document.addEventListener('click', function (e) { if (!e.target.closest || !e.target.closest('#shMenu')) fecharMenu() })
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { fecharMenu(); fechar() } })

  function fechar() { var g = document.getElementById('shGav'); if (g) g.remove(); st.aberto = false }
  function abrir() {
    fechar(); st.aberto = true
    var g = document.createElement('aside'); g.id = 'shGav'; g.setAttribute('role', 'dialog'); g.setAttribute('aria-label', 'Notificações')
    document.body.appendChild(g); desenhar(true)
  }
  function carregar() {
    return api('GET', '/api/hub/notificacoes?estado=' + st.estado + (st.soEsta ? '&app=' + encodeURIComponent(APP) : '')).then(function (d) { st.itens = d.itens || [] })
  }
  function desenhar(recarregar) {
    var g = document.getElementById('shGav'); if (!g) return
    var p = recarregar ? carregar() : Promise.resolve()
    p.then(function () {
      var fil = [['pendentes', 'Pendentes'], ['lidas', 'Lidas'], ['todas', 'Todas']].map(function (x) { return '<button type="button" data-e="' + x[0] + '" class="' + (st.estado === x[0] ? 'on' : '') + '">' + x[1] + '</button>' }).join('') +
        (APP ? '<button type="button" data-s class="' + (st.soEsta ? 'on' : '') + '">' + (st.soEsta ? 'Só desta ferramenta' : 'Todas as ferramentas') + '</button>' : '')
      var l = st.itens.map(function (i, k) {
        var acoes = (i.acoes || []).map(function (a, j) { return '<button type="button" class="' + esc(a.estilo || '') + '" data-a="' + k + ':' + j + '">' + esc(a.rotulo) + '</button>' }).join('')
        return '<div class="i' + (i.lida ? ' lida' : '') + '"><b>' + esc(i.titulo) + '</b><div class="m">' + esc(i.origem_nome || '') + (i.prazo ? ' · prazo ' + esc(String(i.prazo).slice(0, 10)) : '') +
          (i.prioridade === 'critica' || i.prioridade === 'alta' ? ' · <span class="crit">' + esc(i.prioridade) + '</span>' : '') + '</div>' +
          (i.descricao ? '<div class="d">' + esc(i.descricao) + '</div>' : '') +
          '<div class="ac">' + acoes + '<a href="' + esc(i.link) + '" data-abrir="' + k + '">Abrir</a><button type="button" data-l="' + k + '">' + (i.lida ? 'Marcar como não lida' : 'Marcar como lida') + '</button></div></div>'
      }).join('') || '<p style="padding:16px;color:#6b7280">Nada por aqui.</p>'
      g.innerHTML = '<header><h2>Notificações</h2><button type="button" class="x" aria-label="Fechar">×</button></header><div class="f">' + fil + '</div><div class="l">' + l + '</div>' +
        '<footer><a href="/notificacoes">Preferências de notificação (e-mail, Teams, resumo diário)</a> · <a href="/">Portal</a></footer>'
      g.querySelector('.x').onclick = fechar
      g.querySelectorAll('[data-e]').forEach(function (b) { b.onclick = function () { st.estado = b.dataset.e; desenhar(true) } })
      var bs = g.querySelector('[data-s]'); if (bs) bs.onclick = function () { st.soEsta = !st.soEsta; desenhar(true) }
      g.querySelectorAll('[data-l]').forEach(function (b) { b.onclick = function () { var i = st.itens[+b.dataset.l]; api('POST', '/api/hub/notificacoes/lidas', { chaves: [i.chave], lida: !i.lida }).then(function () { contar(); desenhar(true) }) } })
      g.querySelectorAll('[data-abrir]').forEach(function (a) { a.onclick = function () { var i = st.itens[+a.dataset.abrir]; api('POST', '/api/hub/notificacoes/lidas', { chaves: [i.chave], lida: true }).catch(function () { }) } })
      g.querySelectorAll('[data-a]').forEach(function (b) { b.onclick = function () { executar(st.itens[+b.dataset.a.split(':')[0]], +b.dataset.a.split(':')[1]) } })
    }).catch(function (e) { g.innerHTML = '<header><h2>Notificações</h2><button type="button" class="x">×</button></header><p style="padding:16px">' + esc(e.message) + '</p>'; g.querySelector('.x').onclick = fechar })
  }
  function executar(i, j) {
    var a = i.acoes[j], com = ''
    if (a.comentario === 'obrigatorio' || a.comentario === 'opcional') {
      com = window.prompt(a.rotulo + (a.comentario === 'obrigatorio' ? ' — escreva o motivo (obrigatório):' : ' — comentário (opcional):'), '')
      if (com === null) return
      if (a.comentario === 'obrigatorio' && !String(com).trim()) { toast('O motivo é obrigatório.'); return }
    }
    var corpo = Object.assign({}, a.corpo || {})
    if (com) { corpo[a.campo || 'comentario'] = com; corpo.comentario = com }
    api(a.metodo || 'POST', '/apps/' + i.app + a.caminho, corpo).then(function () {
      toast(a.rotulo + ': feito.'); return api('POST', '/api/hub/notificacoes/lidas', { chaves: [i.chave], lida: true })
    }).then(function () { contar(); desenhar(true); try { window.dispatchEvent(new CustomEvent('strathub:acao', { detail: { chave: i.chave } })) } catch (e) { } })
      .catch(function (e) { toast(e.message) })
  }
  window.STRATHUB = { abrirNotificacoes: abrir, contar: contar, temNotificacoes: TEM, app: APP }
  contar(); setInterval(function () { if (!document.hidden) contar() }, 60000)
})()
