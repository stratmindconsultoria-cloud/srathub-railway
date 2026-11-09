// Admin técnico do STRAT·HUB: ferramentas, ecossistema, dependências, Data Core, logs, auditoria, usuários, organização, infraestrutura e configurações.
'use strict'
const $ = s => document.querySelector(s), M = () => $('#adm')
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const ST = { ativo: ['ativo', 'ok'], parado: ['sob demanda', ''], disponivel: ['disponível', 'ok'], iniciando: ['iniciando', 'av'], instalando: ['instalando', 'av'], erro: ['erro', 'err'] }
const pill = (t, c = '') => `<span class="pill ${c}">${esc(t)}</span>`
async function api(path, opts = {}) {
  if (opts.json !== undefined) { opts.body = JSON.stringify(opts.json); opts.headers = { 'Content-Type': 'application/json' }; delete opts.json }
  const r = await fetch(path, opts)
  if (r.status === 401) { location.href = '/entrar?volta=/admin'; throw new Error('sessão') }
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(d.detail || 'Falha')
  return d
}
function toast(m) { const t = document.createElement('div'); t.className = 'toast'; t.textContent = m; document.body.appendChild(t); setTimeout(() => t.remove(), 2800) }
const tabela = (cols, linhas, vazio = 'Nada por aqui.') => `<div class="tw"><table class="tb"><thead><tr>${cols.map(c => `<th>${esc(c[0])}</th>`).join('')}</tr></thead><tbody>${linhas.length ? linhas.map(l => `<tr>${cols.map(c => `<td>${c[1](l)}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${cols.length}" class="mut">${esc(vazio)}</td></tr>`}</tbody></table></div>`
const quando = s => s ? String(s).replace('T', ' ').slice(0, 16) : '—'
let APPS = []

// ── Visão geral
async function visao() {
  const v = await api('/api/admin/visao')
  M().innerHTML = `<h1>Visão geral técnica</h1><p class="sub">STRAT·HUB ${esc(v.versao)} · ligado desde ${new Date(v.ligado_desde * 1000).toLocaleString('pt-BR')}</p>
    <div class="kpis">
      <div class="kpi"><b>${v.ferramentas.total}</b><span>ferramentas plugadas</span></div><div class="kpi"><b>${v.ferramentas.ativas}</b><span>processos ativos · ${v.ferramentas.memoria_mb} MB</span></div>
      <div class="kpi"><b>${v.ferramentas.erro}</b><span>com erro</span></div><div class="kpi"><b>${v.ferramentas.ocultas}</b><span>ocultas do portal</span></div>
      <div class="kpi"><b>${v.datacore.registros}</b><span>registros no Data Core (${v.datacore.tipos} tipos)</span></div><div class="kpi"><b>${v.usuarios}</b><span>usuários</span></div>
      <div class="kpi"><b>${v.backup ? esc(v.backup.em) : '—'}</b><span>último backup</span></div><div class="kpi"><b>${v.lixeira}</b><span>itens na lixeira</span></div></div>
    <h2>Alertas</h2>${v.alertas.length ? v.alertas.map(a => `<div class="alerta ${a.nivel}">${a.nivel === 'erro' ? '⛔' : '⚠️'} ${esc(a.texto)} <span class="esp"></span><a href="${a.link}">ver</a></div>`).join('') : '<p class="mut">Nenhum alerta. Tudo funcionando.</p>'}
    <h2>Últimos eventos</h2>${tabela([['Quando', e => esc(quando(e.em))], ['Quem', e => esc(e.por)], ['Ação', e => esc(e.acao)], ['Alvo', e => esc(e.alvo)]], v.eventos, 'Sem eventos.')}`
}

// ── Ferramentas
async function ferramentas() {
  const [apps, lix] = await Promise.all([api('/api/admin/ferramentas'), api('/api/admin/lixeira')]); APPS = apps
  M().innerHTML = `<h1>Ferramentas</h1><p class="sub">Ajustes feitos aqui ficam no hub (não alteram a pasta da ferramenta). Ocultar tira do portal e desliga; remover leva para a lixeira com os dados.</p>
    ${tabela([['', a => `<span style="font-size:20px">${esc(a.icone)}</span>`],
      ['Ferramenta', a => `<b>${esc(a.nome)}</b> ${a.oculto ? pill('oculta', 'av') : ''}<div class="mut mono">${esc(a.id)} · ${esc(a.versao || '')} · ${esc(a.manifesto)}</div>`],
      ['Situação', a => { const [l, c] = ST[a.status] || [a.status, '']; return pill(l, c) + (a.porta ? `<div class="mut mono">porta ${a.porta} · ${a.memoria_mb ?? '?'} MB</div>` : '') + (a.erro ? `<div style="color:var(--red);font-size:12px">${esc(a.erro)}</div>` : '') }],
      ['Portal', a => `${esc(a.categoria)} · ordem ${a.ordem}`],
      ['Execução', a => a.processo ? (a.sempre_ligado ? 'sempre ligada' : `sob demanda · ${a.ocioso_min ?? 'padrão'} min`) : 'estática'],
      ['', a => `<div class="linha">${a.processo ? `<button class="btn mini" data-ac="iniciar|${esc(a.id)}">▶</button><button class="btn mini" data-ac="reiniciar|${esc(a.id)}">↻</button><button class="btn mini" data-ac="parar|${esc(a.id)}">■</button>` : ''}
        <button class="btn mini" data-ed="${esc(a.id)}">Ajustar</button><button class="btn mini" data-oc="${esc(a.id)}">${a.oculto ? 'Mostrar' : 'Ocultar'}</button>
        <a class="btn mini" href="#logs/${esc(a.id)}">Log</a><button class="btn mini perigo" data-rm="${esc(a.id)}">Remover</button></div>`]], apps)}
    <h2>Lixeira</h2>${tabela([['Ferramenta', l => `<b>${esc(l.nome)}</b><div class="mut mono">${esc(l.pasta)}</div>`], ['Removida em', l => esc(l.removido_em)], ['Dados', l => l.tem_dados ? 'preservados' : '—'], ['Tamanho', l => l.tamanho_mb + ' MB'],
      ['', l => `<button class="btn mini" data-rs="${esc(l.pasta)}">Restaurar</button> <button class="btn mini perigo" data-ex="${esc(l.pasta)}">Excluir de vez</button>`]], lix, 'Lixeira vazia.')}`
  M().querySelectorAll('[data-ac]').forEach(b => b.onclick = async () => { const [ac, id] = b.dataset.ac.split('|'); try { await api(`/api/hub/apps/${id}/${ac}`, { method: 'POST' }); toast('Ok'); setTimeout(ferramentas, 1200) } catch (e) { toast(e.message) } })
  M().querySelectorAll('[data-oc]').forEach(b => b.onclick = async () => { const a = apps.find(x => x.id === b.dataset.oc); try { await api(`/api/admin/ferramentas/${a.id}`, { method: 'PUT', json: { oculto: !a.oculto } }); toast(a.oculto ? 'Visível no portal' : 'Oculta e desligada'); ferramentas() } catch (e) { toast(e.message) } })
  M().querySelectorAll('[data-rm]').forEach(b => b.onclick = async () => { if (!confirm(`Remover "${b.dataset.rm}"?\n\nA ferramenta para e a pasta (com os dados) vai para a lixeira. Dá para restaurar depois.`)) return; try { await api(`/api/admin/ferramentas/${b.dataset.rm}/remover`, { method: 'POST' }); toast('Removida para a lixeira'); ferramentas() } catch (e) { toast(e.message) } })
  M().querySelectorAll('[data-rs]').forEach(b => b.onclick = async () => { try { await api(`/api/admin/lixeira/${b.dataset.rs}/restaurar`, { method: 'POST' }); toast('Restaurada'); ferramentas() } catch (e) { toast(e.message) } })
  M().querySelectorAll('[data-ex]').forEach(b => b.onclick = async () => { if (!confirm('Excluir definitivamente, inclusive os dados? Isso não pode ser desfeito.')) return; try { await api(`/api/admin/lixeira/${b.dataset.ex}`, { method: 'DELETE' }); toast('Excluída'); ferramentas() } catch (e) { toast(e.message) } })
  M().querySelectorAll('[data-ed]').forEach(b => b.onclick = () => editar(apps.find(x => x.id === b.dataset.ed)))
}
function editar(a) {
  const d = document.createElement('div'); d.className = 'fundo'
  d.innerHTML = `<div class="modal"><h2>Ajustar · ${esc(a.id)}</h2><p class="sub">Vazio = usar o valor do manifesto.</p>
    <div class="grid2"><label class="f">Nome<input class="campo" id="eN" value="${esc(a.ajustes.nome || '')}" placeholder="${esc(a.nome)}"></label><label class="f">Ícone<input class="campo" id="eI" value="${esc(a.ajustes.icone || '')}" placeholder="${esc(a.icone)}"></label>
    <label class="f">Categoria<input class="campo" id="eC" value="${esc(a.ajustes.categoria || '')}" placeholder="${esc(a.categoria)}"></label><label class="f">Ordem no portal<input class="campo" id="eO" type="number" value="${esc(a.ajustes.ordem ?? '')}" placeholder="${a.ordem}"></label>
    <label class="f" style="grid-column:1/-1">Descrição<input class="campo" id="eD" value="${esc(a.ajustes.descricao || '')}" placeholder="${esc(a.descricao)}"></label>
    ${a.processo ? `<label class="f">Ociosidade (min, 0 = nunca para)<input class="campo" id="eM" type="number" value="${esc(a.ajustes.ocioso_min ?? '')}" placeholder="${a.ocioso_min ?? 'padrão do hub'}"></label>
    <label class="f">Execução<select class="campo" id="eS"><option value="">manifesto (${a.sempre_ligado ? 'sempre ligada' : 'sob demanda'})</option><option value="1"${a.ajustes.sempre_ligado === true ? ' selected' : ''}>sempre ligada</option><option value="0"${a.ajustes.sempre_ligado === false ? ' selected' : ''}>sob demanda</option></select></label>` : ''}</div>
    ${a.agir_em_nome ? `<div class="cx" style="margin-top:12px"><label style="display:flex;gap:8px;align-items:flex-start"><input type="checkbox" id="eAg" ${a.agir_liberado ? 'checked' : ''}><span><b>Autorizar a agir em nome das pessoas</b><br><span class="sub">Este conector executa nas outras ferramentas a ação que a pessoa escolheu fora do hub (ex.: aprovou no Teams). A ferramenta de destino confere a permissão da pessoa e tudo fica na auditoria.</span></span></label></div>` : ''}
    <div class="linha" style="justify-content:flex-end;margin-top:14px"><button class="btn" id="eX">Cancelar</button><button class="btn primario" id="eOk">Salvar</button></div></div>`
  document.body.appendChild(d); d.onclick = e => { if (e.target === d) d.remove() }; d.querySelector('#eX').onclick = () => d.remove()
  d.querySelector('#eOk').onclick = async () => {
    const v = id => (d.querySelector(id) || {}).value
    const corpo = { nome: v('#eN'), icone: v('#eI'), categoria: v('#eC'), ordem: v('#eO'), descricao: v('#eD') }
    if (a.processo) { corpo.ocioso_min = v('#eM'); corpo.sempre_ligado = v('#eS') === '' ? null : v('#eS') === '1' }
    const ag = d.querySelector('#eAg'); if (ag) corpo.agir_liberado = ag.checked ? true : null
    try { await api(`/api/admin/ferramentas/${a.id}`, { method: 'PUT', json: corpo }); d.remove(); toast('Ajustes salvos'); ferramentas() } catch (e) { toast(e.message) }
  }
}

// ── Ecossistema
async function ecossistema() {
  const m = await api('/api/admin/mapa')
  const SIT = { ok: ['conectado', 'ok'], opcional: ['opcional — outra ponta não instalada', ''], atencao: ['atenção — ferramenta com problema', 'err'] }
  M().innerHTML = `<h1>Ecossistema</h1><p class="sub">Cada ferramenta declara no manifesto o que publica e o que consome no Data Core. O hub liga as pontas: você troca uma peça sem quebrar as outras.</p>
    <div class="cx mapa">${desenharMapa(m)}</div>
    ${tabela([['Contrato', c => `<span class="mono">${esc(c.contrato)}</span>`], ['Publica', c => esc(c.publica.join(', ') || '—')], ['Consome', c => esc(c.consome.join(', ') || '—')],
      ['Registros', c => c.registros], ['Última gravação', c => esc(quando(c.ultimo))], ['Situação', c => { const [l, k] = SIT[c.situacao] || [c.situacao, '']; return pill(l, k) + (c.com_problema && c.com_problema.length ? ` <span class="mut">${esc(c.com_problema.join(', '))}</span>` : '') }]], m.contratos, 'Nenhuma ferramenta declarou contratos.')}
    <h2>Modelos de ecossistema</h2><p class="sub">Monte o portal para uma aplicação: o modelo configura as ferramentas e o portal (mostra as do modelo, oculta as demais). Não altera o perfil da organização nem apaga dados.</p>
    <div class="grid2" id="eMod"><p class="mut">Carregando…</p></div>
    <h2>Exportar e importar ecossistema</h2>
    <div class="grid2"><div class="cx"><b>Exportar</b><p class="sub" style="margin:6px 0 10px">Perfil da organização, configuração de cada ferramenta e ajustes do portal num único arquivo, para montar o mesmo ecossistema em outro servidor.</p>
      <div class="linha"><input class="campo" id="xNome" value="Projetos" style="max-width:220px"><a class="btn primario" id="xBtn" href="#">Baixar ecossistema</a></div></div>
    <div class="cx"><b>Importar</b><p class="sub" style="margin:6px 0 10px">Aplica um arquivo de ecossistema. Ferramentas que não estiverem plugadas aparecem na lista para você instalar.</p>
      <div class="linha"><input type="file" id="iArq" accept=".json"><label style="font-size:12.5px"><input type="checkbox" id="iOrg" checked> aplicar também o perfil da organização</label><button class="btn primario" id="iBtn">Importar</button></div><div id="iRes"></div></div></div>`
  api('/api/admin/ecossistema/modelos').then(ms => {
    $('#eMod').innerHTML = ms.map(x => `<div class="cx"><b>${esc(x.nome)}</b><p class="sub" style="margin:6px 0 8px">${esc(x.descricao)}</p><p style="font-size:12.5px;margin:0 0 10px">Ferramentas: ${x.ferramentas.map(f => x.faltando.includes(f) ? `<span class="mono" style="color:var(--err,#b42318)" title="não plugada">${esc(f)}</span>` : `<span class="mono">${esc(f)}</span>`).join(', ')}</p><button class="btn primario" data-mod="${esc(x.arquivo)}">Aplicar modelo</button></div>`).join('') || '<p class="mut">Nenhum modelo.</p>'
    document.querySelectorAll('[data-mod]').forEach(b => b.onclick = async () => { if (!confirm('Aplicar o modelo "' + b.closest('.cx').querySelector('b').textContent + '"? As ferramentas fora do modelo ficam ocultas no portal.')) return
      try { const r = await api('/api/admin/ecossistema/modelos/' + encodeURIComponent(b.dataset.mod), { method: 'POST' }); toast('Modelo aplicado' + (r.ausentes.length ? ' — faltam plugar: ' + r.ausentes.map(a => a.id).join(', ') : '')); ecossistema() } catch (e) { toast(e.message) } })
  }).catch(() => { $('#eMod').innerHTML = '' })
  $('#xBtn').onclick = e => { e.preventDefault(); location.href = '/api/admin/ecossistema/exportar?nome=' + encodeURIComponent($('#xNome').value || 'Ecossistema') }
  $('#iBtn').onclick = async () => {
    const f = $('#iArq').files[0]; if (!f) return toast('Escolha o arquivo')
    try { const r = await api('/api/admin/ecossistema/importar?organizacao_tambem=' + $('#iOrg').checked, { method: 'POST', json: JSON.parse(await f.text()) })
      $('#iRes').innerHTML = `<div class="alerta ${r.ausentes.length ? 'aviso' : ''}" style="margin-top:10px">Configuradas: ${esc(r.configuradas.join(', ') || 'nenhuma')}${r.organizacao ? ' · organização aplicada' : ''}${r.ausentes.length ? '<br>Faltam plugar: ' + r.ausentes.map(a => esc(a.nome + ' (' + a.id + ' ' + (a.versao || '') + ')')).join(', ') : ''}</div>` } catch (e) { toast(e.message) }
  }
}
function desenharMapa(m) {
  const ns = m.ferramentas; if (!ns.length) return '<p class="mut">Nenhuma ferramenta com contratos declarados.</p>'
  const W = 900, H = Math.max(260, 120 + ns.length * 34), cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 70
  const pos = {}; ns.forEach((n, i) => { const a = -Math.PI / 2 + i * 2 * Math.PI / ns.length; pos[n.id] = [cx + R * 1.5 * Math.cos(a), cy + R * Math.sin(a)] })
  let g = '<defs><marker id="seta" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#4a72b8"/></marker></defs>'
  const pares = {}
  m.contratos.forEach(c => c.publica.forEach(p => c.consome.forEach(q => { if (pos[p] && pos[q]) { const k = p + '>' + q; (pares[k] = pares[k] || []).push(c.contrato) } })))
  Object.entries(pares).forEach(([k, cs]) => { const [p, q] = k.split('>'), [x1, y1] = pos[p], [x2, y2] = pos[q], mx = (x1 + x2) / 2 + (y2 - y1) * .18, my = (y1 + y2) / 2 - (x2 - x1) * .18
    const enc = (a, b, t) => a + (b - a) * t
    g += `<path d="M${enc(x1, mx, .25)},${enc(y1, my, .25)} Q${mx},${my} ${enc(x2, mx, .25)},${enc(y2, my, .25)}" fill="none" stroke="#4a72b8" stroke-width="1.6" marker-end="url(#seta)"><title>${esc(cs.join('\n'))}</title></path>
      <text x="${mx}" y="${my}" font-size="10.5" text-anchor="middle" fill="#6b6960">${esc(cs.map(c => c.split('/')[1]).join(', '))}</text>` })
  ns.forEach(n => { const [x, y] = pos[n.id]; g += `<g><rect x="${x - 82}" y="${y - 20}" width="164" height="40" rx="9" fill="#fff" stroke="#c8c5ba"/><text x="${x}" y="${y + 5}" font-size="12.5" text-anchor="middle" font-weight="600" fill="#1a1916">${esc(n.icone)} ${esc(n.nome.length > 20 ? n.nome.slice(0, 19) + '…' : n.nome)}</text></g>` })
  return `<svg viewBox="0 0 ${W} ${H}">${g}</svg>`
}

// ── Dependências
async function dependencias() {
  const ds = await api('/api/admin/dependencias')
  M().innerHTML = `<h1>Dependências</h1><p class="sub">O que cada ferramenta declara precisar (serviços do hub, outras ferramentas e contratos do Data Core) e os pacotes instalados no ambiente Python dela.</p>` +
    ds.map(d => `<div class="cx"><div class="linha"><b>${esc(d.nome)}</b><span class="mut mono">${esc(d.id)}</span><span class="esp"></span>${d.requisitos ? `<button class="btn mini" data-pk="${esc(d.id)}">Pacotes instalados</button><button class="btn mini" data-ri="${esc(d.id)}">Reinstalar dependências</button>` : ''}</div>
      ${d.itens.length ? tabela([['Item', i => `<span class="mono">${esc(i.item)}</span>`], ['Tipo', i => esc(i.tipo)], ['Situação', i => i.ok ? pill('ok', 'ok') : pill('pendente', 'err')], ['Obs.', i => esc(i.obs || '')]], d.itens) : '<p class="mut" style="margin-top:6px">Nenhuma dependência declarada.</p>'}<div id="pk-${esc(d.id)}"></div></div>`).join('')
  M().querySelectorAll('[data-pk]').forEach(b => b.onclick = async () => { const box = $('#pk-' + CSS.escape(b.dataset.pk)); box.innerHTML = '<p class="mut">consultando…</p>'; const r = await api(`/api/admin/ferramentas/${b.dataset.pk}/pacotes`); box.innerHTML = r.ambiente ? `<div class="mono" style="margin-top:8px;columns:3;font-size:11.5px">${r.pacotes.map(p => esc(p.name + ' ' + p.version)).join('<br>')}</div>` : '<p class="mut">Ambiente ainda não criado (é criado no primeiro início).</p>' })
  M().querySelectorAll('[data-ri]').forEach(b => b.onclick = async () => { if (!confirm('Reinstalar as dependências e reiniciar a ferramenta?')) return; try { await api(`/api/admin/ferramentas/${b.dataset.ri}/reinstalar`, { method: 'POST' }); toast('Reinstalando — acompanhe o log') } catch (e) { toast(e.message) } })
}

// ── Data Core
async function datacoreTela(filtro = {}) {
  const q = new URLSearchParams(Object.entries(filtro).filter(([, v]) => v)); const d = await api('/api/admin/datacore?' + q)
  M().innerHTML = `<h1>Data Core</h1><p class="sub">${d.resumo.records} registros · ${d.resumo.history} gravações no histórico. Cada ferramenta tem seu banco; aqui fica só o que elas trocam.</p>
    ${tabela([['Domínio', t => `<a href="#" data-f="${esc(t.domain)}|${esc(t.entity_type)}">${esc(t.domain)}</a>`], ['Tipo', t => esc(t.entity_type)], ['Registros', t => t.registros], ['Última gravação', t => esc(quando(t.ultimo))]], d.resumo.tipos)}
    <h2>Registros${filtro.domain ? ` · ${esc(filtro.domain)}/${esc(filtro.entity_type)}` : ''}</h2>
    <div class="linha" style="margin-bottom:8px"><input class="campo" id="dBusca" placeholder="buscar por chave, origem ou usuário" value="${esc(filtro.busca || '')}" style="max-width:320px"><button class="btn" id="dOk">Buscar</button>${filtro.domain ? '<button class="btn" id="dLimpa">Todos</button>' : ''}</div>
    ${tabela([['Contrato', r => `<span class="mono">${esc(r.domain)}/${esc(r.entity_type)}</span>`], ['Chave', r => `<b>${esc(r.entity_key)}</b>${r.period ? ' <span class="mut">' + esc(r.period) + '</span>' : ''}${r.deleted ? ' ' + pill('excluído', 'err') : ''}`],
      ['Versão', r => r.version], ['Origem', r => esc(r.source_application) + `<div class="mut">${esc(r.source_user)}</div>`], ['Atualizado', r => esc(quando(r.updated_at))], ['', r => `<button class="btn mini" data-r="${r.id}">Ver</button>`]], d.registros)}<div id="dDet"></div>`
  M().querySelectorAll('[data-f]').forEach(a => a.onclick = e => { e.preventDefault(); const [domain, entity_type] = a.dataset.f.split('|'); datacoreTela({ domain, entity_type }) })
  $('#dOk').onclick = () => datacoreTela({ ...filtro, busca: $('#dBusca').value }); const l = $('#dLimpa'); if (l) l.onclick = () => datacoreTela({})
  M().querySelectorAll('[data-r]').forEach(b => b.onclick = async () => {
    const [r, h] = await Promise.all([api('/api/data-core/records/' + b.dataset.r), api(`/api/data-core/records/${b.dataset.r}/history?limit=30`)])
    $('#dDet').innerHTML = `<div class="cx" style="margin-top:14px"><b>${esc(r.domain)}/${esc(r.entity_type)} · ${esc(r.entity_key)}</b> <span class="mut">v${r.version}</span>
      <pre class="logbox" style="margin-top:8px;max-height:320px">${esc(JSON.stringify(r.payload, (k, v) => typeof v === 'string' && v.length > 300 ? v.slice(0, 120) + '… (' + v.length + ' caracteres)' : v, 2))}</pre>
      <h2>Histórico</h2>${tabela([['Versão', x => x.version], ['Operação', x => esc(x.operation)], ['Quando', x => esc(quando(x.at))], ['Origem', x => esc(x.source_application)], ['Usuário', x => esc(x.source_user)]], h)}</div>`
    $('#dDet').scrollIntoView({ behavior: 'smooth' })
  })
}

// ── Logs
async function logs(alvo) {
  const apps = APPS.length ? APPS : (APPS = await api('/api/admin/ferramentas'))
  alvo = alvo || (apps[0] && apps[0].id) || 'hub'
  M().innerHTML = `<h1>Logs</h1><div class="linha" style="margin:10px 0"><select class="campo" id="lAlvo" style="max-width:280px"><option value="hub">Hub</option>${apps.map(a => `<option value="${esc(a.id)}"${a.id === alvo ? ' selected' : ''}>${esc(a.nome)}</option>`).join('')}</select>
    <input class="campo" id="lBusca" placeholder="filtrar linhas" style="max-width:240px"><select class="campo" id="lQtd" style="max-width:130px"><option>500</option><option>2000</option><option>10000</option></select>
    <button class="btn" id="lAt">Atualizar</button><button class="btn" id="lBx">Baixar</button></div><pre class="logbox" id="lTxt">carregando…</pre>`
  if (alvo === 'hub') $('#lAlvo').value = 'hub'
  const carregar = async () => { const d = await api(`/api/admin/logs/${$('#lAlvo').value}?linhas=${$('#lQtd').value}&busca=${encodeURIComponent($('#lBusca').value)}`); const p = $('#lTxt'); p.textContent = d.log || '(vazio)'; p.scrollTop = p.scrollHeight }
  $('#lAlvo').onchange = carregar; $('#lAt').onclick = carregar; $('#lBusca').onchange = carregar; $('#lQtd').onchange = carregar
  $('#lBx').onclick = () => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([$('#lTxt').textContent])); a.download = `log-${$('#lAlvo').value}.txt`; a.click() }
  carregar()
}

// ── Auditoria
async function auditoriaTela(busca = '') {
  const evs = await api('/api/admin/auditoria?limite=1000&busca=' + encodeURIComponent(busca))
  M().innerHTML = `<h1>Auditoria</h1><p class="sub">Logins, usuários, organização, ferramentas, ecossistema e backups.</p>
    <div class="linha" style="margin-bottom:8px"><input class="campo" id="aB" value="${esc(busca)}" placeholder="buscar" style="max-width:300px"><button class="btn" id="aOk">Buscar</button><a class="btn" href="/api/admin/auditoria?formato=csv&limite=20000&busca=${encodeURIComponent(busca)}">Exportar CSV</a></div>
    ${tabela([['Quando', e => esc(quando(e.em))], ['Quem', e => esc(e.por)], ['Ação', e => esc(e.acao)], ['Alvo', e => esc(e.alvo)], ['IP', e => `<span class="mono">${esc(e.ip || '')}</span>`], ['Detalhes', e => `<span class="mono" style="font-size:11px">${esc(e.detalhes ? JSON.stringify(e.detalhes) : '')}</span>`]], evs, 'Sem eventos.')}`
  $('#aOk').onclick = () => auditoriaTela($('#aB').value)
}

// ── Usuários
async function usuariosTela() {
  const [us, apps, org, eu] = await Promise.all([api('/api/hub/usuarios'), api('/api/admin/ferramentas'), api('/api/hub/organizacao'), api('/api/hub/eu')])
  const teste = eu.ambiente === 'teste'
  const locs = org.localidades || []
  const selLoc = (v, attr) => `<select class="campo" ${attr} style="padding:5px"><option value="">— nacional —</option>${locs.map(l => `<option value="${esc(l.codigo)}"${l.codigo === v ? ' selected' : ''}>${esc(l.nome)}</option>`).join('')}</select>`
  const chk = (u, pref) => `<div class="apps-chk"><label><input type="checkbox" data-${pref}-todas ${(u.apps || ['*']).includes('*') ? 'checked' : ''}> todas</label>${apps.map(a => `<label><input type="checkbox" data-${pref}-app="${esc(a.id)}" ${(u.apps || []).includes(a.id) ? 'checked' : ''}> ${esc(a.nome)}</label>`).join('')}</div>`
  M().innerHTML = `<h1>Usuários</h1><p class="sub">O hub diz <b>quem</b> é a pessoa (login único); cada ferramenta define <b>o que</b> ela faz lá dentro. Administradores do hub veem todas as ferramentas e acessam este Admin. O e-mail é o login e é por ele que as integrações (Teams, Outlook) encontram a pessoa; as ferramentas recebem o e-mail novo no próximo acesso da pessoa. <b>Líder direto</b> monta a hierarquia usada pelas ferramentas (atribuição de tarefas um nível por vez, visão de equipe); a <b>jornada</b> é a base da carga na agenda.${teste ? ' <b>Ambiente de teste:</b> marque MASTER TESTE em quem testa os fluxos de ponta a ponta (recebe todas as pendências e executa qualquer papel). Em produção essa opção não existe.' : ''}</p>
    ${tabela([['Nome e e-mail', u => `<input class="campo" data-nm="${esc(u.id)}" value="${esc(u.nome)}" style="padding:5px;font-weight:600;min-width:170px"><input class="campo" type="email" data-em="${esc(u.id)}" value="${esc(u.email || '')}" placeholder="e-mail (login e integrações)" style="padding:5px;margin-top:4px;min-width:170px;font-size:12.5px">`], ['Papel', u => u.papel === 'dono' ? pill('dono', 'ac') : `<select class="campo" data-pp="${esc(u.id)}" style="padding:5px"><option value="usuario"${u.papel === 'usuario' ? ' selected' : ''}>usuário</option><option value="admin"${u.papel === 'admin' ? ' selected' : ''}>administrador</option></select>`],
      ['Líder direto · jornada', u => `<select class="campo" data-ld="${esc(u.id)}" style="padding:5px;max-width:180px"><option value="">— (topo)</option>${us.filter(x => x.id !== u.id).map(x => `<option value="${esc(x.id)}"${u.lider_id === x.id ? ' selected' : ''}>${esc(x.nome)}</option>`).join('')}</select><label style="display:block;font-size:12px;margin-top:4px"><input class="campo" type="number" min="1" max="16" step="0.5" data-jh="${esc(u.id)}" value="${esc(u.jornada_h || 8)}" style="width:64px;padding:3px 6px"> h/dia</label>`],
      ['Ferramentas', u => u.id === 'dono' ? 'todas' : `<div data-ua="${esc(u.id)}">${chk(u, 'e')}</div>`], ['Feriados', u => `${selLoc(u.localidade || '', `data-lo="${esc(u.id)}"`)}<label style="display:block;font-size:12px;margin-top:4px"><input type="checkbox" data-es="${esc(u.id)}" ${u.escala_feriado ? 'checked' : ''}> trabalha em feriados (escala)</label>`],
      ['Situação', u => (u.id === 'dono' ? pill('ativo', 'ok') : `<label><input type="checkbox" data-at="${esc(u.id)}" ${u.ativo !== false ? 'checked' : ''}> ativo</label>`)
        + (teste ? `<label style="display:block;font-size:12px;margin-top:4px" title="Só em ambiente de teste: recebe todas as pendências e pode executar qualquer papel nas ferramentas"><input type="checkbox" data-mt="${esc(u.id)}" ${u.master_teste ? 'checked' : ''}> MASTER TESTE</label>` : '')],
      ['', u => u.id === 'dono' ? `<button class="btn mini primario" data-sv="dono">Salvar</button>` : `<div class="linha"><button class="btn mini primario" data-sv="${esc(u.id)}">Salvar</button><button class="btn mini" data-pw="${esc(u.id)}">Nova senha</button><button class="btn mini perigo" data-del="${esc(u.id)}">Remover</button></div>`]], us)}
    <h2>Novo usuário</h2><div class="cx"><div class="grid3"><label class="f">Nome<input class="campo" id="nN"></label><label class="f">E-mail<input class="campo" id="nE" type="email"></label><label class="f">Senha inicial (8+)<input class="campo" id="nS" type="password"></label></div>
      <div class="linha" style="margin-top:10px"><label class="f" style="flex-direction:row;align-items:center;gap:6px">Papel <select class="campo" id="nP" style="padding:5px"><option value="usuario">usuário</option><option value="admin">administrador</option></select></label>
        <label class="f" style="flex-direction:row;align-items:center;gap:6px">Localidade ${selLoc('', 'id="nL"')}</label><label style="font-size:12.5px"><input type="checkbox" id="nEs"> trabalha em feriados (escala)</label></div>
      <div style="margin-top:10px" id="nApps">${chk({ apps: ['*'] }, 'n')}</div><div class="linha" style="margin-top:12px"><button class="btn primario" id="nOk">Criar usuário</button></div></div>`
  const lerApps = box => box.querySelector('[data-e-todas],[data-n-todas]').checked ? ['*'] : [...box.querySelectorAll('[data-e-app]:checked,[data-n-app]:checked')].map(x => x.dataset.eApp || x.dataset.nApp)
  M().querySelectorAll('[data-sv]').forEach(b => b.onclick = async () => { const id = b.dataset.sv, q = s => M().querySelector(s)
    const u0 = us.find(x => x.id === id) || {}, mt = q(`[data-mt="${CSS.escape(id)}"]`)
    const fer = { lider_id: q(`[data-ld="${CSS.escape(id)}"]`).value || null, jornada_h: Number(q(`[data-jh="${CSS.escape(id)}"]`).value) || 8, nome: q(`[data-nm="${CSS.escape(id)}"]`).value.trim(), email: q(`[data-em="${CSS.escape(id)}"]`).value.trim().toLowerCase(), localidade: q(`[data-lo="${CSS.escape(id)}"]`).value || null, escala_feriado: q(`[data-es="${CSS.escape(id)}"]`).checked, ...(mt ? { master_teste: mt.checked } : {}) }
    if (fer.email !== (u0.email || '') && !confirm(`Trocar o e-mail de ${u0.nome} de “${u0.email || '(vazio)'}” para “${fer.email}”?\n\nO e-mail é o login da pessoa e é por ele que as integrações (Teams, Outlook) a encontram. A troca fica registrada na auditoria.`)) return
    try { await api('/api/hub/usuarios/' + id, { method: 'PUT', json: id === 'dono' ? fer : { ...fer, papel: q(`[data-pp="${CSS.escape(id)}"]`).value, apps: lerApps(q(`[data-ua="${CSS.escape(id)}"]`)), ativo: q(`[data-at="${CSS.escape(id)}"]`).checked } }); toast('Usuário atualizado'); usuariosTela() } catch (e) { toast(e.message) } })
  M().querySelectorAll('[data-pw]').forEach(b => b.onclick = async () => { const s = prompt('Nova senha (mínimo 8 caracteres):'); if (!s) return; try { await api('/api/hub/usuarios/' + b.dataset.pw, { method: 'PUT', json: { senha: s } }); toast('Senha redefinida') } catch (e) { toast(e.message) } })
  M().querySelectorAll('[data-del]').forEach(b => b.onclick = async () => { if (!confirm('Remover este usuário?')) return; try { await api('/api/hub/usuarios/' + b.dataset.del, { method: 'DELETE' }); usuariosTela() } catch (e) { toast(e.message) } })
  $('#nOk').onclick = async () => { try { await api('/api/hub/usuarios', { method: 'POST', json: { nome: $('#nN').value, email: $('#nE').value, senha: $('#nS').value, papel: $('#nP').value, apps: lerApps($('#nApps')), localidade: $('#nL').value || null, escala_feriado: $('#nEs').checked } }); toast('Usuário criado'); usuariosTela() } catch (e) { toast(e.message) } }
}

// ── Organização
async function organizacaoTela() {
  const o = await api('/api/hub/organizacao')
  M().innerHTML = `<h1>Organização</h1><p class="sub">Perfil lido por todas as ferramentas: trocar de organização é trocar estes dados.</p>
    <div class="cx"><div class="grid3"><label class="f">Nome<input class="campo" id="oNome" value="${esc(o.nome)}"></label><label class="f">Sigla<input class="campo" id="oSigla" value="${esc(o.sigla)}"></label><label class="f">Moeda<input class="campo" id="oMoeda" value="${esc(o.moeda)}"></label>
      <label class="f">Ano base<input class="campo" id="oAno" type="number" value="${esc(o.ano_base)}"></label><label class="f">Horizonte (anos)<input class="campo" id="oHor" type="number" value="${esc(o.horizonte_anos)}"></label><label class="f">Taxa de desconto a.a.<input class="campo" id="oTaxa" type="number" step="0.001" value="${esc(o.taxa_desconto_aa)}"></label>
      <label class="f">Cor primária<input class="campo" id="oCor" type="color" value="${esc(o.cor_primaria)}" style="height:40px"></label><label class="f">Cor de destaque<input class="campo" id="oCor2" type="color" value="${esc(o.cor_destaque)}" style="height:40px"></label></div></div>
    <div class="grid2"><div class="cx"><label class="f">Unidades — uma por linha: <code>CÓDIGO; Nome; Grupo; Localidade</code><textarea class="campo mono" id="oUni" rows="8">${esc(o.unidades.map(u => [u.codigo, u.nome, u.grupo, u.localidade || ''].join('; ').replace(/; $/, '')).join('\n'))}</textarea></label>
      <label class="f" style="margin-top:10px">Áreas — uma por linha<textarea class="campo mono" id="oAreas" rows="5">${esc((o.areas || []).join('\n'))}</textarea></label></div>
    <div class="cx"><label class="f">Vocabulário — <code>chave = termo exibido</code><textarea class="campo mono" id="oVoc" rows="15">${esc(Object.entries(o.vocabulario || {}).map(([k, v]) => k + ' = ' + v).join('\n'))}</textarea></label></div></div>
    <h2>Feriados</h2><div class="cx"><p class="sub" style="margin-top:0">Nacionais são automáticos (inclusive os móveis). Cadastre aqui os estaduais e municipais por <b>localidade</b>; unidades e pessoas apontam para uma localidade.</p>
      <label style="font-size:13px"><input type="checkbox" id="oFac" ${o.feriados_facultativos ? 'checked' : ''}> pontos facultativos (Carnaval, Quarta-feira de Cinzas, Corpus Christi) contam como feriado</label>
      <label class="f" style="margin-top:10px">Localidades — uma por bloco: primeira linha <code>CÓDIGO; Nome</code>, depois uma linha por feriado <code>MM-DD; Nome</code> (todo ano) ou <code>AAAA-MM-DD; Nome</code> (só aquele ano). Separe os blocos com uma linha em branco.
      <textarea class="campo mono" id="oLoc" rows="10">${esc((o.localidades || []).map(l => [l.codigo + '; ' + l.nome, ...(l.feriados || []).map(f => f.data + '; ' + f.nome)].join('\n')).join('\n\n'))}</textarea></label>
      <div class="linha" style="margin-top:8px"><label class="f" style="flex-direction:row;align-items:center;gap:6px">Ver feriados de <input class="campo" id="oFAno" type="number" value="${new Date().getFullYear()}" style="width:90px;padding:5px"></label><select class="campo" id="oFLoc" style="padding:5px;max-width:220px"><option value="">só nacionais</option>${(o.localidades || []).map(l => `<option value="${esc(l.codigo)}">${esc(l.nome)}</option>`).join('')}</select><button class="btn mini" id="oFVer">Ver</button></div><div id="oFLista"></div></div>
    <button class="btn primario" id="oOk">Salvar perfil</button>`
  $('#oFVer').onclick = async () => { try { const fs = await api(`/api/hub/feriados?ano=${$('#oFAno').value}&localidade=${encodeURIComponent($('#oFLoc').value)}`); $('#oFLista').innerHTML = tabela([['Data', f => `<span class="mono">${esc(f.data)}</span>`], ['Feriado', f => esc(f.nome)], ['Tipo', f => pill(f.tipo, f.tipo === 'facultativo' ? 'av' : f.tipo === 'local' ? 'ac' : 'ok')], ['Conta como feriado', f => f.conta ? 'sim' : 'não']], fs) } catch (e) { toast(e.message) } }
  const lerLocalidades = () => $('#oLoc').value.split(/\n\s*\n/).map(b => b.split('\n').map(l => l.trim()).filter(Boolean)).filter(b => b.length).map(b => { const [codigo, nome] = b[0].split(';').map(x => (x || '').trim()); return { codigo, nome, feriados: b.slice(1).map(l => { const [data, ...n] = l.split(';'); return { data: (data || '').trim(), nome: n.join(';').trim() } }) } })
  $('#oOk').onclick = async () => {
    const linhas = id => $(id).value.split('\n').map(l => l.trim()).filter(Boolean)
    try { await api('/api/hub/organizacao', { method: 'PUT', json: { nome: $('#oNome').value, sigla: $('#oSigla').value, moeda: $('#oMoeda').value, ano_base: $('#oAno').value, horizonte_anos: $('#oHor').value, taxa_desconto_aa: $('#oTaxa').value, cor_primaria: $('#oCor').value, cor_destaque: $('#oCor2').value,
      unidades: linhas('#oUni').map(l => { const [codigo, nome, grupo, localidade] = l.split(';').map(x => (x || '').trim()); return { codigo, nome, grupo, localidade } }), areas: linhas('#oAreas'),
      localidades: lerLocalidades(), feriados_facultativos: $('#oFac').checked,
      vocabulario: Object.fromEntries(linhas('#oVoc').map(l => { const i = l.indexOf('='); return [l.slice(0, i).trim(), l.slice(i + 1).trim()] }).filter(([k]) => k)) } }); toast('Perfil salvo — as ferramentas aplicam em até 30 s') } catch (e) { toast(e.message) }
  }
}

// ── Infraestrutura e backup
async function infra() {
  const i = await api('/api/admin/infra'), t = i.tailscale
  M().innerHTML = `<h1>Infraestrutura e backup</h1>
    <div class="kpis"><div class="kpi"><b>${esc(i.hub)}</b><span>STRAT·HUB · Python ${esc(i.python)}</span></div><div class="kpi"><b>${esc(i.disco_livre_gb)} GB</b><span>livres de ${esc(i.disco_total_gb)} GB</span></div>
      <div class="kpi"><b>${esc(i.dados_mb)} MB</b><span>dados do hub · backups ${esc(i.backups_mb)} MB</span></div><div class="kpi"><b>${t.instalado ? (t.publicado ? 'publicado' : 'instalado') : 'ausente'}</b><span>Tailscale ${t.ip ? '· ' + esc(t.ip) : ''}</span></div></div>
    <div class="grid2"><div class="cx"><b>Servidor</b>${tabela([['', x => esc(x[0])], ['', x => `<span class="mono">${esc(x[1])}</span>`]], [['Máquina', i.maquina], ['Sistema', i.sistema], ['Endereço', i.endereco], ['Ligado desde', i.ligado_desde], ['Pasta do hub', i.pasta_hub], ['Ferramentas', i.pasta_apps], ['Dados', i.pasta_dados]])}
      ${t.status ? `<pre class="logbox" style="margin-top:10px;max-height:160px">${esc(t.status)}</pre>` : ''}</div>
    <div class="cx"><b>Portas internas</b>${tabela([['Ferramenta', p => esc(p.nome)], ['Porta', p => p.porta ? `<span class="mono">${p.porta}</span>` : '—'], ['Situação', p => esc(p.status)]], i.portas)}</div></div>
    <h2>Backup</h2><div class="cx"><div class="linha"><span>Copia <code>data\\</code> do hub e as pastas <code>dados\\</code> e <code>config\\</code> de cada ferramenta.</span><span class="esp"></span><button class="btn primario" id="bAgora">Fazer backup agora</button></div>
      <div class="linha" style="margin-top:10px"><label><input type="checkbox" id="bDia" ${i.backup_diario ? 'checked' : ''}> backup diário automático</label><label class="f" style="flex-direction:row;align-items:center;gap:6px">manter <input class="campo" id="bMan" type="number" value="${i.backup_manter}" style="width:70px;padding:5px"> diários</label><button class="btn mini" id="bSalvar">Salvar</button></div>
      <div style="margin-top:12px">${tabela([['Arquivo', b => `<span class="mono">${esc(b.nome)}</span>`], ['Quando', b => esc(b.em)], ['Tamanho', b => b.tamanho_mb + ' MB'], ['', b => `<a class="btn mini" href="/api/admin/backups/${encodeURIComponent(b.nome)}">Baixar</a>`]], i.backups, 'Nenhum backup ainda.')}</div>
      <p class="mut" style="margin-top:8px;font-size:12px">Para restaurar: pare o hub, descompacte <code>hub_data\\</code> sobre <code>data\\</code> e <code>apps\\&lt;id&gt;\\…</code> sobre as pastas das ferramentas, e inicie de novo.</p></div>`
  $('#bAgora').onclick = async () => { $('#bAgora').disabled = true; try { const r = await api('/api/admin/backups', { method: 'POST' }); toast(`Backup criado (${r.tamanho_mb} MB)`); infra() } catch (e) { toast(e.message) } }
  $('#bSalvar').onclick = async () => { try { await api('/api/admin/config', { method: 'PUT', json: { backup_diario: $('#bDia').checked, backup_manter: $('#bMan').value } }); toast('Salvo') } catch (e) { toast(e.message) } }
}

// ── Configurações
async function configTela() {
  const c = await api('/api/admin/config')
  M().innerHTML = `<h1>Configurações</h1><div class="cx"><div class="grid3">
    <label class="f">Nome do hub<input class="campo" id="cNome" value="${esc(c.nome)}"></label><label class="f">Subtítulo<input class="campo" id="cSub" value="${esc(c.subtitulo || '')}"></label>
    <label class="f">Ociosidade padrão (min)<input class="campo" id="cOc" type="number" value="${esc(c.ocioso_min_padrao)}"></label><label class="f">Sessão (dias)<input class="campo" id="cSes" type="number" value="${esc(c.sessao_dias)}"></label>
    <label class="f">Tempo máximo para iniciar uma ferramenta (s)<input class="campo" id="cIni" type="number" value="${esc(c.inicio_timeout_s)}"></label></div>
    <button class="btn primario" id="cOk" style="margin-top:12px">Salvar</button></div>
    <div class="cx"><b>Somente no arquivo</b> <span class="mut">(exigem reiniciar o hub)</span>${tabela([['', x => esc(x[0])], ['', x => `<span class="mono">${esc(x[1])}</span>`]], [['host', c.host], ['porta', c.porta], ['porta inicial das ferramentas', c.porta_inicial_apps], ['ambiente', c.ambiente === 'teste' ? 'teste (MASTER TESTE disponível)' : 'produção']])}
    <p class="mut" style="margin-top:8px;font-size:12px">Edite <code>data\\hub.config.json</code> e reinicie. <code>127.0.0.1</code> = só esta máquina e Tailscale; <code>0.0.0.0</code> = também a rede local. <code>"ambiente": "teste"</code> liga o ambiente de teste (faixa no topo e MASTER TESTE); sem essa linha o hub é produção.</p></div>`
  $('#cOk').onclick = async () => { try { await api('/api/admin/config', { method: 'PUT', json: { nome: $('#cNome').value, subtitulo: $('#cSub').value, ocioso_min_padrao: $('#cOc').value, sessao_dias: $('#cSes').value, inicio_timeout_s: $('#cIni').value } }); toast('Configurações salvas') } catch (e) { toast(e.message) } }
}

// ── Notificações (padrões da organização, e-mail, envios)
async function notificacoesTela() {
  const n = await api('/api/admin/notificacoes'), ROT = { imediato: 'Na hora', resumo: 'Resumo diário', desligado: 'Não enviar' }
  const d = n.disponivel
  M().innerHTML = `<h1>Notificações</h1><p class="sub">As ferramentas publicam as pendências; o hub avisa cada pessoa por e-mail e Teams conforme a preferência dela (Portal › Notificações), partindo destes padrões. Obrigatórias: a pessoa escolhe a frequência, mas não desliga. Pendências anteriores à ativação não geram envio.</p>
    <div class="cx"><b>Canais</b> ${tabela([['', x => esc(x[0])], ['', x => x[1]]], [['E-mail', d.email ? pill('via ' + (d.email_via === 'smtp' ? 'servidor SMTP' : 'Conector Microsoft'), 'ok') : pill('não configurado', 'av')],
      ['Teams (feed de atividades)', d.teams ? pill('pelo Conector Microsoft', 'ok') : pill('não configurado (Conector Microsoft)', 'av')], ['Teams (canal da equipe)', d.teams_canal ? pill('ativo', 'ok') : pill('não configurado', 'av')],
      ['Aprovações no app Aprovações do Teams', d.aprovacoes_teams ? pill('ativo: não repetimos aviso dessas aprovações', 'ok') : '—']])}</div>
    <div class="cx"><div class="grid3"><label class="f"><span><input type="checkbox" id="nAt" ${n.ativo ? 'checked' : ''}> Envio de notificações ligado</span></label>
      <label class="f">Hora do resumo diário<input class="campo" id="nHr" value="${esc(n.hora_resumo)}" placeholder="07:30"></label>
      <label class="f">Endereço público do hub (para os links)<input class="campo" id="nEnd" value="${esc(n.endereco_publico || '')}" placeholder="https://hub.suaempresa.com"></label></div>
      ${tabela([['Tipo', x => esc(n.categorias[x]) ], ...Object.entries(n.canais).map(([ca, l]) => [l, x => `<select class="campo" data-pc="${x}" data-pca="${ca}" style="padding:5px">${Object.entries(ROT).map(([f, t]) => `<option value="${f}"${n.padrao[x][ca] === f ? ' selected' : ''}>${t}</option>`).join('')}</select>`]),
        ['Obrigatória', x => `<input type="checkbox" data-ob="${x}" ${n.obrigatorias.includes(x) ? 'checked' : ''}>`]], Object.keys(n.categorias))}
      <h2 style="margin-top:16px">Servidor de e-mail (SMTP)</h2><p class="sub">Opcional: sem SMTP, o e-mail sai pela conta de integração do Conector Microsoft (permissão Mail.Send).</p>
      <div class="grid3"><label class="f">Servidor<input class="campo" id="sH" value="${esc(n.smtp.host || '')}" placeholder="smtp.office365.com"></label><label class="f">Porta<input class="campo" id="sP" type="number" value="${esc(n.smtp.porta || '')}" placeholder="587"></label>
        <label class="f">Segurança<select class="campo" id="sS">${['starttls', 'ssl', 'nenhuma'].map(x => `<option${(n.smtp.seguranca || 'starttls') === x ? ' selected' : ''}>${x}</option>`).join('')}</select></label>
        <label class="f">Usuário<input class="campo" id="sU" value="${esc(n.smtp.usuario || '')}"></label><label class="f">Senha<input class="campo" id="sW" type="password" placeholder="${n.smtp.senha_definida ? '(definida — deixe em branco para manter)' : ''}"></label>
        <label class="f">Remetente<input class="campo" id="sR" value="${esc(n.smtp.remetente || '')}" placeholder="hub@suaempresa.com"></label></div>
      <div class="linha" style="margin-top:12px"><button class="btn primario" id="nOk">Salvar</button><button class="btn" id="nTes">Enviar e-mail de teste para mim</button></div></div>
    <div class="cx"><b>Envios recentes</b>${tabela([['Quando', e => esc(new Date(e.em * 1000).toLocaleString('pt-BR'))], ['Pessoa', e => esc(e.pessoa)], ['Pendência', e => `<span class="mono" style="font-size:11px">${esc(e.chave)}</span>`], ['Canal', e => esc(e.canal)], ['Situação', e => esc(e.situacao || '') + (e.erro ? ` <span class="mut">${esc(e.erro)}</span>` : '')]], n.envios, 'Nenhum envio ainda.')}</div>`
  $('#nOk').onclick = async () => {
    const padrao = {}; M().querySelectorAll('[data-pc]').forEach(s => { (padrao[s.dataset.pc] = padrao[s.dataset.pc] || {})[s.dataset.pca] = s.value })
    const smtp = { host: $('#sH').value, porta: $('#sP').value, seguranca: $('#sS').value, usuario: $('#sU').value, remetente: $('#sR').value }; if ($('#sW').value) smtp.senha = $('#sW').value
    try { await api('/api/admin/notificacoes', { method: 'PUT', json: { ativo: $('#nAt').checked, hora_resumo: $('#nHr').value, endereco_publico: $('#nEnd').value, padrao, obrigatorias: [...M().querySelectorAll('[data-ob]:checked')].map(x => x.dataset.ob), smtp } }); toast('Notificações salvas'); notificacoesTela() } catch (e) { toast(e.message) }
  }
  $('#nTes').onclick = async () => { try { const r = await api('/api/admin/notificacoes/teste', { method: 'POST' }); toast('Enviado para ' + r.para) } catch (e) { toast(e.message) } }
}

const ROTAS = { notificacoes: notificacoesTela, visao, ferramentas, ecossistema, dependencias, datacore: () => datacoreTela(), logs, auditoria: () => auditoriaTela(), usuarios: usuariosTela, organizacao: organizacaoTela, infra, config: configTela }
async function rota() {
  const [r, arg] = (location.hash.slice(1) || 'visao').split('/')
  document.querySelectorAll('#admNav a').forEach(a => a.classList.toggle('on', a.getAttribute('href') === '#' + r))
  try { await (ROTAS[r] || visao)(arg) } catch (e) { M().innerHTML = `<h1>Erro</h1><div class="alerta erro">${esc(e.message)}</div>` }
  window.scrollTo(0, 0)
}
window.addEventListener('hashchange', rota); rota()
