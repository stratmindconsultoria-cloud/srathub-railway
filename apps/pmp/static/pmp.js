/* PMP · Carteira de Projetos — interface */
'use strict'
const { $, esc, api, V, din, nf, pct, data, dh, toast, modal, pedir, tabela, grafico, opcoes, unidades, areas, st } = K
const pmo = () => st.user.perfil === 'pmo', podeEditar = p => pmo() || (st.user.perfil === 'gerente' && (!st.user.unidade || !p.unidade || p.unidade === st.user.unidade))
const STT = { nova: ['não iniciada', ''], rascunho: ['rascunho', 'warn'], enviada: ['enviada', 'pri'], aprovada: ['aprovada', 'ok'], devolvida: ['devolvida', 'bad'], sem_atualizacao: ['sem atualização', ''] }
const tagT = s => { const [l, c] = STT[s] || [s || '—', '']; return `<span class="tag ${c}">${esc(l)}</span>` }
const fmtD = (dim, v) => dim === 'fis' ? nf(v, 1) + '%' : din(v)
const parseNum = s => { s = String(s).trim(); if (/,\d{1,2}$/.test(s) || /\.\d{3}/.test(s)) s = s.replace(/\./g, '').replace(',', '.'); const n = parseFloat(s.replace(/[^\d.-]/g, '')); return isNaN(n) ? 0 : n }
const soma = a => (a || []).reduce((x, y) => x + (+y || 0), 0)
let filtro = { unidade: '', area: '', status: 'ativo', busca: '' }

// ───────────── Carteira
async function telaCarteira(m) {
  const d = await api('GET', '/api/carteira')
  if (!d.projetos.length) {
    m.innerHTML = `<h1>Carteira de projetos</h1><p class="lede">Nenhum projeto ainda. Os projetos chegam pelo ${esc(V('termo_abertura'))} aprovado (Originação), pelo orçamento aprovado do ${esc(V('plano_sigla'))} ou por planilha (Importações).</p>
      ${pmo() ? '<div class="panel"><button class="btn pri" id="demo">Carregar carteira de exemplo</button> <a class="btn" href="#/integracoes">Receber do hub</a> <a class="btn" href="#/importacoes">Importar planilha</a></div>' : ''}`
    const b = $('#demo'); if (b) b.onclick = async () => { try { await api('POST', '/api/demo'); toast('Carteira de exemplo criada.'); K.render() } catch (e) { toast(e.message) } }
    return
  }
  const ps = d.projetos.filter(p => (!filtro.unidade || p.unidade === filtro.unidade) && (!filtro.area || p.area === filtro.area) && (!filtro.status || p.status === filtro.status) && (!filtro.busca || (p.nome + ' ' + p.codigo).toLowerCase().includes(filtro.busca.toLowerCase())))
  const t = k => ps.reduce((s, p) => s + p.fin[k], 0)
  const orcAno = t('orcado_ano'), realAc = t('realizado_acum'), orcAc = t('orcado_acum'), tendAno = t('tendencia_ano')
  const ac = a => a.reduce((r, v, i) => (r.push((r[i - 1] || 0) + v), r), [])
  const s = d.serie_fin
  m.innerHTML = `<h1>Carteira de projetos · ${d.ano}</h1><p class="lede">${d.rodada ? `${esc(d.rodada.nome)} (${d.rodada.status === 'aberta' ? 'aberta' : 'última fechada'}) · mês de referência ${d.mes_ref}` : 'Sem rodada de tendência'} · visão financeira (caixa)</p>
    <div class="cards"><div class="card"><div class="v">${din(orcAno)}</div><div class="l">Orçado ${d.ano}</div></div>
    <div class="card"><div class="v">${din(realAc)}</div><div class="l">Realizado até o mês ${d.mes_ref} (${pct(orcAc ? realAc / orcAc : 0, 0)} do orçado no período)</div></div>
    <div class="card"><div class="v">${din(tendAno)}</div><div class="l">${esc(V('forecast'))} ${d.ano}</div></div>
    <div class="card ${tendAno > orcAno ? 'bad' : 'ok'}"><div class="v">${din(tendAno - orcAno)}</div><div class="l">Desvio da ${esc(V('forecast').toLowerCase())} sobre o orçado (${pct(orcAno ? (tendAno - orcAno) / orcAno : 0)})</div></div></div>
    <div class="panel"><h2>Curva S da carteira (acumulado)</h2>${grafico([{ nome: 'Orçado', valores: ac(s.orc), cor: '#94a3b8' }, { nome: 'Realizado', valores: ac(s.real).map((v, i) => i < d.mes_ref ? v : null) }, { nome: V('forecast'), valores: ac(s.tend), tracejado: true, cor: '#C2410C' }], st.meta.meses)}</div>
    <div class="filtros"><label class="f">${esc(V('unidade'))}<select class="in" id="fU">${opcoes(unidades(), filtro.unidade, 'Todas')}</select></label>
      <label class="f">${esc(V('area'))}<select class="in" id="fA">${opcoes(areas(), filtro.area, 'Todas')}</select></label>
      <label class="f">Situação<select class="in" id="fS">${opcoes(['ativo', 'suspenso', 'concluido', 'cancelado'], filtro.status, 'Todas')}</select></label>
      <label class="f">Busca<input class="in" id="fB" value="${esc(filtro.busca)}"></label><span class="spacer"></span>
      <a class="btn" href="#" id="bExp">Exportar ${esc(V('forecast').toLowerCase())} .xlsx</a>${pmo() ? ' <button class="btn" id="bNovo">+ Projeto</button>' : ''}</div>
    ${tabela([{ t: 'Código', h: p => `<a href="#/projeto/${encodeURIComponent(p.codigo)}">${esc(p.codigo)}</a>` },
      { t: 'Projeto', wrap: 1, h: p => `<b>${esc(p.nome)}</b><div class="small muted">${esc(p.unidade || '—')} · ${esc(p.area || '—')} · ${esc(p.etapa || '')}${p.gerente ? ' · ' + esc(p.gerente) : ''}</div>` },
      { t: 'Orçado ano', n: 1, h: p => din(p.fin.orcado_ano) }, { t: 'Realizado acum.', n: 1, h: p => din(p.fin.realizado_acum) },
      { t: V('forecast') + ' ano', n: 1, h: p => din(p.fin.tendencia_ano) }, { t: 'Desvio', n: 1, h: p => { const x = p.fin.tendencia_ano - p.fin.orcado_ano; return `<span class="${x > 0 ? 'pos' : x < 0 ? 'neg' : ''}">${din(x)}</span>` } },
      { t: 'Físico real × plan.', n: 1, h: p => `${nf(p.fis.real, 0)}% × ${nf(p.fis.plan, 0)}%` }, { t: V('rodada'), h: p => tagT(p.fin.status_tend || (d.rodada && d.rodada.status === 'aberta' ? 'nova' : '')) }],
      ps, { max: 620, vazio: 'Nenhum projeto com estes filtros.' })}`
  $('#fU').onchange = e => { filtro.unidade = e.target.value; K.render() }; $('#fA').onchange = e => { filtro.area = e.target.value; K.render() }
  $('#fS').onchange = e => { filtro.status = e.target.value; K.render() }; $('#fB').onchange = e => { filtro.busca = e.target.value; K.render() }
  $('#bExp').onclick = e => { e.preventDefault(); K.baixarUrl('/api/exportar/tendencia.xlsx', 'tendencia.xlsx').catch(x => toast(x.message)) }
  const bn = $('#bNovo'); if (bn) bn.onclick = novoProjeto
}
function novoProjeto() {
  const d = modal(`<h2>Novo projeto</h2><div class="grid2"><label class="f">Código (${esc(V('codigo_erp'))} ou interno)<input class="in" id="nC"></label><label class="f">Nome<input class="in" id="nN"></label>
    <label class="f">${esc(V('unidade'))}<select class="in" id="nU">${opcoes(unidades(), '')}</select></label><label class="f">${esc(V('area'))}<select class="in" id="nA">${opcoes(areas(), '')}</select></label>
    <label class="f">Gerente<input class="in" id="nG"></label><label class="f">Etapa<select class="in" id="nE">${opcoes(st.meta.etapas, '')}</select></label></div>
    <div class="row" style="justify-content:flex-end;margin-top:14px"><button class="btn" id="x">Cancelar</button><button class="btn pri" id="ok">Criar</button></div>`)
  d.querySelector('#x').onclick = () => d.close()
  d.querySelector('#ok').onclick = async () => { const cod = d.querySelector('#nC').value.trim(); try { await api('PUT', '/api/projetos/' + encodeURIComponent(cod), { nome: d.querySelector('#nN').value, unidade: d.querySelector('#nU').value, area: d.querySelector('#nA').value, gerente: d.querySelector('#nG').value, etapa: d.querySelector('#nE').value }); d.close(); location.hash = '#/projeto/' + encodeURIComponent(cod) } catch (e) { toast(e.message) } }
}

