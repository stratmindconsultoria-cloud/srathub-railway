// Renderiza o nome do hub no estilo da marca: "STRAT·HUB" → Strat (forte) + HUB (leve)
(function () {
  var el = document.getElementById('marca'); if (!el) return
  var nome = (document.title.split('·').slice(1).join('·') || '').trim() || 'STRAT·HUB'
  var p = nome.split('·')
  el.innerHTML = ''
  var a = document.createElement('span'); a.textContent = p[0]; el.appendChild(a)
  if (p.length > 1) { var b = document.createElement('em'); b.textContent = '·' + p.slice(1).join('·'); el.appendChild(b) }
})()

// faixa de ambiente de teste (declarado só no hub.config.json do servidor)
;(function () {
  if (location.pathname.indexOf('/entrar') === 0 || location.pathname.indexOf('/setup') === 0) return
  fetch('/api/hub/eu', { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null }).then(function (u) {
    if (!u || u.ambiente !== 'teste' || document.getElementById('faixaTeste')) return
    var d = document.createElement('div'); d.id = 'faixaTeste'
    d.textContent = 'AMBIENTE DE TESTE' + (u.master_teste ? ' · você é MASTER TESTE: recebe todas as pendências e pode executar qualquer papel' : '')
    d.style.cssText = 'position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:9999;pointer-events:none;max-width:92vw;background:#b45309;color:#fff;font:600 11.5px/1.3 system-ui,sans-serif;text-align:center;padding:4px 14px;border-radius:0 0 8px 8px;letter-spacing:.03em;box-shadow:0 2px 6px rgba(0,0,0,.18)'
    document.body.insertBefore(d, document.body.firstChild)
  }).catch(function () {})
})()
