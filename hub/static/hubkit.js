/* hubkit.js — integração de páginas de apps com o STRAT·HUB (servido em /hub-static/hubkit.js).
   HubKit.noHub        true quando a página está aberta via /apps/<id>/
   HubKit.eu()         usuário logado no hub {id,nome,email,papel}
   HubKit.org()        perfil da organização
   HubKit.registros(q) consulta o Data Core: {domain, entity_type, entity_key, period, since, limit}
   HubKit.gravar(r)    upsert no Data Core: {domain, entity_type, entity_key, period, payload, source_application}
   HubKit.apps()       ferramentas que o usuário pode abrir
   HubKit.urlApp(id)   '/apps/<id>/' */
(function () {
  const noHub = location.pathname.startsWith('/apps/')
  async function j(url, opts) {
    const r = await fetch(url, Object.assign({ credentials: 'same-origin' }, opts || {}))
    const d = await r.json().catch(() => ({}))
    if (!r.ok) { const e = new Error(d.detail || ('HTTP ' + r.status)); e.status = r.status; throw e }
    return d
  }
  window.HubKit = {
    noHub,
    eu: () => j('/api/hub/eu'),
    org: () => j('/api/hub/organizacao'),
    apps: () => j('/api/hub/apps'),
    registros: (q) => j('/api/data-core/records?' + new URLSearchParams(Object.entries(q || {}).filter(([, v]) => v !== undefined && v !== null && v !== ''))),
    gravar: (r) => j('/api/data-core/records', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(r) }),
    urlApp: (id, caminho) => '/apps/' + id + '/' + String(caminho || '').replace(/^\//, ''),
  }
})()