// ───────────── Projeto
async function telaProjeto(m, partes) {
  const cod = partes[0], aba = partes[1] || 'geral'
  const d = await api('GET', '/api/projetos/' + encodeURIComponent(cod) + (partes[2] ? '?ano=' + partes[2] : ''))
  const p = d.projeto, ed = podeEditar(p)
  const abas = [['geral', 'Visão geral'], ['tendencia', V('forecast') + (d.rodada ? ' · ' + d.rodada.nome : '')], ['mudancas', `Mudanças (${d.mudancas.length})`], ['atas', `Atas e ações (${d.atas.length})`], ['notas', `Comentários e documentos (${d.comentarios.length + d.documentos.length})`], ['historico', 'Histórico']]
  m.innerHTML = `<p class="small"><a href="#/carteira">← Carteira</a></p><div class="row"><h1 style="margin:0">${esc(p.nome)}</h1><span class="tag pri">${esc(p.codigo)}</span><span class="tag">${esc(p.status)}</span></div>
    <p class="lede">${esc(p.unidade || '—')} · ${esc(p.area || '—')} · ${esc(p.etapa || 'sem etapa')} · gerente ${esc(p.gerente || '—')}${p.plano_id ? ' · ' + esc(V('plano_sigla')) + ' ' + esc(p.plano_id) : ''}${p.id_mestre ? ' · Originação ' + esc(p.id_mestre) : ''}</p>
    <nav class="tabs">${abas.map(([k, l]) => `<a href="#/projeto/${encodeURIComponent(cod)}/${k}"${k === aba ? ' aria-current="page"' : ''}>${esc(l)}</a>`).join('')}</nav><div id="aba"></div>`
  const box = $('#aba')
  if (aba === 'geral') abaGeral(box, d, ed)
  else if (aba === 'tendencia') abaTendencia(box, d, ed)
  else if (aba === 'mudancas') abaMudancas(box, d, ed)
  else if (aba === 'atas') abaAtas(box, d, ed)
  else if (aba === 'notas') abaNotas(box, d, ed)
  else abaHistorico(box, d)
}
function abaGeral(box, d, ed) {
  const p = d.projeto, c = d.curvas, mes = d.rodada && d.rodada.ano === d.ano ? d.rodada.mes_ref : 12
  const tend = d.tendencia.fin ? d.tendencia.fin.meses : c.fin.realizado.map((v, i) => i < mes ? v : c.fin.orcado[i])
  box.innerHTML = `<div class="row" style="margin-bottom:10px"><label class="f">Ano<select class="in" id="gAno">${opcoes(d.anos, d.ano)}</select></label></div>
    <div class="cards"><div class="card"><div class="v">${din(soma(c.fin.orcado))}</div><div class="l">Orçado ${d.ano}</div></div><div class="card"><div class="v">${din(soma(c.fin.realizado.slice(0, mes)))}</div><div class="l">Realizado até o mês ${mes}</div></div>
    <div class="card"><div class="v">${din(soma(tend))}</div><div class="l">${esc(V('forecast'))} ${d.ano}</div></div><div class="card"><div class="v">${din(soma(c.fin.comp_ped))}</div><div class="l">Compromisso (pedidos)</div></div>
    <div class="card"><div class="v">${nf(c.fis.realizado[mes - 1] || 0, 1)}% × ${nf(c.fis.orcado[mes - 1] || 0, 1)}%</div><div class="l">Físico realizado × planejado</div></div></div>
    <div class="grid2"><div class="panel"><h2>Financeiro mensal</h2>${grafico([{ nome: 'Orçado', valores: c.fin.orcado, tipo: 'barra', cor: '#94a3b8' }, { nome: 'Realizado', valores: c.fin.realizado.map((v, i) => i < mes ? v : null), tipo: 'barra' }, { nome: V('forecast'), valores: tend, cor: '#C2410C', tracejado: true }], st.meta.meses, { barras: true })}</div>
    <div class="panel"><h2>Físico acumulado (%)</h2>${grafico([{ nome: 'Planejado', valores: c.fis.orcado, cor: '#94a3b8' }, { nome: 'Realizado', valores: c.fis.realizado.map((v, i) => i < mes ? v : null) }, ...(d.tendencia.fis ? [{ nome: V('forecast'), valores: d.tendencia.fis.meses, cor: '#C2410C', tracejado: true }] : [])], st.meta.meses, { fmt: v => nf(v, 0) + '%' })}</div></div>
    <div class="panel"><h2>Curvas ${d.ano}</h2>${['econ', 'fin', 'fis'].map(dim => `<h3>${esc(st.meta.dimensoes[dim])}</h3>` + tabela([{ t: 'Série', k: 's' }, ...st.meta.meses.map((mm, i) => ({ t: mm, n: 1, h: r => dim === 'fis' ? nf(r.v[i], 1) : nf(r.v[i]) })), { t: dim === 'fis' ? 'Dez' : 'Total', n: 1, h: r => `<b>${dim === 'fis' ? nf(r.v[11], 1) + '%' : nf(soma(r.v))}</b>` }],
      Object.entries(st.meta.tipos_curva).filter(([t]) => dim === 'fin' || !t.startsWith('comp')).map(([t, l]) => ({ s: l, v: c[dim][t] })).concat(d.tendencia[dim] ? [{ s: V('forecast') + ' (rodada)', v: d.tendencia[dim].meses }] : []))).join('')}
      ${pmo() ? '<p class="small muted">Curvas vêm das integrações e das importações (menu Importações).</p>' : ''}</div>
    <div class="panel"><h2>Cadastro</h2><div class="grid3">
      ${[['nome', 'Nome'], ['gerente', 'Gerente'], ['grupo', 'Grupo'], ['tipo_investimento', 'Tipo de investimento'], ['inicio_previsto', 'Início previsto'], ['conclusao_prevista', 'Conclusão prevista']].map(([k, l]) => `<label class="f">${l}<input class="in" data-k="${k}" value="${esc(p[k] || '')}" ${ed ? '' : 'disabled'}></label>`).join('')}
      <label class="f">${esc(V('unidade'))}<select class="in" data-k="unidade" ${pmo() ? '' : 'disabled'}>${opcoes(unidades(), p.unidade, '—')}</select></label>
      <label class="f">${esc(V('area'))}<select class="in" data-k="area" ${ed ? '' : 'disabled'}>${opcoes(areas(), p.area, '—')}</select></label>
      <label class="f">Etapa<select class="in" data-k="etapa" ${ed ? '' : 'disabled'}>${opcoes(st.meta.etapas, p.etapa, '—')}</select></label>
      <label class="f">Situação<select class="in" data-k="status" ${pmo() ? '' : 'disabled'}>${opcoes(['ativo', 'suspenso', 'concluido', 'cancelado'], p.status)}</select></label></div>
      ${(st.meta.campos_extras || []).length ? `<div class="grid3" style="margin-top:10px">${st.meta.campos_extras.map(f => { const v = (p.extras || {})[f.chave] ?? ''; return `<label class="f">${esc(f.rotulo || f.chave)}${f.tipo === 'lista' ? `<select class="in" data-x="${esc(f.chave)}" ${ed ? '' : 'disabled'}>${opcoes(f.opcoes || [], v, '—')}</select>` : `<input class="in" data-x="${esc(f.chave)}" type="${f.tipo === 'numero' ? 'number' : f.tipo === 'data' ? 'date' : 'text'}" value="${esc(v)}" ${ed ? '' : 'disabled'}>`}</label>` }).join('')}</div>` : ''}
      <label class="f" style="margin-top:10px">Escopo<textarea class="in" data-k="escopo" rows="3" ${ed ? '' : 'disabled'}>${esc(p.escopo || '')}</textarea></label>
      ${ed ? '<div class="row" style="margin-top:10px"><button class="btn pri" id="gSalvar">Salvar cadastro</button></div>' : ''}</div>
    ${(d.ordens || []).length ? `<div class="panel"><h2>Ordens de serviço vinculadas (${d.ordens.length})</h2>${tabela([{ t: 'OS', h: o => `<a href="/apps/${esc(o.origem_app || 'ordens')}/#/os/${encodeURIComponent(o.numero || o.chave)}">${esc(o.numero || o.chave)}</a>` }, { t: 'Título', wrap: 1, k: 'titulo' }, { t: 'Situação', h: o => `<span class="tag ${o.sla === 'atrasada' ? 'bad' : o.sla === 'em_risco' ? 'warn' : ''}">${esc(o.status_nome || o.status)}</span>` }, { t: 'Execução', h: o => dh(o.execucao_inicio) }, { t: 'Custo previsto', n: 1, h: o => din(o.custo_previsto) }, { t: 'Custo real', n: 1, h: o => din(o.custo_real) }], d.ordens)}</div>` : ''}`
  $('#gAno').onchange = e => { location.hash = `#/projeto/${encodeURIComponent(p.codigo)}/geral/${e.target.value}` }
  const b = $('#gSalvar'); if (b) b.onclick = async () => { const corpo = {}; box.querySelectorAll('[data-k]').forEach(i => { if (!i.disabled) corpo[i.dataset.k] = i.value }); const ex = {}; box.querySelectorAll('[data-x]').forEach(i => { ex[i.dataset.x] = i.value }); if (Object.keys(ex).length) corpo.extras = ex; try { await api('PUT', '/api/projetos/' + encodeURIComponent(p.codigo), corpo); toast('Cadastro salvo.'); K.render() } catch (e) { toast(e.message) } }
}
function abaTendencia(box, d, ed) {
  const r = d.rodada, p = d.projeto
  if (!r) { box.innerHTML = `<div class="note">Não há rodada de ${esc(V('forecast').toLowerCase())} aberta. ${pmo() ? 'Abra uma em <a href="#/rodadas">Rodadas</a>.' : ''}</div>`; return }
  if (r.ano !== d.ano) { box.innerHTML = `<div class="note">A rodada aberta é de ${r.ano}. <a href="#/projeto/${encodeURIComponent(p.codigo)}/tendencia/${r.ano}">Ver ${r.ano}</a></div>`; return }
  const mes = r.mes_ref, anoMax = (st.meta.organizacao.horizonte_anos || 5)
  const futAnos = []; for (let a = r.ano + 1; a <= r.ano + anoMax; a++) futAnos.push(a)
  box.innerHTML = `<p class="lede">Meses até ${st.meta.meses[mes - 1]} usam o realizado (fechados). Ajuste os meses seguintes e os anos futuros; desvios acima da tolerância contra a referência precisam ser classificados e justificados antes do envio ao ${esc(V('pmo'))}.</p>` +
    ['fin', 'econ', 'fis'].map(dim => {
      const t = d.tendencia[dim], bloq = !ed || (['enviada', 'aprovada'].includes(t.status) && !pmo()), unid = dim === 'fis' ? '%' : ''
      return `<div class="panel" data-dim="${dim}"><div class="row"><h2 style="margin:0">${esc(st.meta.dimensoes[dim])}</h2>${tagT(t.status)}${t.parecer ? `<span class="small muted">Parecer: ${esc(t.parecer)}</span>` : ''}<span class="spacer"></span>
        <span class="small muted">Referência: ${esc(t.referencia_nome)} = <b>${fmtD(dim, dim === 'fis' ? t.referencia[11] : soma(t.referencia))}</b></span></div>
        <div class="tw" style="margin-top:8px"><table class="t"><thead><tr><th>Série</th>${st.meta.meses.map(x => `<th class="n">${x}</th>`).join('')}<th class="n">${dim === 'fis' ? 'Dez' : 'Ano'}</th></tr></thead><tbody>
        <tr><td>Orçado</td>${d.curvas[dim].orcado.map(v => `<td class="n">${nf(v, dim === 'fis' ? 1 : 0)}</td>`).join('')}<td class="n">${fmtD(dim, dim === 'fis' ? d.curvas[dim].orcado[11] : soma(d.curvas[dim].orcado))}</td></tr>
        <tr><td>${esc(V('forecast'))}</td>${t.meses.map((v, i) => i < mes ? `<td class="n muted">${nf(v, dim === 'fis' ? 1 : 0)}</td>` : `<td class="n"><input class="cel" data-m="${i}" value="${nf(v, dim === 'fis' ? 1 : 0)}" ${bloq ? 'disabled' : ''}></td>`).join('')}<td class="n"><b class="tot"></b></td></tr></tbody></table></div>
        ${dim !== 'fis' ? `<div class="row" style="margin-top:8px"><span class="small muted">Anos seguintes:</span>${futAnos.map(a => `<label class="f">${a}<input class="in" style="width:120px" data-fut="${a}" value="${nf(t.anos_futuros[a] || 0)}" ${bloq ? 'disabled' : ''}></label>`).join('')}</div>` : ''}
        <div class="row" style="margin-top:10px"><b>Desvio contra a referência: <span class="dev"></span></b><span class="small muted">(tolerância ${fmtD(dim, st.meta.tolerancia[dim])})</span></div>
        <div class="desv" style="margin-top:8px"></div>
        <label class="f" style="margin-top:8px">Justificativa<textarea class="in jus" rows="2" ${bloq ? 'disabled' : ''}>${esc(t.justificativa || '')}</textarea></label>
        <div class="row" style="margin-top:10px">${!bloq ? `<button class="btn" data-salvar="0">Salvar rascunho</button><button class="btn pri" data-salvar="1">Enviar ao ${esc(V('pmo'))}</button>${pmo() ? '<button class="btn ok" data-salvar="2">Salvar e aprovar</button>' : ''}` : ''}
          ${pmo() && t.status === 'enviada' ? `<button class="btn ok" data-dec="aprovar">Aprovar</button><button class="btn bad" data-dec="devolver">Devolver</button>` : ''}</div></div>`
    }).join('')
  box.querySelectorAll('[data-dim]').forEach(pn => {
    const dim = pn.dataset.dim, t = d.tendencia[dim]
    let desv = (t.desvios || []).map(x => ({ ...x }))
    const meses = () => t.meses.map((v, i) => { const inp = pn.querySelector(`[data-m="${i}"]`); return inp ? parseNum(inp.value) : v })
    const recalc = () => {
      const ms = meses(), tot = dim === 'fis' ? ms[11] : soma(ms), ref = dim === 'fis' ? t.referencia[11] : soma(t.referencia), dev = tot - ref
      pn.querySelector('.tot').textContent = fmtD(dim, tot); pn.querySelector('.dev').innerHTML = `<span class="${Math.abs(dev) > st.meta.tolerancia[dim] ? (dim === 'fis' ? (dev < 0 ? 'pos' : 'neg') : (dev > 0 ? 'pos' : 'neg')) : ''}">${fmtD(dim, dev)}</span>`
      const precisa = Math.abs(dev) > st.meta.tolerancia[dim], somaD = desv.reduce((s, x) => s + (+x.valor || 0), 0)
      pn.querySelector('.desv').innerHTML = !precisa && !desv.length ? '<span class="small muted">Dentro da tolerância: classificação dispensada.</span>' :
        `<div class="small" style="margin-bottom:4px">Classifique o desvio (soma ${fmtD(dim, somaD)} de ${fmtD(dim, dev)}):</div>` + desv.map((x, i) => `<div class="row" style="margin-bottom:4px"><select class="in" data-dc="${i}">${opcoes(st.meta.categorias_desvio[dim], x.categoria, 'Categoria…')}</select><input class="in" data-dv="${i}" value="${nf(x.valor, dim === 'fis' ? 1 : 0)}" style="width:140px"><button class="btn mini" data-dx="${i}">✕</button></div>`).join('') +
        `<button class="btn mini" data-da>+ categoria</button>${desv.length === 0 ? '' : ` <button class="btn mini" data-dr>Atribuir o restante à última</button>`}`
      pn.querySelectorAll('[data-dc]').forEach(s => s.onchange = () => { desv[+s.dataset.dc].categoria = s.value })
      pn.querySelectorAll('[data-dv]').forEach(s => s.onchange = () => { desv[+s.dataset.dv].valor = parseNum(s.value); recalc() })
      pn.querySelectorAll('[data-dx]').forEach(s => s.onclick = () => { desv.splice(+s.dataset.dx, 1); recalc() })
      const da = pn.querySelector('[data-da]'); if (da) da.onclick = () => { desv.push({ categoria: '', valor: desv.length ? 0 : dev }); recalc() }
      const dr = pn.querySelector('[data-dr]'); if (dr) dr.onclick = () => { desv[desv.length - 1].valor += dev - somaD; recalc() }
    }
    pn.querySelectorAll('input.cel').forEach(i => { i.onfocus = () => i.select(); i.onchange = recalc })
    recalc()
    pn.querySelectorAll('[data-salvar]').forEach(b => b.onclick = async () => {
      const fut = {}; pn.querySelectorAll('[data-fut]').forEach(i => fut[i.dataset.fut] = parseNum(i.value))
      try { const r2 = await api('PUT', `/api/rodadas/${d.rodada.id}/tendencias/${encodeURIComponent(d.projeto.codigo)}`, { dimensao: dim, meses: meses(), anos_futuros: fut, desvios: desv.filter(x => x.categoria), justificativa: pn.querySelector('.jus').value, enviar: b.dataset.salvar !== '0', aprovar: b.dataset.salvar === '2' }); toast(`${st.meta.dimensoes[dim]}: ${STT[r2.status][0]}.`); K.render() } catch (e) { toast(e.message) }
    })
    pn.querySelectorAll('[data-dec]').forEach(b => b.onclick = async () => { let parecer = ''; if (b.dataset.dec === 'devolver') { parecer = await pedir('Devolver ' + V('forecast').toLowerCase(), 'Explique o que o gerente precisa ajustar.', true); if (parecer === null) return } try { await api('POST', `/api/rodadas/${d.rodada.id}/tendencias/${encodeURIComponent(d.projeto.codigo)}/decidir`, { decisao: b.dataset.dec, dimensao: dim, parecer }); toast('Decisão registrada.'); K.render() } catch (e) { toast(e.message) } })
  })
}
function abaMudancas(box, d, ed) {
  const cod = d.projeto.codigo
  box.innerHTML = tabela([{ t: 'ID', k: 'id' }, { t: 'Mudança', wrap: 1, h: x => `<b>${esc(x.titulo)}</b><div class="small muted">${esc(x.tipo || '')} · ${esc(x.descricao || '')}</div>` }, { t: 'Custo', n: 1, h: x => din(x.impacto_custo) }, { t: 'Prazo (dias)', n: 1, k: 'impacto_prazo_dias' },
    { t: 'Situação', h: x => `<span class="tag ${{ aprovada: 'ok', implementada: 'ok', rejeitada: 'bad' }[x.status] || 'warn'}">${esc(x.status)}</span>` }, { t: 'Solicitação', h: x => esc(x.solicitado_por) + `<div class="small muted">${dh(x.solicitado_em)}</div>` },
    { t: '', h: x => pmo() ? acoesMudanca(x) : '' }], d.mudancas, { vazio: 'Nenhuma mudança registrada.' }) +
    (ed ? `<div class="panel" style="margin-top:14px"><h2>Registrar mudança</h2><div class="grid3"><label class="f">Título<input class="in" id="mT"></label><label class="f">Tipo<select class="in" id="mTp">${opcoes(st.meta.tipos_mudanca, '')}</select></label><span></span>
      <label class="f">Impacto em custo<input class="in" id="mC" type="number" value="0"></label><label class="f">Impacto em prazo (dias)<input class="in" id="mP" type="number" value="0"></label></div>
      <label class="f" style="margin-top:8px">Descrição<textarea class="in" id="mD" rows="3"></textarea></label><div class="row" style="margin-top:10px"><button class="btn pri" id="mOk">Registrar</button></div></div>` : '')
  ligarMudancas(box)
  const ok = $('#mOk'); if (ok) ok.onclick = async () => { try { await api('POST', `/api/projetos/${encodeURIComponent(cod)}/mudancas`, { titulo: $('#mT').value, tipo: $('#mTp').value, impacto_custo: $('#mC').value, impacto_prazo_dias: $('#mP').value, descricao: $('#mD').value }); toast('Mudança registrada.'); K.render() } catch (e) { toast(e.message) } }
}
function acoesMudanca(x) { const prox = { proposta: ['em_analise', 'aprovada', 'rejeitada'], em_analise: ['aprovada', 'rejeitada'], aprovada: ['implementada'] }[x.status] || []; return prox.map(s => `<button class="btn mini" data-mud="${esc(x.id)}|${s}">${esc(s.replace('_', ' '))}</button>`).join(' ') }
function ligarMudancas(box) { box.querySelectorAll('[data-mud]').forEach(b => b.onclick = async () => { const [id, status] = b.dataset.mud.split('|'); const parecer = await pedir('Decisão da mudança', `Mudar para "${status}". Parecer (opcional):`, false); if (parecer === null) return; try { await api('POST', `/api/mudancas/${id}/decidir`, { status, parecer }); toast('Mudança atualizada.'); K.render() } catch (e) { toast(e.message) } }) }
function editorAta(ata, codigo) {
  ata = ata || { acoes: [] }
  const d = modal(`<h2>${ata.id ? 'Editar ata' : 'Nova ata'}</h2><div class="grid3"><label class="f">Título<input class="in" id="aT" value="${esc(ata.titulo || '')}"></label><label class="f">Data<input class="in" id="aD" type="date" value="${esc(ata.data || new Date().toISOString().slice(0, 10))}"></label><label class="f">Tipo<select class="in" id="aTp">${opcoes(st.meta.tipos_ata, ata.tipo)}</select></label></div>
    <label class="f" style="margin-top:8px">Participantes<input class="in" id="aP" value="${esc(ata.participantes || '')}"></label><label class="f" style="margin-top:8px">Pauta<textarea class="in" id="aPa" rows="3">${esc(ata.pauta || '')}</textarea></label>
    <label class="f" style="margin-top:8px">Deliberações<textarea class="in" id="aDe" rows="4">${esc(ata.deliberacoes || '')}</textarea></label>
    <h3>Ações</h3><div id="aAc"></div><button class="btn mini" id="aMais">+ ação</button>
    <div class="row" style="justify-content:flex-end;margin-top:14px"><button class="btn" id="aX">Cancelar</button><button class="btn pri" id="aOk">Salvar ata</button></div>`)
  const acoes = (ata.acoes || []).filter(a => a.status === 'aberta').map(a => ({ ...a }))
  const desenhar = () => { d.querySelector('#aAc').innerHTML = acoes.map((a, i) => `<div class="row" style="margin-bottom:4px"><input class="in" data-ad="${i}" placeholder="Ação" value="${esc(a.descricao || '')}" style="flex:2"><input class="in" data-ar="${i}" placeholder="Responsável" value="${esc(a.responsavel || '')}"><input class="in" type="date" data-ap="${i}" value="${esc(a.prazo || '')}"></div>`).join('') || '<p class="small muted">Nenhuma ação aberta.</p>'
    d.querySelectorAll('[data-ad]').forEach(x => x.oninput = () => acoes[+x.dataset.ad].descricao = x.value); d.querySelectorAll('[data-ar]').forEach(x => x.oninput = () => acoes[+x.dataset.ar].responsavel = x.value); d.querySelectorAll('[data-ap]').forEach(x => x.oninput = () => acoes[+x.dataset.ap].prazo = x.value) }
  desenhar(); d.querySelector('#aMais').onclick = () => { acoes.push({ descricao: '' }); desenhar() }
  d.querySelector('#aX').onclick = () => d.close()
  d.querySelector('#aOk').onclick = async () => { try { await api('POST', '/api/atas', { id: ata.id, codigo: codigo || ata.codigo || null, titulo: d.querySelector('#aT').value, data: d.querySelector('#aD').value, tipo: d.querySelector('#aTp').value, participantes: d.querySelector('#aP').value, pauta: d.querySelector('#aPa').value, deliberacoes: d.querySelector('#aDe').value, acoes }); d.close(); toast('Ata salva.'); K.render() } catch (e) { toast(e.message) } }
}
function tabelaAtas(atas) {
  return tabela([{ t: 'Data', h: a => data(a.data) }, { t: 'Ata', wrap: 1, h: a => `<b>${esc(a.titulo)}</b><div class="small muted">${esc(a.tipo || '')}${a.projeto ? ' · ' + esc(a.projeto) : a.codigo ? '' : ' · carteira'} · ${esc(a.participantes || '')}</div>` },
    { t: 'Situação', h: a => `<span class="tag ${a.status === 'aprovada' ? 'ok' : 'warn'}">${esc(a.status)}</span>` }, { t: '', h: a => `<button class="btn mini" data-ver="${esc(a.id)}">Ver</button>` + (a.status === 'rascunho' && ['pmo', 'gerente'].includes(st.user.perfil) ? ` <button class="btn mini" data-edt="${esc(a.id)}">Editar</button>` : '') + (a.status === 'rascunho' && pmo() ? ` <button class="btn mini ok" data-apr="${esc(a.id)}">Aprovar</button>` : '') }], atas, { vazio: 'Nenhuma ata.' })
}
function ligarAtas(box, atas, codigo) {
  box.querySelectorAll('[data-ver]').forEach(b => b.onclick = () => { const a = atas.find(x => x.id === b.dataset.ver); const d = modal(`<h2>${esc(a.titulo)}</h2><p class="muted">${data(a.data)} · ${esc(a.tipo || '')} · ${esc(a.status)}${a.aprovado_por ? ' por ' + esc(a.aprovado_por) : ''}</p><p><b>Participantes:</b> ${esc(a.participantes || '—')}</p><h3>Pauta</h3><p style="white-space:pre-wrap">${esc(a.pauta || '—')}</p><h3>Deliberações</h3><p style="white-space:pre-wrap">${esc(a.deliberacoes || '—')}</p><h3>Ações</h3>${tabela([{ t: 'Ação', wrap: 1, k: 'descricao' }, { t: 'Responsável', k: 'responsavel' }, { t: 'Prazo', h: x => data(x.prazo) }, { t: 'Situação', k: 'status' }], a.acoes || [])}<div class="row" style="justify-content:flex-end;margin-top:12px"><button class="btn" id="vF">Fechar</button></div>`); d.querySelector('#vF').onclick = () => d.close() })
  box.querySelectorAll('[data-edt]').forEach(b => b.onclick = () => editorAta(atas.find(x => x.id === b.dataset.edt), codigo))
  box.querySelectorAll('[data-apr]').forEach(b => b.onclick = async () => { try { await api('POST', `/api/atas/${b.dataset.apr}/aprovar`); toast('Ata aprovada.'); K.render() } catch (e) { toast(e.message) } })
}
function tabelaAcoes(acoes) {
  return tabela([{ t: 'Ação', wrap: 1, h: a => `<b>${esc(a.descricao)}</b><div class="small muted">${esc(a.ata || '')}${a.projeto ? ' · ' + esc(a.projeto) : ''}</div>` }, { t: 'Responsável', k: 'responsavel' }, { t: 'Prazo', h: a => `<span class="${a.prazo && a.prazo < new Date().toISOString().slice(0, 10) && a.status === 'aberta' ? 'pos' : ''}">${data(a.prazo)}</span>` },
    { t: '', h: a => a.status === 'aberta' && ['pmo', 'gerente'].includes(st.user.perfil) ? `<button class="btn mini ok" data-acs="${esc(a.id)}|concluida">Concluir</button> <button class="btn mini" data-acs="${esc(a.id)}|cancelada">Cancelar</button>` : esc(a.status) }], acoes, { vazio: 'Nenhuma ação aberta.' })
}
function ligarAcoes(box) { box.querySelectorAll('[data-acs]').forEach(b => b.onclick = async () => { const [id, status] = b.dataset.acs.split('|'); try { await api('POST', `/api/acoes/${id}`, { status }); toast('Ação atualizada.'); K.render() } catch (e) { toast(e.message) } }) }
function abaAtas(box, d, ed) {
  const atas = d.atas.map(a => ({ ...a, acoes: d.acoes.filter(x => x.ata_id === a.id) }))
  box.innerHTML = (ed ? '<div class="row" style="margin-bottom:10px"><button class="btn pri" id="nAta">Nova ata</button></div>' : '') + tabelaAtas(atas) + '<h3>Ações do projeto</h3>' + tabelaAcoes(d.acoes.filter(a => a.status === 'aberta'))
  ligarAtas(box, atas, d.projeto.codigo); ligarAcoes(box)
  const n = $('#nAta'); if (n) n.onclick = () => editorAta(null, d.projeto.codigo)
}
function abaNotas(box, d, ed) {
  box.innerHTML = `<div class="grid2"><div class="panel"><h2>Comentários</h2>${st.user.perfil !== 'leitor' ? '<textarea class="in" id="cT" rows="3" style="width:100%"></textarea><div class="row" style="margin:6px 0 12px"><button class="btn pri" id="cOk">Comentar</button></div>' : ''}
    ${d.comentarios.map(c => `<div style="border-bottom:1px solid var(--line);padding:8px 0"><div class="small muted">${esc(c.por)} · ${dh(c.em)}</div><div style="white-space:pre-wrap">${esc(c.texto)}</div></div>`).join('') || '<p class="muted">Sem comentários.</p>'}</div>
    <div class="panel"><h2>Documentos</h2>${tabela([{ t: 'Documento', h: x => `<a href="#" data-doc="${esc(x.id)}" data-nome="${esc(x.nome)}">${esc(x.nome)}</a>` }, { t: 'Tipo', k: 'tipo' }, { t: 'Por', h: x => esc(x.por) + `<div class="small muted">${dh(x.em)}</div>` }], d.documentos, { vazio: 'Nenhum documento.' })}
    ${ed ? '<label class="btn" style="margin-top:10px">Anexar arquivo<input type="file" id="dF" hidden></label>' : ''}</div></div>`
  const ok = $('#cOk'); if (ok) ok.onclick = async () => { try { await api('POST', `/api/projetos/${encodeURIComponent(d.projeto.codigo)}/comentarios`, { texto: $('#cT').value }); K.render() } catch (e) { toast(e.message) } }
  const f = $('#dF'); if (f) f.onchange = async () => { const fd = new FormData(); fd.append('arquivo', f.files[0]); try { await api('POST', `/api/projetos/${encodeURIComponent(d.projeto.codigo)}/documentos`, fd, true); toast('Documento anexado.'); K.render() } catch (e) { toast(e.message) } }
  box.querySelectorAll('[data-doc]').forEach(a => a.onclick = e => { e.preventDefault(); K.baixarUrl('/api/documentos/' + a.dataset.doc, a.dataset.nome).catch(x => toast(x.message)) })
}
function abaHistorico(box, d) {
  const rodadas = [...new Set(d.historico_tendencia.map(h => h.rodada))]
  const serie = dim => rodadas.map(r => (d.historico_tendencia.find(h => h.rodada === r && h.dimensao === dim) || {}).total ?? null)
  box.innerHTML = rodadas.length ? `<div class="panel"><h2>${esc(V('forecast'))} do ano por rodada</h2>${grafico([{ nome: 'Financeiro', valores: serie('fin') }, { nome: 'Econômico', valores: serie('econ'), cor: '#7c3aed' }], rodadas.map(r => (d.historico_tendencia.find(h => h.rodada === r) || {}).nome || r))}</div>` +
    tabela([{ t: V('rodada'), k: 'nome' }, { t: 'Dimensão', h: h => esc(st.meta.dimensoes[h.dimensao]) }, { t: 'Total / Dez', n: 1, h: h => fmtD(h.dimensao, h.total) }, { t: 'Desvios', wrap: 1, h: h => (h.desvios || []).map(x => `${esc(x.categoria)}: ${fmtD(h.dimensao, x.valor)}`).join('<br>') || '—' }, { t: 'Justificativa', wrap: 1, k: 'justificativa' }], d.historico_tendencia) : '<div class="note">Nenhuma rodada fechada para este projeto ainda.</div>'
}

