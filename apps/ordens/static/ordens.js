/* Ordens de Serviço — interface. Usa o kit comum (K). O quadro das equipes está em quadro.js. */
'use strict'
const { $, esc, api, V, din, nf, data, dh, toast, modal, pedir, tabela, opcoes, unidades, st } = K
const pf = () => st.user.perfil
const GESTAO = ['gestor', 'supervisor', 'planejador']
const gestor = () => pf() === 'gestor', gest = () => GESTAO.includes(pf()), sup = () => ['gestor', 'supervisor'].includes(pf()), tec = () => pf() === 'tecnico'
const modPlan = () => ['gestor', 'planejador'].includes(pf())  // modelos de OS e funções
const tem = app => !st.meta.ecossistema || st.meta.ecossistema.includes(app)  // ferramenta instalada? (fora do hub: tudo disponível)
const SLA = { no_prazo: ['No prazo', 'ok'], em_risco: ['Em risco', 'warn'], atrasada: ['Atrasada', 'bad'], cumprido: ['Cumprido', 'ok'], violado: ['Violado', 'bad'], cancelada: ['—', ''], sem_sla: ['Sem SLA', ''] }
const tagSla = s => { const [l, c] = SLA[(s || {}).situacao] || ['—', '']; return `<span class="tag ${c}" title="${s && s.restante_h != null ? esc(nf(s.restante_h, 1)) + ' h restantes' : ''}">${l}</span>` }
const tagSt = o => `<span class="tag ${o.status === 'cancelada' ? 'bad' : ['concluida', 'encerrada'].includes(o.status) ? 'ok' : o.status === 'pausada' ? 'warn' : 'pri'}">${esc(o.status_nome || o.status)}</span>`
const prio = p => `<span class="tag ${p === 'urgente' ? 'bad' : p === 'alta' ? 'warn' : ''}">${esc(st.meta.prioridades[p] || p)}</span>`
const linkOS = o => `<a href="#/os/${encodeURIComponent(o.numero)}">${esc(o.numero)}</a>`
const hhmm = s => s ? new Date(s).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : ''
const dtLocal = s => { if (!s) return ''; const d = new Date(s); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16) }
const fdata = iso => iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}` : ''
const urlCampo = n => 'campo.html' + (n ? '#/os/' + encodeURIComponent(n) : '')
let EQUIPES = [], MATERIAIS = [], FUNCOES = []
async function cadastros() { [EQUIPES, MATERIAIS, FUNCOES] = await Promise.all([api('GET', '/api/equipes'), api('GET', '/api/materiais'), api('GET', '/api/funcoes')]) }
const podeExecutor = f => { const x = FUNCOES.find(y => y.nome === f); return x ? !!x.pode_executor : true }

// ───────────── Painel
async function telaPainel(m) {
  const p = await api('GET', '/api/painel')
  if (!p.total) {
    m.innerHTML = `<h1>Ordens de Serviço</h1><p class="lede">Execução operacional: cada OS nasce de um modelo (checklist, fotos, SLA), tem equipe, um executor, data de execução, materiais e horas.</p>
      <div class="panel"><p>Nenhuma OS ainda. ${!tec() ? '<a href="#/nova">Abra a primeira</a>' : ''}${gestor() ? ' ou carregue um exemplo para conhecer a ferramenta.' : '.'}</p>${gestor() ? '<button class="btn pri" id="demo">Carregar exemplo</button>' : ''}</div>`
    const b = $('#demo'); if (b) b.onclick = async () => { try { await api('POST', '/api/demo'); toast('Exemplo carregado.'); K.render() } catch (e) { toast(e.message) } }
    return
  }
  const bar = obj => { const tot = Object.values(obj).reduce((a, b) => a + b, 0) || 1; return Object.entries(obj).map(([k, v]) => `<div class="row small" style="margin:4px 0"><span style="width:170px">${esc(k)}</span><div class="bar" style="flex:1"><span style="width:${100 * v / tot}%"></span></div><b style="width:34px;text-align:right">${v}</b></div>`).join('') || '<p class="muted small">—</p>' }
  m.innerHTML = `<h1>Painel de serviços</h1><p class="lede">${p.abertas} OS em andamento de ${p.total} registradas.</p>
    <div class="cards"><div class="card"><div class="v">${p.abertas}</div><div class="l">Em andamento</div></div>
    <div class="card ok"><div class="v">${p.sla.no_prazo}</div><div class="l">No prazo</div></div><div class="card warn"><div class="v">${p.sla.em_risco}</div><div class="l">SLA em risco</div></div>
    <div class="card bad"><div class="v">${p.sla.atrasada}</div><div class="l">SLA estourado</div></div>
    <div class="card"><div class="v">${p.aguardando_aprovacao || 0}</div><div class="l">Aguardando aprovação</div></div>
    <div class="card"><div class="v">${p.sla_cumprido_pct == null ? '—' : nf(p.sla_cumprido_pct, 1) + '%'}</div><div class="l">SLA cumprido (finalizadas)</div></div>
    <div class="card"><div class="v">${p.tempo_medio_h == null ? '—' : nf(p.tempo_medio_h, 1) + ' h'}</div><div class="l">Tempo médio de atendimento</div></div>
    ${p.custo_previsto != null ? `<div class="card"><div class="v">${din(p.custo_previsto)}</div><div class="l">Custo previsto</div></div><div class="card"><div class="v">${din(p.custo_real)}</div><div class="l">Custo realizado</div></div>` : ''}</div>
    <div class="grid2"><div class="panel"><h2>Por situação</h2>${bar(Object.fromEntries(Object.entries(p.por_status).filter(([, v]) => v).map(([k, v]) => [st.meta.status[k], v])))}</div>
    <div class="panel"><h2>Em andamento por tipo</h2>${bar(p.por_tipo)}</div>
    <div class="panel"><h2>Em andamento por prioridade</h2>${bar(p.por_prioridade)}</div><div class="panel"><h2>Em andamento por ${esc(V('unidade').toLowerCase())}</h2>${bar(p.por_unidade)}</div></div>
    <div class="panel"><h2>Atenção: prazos</h2>${tabela([{ t: 'OS', h: linkOS }, { t: 'Título', wrap: 1, k: 'titulo' }, { t: 'Situação', h: tagSt }, { t: 'Prioridade', h: o => prio(o.prioridade) }, { t: 'SLA', h: o => tagSla(o.sla) }, { t: 'Prazo', h: o => dh(o.prazo_sla) }, { t: 'Equipe', h: o => esc(o.equipe || '—') }], p.criticas, { vazio: 'Nenhuma OS com prazo em risco.' })}</div>`
}

// ───────────── Kanban e lista
const COLUNAS = ['aberta', 'planejada', 'em_execucao', 'pausada', 'concluida']
async function telaKanban(m) {
  const os = await api('GET', '/api/ordens' + (tec() ? '?minhas=true' : ''))
  m.innerHTML = `<div class="row"><h1 style="margin:0">Quadro</h1><span class="spacer"></span>${!tec() ? '<a class="btn pri" href="#/nova">+ Nova OS</a>' : ''}</div><p class="lede">Fluxo Aberta → Planejada → Em execução → Concluída → Encerrada. A cor da borda indica o SLA.</p>
    <div class="kanban">${COLUNAS.map(s => { const xs = os.filter(o => o.status === s); return `<div class="col"><h3>${esc(st.meta.status[s])}<span>${xs.length}</span></h3>${xs.map(o => `<a class="os-card ${esc((o.sla || {}).situacao)}" href="#/os/${encodeURIComponent(o.numero)}"><span class="small muted">${esc(o.numero)} · ${esc(o.tipo_nome)}</span><b>${esc(o.titulo)}</b><div class="small">${prio(o.prioridade)} ${tagSla(o.sla)}${o.aprovacao === 'pendente' ? ' <span class="tag warn">aguardando aprovação</span>' : ''}</div><div class="small muted">${esc(o.equipe || 'sem equipe')}${o.executor ? ' · ' + esc(o.executor) : ''}${o.execucao_inicio ? ' · ' + dh(o.execucao_inicio) : ''}</div></a>`).join('') || '<p class="small muted">—</p>'}</div>` }).join('')}</div>`
}
const filtro = { status: '', sla_sit: '', busca: '', equipe: '', minhas: '' }
async function telaLista(m) {
  await cadastros()
  if (tec() && filtro.minhas === '') filtro.minhas = 'true'
  const q = new URLSearchParams(Object.entries(filtro).filter(([, v]) => v)).toString()
  const os = await api('GET', '/api/ordens' + (q ? '?' + q : ''))
  m.innerHTML = `<div class="row"><h1 style="margin:0">Ordens de serviço</h1><span class="spacer"></span>${!tec() ? '<a class="btn pri" href="#/nova">+ Nova OS</a>' : ''}</div><p class="lede">${os.length} OS.</p>
    <div class="filtros"><label class="f">Situação<select class="in" id="fSt">${opcoes(Object.entries(st.meta.status).map(([v, l]) => ({ v, l })), filtro.status, 'Todas')}</select></label>
    <label class="f">SLA<select class="in" id="fSla">${opcoes(Object.entries(SLA).filter(([k]) => !['cancelada', 'sem_sla'].includes(k)).map(([v, [l]]) => ({ v, l })), filtro.sla_sit, 'Todos')}</select></label>
    <label class="f">Equipe<select class="in" id="fEq">${opcoes(EQUIPES.map(e => ({ v: e.id, l: e.nome })), filtro.equipe, 'Todas')}</select></label>
    ${pf() !== 'solicitante' ? `<label class="f">Mostrar<select class="in" id="fMi">${opcoes([{ v: 'false', l: 'Todas' }, { v: 'true', l: 'Só as minhas' }], filtro.minhas || 'false')}</select></label>` : ''}
    <label class="f">Busca<input class="in" id="fBu" value="${esc(filtro.busca)}" placeholder="número, título, local, cliente"></label></div>
    ${tabela([{ t: 'OS', h: linkOS }, { t: 'Título', wrap: 1, h: o => `<b>${esc(o.titulo)}</b><div class="small muted">${esc(o.tipo_nome)} · ${esc(o.local || o.unidade || '')}${o.cliente ? ' · ' + esc(o.cliente) : ''}</div>` }, { t: 'Situação', h: tagSt }, { t: 'Prioridade', h: o => prio(o.prioridade) },
      { t: 'SLA', h: o => tagSla(o.sla) }, { t: 'Equipe', h: o => esc(o.equipe || '—') + (o.executor ? `<div class="small muted">${esc(o.executor)}</div>` : '') }, { t: 'Execução', h: o => dh(o.execucao_inicio) }, { t: 'Checklist', h: o => `${o.checklist.feitos}/${o.checklist.total}` },
      ...(gest() ? [{ t: 'Previsto', n: 1, h: o => din(o.custos.previsto) }, { t: 'Real', n: 1, h: o => din(o.custos.real) }] : [])], os, { vazio: 'Nenhuma OS com estes filtros.', max: 680 })}`
  $('#fSt').onchange = e => { filtro.status = e.target.value; K.render() }; $('#fSla').onchange = e => { filtro.sla_sit = e.target.value; K.render() }
  $('#fEq').onchange = e => { filtro.equipe = e.target.value; K.render() }; $('#fBu').onchange = e => { filtro.busca = e.target.value; K.render() }
  const mi = $('#fMi'); if (mi) mi.onchange = e => { filtro.minhas = e.target.value === 'true' ? 'true' : 'false'; K.render() }
}

// ───────────── Nova OS (a partir de um modelo)
async function telaNova(m) {
  if (tec()) { m.innerHTML = '<h1>Nova OS</h1><div class="note">Técnicos executam OS; a abertura é do solicitante, do planejador ou do gestor.</div>'; return }
  const [projetos, modelos] = await Promise.all([gest() && tem('pmp') ? api('GET', '/api/projetos').catch(() => []) : [], api('GET', '/api/modelos')])
  if (!modelos.length) { m.innerHTML = `<h1>Nova OS</h1><div class="note warn">Nenhum modelo de OS ativo. ${modPlan() ? '<a href="#/modelos">Cadastre um modelo</a>.' : 'Peça ao planejador para cadastrar um modelo.'}</div>`; return }
  m.innerHTML = `<h1>Nova ordem de serviço</h1><p class="lede">Escolha o modelo: ele traz o checklist, as fotos obrigatórias, o SLA e as orientações. ${modPlan() ? 'Se nenhum servir, <a href="#/modelos/novo">crie um modelo novo</a>.' : ''}</p>
    <div class="panel"><div class="grid3">
      <label class="f" style="grid-column:span 2">Título<input class="in" id="nTit" maxlength="140"></label>
      <label class="f">Modelo de OS<select class="in" id="nMod">${opcoes(modelos.map(t => ({ v: t.id, l: t.nome })))}</select></label>
      <label class="f">Prioridade<select class="in" id="nPri"></select></label>
      <label class="f">${esc(V('unidade'))}<select class="in" id="nUni">${opcoes(unidades(), st.user.unidade, '—')}</select></label>
      <label class="f">Local<input class="in" id="nLoc" placeholder="prédio, sala, endereço"></label>
      <label class="f">Ativo / equipamento<input class="in" id="nAtv"></label>
      <label class="f">Contato<input class="in" id="nCont" placeholder="nome, telefone"></label>
      ${gest() ? `<label class="f">Cliente<input class="in" id="nCli"></label>${tem('pmp') ? `<label class="f">Projeto vinculado (PMP)${projetos.length ? `<select class="in" id="nProj">${opcoes(projetos.map(p => ({ v: p.codigo, l: p.codigo + ' · ' + p.nome })), '', '—')}</select>` : '<input class="in" id="nProj" placeholder="código do projeto">'}</label>` : ''}` : ''}
    </div><label class="f" style="margin-top:10px">Descrição<textarea class="in" id="nDesc" rows="4"></textarea></label>
    <div id="nPrev" class="small" style="margin-top:10px"></div>
    <div class="row" style="margin-top:10px"><button class="btn pri" id="nSalvar">Abrir OS</button><a class="btn" href="#/lista">Cancelar</a></div></div>`
  const atual = () => modelos.find(x => x.id === $('#nMod').value)
  const prev = () => {
    const md = atual(), h = (md.sla_horas || {})[$('#nPri').value]
    const fotos = md.checklist.filter(i => i.foto_min).length
    $('#nPrev').innerHTML = `<div class="note">${h ? `Prazo de atendimento: <b>${h} h</b> a partir da abertura.` : 'Sem SLA para esta prioridade.'} · ${md.checklist.length} item(ns) de checklist${fotos ? `, ${fotos} com foto obrigatória` : ''}${md.fotos_depois_min ? ` · ${md.fotos_depois_min} foto(s) do depois` : ''} · ${md.exige_aprovacao ? 'encerramento com aprovação do supervisor' : 'encerra ao concluir'}${md.duracao_h ? ` · duração típica ${nf(md.duracao_h, 1)} h` : ''}</div>${md.orientacoes ? `<p class="muted"><b>Orientações:</b> ${esc(md.orientacoes)}</p>` : ''}`
  }
  const trocaModelo = () => { const md = atual(); $('#nPri').innerHTML = opcoes(Object.entries(st.meta.prioridades).map(([v, l]) => ({ v, l })), md.prioridade || 'media'); if (!$('#nDesc').value) $('#nDesc').placeholder = md.descricao || ''; prev() }
  $('#nMod').onchange = trocaModelo; $('#nPri').onchange = prev; trocaModelo()
  $('#nSalvar').onclick = async () => {
    const v = id => { const e = $(id); return e ? e.value.trim() : undefined }
    try { const o = await api('POST', '/api/ordens', { titulo: v('#nTit'), modelo_id: v('#nMod'), prioridade: v('#nPri'), unidade: v('#nUni'), local: v('#nLoc'), ativo: v('#nAtv'), contato: v('#nCont'), cliente: v('#nCli'), codigo_projeto: v('#nProj'), descricao: v('#nDesc') }); toast('OS ' + o.numero + ' aberta.'); location.hash = '#/os/' + encodeURIComponent(o.numero) } catch (e) { toast(e.message) }
  }
}

// ───────────── Ficha da OS
const FLUXO = ['aberta', 'planejada', 'em_execucao', 'concluida', 'encerrada']
async function telaOS(m, partes) {
  await cadastros()
  const o = await api('GET', '/api/ordens/' + encodeURIComponent(partes[0])), aba = partes[1] || 'geral'
  const fin = ['concluida', 'encerrada', 'cancelada'].includes(o.status), ex = o.eu.pode_executar
  const idx = FLUXO.indexOf(o.status === 'pausada' ? 'em_execucao' : o.status)
  // segregação de funções: quem concluiu não aprova o próprio encerramento (o MASTER TESTE é exceção, só em ambiente de teste)
  const aprova = o.status === 'concluida' && o.aprovacao === 'pendente' && (!o.eu.concluiu || o.eu.master)
  const acoes = [
    gest() && ['aberta', 'planejada', 'pausada'].includes(o.status) && ['planejar', o.status === 'aberta' ? 'Planejar' : 'Replanejar', 'pri'],
    ex && ['planejada', 'pausada'].includes(o.status) && ['iniciar', o.status === 'pausada' ? 'Retomar execução' : 'Iniciar execução', 'pri'],
    ex && o.status === 'em_execucao' && ['pausar', 'Pausar', ''],
    ex && o.status === 'em_execucao' && ['concluir', 'Concluir', 'ok'],
    sup() && aprova && ['encerrar', 'Aprovar e encerrar', 'ok'],
    sup() && aprova && ['devolver', 'Devolver ao executor', ''],
    gestor() && ['concluida', 'encerrada'].includes(o.status) && ['reabrir', 'Reabrir', ''],
    gest() && !fin && ['cancelar', 'Cancelar OS', 'bad']].filter(Boolean)
  const f = o.feriado
  const abas = [['geral', 'Visão geral'], ['checklist', `Checklist (${o.checklist.feitos}/${o.checklist.total})`], ['materiais', `Materiais (${o.materiais.length})`], ['horas', `Horas (${o.apontamentos.length})`], ['anexos', `Fotos e anexos (${o.anexos.length})`], ['historico', 'Histórico']]
  m.innerHTML = `<p class="small"><a href="#/lista">← Ordens</a></p><div class="row"><h1 style="margin:0">${esc(o.titulo)}</h1><span class="tag pri">${esc(o.numero)}</span>${tagSt(o)}${prio(o.prioridade)}${tagSla(o.sla)}${o.cronometro_ativo ? '<span class="tag ok">⏱ em andamento</span>' : ''}</div>
    <p class="lede">${esc(o.tipo_nome)} · ${esc(o.unidade || '—')} · ${esc(o.local || 'local não informado')}${o.cliente ? ' · cliente ' + esc(o.cliente) : ''}${o.origem === 'crm' ? ' · <span class="tag">via CRM ' + esc(o.ref_externa || '') + '</span>' : ''}${o.codigo_projeto ? ' · projeto ' + esc(o.codigo_projeto) : ''}</p>
    <div class="fluxo">${FLUXO.map((s, i) => `<span class="${o.status === 'cancelada' ? '' : i < idx ? 'ok' : i === idx ? 'on' : ''}">${esc(st.meta.status[s])}${s === 'em_execucao' && o.status === 'pausada' ? ' (pausada)' : ''}</span>`).join('')}${o.status === 'cancelada' ? '<span class="on" style="background:var(--bad)">Cancelada</span>' : ''}</div>
    ${o.motivo && ['pausada', 'cancelada', 'aberta'].includes(o.status) ? `<div class="note warn">${o.aprovacao === 'devolvida' ? 'Encerramento devolvido' : 'Motivo'}: ${esc(o.motivo)}</div>` : ''}
    ${o.status === 'concluida' && o.aprovacao === 'pendente' ? `<div class="note">Concluída pelo executor · aguardando aprovação do supervisor para encerrar.${o.eu.concluiu && !o.eu.master && sup() ? ' Você concluiu esta OS: a aprovação é de outro supervisor ou do gestor.' : ''}${o.eu.concluiu && o.eu.master ? ' <b>Exceção de teste:</b> como MASTER TESTE você pode aprovar o que concluiu.' : ''}</div>` : ''}
    ${f && f.situacao === 'aguardando_gestor' ? `<div class="note warn">Execução em feriado (${esc(f.nome)}, ${fdata(f.data)}). Sem escala: ${esc(f.sem_escala.join(', '))}. Ciência do planejamento: ${esc(f.ciente_por || '')}. Aguardando de acordo do gestor de serviços.${sup() ? ' <button class="btn mini ok" id="fSim">De acordo</button> <button class="btn mini" id="fNao">Não de acordo</button>' : ''}</div>` : ''}
    ${f && f.situacao === 'de_acordo' ? `<div class="note">Execução em feriado (${esc(f.nome)}) com de acordo de ${esc(f.decidido_por || 'gestor')}.</div>` : ''}
    ${f && f.situacao === 'escala' ? `<div class="note">${fdata(f.data)} é feriado (${esc(f.nome)}) · equipe da OS trabalha em escala.</div>` : ''}
    ${o.conflitos.length ? `<div class="note bad">Conflito de agenda da equipe: ${o.conflitos.map(x => `<a href="#/os/${encodeURIComponent(x.numero)}">${esc(x.numero)}</a> (${dh(x.execucao_inicio)})`).join(', ')}.</div>` : ''}
    <div class="row" style="margin-bottom:12px">${acoes.map(([a, l, c]) => `<button class="btn ${c}" data-acao="${a}">${l}</button>`).join('')}${(o.eu.participante || gest()) && !['encerrada', 'cancelada'].includes(o.status) && o.equipe_id ? `<span class="spacer"></span><a class="btn" href="${urlCampo(o.numero)}">📱 Modo execução</a>` : ''}</div>
    <nav class="tabs">${abas.map(([k, l]) => `<a href="#/os/${encodeURIComponent(o.numero)}/${k}"${k === aba ? ' aria-current="page"' : ''}>${esc(l)}</a>`).join('')}</nav><div id="aba"></div>`
  m.querySelectorAll('[data-acao]').forEach(b => b.onclick = () => acao(o, b.dataset.acao))
  const fs = $('#fSim'), fn = $('#fNao')
  if (fs) fs.onclick = async () => { try { await api('POST', `/api/ordens/${encodeURIComponent(o.numero)}/feriado`, { decisao: 'de_acordo' }); toast('De acordo registrado.'); K.render() } catch (e) { toast(e.message) } }
  if (fn) fn.onclick = async () => { const c = await pedir('Não de acordo', 'A OS volta para planejamento. Comentário (opcional):', false); if (c == null) return; try { await api('POST', `/api/ordens/${encodeURIComponent(o.numero)}/feriado`, { decisao: 'nao', comentario: c }); toast('OS devolvida ao planejamento.'); K.render() } catch (e) { toast(e.message) } }
  const box = $('#aba')
  if (aba === 'checklist') abaChecklist(box, o)
  else if (aba === 'materiais') abaMateriais(box, o)
  else if (aba === 'horas') abaHoras(box, o)
  else if (aba === 'anexos') abaAnexos(box, o)
  else if (aba === 'historico') box.innerHTML = tabela([{ t: 'Quando', h: e => dh(e.em) }, { t: 'Quem', k: 'por' }, { t: 'Ação', k: 'acao' }, { t: 'De', h: e => esc(st.meta.status[e.de] || e.de || '') }, { t: 'Para', h: e => esc(st.meta.status[e.para] || e.para || '') }, { t: 'Comentário', wrap: 1, k: 'comentario' }], o.eventos)
  else abaGeral(box, o)
}
function abaGeral(box, o) {
  const c = o.custos, ed = gest() && (!['concluida', 'encerrada', 'cancelada'].includes(o.status) || gestor()), r = o.regras || {}
  const part = new Set(o.participantes.map(p => p.id))
  box.innerHTML = `<div class="cards"><div class="card"><div class="v">${dh(o.prazo_sla)}</div><div class="l">Prazo (SLA)${o.sla.restante_h != null && !['concluida', 'encerrada', 'cancelada'].includes(o.status) ? ' · ' + nf(o.sla.restante_h, 1) + ' h' : ''}</div></div>
    <div class="card"><div class="v">${esc(o.equipe || '—')}</div><div class="l">Equipe${o.executor ? ' · executor ' + esc(o.executor) : ''}</div></div><div class="card"><div class="v">${o.execucao_inicio ? dh(o.execucao_inicio) : '—'}</div><div class="l">Execução · ${o.duracao_h ? nf(o.duracao_h, 1) + ' h' : 'duração —'}</div></div>
    ${c ? `<div class="card"><div class="v">${din(c.previsto)}</div><div class="l">Custo previsto (mat. ${din(c.material_previsto)} · MO ${din(c.mao_obra_prevista)})</div></div>
    <div class="card ${c.real > c.previsto && c.previsto ? 'bad' : 'ok'}"><div class="v">${din(c.real)}</div><div class="l">Custo real (mat. ${din(c.material_real)} · MO ${din(c.mao_obra_real)})</div></div>
    <div class="card"><div class="v">${nf(c.horas_reais, 1)} / ${nf(c.horas_previstas, 1)} h</div><div class="l">Horas apontadas / previstas</div></div>` : ''}</div>
    <div class="grid2"><div class="panel"><h2>Dados</h2><div class="grid2">
      ${[['titulo', 'Título'], ['local', 'Local'], ['ativo', 'Ativo / equipamento'], ['contato', 'Contato'], ['cliente', 'Cliente'], ...(tem('pmp') ? [['codigo_projeto', 'Projeto (PMP)']] : [])].map(([k, l]) => `<label class="f">${l}<input class="in" data-k="${k}" value="${esc(o[k] || '')}" ${ed ? '' : 'disabled'}></label>`).join('')}
      <label class="f">${esc(V('unidade'))}<select class="in" data-k="unidade" ${ed ? '' : 'disabled'}>${opcoes(unidades(), o.unidade, '—')}</select></label>
      <label class="f">Prioridade<select class="in" data-k="prioridade" ${ed ? '' : 'disabled'}>${opcoes(Object.entries(st.meta.prioridades).map(([v, l]) => ({ v, l })), o.prioridade)}</select></label></div>
      <label class="f" style="margin-top:10px">Descrição / escopo<textarea class="in" data-k="descricao" rows="4" ${ed ? '' : 'disabled'}>${esc(o.descricao || '')}</textarea></label>
      ${ed ? '<div class="row" style="margin-top:10px"><button class="btn pri" id="gSalvar">Salvar</button></div>' : ''}
      ${r.orientacoes || o.anexos_modelo.length ? `<h3>Orientações (${esc(o.modelo_nome || 'modelo')})</h3><p>${esc(r.orientacoes || '')}</p>${o.anexos_modelo.map(a => `<a href="#" class="small" data-axm="${esc(a.id)}" data-nome="${esc(a.nome)}">📎 ${esc(a.nome)}</a>`).join(' ')}` : ''}</div>
      <div class="panel"><h2>Execução</h2><p class="small muted">Aberta ${dh(o.aberta_em)} por ${esc(o.solicitante || '—')}${o.planejada_em ? ' · planejada ' + dh(o.planejada_em) : ''}${o.iniciada_em ? ' · iniciada ' + dh(o.iniciada_em) : ''}${o.concluida_em ? ' · concluída ' + dh(o.concluida_em) : ''}${o.encerrada_em ? ' · encerrada ' + dh(o.encerrada_em) : ''}</p>
      ${o.resumo_execucao ? `<h3>Relato da execução</h3><p>${esc(o.resumo_execucao)}</p>` : ''}${o.aceite_nome ? `<p class="small">Aceite do cliente: <b>${esc(o.aceite_nome)}</b> · ${dh(o.aceite_em)}</p>` : ''}
      <p class="small muted">Regras do modelo: ${r.fotos_antes_min ? r.fotos_antes_min + ' foto(s) do antes · ' : ''}${r.fotos_depois_min ? r.fotos_depois_min + ' foto(s) do depois · ' : ''}${r.exige_aprovacao ? 'aprovação do supervisor' : 'sem aprovação'}${r.exige_aceite ? ' · aceite do cliente' : ''}${(r.composicao || []).length ? ' · composição sugerida: ' + esc(r.composicao.map(x => `${x.qtd} ${x.funcao}${x.executor ? ' (executor)' : ''}`).join(' + ')) : ''}</p>
      <h3>Quem vai</h3>${o.membros.length ? tabela([{ t: 'Membro', h: x => esc(x.nome) + (o.participantes.find(p => p.id === x.id && p.executor) ? ' <span class="tag pri">executor</span>' : '') }, { t: 'Função', k: 'funcao' }, { t: 'Nesta OS', h: x => part.has(x.id) ? '✓' : '<span class="muted">—</span>' }, ...(gest() ? [{ t: 'Custo/h', n: 1, h: x => din(x.custo_hora) }] : [])], o.membros) : '<p class="muted small">Sem equipe definida.</p>'}</div></div>`
  const b = $('#gSalvar'); if (b) b.onclick = async () => { const corpo = {}; box.querySelectorAll('[data-k]').forEach(i => corpo[i.dataset.k] = i.value); try { await api('PUT', '/api/ordens/' + encodeURIComponent(o.numero), corpo); toast('OS atualizada.'); K.render() } catch (e) { toast(e.message) } }
  box.querySelectorAll('[data-axm]').forEach(a => a.onclick = ev => { ev.preventDefault(); K.baixarUrl('/api/modelo-anexos/' + a.dataset.axm, a.dataset.nome).catch(x => toast(x.message)) })
}
async function acao(o, a) {
  if (a === 'planejar') return planejar(o)
  if (a === 'concluir') return concluir(o)
  let corpo = {}
  if (a === 'pausar') { const mot = await escolherMotivo(); if (mot == null) return; corpo.motivo = mot }
  if (['cancelar', 'reabrir', 'devolver'].includes(a)) { const mot = await pedir({ cancelar: 'Cancelar a OS', reabrir: 'Reabrir a OS', devolver: 'Devolver ao executor' }[a], a === 'devolver' ? 'O que falta para aprovar? O executor recebe este motivo.' : 'Informe o motivo (fica no histórico e vai para o solicitante/CRM).', true); if (mot == null) return; corpo.motivo = mot }
  try { await api('POST', `/api/ordens/${encodeURIComponent(o.numero)}/acoes/${a}`, corpo); toast('Feito.'); K.render() } catch (e) { toast(e.message) }
}
function escolherMotivo() {
  return new Promise(res => {
    const d = modal(`<h2>Pausar a OS</h2><p class="muted">O cronômetro para e as horas até aqui são apontadas para quem está na OS.</p><div class="row">${st.meta.motivos_pausa.map(x => `<button class="btn" data-mot="${esc(x)}">${esc(x)}</button>`).join('')}</div>
      <label class="f" style="margin-top:10px">Detalhe (opcional)<input class="in" id="mDet"></label><div class="row" style="justify-content:flex-end;margin-top:12px"><button class="btn" id="mC">Cancelar</button></div>`)
    d.querySelectorAll('[data-mot]').forEach(b => b.onclick = () => { const det = d.querySelector('#mDet').value.trim(); d.close(); res(b.dataset.mot + (det ? ': ' + det : '')) })
    d.querySelector('#mC').onclick = () => { d.close(); res(null) }; d.addEventListener('cancel', () => res(null))
  })
}
function concluir(o) {
  const r = o.regras || {}
  const d = modal(`<h2>Concluir ${esc(o.numero)}</h2><label class="f">Relato da execução<textarea class="in" id="cRel" rows="4" placeholder="o que foi feito, pendências, observações"></textarea></label>
    <label class="f" style="margin-top:8px">Aceite do cliente — nome de quem recebeu${r.exige_aceite ? ' (obrigatório)' : ' (opcional)'}<input class="in" id="cAce"></label>
    <label class="row small" style="margin-top:6px"><input type="checkbox" id="cConf"> O cliente confirmou que o serviço foi realizado</label>
    <p class="small muted">${r.exige_aprovacao ? 'Depois de concluída, a OS vai para aprovação do supervisor.' : 'Este tipo de OS encerra ao concluir.'}</p><p id="cErr" style="color:var(--bad);min-height:18px"></p>
    <div class="row" style="justify-content:flex-end"><button class="btn" id="cC">Cancelar</button><button class="btn ok" id="cOk">Concluir</button></div>`)
  d.querySelector('#cC').onclick = () => d.close()
  d.querySelector('#cOk').onclick = async () => {
    const nome = d.querySelector('#cAce').value.trim()
    if (nome && !d.querySelector('#cConf').checked) { d.querySelector('#cErr').textContent = 'Marque a confirmação do cliente.'; return }
    try { await api('POST', `/api/ordens/${encodeURIComponent(o.numero)}/acoes/concluir`, { resumo: d.querySelector('#cRel').value, aceite_nome: nome }); d.close(); toast('OS concluída.'); K.render() } catch (e) { d.querySelector('#cErr').textContent = e.message }
  }
}

// Planejar: equipe, quem vai, UM executor, data e duração. Feriado pede ciência; conflitos avisam.
function planejar(o, pre = {}) {
  return new Promise(resolve => {
    const eqIni = pre.equipe_id || o.equipe_id || '', partAtual = new Set((o.participantes || []).map(p => p.id)), exAtual = ((o.participantes || []).find(p => p.executor) || {}).id
    const comp = ((o.regras || {}).composicao || [])
    const d = modal(`<h2>Planejar ${esc(o.numero)}</h2><p class="muted">${esc(o.titulo)}</p>
      <div class="grid3"><label class="f">Equipe<select class="in" id="pEq">${opcoes(EQUIPES.filter(e => e.ativo).map(e => ({ v: e.id, l: e.nome + (e.unidade ? ' · ' + e.unidade : '') })), eqIni, '—')}</select></label>
      <label class="f">Início da execução<input class="in" id="pIni" type="datetime-local" value="${esc(dtLocal(pre.inicio || o.execucao_inicio))}"></label>
      <label class="f">Duração estimada (h)<input class="in" id="pDur" type="number" min="0.5" step="0.5" value="${esc(pre.duracao_h || o.duracao_h || 2)}"></label></div>
      ${comp.length ? `<p class="small muted" style="margin:8px 0 0">Composição sugerida pelo modelo: ${esc(comp.map(x => `${x.qtd} ${x.funcao}${x.executor ? ' (executor)' : ''}`).join(' + '))}</p>` : ''}
      <h3>Quem vai e quem executa</h3><div id="pMb" class="small"></div>
      <div id="pFer"></div><p class="small muted" id="pCusto"></p><p id="pErr" style="color:var(--bad);min-height:18px"></p>
      <div class="row" style="justify-content:flex-end"><button class="btn" id="pC">Cancelar</button><button class="btn pri" id="pOk">Salvar planejamento</button></div>`)
    let ciencia = false
    const eq = () => EQUIPES.find(x => x.id === d.querySelector('#pEq').value)
    const membros = () => {
      const e = eq(); if (!e) { d.querySelector('#pMb').innerHTML = '<p class="muted">Escolha a equipe.</p>'; return }
      const ativos = e.membros.filter(x => x.ativo), mesma = e.id === o.equipe_id && partAtual.size
      const exDef = (mesma && exAtual) || (ativos.find(x => x.pode_executor) || {}).id
      d.querySelector('#pMb').innerHTML = `<table class="t"><thead><tr><th>Vai</th><th>Executor</th><th>Membro</th><th>Função</th></tr></thead><tbody>${ativos.map(x => `<tr><td><input type="checkbox" data-vai="${esc(x.id)}" ${!mesma || partAtual.has(x.id) ? 'checked' : ''}></td>
        <td><input type="radio" name="pEx" value="${esc(x.id)}" ${x.id === exDef ? 'checked' : ''} ${x.pode_executor ? '' : 'disabled title="Esta função não pode ser executora"'}></td><td>${esc(x.nome)}</td><td>${esc(x.funcao || '—')}${x.pode_executor ? '' : ' <span class="tag">não executa</span>'}</td></tr>`).join('')}</tbody></table>`
      d.querySelectorAll('[data-vai],[name=pEx]').forEach(i => i.onchange = custo)
      custo()
    }
    const sel = () => ({ ids: [...d.querySelectorAll('[data-vai]:checked')].map(i => i.dataset.vai), ex: (d.querySelector('[name=pEx]:checked') || {}).value })
    const custo = () => {
      const e = eq(); if (!e || !gest() || e.custo_hora == null) { d.querySelector('#pCusto').textContent = ''; return }
      const { ids } = sel(), ch = e.membros.filter(x => ids.includes(x.id)).reduce((a, x) => a + (x.custo_hora || 0), 0)
      d.querySelector('#pCusto').textContent = `Mão de obra prevista: ${din(ch * (parseFloat(d.querySelector('#pDur').value) || 0))} (${ids.length} pessoa(s), ${din(ch)}/h).`
    }
    d.querySelector('#pEq').onchange = () => { ciencia = false; d.querySelector('#pFer').innerHTML = ''; membros() }
    d.querySelector('#pIni').onchange = () => { ciencia = false; d.querySelector('#pFer').innerHTML = '' }
    d.querySelector('#pDur').oninput = custo; membros()
    d.querySelector('#pC').onclick = () => { d.close(); resolve(null) }
    d.addEventListener('cancel', () => resolve(null))
    d.querySelector('#pOk').onclick = async () => {
      const ini = d.querySelector('#pIni').value, { ids, ex } = sel()
      if (ids.length && ex && !ids.includes(ex)) { d.querySelector('#pErr').textContent = 'O executor precisa estar entre quem vai.'; return }
      const cb = d.querySelector('#pCiente'); if (cb) ciencia = cb.checked
      try {
        const r = await api('POST', `/api/ordens/${encodeURIComponent(o.numero)}/planejar`, { equipe_id: d.querySelector('#pEq').value, execucao_inicio: ini ? new Date(ini).toISOString() : '', duracao_h: d.querySelector('#pDur').value, participantes: ids, executor_id: ex || null, ciencia_feriado: ciencia })
        d.close()
        toast(r.conflitos.length ? `Planejada — atenção: conflito com ${r.conflitos.map(x => x.numero).join(', ')}.` : r.feriado && r.feriado.situacao === 'aguardando_gestor' ? 'Planejada em feriado: aguardando de acordo do gestor de serviços.' : 'OS planejada.')
        resolve(r); if (!pre.semRender) K.render()
      } catch (e) {
        if (e.data && e.data.codigo === 'FERIADO') {
          d.querySelector('#pFer').innerHTML = `<div class="note warn"><b>${esc(e.data.mensagem)}</b><br>Sem escala de feriado: ${esc(e.data.sem_escala.join(', '))}.<label class="row" style="margin-top:6px"><input type="checkbox" id="pCiente"> Estou ciente. A OS fica aguardando o de acordo do gestor de serviços.</label></div>`
          d.querySelector('#pErr').textContent = ''
        } else d.querySelector('#pErr').textContent = e.message
      }
    }
  })
}

// Checklist: respostas, fotos por item, edição dos itens pelo planejamento
const RESP = () => st.meta.respostas
function campoResposta(i, pode) {
  const v = i.resposta ?? '', dis = pode ? '' : 'disabled'
  if (i.resposta_tipo === 'simnao') return `<select class="in" data-resp="${i.seq}" ${dis}>${opcoes([{ v: 'sim', l: 'Sim' }, { v: 'nao', l: 'Não' }], v, '—')}</select>`
  if (i.resposta_tipo === 'numero') return `<span class="row" style="gap:4px"><input class="in" data-resp="${i.seq}" inputmode="decimal" value="${esc(v)}" style="width:110px" ${dis}><span class="muted">${esc(i.unidade || '')}</span></span>`
  if (i.resposta_tipo === 'texto') return `<input class="in" data-resp="${i.seq}" value="${esc(v)}" style="min-width:220px" ${dis}>`
  return ''
}
function abaChecklist(box, o) {
  const fin = ['concluida', 'encerrada', 'cancelada'].includes(o.status), podeMarcar = o.eu.pode_executar && !fin, podeEditar = gest() && !fin
  const fotos = seq => o.anexos.filter(a => a.checklist_seq === seq).length
  box.innerHTML = `<div class="panel">${o.itens_checklist.map(i => `<div class="ck"><input type="checkbox" data-seq="${i.seq}" ${i.feito ? 'checked' : ''} ${podeMarcar ? '' : 'disabled'}><div style="flex:1"><div>${esc(i.item)}${i.obrigatorio ? '' : ' <span class="tag">opcional</span>'}${i.foto_min ? ` <span class="tag ${fotos(i.seq) >= i.foto_min ? 'ok' : 'warn'}">📷 ${fotos(i.seq)}/${i.foto_min}</span>` : ''}</div>
      <div class="row" style="margin-top:4px">${campoResposta(i, podeMarcar)}<input class="in" data-obs="${i.seq}" value="${esc(i.obs || '')}" placeholder="observação" style="flex:1;min-width:160px" ${podeMarcar ? '' : 'disabled'}>${podeMarcar ? `<label class="btn mini">📷 Foto<input type="file" accept="image/*" capture="environment" data-foto="${i.seq}" hidden></label>` : ''}</div>
      ${i.feito ? `<div class="meta">✓ ${esc(i.por || '')} · ${dh(i.em)}</div>` : ''}</div></div>`).join('') || '<p class="muted">Sem itens.</p>'}
    ${podeMarcar ? '<p class="small muted" style="margin-top:10px">Cada item é salvo assim que você marca, responde ou escreve a observação.</p>' : ''}
    ${podeEditar ? '<details class="conting"><summary>Editar itens do checklist desta OS</summary><p class="small muted">Vale só para esta OS; o modelo não muda.</p><div id="ckEd"></div><div class="row" style="margin-top:8px"><button class="btn" id="ckRedef">Salvar itens</button></div></details>' : ''}</div>`
  // salva item a item (como no modo execução): enviar foto ou trocar de aba não perde o que foi marcado
  let fila = Promise.resolve()
  const salvarItem = (seq, marcou) => {
    const i = o.itens_checklist.find(x => x.seq === seq), r = box.querySelector(`[data-resp="${seq}"]`), cb = box.querySelector(`[data-seq="${seq}"]`)
    const item = { seq, feito: cb.checked, obs: box.querySelector(`[data-obs="${seq}"]`).value, ...(r ? { resposta: r.value } : {}) }
    fila = fila.then(async () => {
      try { await api('PUT', `/api/ordens/${encodeURIComponent(o.numero)}/checklist`, { itens: [item], parcial: true }); Object.assign(i, { feito: item.feito ? 1 : 0, obs: item.obs, ...(r ? { resposta: item.resposta } : {}) }); if (marcou) toast(item.feito ? 'Item marcado.' : 'Item desmarcado.') }
      catch (e) { if (marcou) cb.checked = !!i.feito; toast(e.message) }
    })
    return fila
  }
  box.querySelectorAll('[data-seq]').forEach(cb => cb.onchange = () => salvarItem(Number(cb.dataset.seq), true))
  box.querySelectorAll('[data-resp],[data-obs]').forEach(x => x.onchange = () => salvarItem(Number(x.dataset.resp || x.dataset.obs)))
  box.querySelectorAll('[data-foto]').forEach(inp => inp.onchange = async () => {
    const f = inp.files[0]; if (!f) return
    const ae = document.activeElement; if (ae && box.contains(ae) && (ae.dataset.obs || ae.dataset.resp)) ae.blur()  // texto ainda em edição: salva antes
    try { await fila; await enviarFoto(o.numero, f, 'item', inp.dataset.foto); toast('Foto incluída.'); K.render() } catch (e) { toast(e.message) } })
  if (podeEditar) {
    const ed = editorChecklist($('#ckEd'), o.itens_checklist.map(i => ({ ...i })))
    $('#ckRedef').onclick = async () => { try { await api('PUT', `/api/ordens/${encodeURIComponent(o.numero)}/checklist`, { itens: ed.valor() }); toast('Itens salvos.'); K.render() } catch (e) { toast(e.message) } }
  }
}
// editor de checklist reutilizado em Modelos e na OS: ordem, obrigatório, tipo de resposta, unidade e fotos mínimas
function editorChecklist(box, itens) {
  const desenhar = () => {
    box.innerHTML = `<div class="tw"><table class="t"><thead><tr><th></th><th>Item</th><th>Obrigatório</th><th>Resposta</th><th>Unidade</th><th>Fotos mín.</th><th></th></tr></thead><tbody>${itens.map((i, k) => `<tr>
      <td><button class="btn mini" data-up="${k}" ${k ? '' : 'disabled'} title="Subir">↑</button><button class="btn mini" data-dn="${k}" ${k < itens.length - 1 ? '' : 'disabled'} title="Descer">↓</button></td>
      <td><input class="in" data-c="item" data-k="${k}" value="${esc(i.item || '')}" style="min-width:260px"></td>
      <td style="text-align:center"><input type="checkbox" data-c="obrigatorio" data-k="${k}" ${i.obrigatorio === false || i.obrigatorio === 0 ? '' : 'checked'}></td>
      <td><select class="in" data-c="resposta_tipo" data-k="${k}">${opcoes(Object.entries(RESP()).map(([v, l]) => ({ v, l })), i.resposta_tipo || 'check')}</select></td>
      <td><input class="in" data-c="unidade" data-k="${k}" value="${esc(i.unidade || '')}" style="width:80px" ${i.resposta_tipo === 'numero' ? '' : 'disabled'}></td>
      <td><input class="in" type="number" min="0" max="20" data-c="foto_min" data-k="${k}" value="${esc(i.foto_min || 0)}" style="width:70px"></td>
      <td><button class="btn mini" data-rm="${k}">Remover</button></td></tr>`).join('')}</tbody></table></div><div class="row" style="margin-top:6px"><button class="btn mini" id="ckMais">+ Item</button></div>`
    box.querySelectorAll('[data-c]').forEach(x => x.onchange = () => { const i = itens[x.dataset.k]; i[x.dataset.c] = x.type === 'checkbox' ? x.checked : x.type === 'number' ? Number(x.value) : x.value; if (x.dataset.c === 'resposta_tipo') desenhar() })
    box.querySelectorAll('[data-up]').forEach(b => b.onclick = () => { const k = +b.dataset.up;[itens[k - 1], itens[k]] = [itens[k], itens[k - 1]]; desenhar() })
    box.querySelectorAll('[data-dn]').forEach(b => b.onclick = () => { const k = +b.dataset.dn;[itens[k + 1], itens[k]] = [itens[k], itens[k + 1]]; desenhar() })
    box.querySelectorAll('[data-rm]').forEach(b => b.onclick = () => { itens.splice(+b.dataset.rm, 1); desenhar() })
    box.querySelector('#ckMais').onclick = () => { itens.push({ item: '', obrigatorio: true, resposta_tipo: 'check', foto_min: 0 }); desenhar(); const ins = box.querySelectorAll('[data-c=item]'); ins[ins.length - 1].focus() }
  }
  desenhar()
  return { valor: () => itens.filter(i => String(i.item || '').trim()).map(i => ({ ...(i.seq ? { seq: i.seq } : {}), item: i.item.trim(), obrigatorio: !(i.obrigatorio === false || i.obrigatorio === 0), resposta_tipo: i.resposta_tipo || 'check', unidade: i.unidade || null, foto_min: Number(i.foto_min) || 0 })) }
}
// fotos: reduz para no máximo 1600 px antes de enviar (economiza dados no celular)
async function reduzir(arq) {
  if (!arq || !/^image\//.test(arq.type) || arq.size < 400 * 1024) return arq
  try {
    const img = await createImageBitmap(arq), k = Math.min(1, 1600 / Math.max(img.width, img.height))
    const c = document.createElement('canvas'); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k); c.getContext('2d').drawImage(img, 0, 0, c.width, c.height)
    const b = await new Promise(r => c.toBlob(r, 'image/jpeg', .82)); return b ? new File([b], (arq.name || 'foto').replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' }) : arq
  } catch (e) { return arq }
}
async function enviarFoto(numero, arq, etapa, seq) {
  const fd = new FormData(); fd.append('arquivo', await reduzir(arq)); fd.append('etapa', etapa || 'geral'); if (seq) fd.append('checklist_seq', seq)
  return api('POST', `/api/ordens/${encodeURIComponent(numero)}/anexos`, fd, true)
}
function abaMateriais(box, o) {
  const fin = ['concluida', 'encerrada', 'cancelada'].includes(o.status), mexe = o.eu.pode_executar && !fin, vc = gest()
  const proximos = { a_separar: ['separado'], separado: ['consumido', 'devolvido', 'a_separar'], consumido: ['devolvido'], devolvido: [] }
  box.innerHTML = `${gest() && !fin ? `<div class="panel"><h2>Incluir material</h2><div class="row"><label class="f" style="flex:2">Catálogo<select class="in" id="mCod">${opcoes(MATERIAIS.filter(x => x.ativo).map(x => ({ v: x.codigo, l: `${x.codigo} · ${x.descricao} (${x.unidade_medida}, ${din(x.custo_unitario)})` })), '', 'Item fora do catálogo…')}</select></label>
      <label class="f" style="flex:2">Descrição (fora do catálogo)<input class="in" id="mDesc"></label><label class="f">Qtd. prevista<input class="in" id="mQtd" type="number" min="0" step="any" style="width:100px"></label><label class="f">Custo unit.<input class="in" id="mCus" type="number" min="0" step="any" style="width:110px" placeholder="do catálogo"></label><button class="btn pri" id="mAdd">Incluir</button></div></div>` : ''}
    ${tabela([{ t: 'Material', wrap: 1, h: x => `<b>${esc(x.descricao)}</b><div class="small muted">${esc(x.codigo || 'fora do catálogo')}</div>` }, { t: 'Prevista', n: 1, h: x => nf(x.qtd_prevista, 2) + ' ' + esc(x.unidade_medida || '') },
      { t: 'Consumida', n: 1, h: x => mexe && x.status !== 'devolvido' ? `<input class="cel" data-qr="${esc(x.id)}" value="${esc(x.qtd_real || '')}" placeholder="${esc(x.qtd_prevista)}">` : nf(x.qtd_real, 2) },
      ...(vc ? [{ t: 'Custo unit.', n: 1, h: x => din(x.custo_unitario) }, { t: 'Previsto', n: 1, h: x => din(x.qtd_prevista * x.custo_unitario) }, { t: 'Real', n: 1, h: x => x.status === 'consumido' ? din(x.qtd_real * x.custo_unitario) : '—' }] : []),
      { t: 'Situação', h: x => `<span class="tag ${x.status === 'consumido' ? 'ok' : x.status === 'devolvido' ? '' : 'warn'}">${esc(st.meta.status_material[x.status])}</span>` },
      { t: '', h: x => mexe ? proximos[x.status].map(s => `<button class="btn mini" data-mat="${esc(x.id)}" data-st="${s}">${esc(st.meta.status_material[s])}</button>`).join(' ') + (gest() && x.status === 'a_separar' ? ` <button class="btn mini" data-rm="${esc(x.id)}">Remover</button>` : '') : '' }], o.materiais, { vazio: 'Nenhum material previsto.' })}
    <p class="small muted">Antes de concluir a OS, todo material precisa estar consumido ou devolvido.</p>`
  const add = $('#mAdd'); if (add) add.onclick = async () => { try { await api('POST', `/api/ordens/${encodeURIComponent(o.numero)}/materiais`, { codigo: $('#mCod').value || null, descricao: $('#mDesc').value, qtd_prevista: $('#mQtd').value, custo_unitario: $('#mCus').value }); toast('Material incluído.'); K.render() } catch (e) { toast(e.message) } }
  box.querySelectorAll('[data-mat]').forEach(b => b.onclick = async () => { const q = box.querySelector(`[data-qr="${CSS.escape(b.dataset.mat)}"]`); try { await api('PUT', `/api/ordens/${encodeURIComponent(o.numero)}/materiais/${b.dataset.mat}`, { status: b.dataset.st, ...(q && q.value !== '' ? { qtd_real: q.value } : {}) }); K.render() } catch (e) { toast(e.message) } })
  box.querySelectorAll('[data-rm]').forEach(b => b.onclick = async () => { try { await api('PUT', `/api/ordens/${encodeURIComponent(o.numero)}/materiais/${b.dataset.rm}`, { remover: true }); K.render() } catch (e) { toast(e.message) } })
}
function abaHoras(box, o) {
  const pode = o.eu.pode_executar && ['em_execucao', 'pausada', 'concluida'].includes(o.status), c = o.custos
  box.innerHTML = `<div class="note">O cronômetro aponta as horas sozinho: ao pausar ou concluir, o tempo decorrido vai para cada pessoa da OS. Use o apontamento manual só para ajustes.</div>
    ${pode ? `<div class="panel"><h2>Apontamento manual</h2><div class="row"><label class="f">Membro<select class="in" id="hMb">${opcoes(o.membros.map(x => ({ v: x.id, l: x.nome + ' · ' + (x.funcao || '') })), (o.membros.find(x => x.usuario_id === st.user.id) || {}).id)}</select></label>
      <label class="f">Data<input class="in" id="hDt" type="date" value="${new Date().toISOString().slice(0, 10)}"></label><label class="f">Horas<input class="in" id="hH" type="number" min="0.25" max="24" step="0.25" style="width:90px"></label>
      <label class="f" style="flex:1">Observação<input class="in" id="hObs"></label><button class="btn pri" id="hAdd">Apontar</button></div></div>` : ''}
    ${tabela([{ t: 'Data', h: x => data(x.data) }, { t: 'Membro', k: 'nome' }, { t: 'Horas', n: 1, h: x => nf(x.horas, 2) }, ...(c ? [{ t: 'Custo/h', n: 1, h: x => din(x.custo_hora) }, { t: 'Custo', n: 1, h: x => din(x.horas * x.custo_hora) }] : []), { t: 'Obs.', wrap: 1, k: 'obs' }, { t: 'Lançado por', k: 'por' },
      { t: '', h: x => gest() ? `<button class="btn mini" data-rmh="${esc(x.id)}">Remover</button>` : '' }], o.apontamentos, { vazio: 'Nenhum apontamento.', total: c ? { Horas: nf(c.horas_reais, 2), Custo: din(c.mao_obra_real) } : null })}`
  const b = $('#hAdd'); if (b) b.onclick = async () => { try { await api('POST', `/api/ordens/${encodeURIComponent(o.numero)}/apontamentos`, { membro_id: $('#hMb').value, data: $('#hDt').value, horas: $('#hH').value, obs: $('#hObs').value }); toast('Horas apontadas.'); K.render() } catch (e) { toast(e.message) } }
  box.querySelectorAll('[data-rmh]').forEach(x => x.onclick = async () => { try { await api('DELETE', `/api/ordens/${encodeURIComponent(o.numero)}/apontamentos/${x.dataset.rmh}`); K.render() } catch (e) { toast(e.message) } })
}
const ETAPAS = { antes: 'Antes', depois: 'Depois', item: 'Item do checklist', geral: 'Geral' }
function abaAnexos(box, o) {
  const r = o.regras || {}, n = e => o.anexos.filter(a => a.etapa === e).length
  box.innerHTML = `<div class="panel"><div class="row"><label class="f">Etapa<select class="in" id="axE">${opcoes(Object.entries(ETAPAS).filter(([k]) => k !== 'item' && (k === 'geral' || o.eu.pode_executar)).map(([v, l]) => ({ v, l })), 'geral')}</select></label><label class="btn pri" style="align-self:flex-end">Anexar foto ou arquivo<input type="file" id="axF" hidden></label><span class="small muted" style="align-self:flex-end">até 20 MB</span></div>
    <p class="small muted">${r.fotos_antes_min ? `Fotos do antes: ${n('antes')}/${r.fotos_antes_min} · ` : ''}${r.fotos_depois_min ? `Fotos do depois: ${n('depois')}/${r.fotos_depois_min}` : ''}</p></div>
    ${tabela([{ t: 'Arquivo', h: x => `<a href="#" data-ax="${esc(x.id)}" data-nome="${esc(x.nome)}">${esc(x.nome)}</a>` }, { t: 'Etapa', h: x => esc(ETAPAS[x.etapa] || x.etapa) + (x.checklist_seq ? ' · item ' + x.checklist_seq : '') }, { t: 'Por', k: 'por' }, { t: 'Quando', h: x => dh(x.em) }], o.anexos, { vazio: 'Nenhum anexo.' })}`
  $('#axF').onchange = async e => { try { await enviarFoto(o.numero, e.target.files[0], $('#axE').value); toast('Anexo incluído.'); K.render() } catch (x) { toast(x.message) } }
  box.querySelectorAll('[data-ax]').forEach(a => a.onclick = ev => { ev.preventDefault(); K.baixarUrl('/api/anexos/' + a.dataset.ax, a.dataset.nome).catch(x => toast(x.message)) })
}

// ───────────── Modelos de OS
async function telaModelos(m, partes) {
  if (!modPlan()) { m.innerHTML = '<h1>Modelos de OS</h1><div class="note">Área do planejador e do gestor.</div>'; return }
  if (partes[0]) return editorModelo(m, partes[0])
  const [mods, origens] = await Promise.all([api('GET', '/api/modelos?todos=true'), api('GET', '/api/origens-modelo')])
  const pri = Object.keys(st.meta.prioridades)
  m.innerHTML = `<div class="row"><h1 style="margin:0">Modelos de OS</h1><span class="spacer"></span><button class="btn" id="moOri">Padrão por origem</button>${gestor() ? '<button class="btn" id="moImp">Importar</button>' : ''}<button class="btn" id="moExp">Exportar</button><a class="btn pri" href="#/modelos/novo">+ Novo modelo</a></div>
    <p class="lede">Cada OS nasce de um modelo: checklist, fotos obrigatórias, SLA, composição da equipe, materiais e orientações. Editar um modelo não muda as OS já abertas (cada OS guarda a sua cópia).</p>
    ${tabela([{ t: 'Modelo', wrap: 1, h: x => `<a href="#/modelos/${encodeURIComponent(x.id)}"><b>${esc(x.nome)}</b></a>${x.ativo ? '' : ' <span class="tag">inativo</span>'}<div class="small muted">${esc(x.tipo)}${x.descricao ? ' · ' + esc(x.descricao.slice(0, 90)) : ''}</div>` },
      { t: 'SLA (h) ' + pri.map(p => st.meta.prioridades[p][0]).join('/'), h: x => pri.map(p => (x.sla_horas || {})[p] ?? '—').join(' / ') },
      { t: 'Checklist', h: x => `${x.checklist.length} itens${x.checklist.some(i => i.foto_min) ? ' · 📷' : ''}` }, { t: 'Fotos antes/depois', h: x => `${x.fotos_antes_min}/${x.fotos_depois_min}` },
      { t: 'Encerramento', h: x => (x.exige_aprovacao ? 'aprovação' : 'direto') + (x.exige_aceite ? ' · aceite' : '') }, { t: 'Origem padrão', h: x => esc(x.origens.join(', ') || '—') },
      { t: '', h: x => `<a class="btn mini" href="#/modelos/${encodeURIComponent(x.id)}">Editar</a> <button class="btn mini" data-dup="${esc(x.id)}">Duplicar</button> <button class="btn mini" data-at="${esc(x.id)}">${x.ativo ? 'Desativar' : 'Ativar'}</button>` }], mods, { vazio: 'Nenhum modelo.' })}`
  m.querySelectorAll('[data-dup]').forEach(b => b.onclick = async () => { try { const d = await api('POST', `/api/modelos/${encodeURIComponent(b.dataset.dup)}/duplicar`); toast('Cópia criada.'); location.hash = '#/modelos/' + encodeURIComponent(d.id) } catch (e) { toast(e.message) } })
  m.querySelectorAll('[data-at]').forEach(b => b.onclick = async () => { const x = mods.find(y => y.id === b.dataset.at); try { await api('PUT', '/api/modelos/' + encodeURIComponent(x.id), { ...x, ativo: !x.ativo }); K.render() } catch (e) { toast(e.message) } })
  $('#moExp').onclick = () => K.baixarUrl('/api/modelos-exportar', 'modelos-os.json').catch(e => toast(e.message))
  const imp = $('#moImp'); if (imp) imp.onclick = () => {
    const d = modal(`<h2>Importar modelos</h2><p class="muted">Arquivo exportado de outra instalação (ou o arquivo de configuração da implantação).</p><input type="file" id="iArq" accept=".json,application/json">
      <label class="row" style="margin-top:10px"><input type="radio" name="iModo" value="adicionar" checked> Só adicionar os que não existem</label><label class="row"><input type="radio" name="iModo" value="atualizar"> Adicionar e atualizar os existentes (a versão anterior fica no histórico)</label>
      <p id="iErr" style="color:var(--bad);min-height:18px"></p><div class="row" style="justify-content:flex-end"><button class="btn" id="iC">Cancelar</button><button class="btn pri" id="iOk">Importar</button></div>`)
    d.querySelector('#iC').onclick = () => d.close()
    d.querySelector('#iOk').onclick = async () => {
      const f = d.querySelector('#iArq').files[0], modo = d.querySelector('[name=iModo]:checked').value
      if (!f) { d.querySelector('#iErr').textContent = 'Escolha o arquivo.'; return }
      if (modo === 'atualizar' && !confirm('Os modelos com o mesmo código serão substituídos pelos do arquivo. Continuar?')) return
      const fd = new FormData(); fd.append('arquivo', f); fd.append('modo', modo)
      try { const r = await api('POST', '/api/modelos-importar', fd, true); d.close(); toast(`${r.criados} criado(s), ${r.atualizados} atualizado(s), ${r.ignorados} já existente(s).`); K.render() } catch (e) { d.querySelector('#iErr').textContent = e.message }
    }
  }
  $('#moOri').onclick = () => {
    const linhas = origens.length ? origens.map(o => ({ ...o })) : [{ origem: 'crm', modelo_id: '' }]
    const ativos = mods.filter(x => x.ativo)
    const d = modal(`<h2>Modelo padrão por origem</h2><p class="muted">Quando outra ferramenta cria a OS, este é o modelo usado. Use <code>crm</code> para todas as solicitações do CRM ou <code>crm:corretiva</code> para um tipo específico; <code>orcamentos:visita</code> para visitas técnicas pedidas pelo módulo de orçamentos.</p><div id="oL"></div>
      <div class="row" style="margin-top:6px"><button class="btn mini" id="oMais">+ Origem</button></div><p id="oErr" style="color:var(--bad);min-height:18px"></p><div class="row" style="justify-content:flex-end"><button class="btn" id="oC">Cancelar</button><button class="btn pri" id="oOk">Salvar</button></div>`)
    const des = () => { d.querySelector('#oL').innerHTML = linhas.map((l, k) => `<div class="row" style="margin-bottom:6px"><input class="in" data-o="${k}" value="${esc(l.origem)}" placeholder="origem" style="width:200px"><select class="in" data-m="${k}">${opcoes(ativos.map(x => ({ v: x.id, l: x.nome })), l.modelo_id, '—')}</select><button class="btn mini" data-x="${k}">Remover</button></div>`).join('')
      d.querySelectorAll('[data-o]').forEach(i => i.oninput = () => linhas[i.dataset.o].origem = i.value); d.querySelectorAll('[data-m]').forEach(i => i.onchange = () => linhas[i.dataset.m].modelo_id = i.value)
      d.querySelectorAll('[data-x]').forEach(b => b.onclick = () => { linhas.splice(+b.dataset.x, 1); des() }) }
    des(); d.querySelector('#oMais').onclick = () => { linhas.push({ origem: '', modelo_id: '' }); des() }; d.querySelector('#oC').onclick = () => d.close()
    d.querySelector('#oOk').onclick = async () => { try { await api('PUT', '/api/origens-modelo', Object.fromEntries(linhas.filter(l => l.origem.trim() && l.modelo_id).map(l => [l.origem.trim(), l.modelo_id]))); d.close(); toast('Padrões salvos.'); K.render() } catch (e) { d.querySelector('#oErr').textContent = e.message } }
  }
}
async function editorModelo(m, id) {
  await cadastros()
  const novo = id === 'novo'
  const md = novo ? { nome: '', tipo: '', descricao: '', prioridade: 'media', sla_horas: {}, duracao_h: null, composicao: [], checklist: [], fotos_antes_min: 0, fotos_depois_min: 1, materiais: [], exige_aprovacao: true, exige_aceite: false, orientacoes: '', ativo: true, anexos: [], origens: [] } : await api('GET', '/api/modelos/' + encodeURIComponent(id))
  const comp = md.composicao.map(x => ({ ...x })), mats = md.materiais.map(x => ({ ...x }))
  const funcoes = FUNCOES.filter(f => f.ativo).map(f => f.nome)
  m.innerHTML = `<p class="small"><a href="#/modelos">← Modelos de OS</a></p><div class="row"><h1 style="margin:0">${novo ? 'Novo modelo de OS' : esc(md.nome)}</h1>${!novo && !md.ativo ? '<span class="tag">inativo</span>' : ''}<span class="spacer"></span><button class="btn" id="mdPrev">Pré-visualizar</button><button class="btn pri" id="mdSalvar">Salvar modelo</button></div>
    <p class="lede">${novo ? 'Defina o que toda OS deste tipo precisa ter.' : `Atualizado por ${esc(md.atualizado_por || '—')} em ${dh(md.atualizado_em)}. OS já abertas não mudam.`}</p>
    <div class="grid2"><div class="panel"><h2>Identificação</h2><div class="grid2">
      <label class="f">Nome<input class="in" id="mdN" value="${esc(md.nome)}"></label><label class="f">Tipo (código para relatórios/integrações)<input class="in" id="mdT" value="${esc(md.tipo || '')}" placeholder="ex.: corretiva"></label>
      <label class="f">Prioridade padrão<select class="in" id="mdP">${opcoes(Object.entries(st.meta.prioridades).map(([v, l]) => ({ v, l })), md.prioridade)}</select></label><label class="f">Duração típica (h)<input class="in" id="mdD" type="number" min="0" step="0.5" value="${esc(md.duracao_h ?? '')}"></label></div>
      <label class="f" style="margin-top:8px">Descrição / escopo<textarea class="in" id="mdDesc" rows="3">${esc(md.descricao || '')}</textarea></label>
      <h3>SLA por prioridade (horas a partir da abertura)</h3><div class="row">${Object.entries(st.meta.prioridades).map(([p, l]) => `<label class="f">${esc(l)}<input class="in" type="number" min="0" step="0.5" data-sla="${p}" value="${esc((md.sla_horas || {})[p] ?? '')}" style="width:90px"></label>`).join('')}</div></div>
    <div class="panel"><h2>Equipe e encerramento</h2><h3 style="margin-top:0">Composição sugerida</h3><div id="mdComp"></div>
      <h3>Encerramento</h3><label class="row small"><input type="checkbox" id="mdApr" ${md.exige_aprovacao ? 'checked' : ''}> Exige aprovação do supervisor para encerrar</label><label class="row small"><input type="checkbox" id="mdAce" ${md.exige_aceite ? 'checked' : ''}> Exige aceite do cliente (nome de quem recebeu)</label>
      <h3>Fotos obrigatórias</h3><div class="row"><label class="f">Do antes (mín.)<input class="in" type="number" min="0" id="mdFa" value="${esc(md.fotos_antes_min)}" style="width:90px"></label><label class="f">Do depois (mín.)<input class="in" type="number" min="0" id="mdFd" value="${esc(md.fotos_depois_min)}" style="width:90px"></label><span class="small muted">Fotos por item ficam no checklist.</span></div>
      <h3>Origem padrão</h3><label class="f">Origens que usam este modelo (separadas por vírgula)<input class="in" id="mdOri" value="${esc(md.origens.join(', '))}" placeholder="crm, orcamentos:visita"></label></div></div>
    <div class="panel"><h2>Checklist</h2><div id="mdCk"></div></div>
    <div class="grid2"><div class="panel"><h2>Materiais padrão</h2><div id="mdMat"></div></div>
    <div class="panel"><h2>Orientações (segurança, EPI)</h2><textarea class="in" id="mdOr" rows="4" style="width:100%">${esc(md.orientacoes || '')}</textarea>
      <h3>Anexos fixos</h3>${novo ? '<p class="small muted">Salve o modelo para anexar procedimentos e manuais.</p>' : `<div>${md.anexos.map(a => `<div class="row small"><a href="#" data-axm="${esc(a.id)}" data-nome="${esc(a.nome)}">📎 ${esc(a.nome)}</a><button class="btn mini" data-rmax="${esc(a.id)}">Remover</button></div>`).join('') || '<p class="small muted">Nenhum.</p>'}</div><label class="btn mini" style="margin-top:6px">+ Anexar<input type="file" id="mdAx" hidden></label>`}</div></div>`
  const ck = editorChecklist($('#mdCk'), md.checklist.map(i => ({ ...i })))
  const desComp = () => {
    $('#mdComp').innerHTML = comp.map((x, k) => `<div class="row" style="margin-bottom:5px"><input class="in" type="number" min="1" data-cq="${k}" value="${esc(x.qtd)}" style="width:64px"><input class="in" list="dlFun" data-cf="${k}" value="${esc(x.funcao)}" placeholder="função" style="width:170px"><label class="row small"><input type="checkbox" data-ce="${k}" ${x.executor ? 'checked' : ''}> executor</label><button class="btn mini" data-cx="${k}">Remover</button></div>`).join('') + `<datalist id="dlFun">${funcoes.map(f => `<option value="${esc(f)}">`).join('')}</datalist><button class="btn mini" id="cMais">+ Função</button>`
    $('#mdComp').querySelectorAll('[data-cq]').forEach(i => i.oninput = () => comp[i.dataset.cq].qtd = Number(i.value) || 1)
    $('#mdComp').querySelectorAll('[data-cf]').forEach(i => i.oninput = () => comp[i.dataset.cf].funcao = i.value)
    $('#mdComp').querySelectorAll('[data-ce]').forEach(i => i.onchange = () => { if (i.checked && !podeExecutor(comp[i.dataset.ce].funcao)) { toast('Esta função não pode ser executora.'); i.checked = false; return } comp.forEach((c, k) => c.executor = i.checked && k === +i.dataset.ce); desComp() })
    $('#mdComp').querySelectorAll('[data-cx]').forEach(b => b.onclick = () => { comp.splice(+b.dataset.cx, 1); desComp() })
    $('#cMais').onclick = () => { comp.push({ funcao: '', qtd: 1, executor: !comp.some(c => c.executor) }); desComp() }
  }
  const desMat = () => {
    $('#mdMat').innerHTML = mats.map((x, k) => `<div class="row" style="margin-bottom:5px"><select class="in" data-mc="${k}">${opcoes(MATERIAIS.filter(y => y.ativo || y.codigo === x.codigo).map(y => ({ v: y.codigo, l: y.codigo + ' · ' + y.descricao + ' (' + y.unidade_medida + ')' })), x.codigo, '—')}</select><input class="in" type="number" min="0" step="any" data-mq="${k}" value="${esc(x.qtd)}" style="width:90px"><button class="btn mini" data-mx="${k}">Remover</button></div>`).join('') + (MATERIAIS.length ? '<button class="btn mini" id="mtMais">+ Material</button>' : '<p class="small muted">Cadastre itens em Materiais para usar aqui.</p>')
    $('#mdMat').querySelectorAll('[data-mc]').forEach(i => i.onchange = () => mats[i.dataset.mc].codigo = i.value)
    $('#mdMat').querySelectorAll('[data-mq]').forEach(i => i.oninput = () => mats[i.dataset.mq].qtd = Number(i.value) || 0)
    $('#mdMat').querySelectorAll('[data-mx]').forEach(b => b.onclick = () => { mats.splice(+b.dataset.mx, 1); desMat() })
    const b = $('#mtMais'); if (b) b.onclick = () => { mats.push({ codigo: '', qtd: 1 }); desMat() }
  }
  desComp(); desMat()
  const valor = () => ({ nome: $('#mdN').value, tipo: $('#mdT').value, prioridade: $('#mdP').value, duracao_h: $('#mdD').value, descricao: $('#mdDesc').value,
    sla_horas: Object.fromEntries([...m.querySelectorAll('[data-sla]')].filter(i => i.value !== '').map(i => [i.dataset.sla, Number(i.value)])),
    composicao: comp.filter(c => c.funcao.trim()), checklist: ck.valor().map(({ seq, ...r }) => r), materiais: mats.filter(x => x.codigo && x.qtd > 0),
    fotos_antes_min: Number($('#mdFa').value) || 0, fotos_depois_min: Number($('#mdFd').value) || 0, exige_aprovacao: $('#mdApr').checked, exige_aceite: $('#mdAce').checked,
    orientacoes: $('#mdOr').value, ativo: md.ativo, origens: $('#mdOri').value.split(',').map(s => s.trim()).filter(Boolean) })
  $('#mdSalvar').onclick = async () => { try { const r = await api('PUT', '/api/modelos/' + encodeURIComponent(novo ? 'novo' : id), valor()); toast('Modelo salvo.'); if (novo) location.hash = '#/modelos/' + encodeURIComponent(r.id); else K.render() } catch (e) { toast(e.message) } }
  $('#mdPrev').onclick = () => previaModelo(valor())
  const ax = $('#mdAx'); if (ax) ax.onchange = async e => { const fd = new FormData(); fd.append('arquivo', e.target.files[0]); try { await api('POST', `/api/modelos/${encodeURIComponent(id)}/anexos`, fd, true); toast('Anexo incluído.'); K.render() } catch (x) { toast(x.message) } }
  m.querySelectorAll('[data-rmax]').forEach(b => b.onclick = async () => { try { await api('DELETE', `/api/modelos/${encodeURIComponent(id)}/anexos/${b.dataset.rmax}`); K.render() } catch (e) { toast(e.message) } })
  m.querySelectorAll('[data-axm]').forEach(a => a.onclick = ev => { ev.preventDefault(); K.baixarUrl('/api/modelo-anexos/' + a.dataset.axm, a.dataset.nome).catch(x => toast(x.message)) })
}
// pré-visualização: como o executor verá a OS no celular
function previaModelo(v) {
  const resp = i => i.resposta_tipo === 'numero' ? `<input class="in" disabled placeholder="0,0" style="width:90px"> ${esc(i.unidade || '')}` : i.resposta_tipo === 'simnao' ? '<select class="in" disabled><option>Sim / Não</option></select>' : i.resposta_tipo === 'texto' ? '<input class="in" disabled placeholder="resposta">' : ''
  const d = modal(`<h2>Pré-visualização · modo execução</h2><div class="previa"><div class="pv-cab"><b>${esc(v.nome || 'Modelo')}</b><div class="small">${v.duracao_h ? nf(v.duracao_h, 1) + ' h · ' : ''}${esc(st.meta.prioridades[v.prioridade] || '')}</div></div>
    ${v.orientacoes ? `<div class="note warn small">⚠ ${esc(v.orientacoes)}</div>` : ''}${v.fotos_antes_min ? `<p class="small">📷 ${v.fotos_antes_min} foto(s) do antes para iniciar</p>` : ''}
    ${v.checklist.map(i => `<div class="ck"><input type="checkbox" disabled><div style="flex:1">${esc(i.item)}${i.obrigatorio ? '' : ' <span class="tag">opcional</span>'}${i.foto_min ? ` <span class="tag warn">📷 ${i.foto_min}</span>` : ''}<div>${resp(i)}</div></div></div>`).join('') || '<p class="muted">Sem checklist.</p>'}
    ${v.materiais.length ? `<p class="small">${v.materiais.length} material(is) previsto(s)</p>` : ''}<p class="small">Concluir: ${v.fotos_depois_min ? v.fotos_depois_min + ' foto(s) do depois · ' : ''}${v.exige_aceite ? 'aceite do cliente · ' : ''}${v.exige_aprovacao ? 'vai para aprovação do supervisor' : 'encerra direto'}</p></div>
    <div class="row" style="justify-content:flex-end"><button class="btn" id="pvC">Fechar</button></div>`)
  d.querySelector('#pvC').onclick = () => d.close()
}

// ───────────── Cadastros: equipes, funções, materiais
async function telaEquipes(m) {
  await cadastros()
  m.innerHTML = `<div class="row"><h1 style="margin:0">Equipes</h1><span class="spacer"></span>${gest() ? '<button class="btn pri" id="eNova">+ Equipe</button>' : ''}</div><p class="lede">Equipes e membros${gest() ? ' (custo por hora usado no custo previsto e realizado)' : ''}. Vincule a pessoa do hub ao membro: ela recebe as OS na agenda e no modo execução.</p>
    ${tabela([{ t: 'Equipe', h: e => `<b>${esc(e.nome)}</b>${e.ativo ? '' : ' <span class="tag">inativa</span>'}<div class="small muted">${esc(e.especialidade || '')}</div>` }, { t: V('unidade'), k: 'unidade' }, { t: 'Membros', wrap: 1, h: e => e.membros.filter(x => x.ativo).map(x => esc(x.nome) + `<span class="muted"> (${esc(x.funcao || '—')})</span>`).join(', ') || '—' }, ...(gest() ? [{ t: 'Custo/h da equipe', n: 1, h: e => din(e.custo_hora) }] : []), { t: '', h: e => gest() ? `<button class="btn mini" data-eq="${esc(e.id)}">Editar</button>` : '' }], EQUIPES, { vazio: 'Nenhuma equipe cadastrada.' })}
    ${modPlan() ? `<div class="panel" style="margin-top:16px"><h2>Funções</h2><p class="small muted">Define quem pode ser o executor (responsável) de uma OS. Uma OS não pode ter só funções que não executam (ex.: auxiliares).</p><div id="fnL"></div><div class="row" style="margin-top:6px"><button class="btn mini" id="fnMais">+ Função</button><span class="spacer"></span><button class="btn pri" id="fnOk">Salvar funções</button></div></div>` : ''}`
  const n = $('#eNova'); if (n) n.onclick = () => editorEquipe(null)
  m.querySelectorAll('[data-eq]').forEach(b => b.onclick = () => editorEquipe(EQUIPES.find(e => e.id === b.dataset.eq)))
  if (modPlan()) {
    const fs = FUNCOES.map(f => ({ ...f }))
    const des = () => { $('#fnL').innerHTML = `<table class="t"><thead><tr><th>Função</th><th>Pode ser executor</th><th>Ativa</th></tr></thead><tbody>${fs.map((f, k) => `<tr><td><input class="in" data-fn="${k}" value="${esc(f.nome)}" ${f.novo ? '' : 'disabled'}></td><td><input type="checkbox" data-fe="${k}" ${f.pode_executor ? 'checked' : ''}></td><td><input type="checkbox" data-fa="${k}" ${f.ativo ? 'checked' : ''}></td></tr>`).join('')}</tbody></table>`
      $('#fnL').querySelectorAll('[data-fn]').forEach(i => i.oninput = () => fs[i.dataset.fn].nome = i.value); $('#fnL').querySelectorAll('[data-fe]').forEach(i => i.onchange = () => fs[i.dataset.fe].pode_executor = i.checked); $('#fnL').querySelectorAll('[data-fa]').forEach(i => i.onchange = () => fs[i.dataset.fa].ativo = i.checked) }
    des(); $('#fnMais').onclick = () => { fs.push({ nome: '', pode_executor: true, ativo: true, novo: true }); des() }
    $('#fnOk').onclick = async () => { try { await api('PUT', '/api/funcoes', { funcoes: fs.filter(f => f.nome.trim()).map(f => ({ nome: f.nome.trim(), pode_executor: !!f.pode_executor, ativo: !!f.ativo })) }); toast('Funções salvas.'); K.render() } catch (e) { toast(e.message) } }
  }
}
async function editorEquipe(e) {
  e = e || { id: 'nova', nome: '', unidade: '', especialidade: '', ativo: 1, membros: [] }
  const pessoas = await api('GET', '/api/pessoas-hub').catch(() => st.usuarios.filter(u => u.perfil === 'tecnico').map(u => ({ id: u.id, nome: u.nome })))
  const funcoes = FUNCOES.filter(f => f.ativo).map(f => f.nome), vc = gest()
  const linha = x => `<tr><td><input class="in" data-mn value="${esc(x.nome || '')}"><input type="hidden" data-mid value="${esc(x.id || '')}"></td><td><input class="in" list="dlF" data-mf value="${esc(x.funcao || '')}" style="width:140px"></td>
    ${vc ? `<td><input class="in" data-mc type="number" min="0" step="any" value="${esc(x.custo_hora || 0)}" style="width:90px"></td>` : ''}<td><select class="in" data-mu>${opcoes(pessoas.map(u => ({ v: u.id, l: u.nome })), x.usuario_id, '—')}</select></td>
    <td style="text-align:center"><input type="checkbox" data-mesc ${x.escala_feriado ? 'checked' : ''} title="Usado quando o membro não está vinculado a uma pessoa do hub (no hub vale o cadastro do usuário)"></td><td style="text-align:center"><input type="checkbox" data-ma ${x.ativo === 0 ? '' : 'checked'}></td></tr>`
  const d = modal(`<h2>${e.id === 'nova' ? 'Nova equipe' : esc(e.nome)}</h2><div class="grid3"><label class="f">Nome<input class="in" id="qN" value="${esc(e.nome)}"></label><label class="f">${esc(V('unidade'))}<select class="in" id="qU">${opcoes(unidades(), e.unidade, '—')}</select></label><label class="f">Especialidade<input class="in" id="qE" value="${esc(e.especialidade || '')}"></label></div>
    <label class="row small" style="margin:8px 0"><input type="checkbox" id="qA" ${e.ativo ? 'checked' : ''}> Equipe ativa</label>
    <h3>Membros</h3><datalist id="dlF">${funcoes.map(f => `<option value="${esc(f)}">`).join('')}</datalist><div class="tw"><table class="t"><thead><tr><th>Nome</th><th>Função</th>${vc ? '<th>Custo/h</th>' : ''}<th>Pessoa (login)</th><th>Escala feriado</th><th>Ativo</th></tr></thead><tbody id="qM">${e.membros.map(linha).join('')}</tbody></table></div>
    <div class="row" style="margin-top:8px"><button class="btn mini" id="qMais">+ Membro</button><span class="small muted">Escala de feriado: no hub vale o cadastro da pessoa; aqui só para membros sem login.</span></div><p id="qErr" style="color:var(--bad);min-height:18px"></p>
    <div class="row" style="justify-content:flex-end"><button class="btn" id="qC">Cancelar</button><button class="btn pri" id="qOk">Salvar</button></div>`)
  d.style.maxWidth = 'min(940px,96vw)'
  d.querySelector('#qMais').onclick = () => d.querySelector('#qM').insertAdjacentHTML('beforeend', linha({}))
  d.querySelector('#qC').onclick = () => d.close()
  d.querySelector('#qOk').onclick = async () => {
    const membros = [...d.querySelectorAll('#qM tr')].map(tr => ({ id: tr.querySelector('[data-mid]').value || null, nome: tr.querySelector('[data-mn]').value, funcao: tr.querySelector('[data-mf]').value, ...(vc ? { custo_hora: tr.querySelector('[data-mc]').value } : {}), usuario_id: tr.querySelector('[data-mu]').value || null, escala_feriado: tr.querySelector('[data-mesc]').checked, ativo: tr.querySelector('[data-ma]').checked }))
    try { await api('PUT', '/api/equipes/' + encodeURIComponent(e.id), { nome: d.querySelector('#qN').value, unidade: d.querySelector('#qU').value, especialidade: d.querySelector('#qE').value, ativo: d.querySelector('#qA').checked, membros }); d.close(); toast('Equipe salva.'); K.render() } catch (x) { d.querySelector('#qErr').textContent = x.message }
  }
}
async function telaMateriais(m) {
  await cadastros()
  const vc = gest()
  m.innerHTML = `<h1>Catálogo de materiais</h1><p class="lede">Itens usados nas listas de materiais das OS e nos modelos (sem controle de estoque nesta versão).</p>
    ${vc ? `<div class="panel"><div class="row"><label class="f">Código<input class="in" id="kC" style="width:130px"></label><label class="f" style="flex:1">Descrição<input class="in" id="kD"></label><label class="f">Unidade<input class="in" id="kU" value="un" style="width:80px"></label><label class="f">Custo unitário<input class="in" id="kV" type="number" min="0" step="any" style="width:120px"></label><button class="btn pri" id="kOk">Salvar item</button></div><p class="small muted">Para editar, informe o mesmo código.</p></div>` : ''}
    ${tabela([{ t: 'Código', k: 'codigo' }, { t: 'Descrição', wrap: 1, h: x => esc(x.descricao) + (x.ativo ? '' : ' <span class="tag">inativo</span>') }, { t: 'Unidade', k: 'unidade_medida' }, ...(vc ? [{ t: 'Custo unitário', n: 1, h: x => din(x.custo_unitario) }] : []), { t: '', h: x => vc ? `<button class="btn mini" data-ed="${esc(x.codigo)}">Editar</button> <button class="btn mini" data-at="${esc(x.codigo)}">${x.ativo ? 'Inativar' : 'Ativar'}</button>` : '' }], MATERIAIS, { vazio: 'Catálogo vazio.' })}`
  const ok = $('#kOk'); if (ok) ok.onclick = async () => { try { await api('PUT', '/api/materiais/' + encodeURIComponent($('#kC').value.trim()), { descricao: $('#kD').value, unidade_medida: $('#kU').value, custo_unitario: $('#kV').value }); toast('Item salvo.'); K.render() } catch (e) { toast(e.message) } }
  m.querySelectorAll('[data-ed]').forEach(b => b.onclick = () => { const x = MATERIAIS.find(y => y.codigo === b.dataset.ed); $('#kC').value = x.codigo; $('#kD').value = x.descricao; $('#kU').value = x.unidade_medida; $('#kV').value = x.custo_unitario; $('#kD').focus() })
  m.querySelectorAll('[data-at]').forEach(b => b.onclick = async () => { const x = MATERIAIS.find(y => y.codigo === b.dataset.at); try { await api('PUT', '/api/materiais/' + encodeURIComponent(x.codigo), { ...x, ativo: !x.ativo }); K.render() } catch (e) { toast(e.message) } })
}

// ───────────── Integrações
async function telaIntegracoes(m) {
  if (!gest()) { m.innerHTML = '<h1>Integrações</h1><div class="note">Área da operação.</div>'; return }
  const r = await api('GET', '/api/integracoes')
  const crm = tem('conector_crm')
  m.innerHTML = `<h1>Integrações</h1><p class="lede">A ferramenta funciona sozinha. Quando há outras ferramentas no hub: solicitações do Conector CRM viram OS e o status volta ao CRM; o resumo das OS vai para a PMP; as execuções entram travadas na agenda de cada técnico.</p>
    ${r.modo !== 'hub' ? '<div class="note">Modo isolado: o Data Core é local. No hub, o recebimento é automático a cada 2 minutos.</div>' : '<div class="note ok">Recebimento automático ativo (ao abrir e a cada 2 minutos).</div>'}
    ${crm ? `<div class="panel"><div class="row"><h2 style="margin:0">Aguardando recebimento (${r.pendentes.length})</h2><span class="spacer"></span><button class="btn pri" id="iSync" ${r.pendentes.length ? '' : 'disabled'}>Receber agora</button></div>
    ${tabela([{ t: 'Solicitação', k: 'chave' }, { t: 'Título', wrap: 1, k: 'titulo' }, { t: 'Origem', k: 'crm' }, { t: 'Publicada', h: x => dh(x.em) }], r.pendentes, { vazio: 'Nada pendente.' })}</div>
    <div class="panel"><h2>OS abertas a partir do CRM</h2>${tabela([{ t: 'OS', h: linkOS }, { t: 'Título', wrap: 1, k: 'titulo' }, { t: 'Situação', h: o => esc(st.meta.status[o.status]) }, { t: 'Referência no CRM', k: 'ref_externa' }, { t: 'Aberta', h: o => dh(o.aberta_em) }], r.recebidas, { vazio: 'Nenhuma.' })}</div>` : '<div class="panel"><p class="muted">Conector CRM não instalado neste hub (opcional). As OS são abertas aqui mesmo.</p></div>'}`
  const s = $('#iSync'); if (s) s.onclick = async () => { try { const x = await api('POST', '/api/integracoes/sincronizar'); toast(`${x.solicitacoes_do_crm} OS aberta(s).`); K.render() } catch (e) { toast(e.message) } }
}

// ───────────── Atalho do modo execução (link + QR)
function qrSvg(texto, px = 4) {
  const mz = QRMatriz(texto), n = mz.length, q = 4
  let r = ''; mz.forEach((l, y) => l.forEach((on, x) => { if (on) r += `M${x + q} ${y + q}h1v1h-1z` }))
  return `<svg viewBox="0 0 ${n + 2 * q} ${n + 2 * q}" width="${(n + 2 * q) * px}" height="${(n + 2 * q) * px}" shape-rendering="crispEdges" role="img" aria-label="QR code"><rect width="100%" height="100%" fill="#fff"/><path d="${r}" fill="#000"/></svg>`
}
function atalho() {
  const url = new URL(urlCampo(), location.href).href, https = location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1'
  const d = modal(`<h2>Atalho do modo execução</h2><p class="muted">A tela de campo abre direto nas OS do dia. Envie o link ou mostre o QR para o técnico apontar a câmera do celular.</p>
    <div class="row" style="align-items:flex-start;gap:18px"><div>${qrSvg(url)}</div><div style="flex:1;min-width:240px"><label class="f">Link<input class="in" id="atU" value="${esc(url)}" readonly></label>
    <div class="row" style="margin-top:8px"><button class="btn pri" id="atCp">Copiar link</button><a class="btn" href="${esc(urlCampo())}" target="_blank">Abrir</a></div>
    <h3>Instalar como aplicativo</h3><p class="small">Android (Chrome): abra o link → menu ⋮ → <b>Instalar app</b> / <b>Adicionar à tela inicial</b>.<br>iPhone (Safari): abra o link → Compartilhar → <b>Adicionar à Tela de Início</b>.</p>
    ${https ? '' : '<div class="note warn small">Este endereço não usa HTTPS: o atalho funciona, mas a instalação como app e o modo offline só ficam disponíveis com HTTPS (padrão no servidor hospedado).</div>'}</div></div>
    <div class="row" style="justify-content:flex-end;margin-top:10px"><button class="btn" id="atC">Fechar</button></div>`)
  d.querySelector('#atC').onclick = () => d.close()
  d.querySelector('#atCp').onclick = async () => { try { await navigator.clipboard.writeText(url); toast('Link copiado.') } catch (e) { d.querySelector('#atU').select(); toast('Selecione e copie o link.') } }
}

K.iniciar({
  nome: 'Ordens de Serviço', inicio: 'painel', subtitulo: 'Serviços',
  nav: () => [['painel', 'Painel'], ['quadro', 'Quadro'], ['lista', 'Ordens'], !tec() && ['nova', 'Nova OS'], pf() !== 'solicitante' && ['agenda', 'Agenda das equipes'],
    pf() !== 'solicitante' && ['campo', '📱 Modo execução'], modPlan() && ['modelos', 'Modelos de OS'],
    pf() !== 'solicitante' && ['equipes', 'Equipes'], pf() !== 'solicitante' && ['materiais', 'Materiais'], gest() && ['integracoes', 'Integrações'], K.eAdmin() && ['usuarios', 'Usuários e perfis']],
  rotas: { painel: telaPainel, quadro: telaKanban, lista: telaLista, nova: telaNova, os: telaOS, agenda: m => Quadro.tela(m), modelos: telaModelos, equipes: telaEquipes, materiais: telaMateriais, integracoes: telaIntegracoes, usuarios: K.telaUsuarios,
    campo: m => { m.innerHTML = `<h1>Modo execução</h1><p class="lede">Tela de campo para o celular: OS do dia, como chegar, checklist com fotos, materiais, pausa e conclusão — funciona sem sinal e sincroniza depois.</p><div class="row"><a class="btn pri" href="${urlCampo()}">Abrir modo execução</a><button class="btn" id="cAt">Gerar atalho (link e QR)</button></div>`; $('#cAt').onclick = atalho } }
})