// ───────────── Rodadas
async function telaRodadas(m) {
  const rs = await api('GET', '/api/rodadas'), aberta = rs.find(r => r.status === 'aberta')
  const sit = aberta ? await api('GET', `/api/rodadas/${aberta.id}/situacao`) : null
  const hoje = new Date()
  m.innerHTML = `<h1>Rodadas de ${esc(V('forecast').toLowerCase())}</h1><p class="lede">Abra a rodada do mês, acompanhe o envio dos gerentes, aprove ou devolva e feche. Ao fechar, a ${esc(V('forecast').toLowerCase())} vira oficial e é publicada no hub para o Analisador e para o ${esc(V('plano_sigla'))}.</p>
    ${pmo() && !aberta ? `<div class="panel"><div class="filtros"><label class="f">Ano<input class="in" id="rA" type="number" value="${hoje.getFullYear()}" style="width:100px"></label><label class="f">Mês de referência (último fechado)<select class="in" id="rM">${opcoes(st.meta.meses.map((x, i) => ({ v: i + 1, l: x })), Math.max(1, hoje.getMonth()))}</select></label><label class="f">Nome<input class="in" id="rN" placeholder="opcional"></label><button class="btn pri" id="rAbrir">Abrir rodada</button></div></div>` : ''}
    ${aberta ? `<div class="panel"><div class="row"><h2 style="margin:0">${esc(aberta.nome)} · aberta</h2><span class="spacer"></span><button class="btn" id="rExp">Exportar .xlsx</button>${pmo() ? '<button class="btn pri" id="rFechar">Fechar e publicar</button>' : ''}</div>
      <p class="small muted">Projetos sem ${esc(V('forecast').toLowerCase())} aprovada ao fechar mantêm a referência anterior e ficam marcados como “sem atualização”.</p>
      ${tabela([{ t: 'Projeto', wrap: 1, h: p => `<a href="#/projeto/${encodeURIComponent(p.codigo)}/tendencia"><b>${esc(p.nome)}</b></a><div class="small muted">${esc(p.codigo)} · ${esc(p.unidade || '')} · ${esc(p.gerente || '')}</div>` },
        ...['fin', 'econ', 'fis'].map(dim => ({ t: st.meta.dimensoes[dim], h: p => tagT((p.dims[dim] || {}).status || 'nova') })),
        { t: '', h: p => pmo() && Object.values(p.dims).some(x => x.status === 'enviada') ? `<button class="btn mini ok" data-ap="${esc(p.codigo)}">Aprovar todas</button>` : '' }], sit.projetos, { max: 520 })}</div>` : ''}
    <h2>Histórico</h2>${tabela([{ t: V('rodada'), k: 'nome' }, { t: 'Referência', h: r => `${st.meta.meses[r.mes_ref - 1]}/${r.ano}` }, { t: 'Situação', h: r => `<span class="tag ${r.status === 'aberta' ? 'warn' : 'ok'}">${esc(r.status)}</span>` },
      { t: 'Aprovadas', n: 1, h: r => r.situacao.aprovada || 0 }, { t: 'Sem atualização', n: 1, h: r => r.situacao.sem_atualizacao || 0 }, { t: 'Fechada', h: r => r.fechada_em ? dh(r.fechada_em) + ' · ' + esc(r.fechada_por) : '—' },
      { t: 'Publicação', h: r => r.publicacao ? (r.publicacao.rodada ? `<span class="tag ok">publicada</span> ${r.publicacao.execucao} proj.` : `<span class="tag bad">falhou</span>`) : '—' },
      { t: '', h: r => r.status === 'fechada' ? `<button class="btn mini" data-json="${esc(r.id)}" title="Para importar no Analisador fora do hub">JSON</button>` + (pmo() ? ` <button class="btn mini" data-rep="${esc(r.id)}">Republicar</button>` : '') : '' }], rs, { vazio: 'Nenhuma rodada.' })}`
  const ab = $('#rAbrir'); if (ab) ab.onclick = async () => { try { await api('POST', '/api/rodadas', { ano: $('#rA').value, mes_ref: $('#rM').value, nome: $('#rN').value }); toast('Rodada aberta.'); K.render() } catch (e) { toast(e.message) } }
  const fe = $('#rFechar'); if (fe) fe.onclick = async () => { if (!confirm('Fechar a rodada e publicar a ' + V('forecast').toLowerCase() + ' oficial?')) return; try { const r = await api('POST', `/api/rodadas/${aberta.id}/fechar`); toast(`Rodada fechada. ${r.publicacao.rodada ? 'Publicada no hub' : 'Falha ao publicar'} · ${r.sem_atualizacao} série(s) sem atualização.`); K.render() } catch (e) { toast(e.message) } }
  const ex = $('#rExp'); if (ex) ex.onclick = () => K.baixarUrl(`/api/exportar/tendencia.xlsx?rodada=${aberta.id}`, `tendencia-${aberta.id}.xlsx`).catch(e => toast(e.message))
  m.querySelectorAll('[data-ap]').forEach(b => b.onclick = async () => { try { await api('POST', `/api/rodadas/${aberta.id}/tendencias/${encodeURIComponent(b.dataset.ap)}/decidir`, { decisao: 'aprovar' }); toast('Aprovado.'); K.render() } catch (e) { toast(e.message) } })
  m.querySelectorAll('[data-json]').forEach(b => b.onclick = () => K.baixarUrl(`/api/rodadas/${b.dataset.json}/snapshot`, `rodada-${b.dataset.json}.json`).catch(e => toast(e.message)))
  m.querySelectorAll('[data-rep]').forEach(b => b.onclick = async () => { try { const r = await api('POST', `/api/rodadas/${b.dataset.rep}/republicar`); toast(r.rodada ? 'Republicada.' : 'Falhou: ' + r.erros.join('; ')); K.render() } catch (e) { toast(e.message) } })
}

// ───────────── Mudanças e atas (visão da carteira)
async function telaMudancas(m) {
  const ms = await api('GET', '/api/mudancas')
  m.innerHTML = `<h1>Mudanças</h1><p class="lede">Registro e decisão das mudanças de escopo, prazo e custo de toda a carteira.</p>` +
    tabela([{ t: 'Projeto', wrap: 1, h: x => `<a href="#/projeto/${encodeURIComponent(x.codigo)}/mudancas">${esc(x.projeto)}</a><div class="small muted">${esc(x.unidade || '')}</div>` }, { t: 'Mudança', wrap: 1, h: x => `<b>${esc(x.titulo)}</b><div class="small muted">${esc(x.tipo || '')}</div>` },
      { t: 'Custo', n: 1, h: x => din(x.impacto_custo) }, { t: 'Prazo (dias)', n: 1, k: 'impacto_prazo_dias' }, { t: 'Situação', h: x => `<span class="tag ${{ aprovada: 'ok', implementada: 'ok', rejeitada: 'bad' }[x.status] || 'warn'}">${esc(x.status)}</span>` },
      { t: 'Solicitação', h: x => esc(x.solicitado_por) + `<div class="small muted">${dh(x.solicitado_em)}</div>` }, { t: '', h: x => pmo() ? acoesMudanca(x) : '' }], ms, { vazio: 'Nenhuma mudança.' })
  ligarMudancas(m)
}
async function telaAtas(m) {
  const [atas, acoes] = await Promise.all([api('GET', '/api/atas'), api('GET', '/api/acoes')])
  m.innerHTML = `<h1>Atas e ações</h1><p class="lede">Reuniões de status, comitês e lições aprendidas, com acompanhamento das ações.</p>
    ${['pmo', 'gerente'].includes(st.user.perfil) ? '<div class="row" style="margin-bottom:10px"><button class="btn pri" id="nAta">Nova ata da carteira</button></div>' : ''}
    <div class="grid2"><div><h2>Atas</h2>${tabelaAtas(atas)}</div><div><h2>Ações abertas (${acoes.length})</h2>${tabelaAcoes(acoes)}</div></div>`
  ligarAtas(m, atas, null); ligarAcoes(m)
  const n = $('#nAta'); if (n) n.onclick = () => editorAta(null, null)
}

// ───────────── Importações e integrações
async function telaImportacoes(m) {
  const cargas = await api('GET', '/api/cargas')
  m.innerHTML = `<h1>Importações</h1><p class="lede">${st.meta.modo === 'hub' ? 'No ecossistema, projetos, orçamento e plano chegam sozinhos pelas integrações. Use as planilhas para o que vem de fora (realizado e compromissos do ERP, carteira legada).' : 'Modelos genéricos: qualquer sistema de origem (ERP, planilhas, cronogramas) entra convertendo seus dados para estes formatos.'}</p>
    <div class="grid3">${[['projetos', 'Cadastro de projetos', 'Código, nome, unidade, área, gerente, etapa, situação, datas.'], ['curvas', 'Curvas mensais', 'Orçado, realizado e compromisso por mês — econômico, financeiro e físico (%).'], ['anos_futuros', 'Orçado de anos futuros', 'Valor anual de orçado para os anos seguintes.']].map(([t, n, d]) => `<div class="panel"><h2>${n}</h2><p class="small muted">${d}</p><div class="row"><button class="btn" data-mod="${t}">Baixar modelo</button>${pmo() ? `<label class="btn pri">Importar<input type="file" accept=".xlsx" data-imp="${t}" hidden></label>` : ''}</div></div>`).join('')}</div>
    <h2>Cargas realizadas</h2>${tabela([{ t: 'Quando', h: x => dh(x.em) }, { t: 'Tipo', k: 'tipo' }, { t: 'Arquivo', k: 'arquivo' }, { t: 'Linhas', n: 1, k: 'linhas' }, { t: 'Erros', wrap: 1, h: x => x.resultado.length ? esc(x.resultado.slice(0, 5).join('; ')) + (x.resultado.length > 5 ? ` (+${x.resultado.length - 5})` : '') : '—' }, { t: 'Por', k: 'por' }], cargas, { vazio: 'Nenhuma carga.' })}`
  m.querySelectorAll('[data-mod]').forEach(b => b.onclick = () => K.baixarUrl(`/api/modelos/${b.dataset.mod}.xlsx`, `modelo-${b.dataset.mod}.xlsx`).catch(e => toast(e.message)))
  m.querySelectorAll('[data-imp]').forEach(i => i.onchange = async () => { const fd = new FormData(); fd.append('arquivo', i.files[0]); try { const r = await api('POST', `/api/importar/${i.dataset.imp}`, fd, true); toast(`${r.importadas} linha(s) importada(s)${r.erros.length ? ', ' + r.erros.length + ' com erro' : ''}.`); K.render() } catch (e) { toast(e.message) } })
}
async function telaIntegracoes(m) {
  if (!pmo()) { m.innerHTML = '<h1>Integrações</h1><div class="note">Área do PMO.</div>'; return }
  const r = await api('GET', '/api/integracoes/pendentes')
  m.innerHTML = `<h1>Integrações</h1><p class="lede">Entradas pelo Data Core do hub: ${esc(V('termo_abertura'))} aprovado (Originação) cria o projeto com orçamento, curva e PDF; o orçamento aprovado do ${esc(V('plano_sigla'))} atualiza o orçado da carteira em execução. As saídas (rodadas de ${esc(V('forecast').toLowerCase())}) são publicadas ao fechar cada rodada.</p>
    ${r.modo !== 'hub' ? '<div class="note">Modo isolado: o Data Core é local.</div>' : ''}
    <div class="cards"><div class="card warn"><div class="v">${r.tap.length}</div><div class="l">${esc(V('termo_abertura'))} aprovados a receber</div></div><div class="card warn"><div class="v">${r.plano.length}</div><div class="l">Orçamentos do ${esc(V('plano_sigla'))} a receber</div></div></div>
    <div class="panel"><div class="row"><h2 style="margin:0">Pendentes</h2><span class="spacer"></span><button class="btn pri" id="iSync" ${r.tap.length + r.plano.length ? '' : 'disabled'}>Receber agora</button></div>
    ${r.modo === 'hub' ? '<div class="note ok">Recebimento automático: a carteira busca as entradas ao abrir e a cada 2 minutos — inclusive rascunhos de ata vindos da agenda e ordens de serviço vinculadas a projetos.</div>' : ''}
    <div class="grid2" style="margin-top:10px">${tabela([{ t: V('termo_abertura'), k: 'chave' }, { t: 'Projeto', wrap: 1, k: 'nome' }], r.tap, { vazio: 'Nada pendente.' })}${tabela([{ t: V('plano_sigla'), k: 'chave' }, { t: 'Projeto', wrap: 1, k: 'nome' }, { t: 'Decisão', k: 'status' }], r.plano, { vazio: 'Nada pendente.' })}</div></div>`
  $('#iSync').onclick = async () => { try { const x = await api('POST', '/api/integracoes/sincronizar'); toast(`Recebidos: ${x.tap.length} ${V('termo_abertura')}, ${x.plano.length} orçamentos, ${x.atas.length} rascunhos de ata.`); K.render() } catch (e) { toast(e.message) } }
}

K.iniciar({
  nome: 'PMP · Carteira de Projetos', inicio: 'carteira',
  aoSincronizar: async () => { },
  nav: () => [['carteira', 'Carteira'], ['rodadas', 'Rodadas de ' + V('forecast').toLowerCase()], ['mudancas', 'Mudanças'], ['atas', 'Atas e ações'], ['importacoes', 'Importações'], pmo() && ['integracoes', 'Integrações'], K.eAdmin() && ['usuarios', 'Usuários e perfis']],
  rotas: { carteira: telaCarteira, projeto: telaProjeto, rodadas: telaRodadas, mudancas: telaMudancas, atas: telaAtas, importacoes: telaImportacoes, integracoes: telaIntegracoes, usuarios: K.telaUsuarios }
})
