/* Plano de Investimentos — interface */
'use strict'
const { $, esc, api, V, din, nf, pct, dh, toast, modal, pedir, tabela, grafico, opcoes, unidades, areas, st } = K
const FASE = { preparacao: 'Preparação (base)', pleitos: 'Pleitos', comite: 'Comitê', aprovado: 'Aprovado', encerrado: 'Encerrado' }
const ST = { proposto: ['Proposto', ''], aprovado: ['Aprovado', 'ok'], cortado: ['Cortado', 'bad'], postergado: ['Postergado', 'warn'], devolvido: ['Devolvido', 'bad'] }
const ORIG = { originacao: 'Originação', carteira: 'Carteira (em execução)', importacao: 'Planilha', manual: 'Manual' }
let CICLOS = [], CICLO = null
const chaveCiclo = 'plano:ciclo'
async function carregarCiclos() {
  CICLOS = await api('GET', '/api/ciclos')
  let id = ''; try { id = localStorage.getItem(chaveCiclo) || '' } catch (e) { }
  CICLO = CICLOS.find(c => c.id === id) || CICLOS[0] || null
}
function escolherCiclo(id) { CICLO = CICLOS.find(c => c.id === id) || CICLO; try { localStorage.setItem(chaveCiclo, CICLO.id) } catch (e) { } K.render() }
const pmo = () => st.user.perfil === 'pmo'
function cabCiclo(titulo, extra = '') {
  return `<div class="row" style="margin-bottom:6px"><h1 style="margin:0">${esc(titulo)}</h1><span class="spacer"></span>
    <label class="f">Ciclo<select class="in" id="selCiclo">${opcoes(CICLOS.map(c => ({ v: c.id, l: c.nome })), CICLO && CICLO.id)}</select></label>${extra}</div>
    ${CICLO ? `<p class="lede">${esc(CICLO.ano_inicio)}–${esc(CICLO.ano_fim)} · fase <b>${esc(FASE[CICLO.fase] || CICLO.fase)}</b>${CICLO.rodada ? ' · rodada ' + CICLO.rodada : ''} · ${CICLO.projetos} projeto(s)</p>` : ''}`
}
function ligarCiclo() { const s = $('#selCiclo'); if (s) s.onchange = e => escolherCiclo(e.target.value) }
function semCiclo(m) {
  m.innerHTML = `<h1>${esc(V('plano_nome'))}</h1><p class="lede">Nenhum ciclo cadastrado ainda.</p>
    <div class="panel"><p>Crie o primeiro ciclo em <a href="#/ciclos">Ciclos</a>${pmo() ? ' ou carregue um exemplo para conhecer a ferramenta.' : '.'}</p>${pmo() ? '<button class="btn pri" id="demo">Carregar ciclo de exemplo</button>' : ''}</div>`
  const b = $('#demo'); if (b) b.onclick = async () => { try { const r = await api('POST', '/api/demo'); await carregarCiclos(); escolherCiclo(r.ciclo_id); toast('Ciclo de exemplo criado.') } catch (e) { toast(e.message) } }
}
const camadaPadrao = () => !CICLO ? 'base' : CICLO.fase === 'preparacao' ? 'base' : CICLO.fase === 'aprovado' || CICLO.fase === 'encerrado' ? 'aprovado' : 'pleito'
const tagSt = s => { const [l, c] = ST[s] || [s, '']; return `<span class="tag ${c}">${esc(l)}</span>` }

// ───────────── Painel
async function telaPainel(m) {
  if (!CICLO) return semCiclo(m)
  const cam = camadaPadrao()
  const p = await api('GET', `/api/ciclos/${CICLO.id}/painel?camada=${cam === 'aprovado' ? 'aprovado' : cam}`)
  const anos = p.anos, rot = anos.map(String)
  const unis = Object.entries(p.por_unidade).sort((a, b) => Object.values(b[1]).reduce((x, y) => x + y, 0) - Object.values(a[1]).reduce((x, y) => x + y, 0))
  m.innerHTML = cabCiclo('Painel do ciclo') + `
    <div class="cards">
      <div class="card"><div class="v">${din(p.total)}</div><div class="l">Total ${cam === 'base' ? 'da base' : cam === 'aprovado' ? 'aprovado' : 'pleiteado'} (${p.projetos} projetos)</div></div>
      <div class="card"><div class="v">${din(p.base.total)}</div><div class="l">Base do ciclo (imutável)</div></div>
      <div class="card ${p.total > p.base.total ? 'bad' : 'ok'}"><div class="v">${din(p.total - p.base.total)}</div><div class="l">Variação sobre a base</div></div>
      <div class="card"><div class="v">${din(p.vpl)}</div><div class="l">VPL a ${pct(CICLO.taxa_desconto)} a.a.</div></div>
      <div class="card warn"><div class="v">${p.pleitos_pendentes}</div><div class="l">Pleitos aguardando revisão</div></div>
    </div>
    <div class="grid2"><div class="panel"><h2>Por ano</h2>${grafico([{ nome: 'Base', valores: anos.map(a => p.base.por_ano[a]), tipo: 'barra', cor: '#94a3b8' }, { nome: cam === 'aprovado' ? 'Aprovado' : cam === 'base' ? 'Base' : 'Pleito', valores: anos.map(a => p.por_ano[a]), tipo: 'barra' }], rot, { barras: true })}</div>
    <div class="panel"><h2>Por ${esc(V('unidade').toLowerCase())}</h2>${unis.map(([u, v]) => { const t = Object.values(v).reduce((a, b) => a + b, 0); return `<div style="margin-bottom:8px"><div class="row small"><span>${esc(u)}</span><span class="spacer"></span><b>${din(t)}</b></div><div class="bar"><span style="width:${p.total ? 100 * t / p.total : 0}%"></span></div></div>` }).join('') || '<p class="muted">Sem valores.</p>'}</div></div>
    <div class="grid2"><div class="panel"><h2>Por ${esc(V('area').toLowerCase())}</h2>${tabela([{ t: V('area'), k: 'a' }, { t: 'Total', n: 1, h: r => din(r.v) }], Object.entries(p.por_area).map(([a, v]) => ({ a, v })).sort((a, b) => b.v - a.v))}</div>
    <div class="panel"><h2>Origem e situação</h2>${tabela([{ t: 'Origem', h: r => esc(ORIG[r.o] || r.o) }, { t: 'Total', n: 1, h: r => din(r.v) }], Object.entries(p.por_origem).map(([o, v]) => ({ o, v })))}
      <p class="small" style="margin-top:10px">${Object.entries(p.status).map(([s, n]) => tagSt(s) + ' ' + n).join(' &nbsp; ')}</p></div></div>`
  ligarCiclo()
}

// ───────────── Ciclos
async function telaCiclos(m) {
  m.innerHTML = `<h1>Ciclos de planejamento</h1><p class="lede">Cada ciclo tem uma base imutável, rodadas de pleitos, cenários de comitê e um plano aprovado.</p>` +
    tabela([{ t: 'Ciclo', h: c => `<a href="#/painel" data-c="${esc(c.id)}">${esc(c.nome)}</a>` }, { t: 'Anos', h: c => `${c.ano_inicio}–${c.ano_fim}` }, { t: 'Fase', h: c => esc(FASE[c.fase]) },
    { t: 'Rodada', k: 'rodada', n: 1 }, { t: 'Projetos', k: 'projetos', n: 1 }, { t: 'Taxa', n: 1, h: c => pct(c.taxa_desconto) },
    { t: '', h: c => pmo() ? acoesFase(c) : '' }], CICLOS, { vazio: 'Nenhum ciclo.' }) +
    (pmo() ? `<div class="panel" style="margin-top:16px"><h2>Novo ciclo</h2><div class="filtros">
      <label class="f">Nome<input class="in" id="cNome" placeholder="${esc(V('plano_sigla'))} 2027–2031"></label>
      <label class="f">Ano inicial<input class="in" id="cIni" type="number" value="${(st.meta.organizacao.ano_base || 2026) + 1}" style="width:100px"></label>
      <label class="f">Ano final<input class="in" id="cFim" type="number" value="${(st.meta.organizacao.ano_base || 2026) + (st.meta.organizacao.horizonte_anos || 5)}" style="width:100px"></label>
      <label class="f">Taxa de desconto a.a.<input class="in" id="cTaxa" type="number" step="0.001" value="${st.meta.organizacao.taxa_desconto_aa || 0.1}" style="width:110px"></label>
      <button class="btn pri" id="cCriar">Criar ciclo</button><button class="btn" id="demo">Carregar exemplo</button></div></div>` : '')
  m.querySelectorAll('[data-c]').forEach(a => a.onclick = () => escolherCiclo(a.dataset.c))
  m.querySelectorAll('[data-fase]').forEach(b => b.onclick = async () => {
    const [id, fase] = b.dataset.fase.split('|')
    if (!confirm(`Mudar o ciclo para "${FASE[fase]}"?` + (fase === 'pleitos' && b.dataset.de === 'preparacao' ? '\n\nA base fica congelada (imutável) e abre a rodada 1 de pleitos.' : ''))) return
    try { await api('POST', `/api/ciclos/${id}/fase`, { fase }); await carregarCiclos(); K.render(); toast('Fase alterada.') } catch (e) { toast(e.message) }
  })
  const cc = $('#cCriar'); if (cc) cc.onclick = async () => { try { const r = await api('POST', '/api/ciclos', { nome: $('#cNome').value, ano_inicio: $('#cIni').value, ano_fim: $('#cFim').value, taxa_desconto: $('#cTaxa').value }); await carregarCiclos(); escolherCiclo(r.id); toast('Ciclo criado.') } catch (e) { toast(e.message) } }
  const d = $('#demo'); if (d) d.onclick = async () => { try { const r = await api('POST', '/api/demo'); await carregarCiclos(); escolherCiclo(r.ciclo_id); toast('Ciclo de exemplo criado.') } catch (e) { toast(e.message) } }
}
function acoesFase(c) {
  const prox = { preparacao: [['pleitos', 'Abrir pleitos']], pleitos: [['comite', 'Enviar ao comitê']], comite: [['pleitos', 'Nova rodada de pleitos']], aprovado: [['encerrado', 'Encerrar']] }[c.fase] || []
  return prox.map(([f, l]) => `<button class="btn mini" data-fase="${esc(c.id)}|${f}" data-de="${esc(c.fase)}">${esc(l)}</button>`).join(' ')
}

// ───────────── Carteira (base / pleito / cenário)
let filtro = { unidade: '', area: '', origem: '', busca: '' }
async function telaCarteira(m, partes) {
  if (!CICLO) return semCiclo(m)
  const cenarios = await api('GET', `/api/ciclos/${CICLO.id}/cenarios`)
  let cam = partes[0] || camadaPadrao()
  if (cam === 'aprovado') cam = CICLO.cenario_aprovado ? 'c:' + CICLO.cenario_aprovado : 'pleito'
  const camadas = cam === 'base' ? 'base' : 'base,' + cam
  const d = await api('GET', `/api/ciclos/${CICLO.id}/carteira?camadas=${encodeURIComponent(camadas)}`)
  const cen = cam.startsWith('c:') ? cenarios.find(x => 'c:' + x.id === cam) : null
  const editavel = (cam === 'base' && CICLO.fase === 'preparacao' && pmo()) || (cam === 'pleito' && CICLO.fase === 'pleitos' && ['pmo', 'gestor'].includes(st.user.perfil)) || (cen && !cen.congelado && CICLO.fase === 'comite' && pmo())
  const ps = d.projetos.filter(p => p.status !== 'devolvido' && (!filtro.unidade || p.unidade === filtro.unidade) && (!filtro.area || p.area === filtro.area) && (!filtro.origem || p.origem === filtro.origem) && (!filtro.busca || (p.nome + ' ' + p.codigo + ' ' + p.plano_id).toLowerCase().includes(filtro.busca.toLowerCase())))
  const podeLinha = p => editavel && !(cam === 'pleito' && st.user.perfil === 'gestor' && st.user.unidade && p.unidade !== st.user.unidade)
  const tot = {}; d.anos.forEach(a => tot[a] = ps.reduce((s, p) => s + (p.valores[cam][a] || 0), 0))
  const totBase = ps.reduce((s, p) => s + Object.values(p.valores.base).reduce((a, b) => a + b, 0), 0)
  const opCam = [{ v: 'base', l: 'Base (imutável)' }, { v: 'pleito', l: 'Pleito' }, ...cenarios.map(c => ({ v: 'c:' + c.id, l: 'Cenário: ' + c.nome + (c.aprovado ? ' ✓' : c.congelado ? ' (congelado)' : '') }))]
  m.innerHTML = cabCiclo('Carteira do ciclo', `<label class="f">Visão<select class="in" id="selCam">${opcoes(opCam, cam)}</select></label>`) + `
    ${cen && cen.congelado ? '<div class="note">Cenário congelado: somente leitura.</div>' : ''}
    ${!editavel ? '<div class="note">Somente leitura nesta fase/perfil.' + (cam === 'pleito' && CICLO.fase === 'preparacao' ? ' Os pleitos abrem quando o PMO congela a base.' : '') + '</div>' : cam === 'pleito' ? '<div class="note warn">Rodada ' + CICLO.rodada + ' de pleitos aberta: altere os valores e informe a justificativa. Cada mudança fica registrada.</div>' : ''}
    <div class="filtros">
      <label class="f">${esc(V('unidade'))}<select class="in" id="fUni">${opcoes(unidades(), filtro.unidade, 'Todas')}</select></label>
      <label class="f">${esc(V('area'))}<select class="in" id="fArea">${opcoes(areas(), filtro.area, 'Todas')}</select></label>
      <label class="f">Origem<select class="in" id="fOrig">${opcoes(Object.entries(ORIG).map(([v, l]) => ({ v, l })), filtro.origem, 'Todas')}</select></label>
      <label class="f">Busca<input class="in" id="fBusca" value="${esc(filtro.busca)}" placeholder="nome, código, ID"></label>
      <span class="spacer"></span>
      ${pmo() && CICLO.fase === 'preparacao' && st.meta.modo !== 'hub' ? '<button class="btn" id="bModelo">Modelo .xlsx</button><label class="btn">Importar base<input type="file" id="fImp" accept=".xlsx" hidden></label>' : ''}
      ${pmo() && !['aprovado', 'encerrado'].includes(CICLO.fase) ? '<button class="btn" id="bNovo">+ Projeto</button>' : ''}
      <button class="btn" id="bExp">Exportar .xlsx</button></div>
    ${tabela([
      { t: 'ID', h: p => `<span title="${esc(p.codigo || '')}">${esc(p.plano_id)}</span>` },
      { t: 'Projeto', wrap: 1, h: p => `<b>${esc(p.nome)}</b><div class="small muted">${esc(p.unidade || '—')} · ${esc(p.area || '—')} · ${esc(ORIG[p.origem] || p.origem)}${p.codigo ? ' · ' + esc(p.codigo) : ''}</div>` },
      { t: 'Situação', h: p => tagSt(p.status) + (cen && cen.decisoes[p.plano_id] ? ` <span class="tag warn">${esc(cen.decisoes[p.plano_id].decisao)}</span>` : '') },
      ...d.anos.map(a => ({ t: String(a), n: 1, h: p => { const v = p.valores[cam][a] || 0, b = p.valores.base[a] || 0; return podeLinha(p) ? `<input class="cel${Math.round(v) !== Math.round(b) && cam !== 'base' ? ' alt' : ''}" data-p="${esc(p.plano_id)}" data-a="${a}" value="${nf(v)}" title="Base: ${nf(b)}">` : `<span${Math.round(v) !== Math.round(b) && cam !== 'base' ? ' class="tag warn"' : ''} title="Base: ${nf(b)}">${nf(v)}</span>` } })),
      { t: 'Total', n: 1, h: p => `<b>${din(Object.values(p.valores[cam]).reduce((a, b) => a + b, 0))}</b>` },
      { t: '', h: p => (cen && !cen.congelado && pmo() && CICLO.fase === 'comite' ? `<button class="btn mini" data-dec="${esc(p.plano_id)}">Decidir</button> ` : '') + `<button class="btn mini" data-hist="${esc(p.plano_id)}">Histórico</button>` + (pmo() && p.origem === 'originacao' && !['aprovado', 'encerrado'].includes(CICLO.fase) ? ` <button class="btn mini" data-dev="${esc(p.plano_id)}">Devolver</button>` : '') }
    ], ps, { total: { ...Object.fromEntries(d.anos.map(a => [String(a), nf(tot[a])])), 'Total': din(Object.values(tot).reduce((a, b) => a + b, 0)) }, max: 640, vazio: 'Nenhum projeto com estes filtros.' })}
    <p class="small muted">${ps.length} projeto(s) · base dos filtrados: ${din(totBase)} · valores anuais nominais.</p>
    ${pmo() && CICLO.fase === 'preparacao' && st.meta.modo === 'hub' ? `<details class="conting"><summary>Contingência: carregar a base por planilha</summary><p class="small muted">No hub, a base chega sozinha da Originação e dos projetos em execução da PMP. Use a planilha só para projetos que ainda não estão no ecossistema.</p><div class="row"><button class="btn" id="bModelo">Modelo .xlsx</button><label class="btn">Importar base<input type="file" id="fImp" accept=".xlsx" hidden></label></div></details>` : ''}`
  // total no rodapé usa chaves k; ajusta cabeçalho
  ligarCiclo()
  $('#selCam').onchange = e => { location.hash = '#/carteira/' + encodeURIComponent(e.target.value) }
  $('#fUni').onchange = e => { filtro.unidade = e.target.value; K.render() }; $('#fArea').onchange = e => { filtro.area = e.target.value; K.render() }
  $('#fOrig').onchange = e => { filtro.origem = e.target.value; K.render() }
  $('#fBusca').onchange = e => { filtro.busca = e.target.value; K.render() }
  $('#bExp').onclick = () => K.baixarUrl(`/api/ciclos/${CICLO.id}/exportar.xlsx?camadas=${encodeURIComponent(['base', 'pleito', ...cenarios.map(c => 'c:' + c.id)].join(','))}`, `plano-${CICLO.id}.xlsx`).catch(e => toast(e.message))
  const bm = $('#bModelo'); if (bm) bm.onclick = () => K.baixarUrl(`/api/ciclos/${CICLO.id}/modelo.xlsx`, `modelo-base-${CICLO.id}.xlsx`).catch(e => toast(e.message))
  const fi = $('#fImp'); if (fi) fi.onchange = async () => { const fd = new FormData(); fd.append('arquivo', fi.files[0]); try { const r = await api('POST', `/api/ciclos/${CICLO.id}/importar`, fd, true); toast(`Base importada: ${r.incluidos} novos, ${r.atualizados} atualizados.`); await carregarCiclos(); K.render() } catch (e) { toast(e.message) } }
  const bn = $('#bNovo'); if (bn) bn.onclick = () => novoProjeto(d.anos)
  // edição de células
  m.querySelectorAll('input.cel').forEach(inp => {
    inp.onfocus = () => { inp.value = String(Math.round(parseNum(inp.value))) ; inp.select() }
    inp.onkeydown = e => { if (e.key === 'Enter') inp.blur(); if (e.key === 'Escape') { inp.dataset.cancel = 1; inp.blur() } }
    inp.onblur = async () => {
      const p = d.projetos.find(x => x.plano_id === inp.dataset.p), a = inp.dataset.a, antigo = p.valores[cam][a] || 0, novo = parseNum(inp.value)
      if (inp.dataset.cancel || Math.round(novo) === Math.round(antigo)) { delete inp.dataset.cancel; inp.value = nf(antigo); return }
      let just = ''
      if (cam === 'pleito') { just = await pedir('Justificativa do pleito', `${p.nome} · ${a}: ${nf(antigo)} → ${nf(novo)}`, true); if (just === null) { inp.value = nf(antigo); return } }
      try { await api('PUT', `/api/ciclos/${CICLO.id}/valores`, { plano_id: p.plano_id, camada: cam, valores: { [a]: novo }, justificativa: just }); toast('Valor atualizado.'); K.render() } catch (e) { toast(e.message); inp.value = nf(antigo) }
    }
  })
  m.querySelectorAll('[data-hist]').forEach(b => b.onclick = () => historico(b.dataset.hist))
  m.querySelectorAll('[data-dec]').forEach(b => b.onclick = () => decidir(cen, d.projetos.find(p => p.plano_id === b.dataset.dec)))
  m.querySelectorAll('[data-dev]').forEach(b => b.onclick = async () => { const c = await pedir('Devolver à Originação', 'O projeto sai do ciclo e volta para a Originação com o motivo informado.', true); if (c === null) return; try { await api('POST', `/api/ciclos/${CICLO.id}/projetos/${b.dataset.dev}/devolver`, { comentario: c }); toast('Projeto devolvido.'); await carregarCiclos(); K.render() } catch (e) { toast(e.message) } })
}
const parseNum = s => { s = String(s).trim(); if (/,\d{1,2}$/.test(s) || /\.\d{3}/.test(s)) s = s.replace(/\./g, '').replace(',', '.'); const n = parseFloat(s.replace(/[^\d.-]/g, '')); return isNaN(n) ? 0 : n }
function novoProjeto(anos) {
  const d = modal(`<h2>Novo projeto no ciclo</h2><div class="grid2"><label class="f">Nome<input class="in" id="nNome"></label><label class="f">${esc(V('codigo_erp'))}<input class="in" id="nCod"></label>
    <label class="f">${esc(V('unidade'))}<select class="in" id="nUni">${opcoes(unidades(), '')}</select></label><label class="f">${esc(V('area'))}<select class="in" id="nArea">${opcoes(areas(), '')}</select></label>
    <label class="f">Categoria<select class="in" id="nCat">${opcoes(st.meta.categorias || [], '')}</select></label><label class="f">Prioridade<input class="in" id="nPri" type="number" value="2"></label></div>
    <h3>Valores por ano</h3><div class="row">${anos.map(a => `<label class="f">${a}<input class="in" data-ano="${a}" type="number" value="0" style="width:110px"></label>`).join('')}</div>
    <div class="row" style="justify-content:flex-end;margin-top:14px"><button class="btn" id="nC">Cancelar</button><button class="btn pri" id="nO">Incluir</button></div>`)
  d.querySelector('#nC').onclick = () => d.close()
  d.querySelector('#nO').onclick = async () => {
    const valores = {}; d.querySelectorAll('[data-ano]').forEach(i => valores[i.dataset.ano] = parseFloat(i.value) || 0)
    try { await api('POST', `/api/ciclos/${CICLO.id}/projetos`, { nome: d.querySelector('#nNome').value, codigo: d.querySelector('#nCod').value, unidade: d.querySelector('#nUni').value, area: d.querySelector('#nArea').value, categoria: d.querySelector('#nCat').value, prioridade: d.querySelector('#nPri').value, valores }); d.close(); toast('Projeto incluído.'); await carregarCiclos(); K.render() } catch (e) { toast(e.message) }
  }
}
async function historico(pid) {
  const alts = await api('GET', `/api/ciclos/${CICLO.id}/alteracoes?plano_id=${encodeURIComponent(pid)}`)
  const d = modal(`<h2>Histórico · ${esc(pid)}</h2>` + tabela([{ t: 'Rodada', k: 'rodada' }, { t: 'Camada', k: 'camada' }, { t: 'Ano', k: 'ano' }, { t: 'De', n: 1, h: a => nf(a.de) }, { t: 'Para', n: 1, h: a => nf(a.para) }, { t: 'Justificativa', wrap: 1, k: 'justificativa' }, { t: 'Por', k: 'por' }, { t: 'Em', h: a => dh(a.em) }], alts, { vazio: 'Sem alterações.', max: 420 }) + `<div class="row" style="justify-content:flex-end;margin-top:12px"><button class="btn" id="hF">Fechar</button></div>`)
  d.querySelector('#hF').onclick = () => d.close()
}
function decidir(cen, p) {
  const d = modal(`<h2>Decisão do comitê</h2><p><b>${esc(p.nome)}</b><br><span class="muted">${esc(p.unidade || '')} · total no cenário ${din(Object.values(p.valores['c:' + cen.id]).reduce((a, b) => a + b, 0))}</span></p>
    <div class="row"><label class="f">Decisão<select class="in" id="dD"><option value="manter">Manter como está</option><option value="cortar">Cortar (zerar)</option><option value="postergar">Postergar</option></select></label>
    <label class="f">Anos (postergação)<input class="in" id="dA" type="number" value="1" min="1" style="width:90px"></label></div>
    <label class="f" style="margin-top:10px">Observação<input class="in" id="dO"></label>
    <div class="row" style="justify-content:flex-end;margin-top:14px"><button class="btn" id="dC">Cancelar</button><button class="btn pri" id="dOk">Aplicar</button></div>`)
  d.querySelector('#dC').onclick = () => d.close()
  d.querySelector('#dOk').onclick = async () => { try { await api('POST', `/api/ciclos/${CICLO.id}/cenarios/${cen.id}/decisao`, { plano_id: p.plano_id, decisao: d.querySelector('#dD').value, anos: d.querySelector('#dA').value, obs: d.querySelector('#dO').value }); d.close(); toast('Decisão aplicada.'); K.render() } catch (e) { toast(e.message) } }
}

// ───────────── Pleitos
async function telaPleitos(m) {
  if (!CICLO) return semCiclo(m)
  const alts = (await api('GET', `/api/ciclos/${CICLO.id}/alteracoes`)).filter(a => a.camada === 'pleito')
  const pend = alts.filter(a => a.status === 'registrada')
  m.innerHTML = cabCiclo('Pleitos da rodada') + `<p class="lede">Cada alteração da carteira na camada de pleito, com justificativa. O PMO aceita, pede revisão ou rejeita (rejeitar devolve o valor anterior).</p>
    <div class="cards"><div class="card warn"><div class="v">${pend.length}</div><div class="l">Aguardando revisão</div></div><div class="card"><div class="v">${alts.length}</div><div class="l">Pleitos registrados</div></div>
    <div class="card"><div class="v">${din(alts.reduce((s, a) => s + (a.para - a.de), 0))}</div><div class="l">Efeito líquido dos pleitos</div></div></div>` +
    tabela([{ t: 'Rodada', k: 'rodada', n: 1 }, { t: 'Projeto', wrap: 1, h: a => `<b>${esc(a.projeto || a.plano_id)}</b><div class="small muted">${esc(a.unidade || '')}</div>` }, { t: 'Ano', k: 'ano' },
    { t: 'De', n: 1, h: a => nf(a.de) }, { t: 'Para', n: 1, h: a => nf(a.para) }, { t: 'Dif.', n: 1, h: a => `<span class="${a.para > a.de ? 'pos' : 'neg'}">${nf(a.para - a.de)}</span>` },
    { t: 'Justificativa', wrap: 1, k: 'justificativa' }, { t: 'Por', h: a => esc(a.por) + `<div class="small muted">${dh(a.em)}</div>` },
    { t: 'Situação', h: a => `<span class="tag ${{ aceito: 'ok', rejeitado: 'bad', revisar: 'warn' }[a.status] || ''}">${esc(a.status)}</span>` },
    { t: '', h: a => pmo() && a.status === 'registrada' ? `<button class="btn mini ok" data-rev="${a.seq}|aceito">Aceitar</button> <button class="btn mini" data-rev="${a.seq}|revisar">Revisar</button> <button class="btn mini bad" data-rev="${a.seq}|rejeitado">Rejeitar</button>` : '' }], alts, { vazio: 'Nenhum pleito registrado.', max: 700 })
  ligarCiclo()
  m.querySelectorAll('[data-rev]').forEach(b => b.onclick = async () => { const [seq, decisao] = b.dataset.rev.split('|'); try { await api('POST', `/api/ciclos/${CICLO.id}/alteracoes/${seq}/revisar`, { decisao }); toast('Pleito ' + decisao + '.'); K.render() } catch (e) { toast(e.message) } })
}

// ───────────── Cenários e aprovação
async function telaCenarios(m) {
  if (!CICLO) return semCiclo(m)
  const cs = await api('GET', `/api/ciclos/${CICLO.id}/cenarios`)
  const metas = await api('GET', `/api/ciclos/${CICLO.id}/metas`)
  const podeAprovar = ['comite', 'pmo'].includes(st.user.perfil) && CICLO.fase === 'comite'
  m.innerHTML = cabCiclo('Comitê e cenários') + `<p class="lede">Durante o comitê o PMO cria cenários a partir do pleito (ou de outro cenário), decide projeto a projeto (manter, cortar, postergar) e congela. O comitê aprova um cenário congelado, que vira o plano oficial e é publicado.</p>
    ${CICLO.fase !== 'comite' ? `<div class="note">${CICLO.fase === 'aprovado' ? 'Ciclo aprovado.' : 'O ciclo precisa estar na fase Comitê para criar e aprovar cenários.'}</div>` : ''}
    ${pmo() && CICLO.fase === 'comite' ? `<div class="panel"><div class="filtros"><label class="f">Nome<input class="in" id="sNome" placeholder="Cenário A"></label><label class="f">A partir de<select class="in" id="sOrig">${opcoes([{ v: 'pleito', l: 'Pleito' }, ...cs.map(c => ({ v: 'c:' + c.id, l: c.nome }))], 'pleito')}</select></label><button class="btn pri" id="sCriar">Criar cenário</button></div></div>` : ''}` +
    tabela([{ t: 'Cenário', h: c => `<b>${esc(c.nome)}</b>${c.aprovado ? ' <span class="tag ok">aprovado</span>' : c.congelado ? ' <span class="tag pri">congelado</span>' : ' <span class="tag">em edição</span>'}<div class="small muted">a partir de ${esc(c.origem)} · ${esc(c.criado_por)} · ${dh(c.criado_em)}</div>` },
    { t: 'Total', n: 1, h: c => din(c.total) }, { t: 'Decisões', n: 1, h: c => Object.keys(c.decisoes).length },
    { t: '', h: c => `<a class="btn mini" href="#/carteira/${encodeURIComponent('c:' + c.id)}">Abrir</a> <a class="btn mini" href="#/comparar/pleito/${encodeURIComponent('c:' + c.id)}">Comparar</a>` +
      (pmo() && !c.congelado && CICLO.fase === 'comite' ? ` <button class="btn mini" data-cong="${c.id}">Congelar</button> <button class="btn mini bad" data-del="${c.id}">Excluir</button>` : '') +
      (podeAprovar && c.congelado && !c.aprovado ? ` <button class="btn mini ok" data-apr="${c.id}">Aprovar</button>` : '') }], cs, { vazio: 'Nenhum cenário.' }) + `
    <div class="panel" style="margin-top:16px"><h2>Metas de corte por ${esc(V('unidade').toLowerCase())}</h2><p class="muted small">Registre o corte pedido pelo comitê; a comparação mostra se cada ${esc(V('unidade').toLowerCase())} atingiu a meta.</p>
    <div class="grid3">${unidades().map(u => { const mm = metas.find(x => x.escopo === 'unidade' && x.chave === u); return `<label class="f">${esc(u)} (% de corte)<input class="in" data-meta="${esc(u)}" type="number" step="0.5" value="${mm ? mm.percentual * 100 : ''}" ${['pmo', 'comite'].includes(st.user.perfil) ? '' : 'disabled'}></label>` }).join('')}</div>
    ${['pmo', 'comite'].includes(st.user.perfil) ? '<div class="row" style="margin-top:10px"><button class="btn" id="mSalvar">Salvar metas</button></div>' : ''}</div>`
  ligarCiclo()
  const sc = $('#sCriar'); if (sc) sc.onclick = async () => { try { const r = await api('POST', `/api/ciclos/${CICLO.id}/cenarios`, { nome: $('#sNome').value, origem: $('#sOrig').value }); location.hash = '#/carteira/' + encodeURIComponent('c:' + r.id); toast('Cenário criado.') } catch (e) { toast(e.message) } }
  m.querySelectorAll('[data-cong]').forEach(b => b.onclick = async () => { if (!confirm('Congelar o cenário? Depois disso ele não pode ser alterado.')) return; try { await api('POST', `/api/ciclos/${CICLO.id}/cenarios/${b.dataset.cong}/congelar`); K.render() } catch (e) { toast(e.message) } })
  m.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => { if (!confirm('Excluir o cenário?')) return; try { await api('DELETE', `/api/ciclos/${CICLO.id}/cenarios/${b.dataset.del}`); K.render() } catch (e) { toast(e.message) } })
  m.querySelectorAll('[data-apr]').forEach(b => b.onclick = async () => {
    if (!confirm('Aprovar este cenário como plano oficial do ciclo?\n\nO resultado é publicado no hub: projetos da Originação recebem o retorno (aprovado/cancelado) e a carteira em execução recebe o novo orçamento na PMP.')) return
    try { const r = await api('POST', `/api/ciclos/${CICLO.id}/cenarios/${b.dataset.apr}/aprovar`, {}); toast(`Aprovado. Publicados: ${r.publicacao.publicados}${r.publicacao.falhas ? ' · falhas: ' + r.publicacao.falhas : ''}.`); await carregarCiclos(); K.render() } catch (e) { toast(e.message) }
  })
  const ms = $('#mSalvar'); if (ms) ms.onclick = async () => { const metas = [...m.querySelectorAll('[data-meta]')].map(i => ({ escopo: 'unidade', chave: i.dataset.meta, percentual: i.value === '' ? null : parseFloat(i.value) / 100 })); try { await api('PUT', `/api/ciclos/${CICLO.id}/metas`, { metas }); toast('Metas salvas.') } catch (e) { toast(e.message) } }
}

// ───────────── Comparação e ponte
async function telaComparar(m, partes) {
  if (!CICLO) return semCiclo(m)
  const cs = await api('GET', `/api/ciclos/${CICLO.id}/cenarios`)
  const a = partes[0] || 'base', b = partes[1] || (cs.length ? 'c:' + cs[cs.length - 1].id : CICLO.fase === 'preparacao' ? 'base' : 'pleito')
  const op = [{ v: 'base', l: 'Base' }, { v: 'pleito', l: 'Pleito' }, ...cs.map(c => ({ v: 'c:' + c.id, l: c.nome }))]
  const r = await api('GET', `/api/ciclos/${CICLO.id}/comparar?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`)
  const pt = r.ponte, nomeA = (op.find(x => x.v === a) || {}).l, nomeB = (op.find(x => x.v === b) || {}).l
  const passos = [[nomeA, r.total_a], ['Novos', pt.novos], ['Aumentos', pt.aumentos], ['Reduções', pt.reducoes], ['Cortes', pt.cortes], [nomeB, r.total_b]]
  m.innerHTML = cabCiclo('Comparação de cenários', `<label class="f">A<select class="in" id="cA">${opcoes(op, a)}</select></label><label class="f">B<select class="in" id="cB">${opcoes(op, b)}</select></label>`) + `
    <div class="cards"><div class="card"><div class="v">${din(r.total_a)}</div><div class="l">${esc(nomeA)}</div></div><div class="card"><div class="v">${din(r.total_b)}</div><div class="l">${esc(nomeB)}</div></div>
      <div class="card ${r.total_b > r.total_a ? 'bad' : 'ok'}"><div class="v">${din(r.total_b - r.total_a)}</div><div class="l">Diferença (${pct(r.total_a ? (r.total_b - r.total_a) / r.total_a : 0)})</div></div>
      <div class="card warn"><div class="v">${din(pt.postergacoes)}</div><div class="l">Deslocado do 1º ano por postergações</div></div></div>
    <div class="panel"><h2>Ponte ${esc(nomeA)} → ${esc(nomeB)}</h2>${ponte(passos)}</div>
    <div class="panel"><h2>Por ${esc(V('unidade').toLowerCase())} e metas de corte</h2>${tabela([{ t: V('unidade'), k: 'unidade' }, { t: nomeA, n: 1, h: u => din(u.a) }, { t: nomeB, n: 1, h: u => din(u.b) }, { t: 'Corte', n: 1, h: u => pct(u.corte_pct) }, { t: 'Meta', n: 1, h: u => u.meta_pct == null ? '—' : pct(u.meta_pct) }, { t: '', h: u => u.meta_ok == null ? '' : u.meta_ok ? '<span class="tag ok">atingida</span>' : '<span class="tag bad">abaixo da meta</span>' }], r.unidades)}</div>
    <div class="panel"><h2>Projetos que mudaram</h2>${tabela([{ t: 'Projeto', wrap: 1, h: p => `<b>${esc(p.nome)}</b><div class="small muted">${esc(p.plano_id)} · ${esc(p.unidade)}</div>` }, { t: 'Tipo', h: p => `<span class="tag">${esc(p.tipo)}</span>` }, { t: nomeA, n: 1, h: p => din(p.a) }, { t: nomeB, n: 1, h: p => din(p.b) }, { t: 'Diferença', n: 1, h: p => `<span class="${p.dif > 0 ? 'pos' : 'neg'}">${din(p.dif)}</span>` }], r.projetos, { vazio: 'Nenhuma diferença.', max: 520 })}</div>`
  ligarCiclo()
  $('#cA').onchange = $('#cB').onchange = () => { location.hash = `#/comparar/${encodeURIComponent($('#cA').value)}/${encodeURIComponent($('#cB').value)}` }
}
function ponte(passos) {
  const W = 760, H = 230, pl = 70, pb = 34, n = passos.length, bw = (W - pl - 20) / n * .62
  let acum = 0; const barras = passos.map(([l, v], i) => { const ini = i === 0 || i === n - 1 ? 0 : acum; const fim = i === 0 || i === n - 1 ? v : acum + v; if (i === 0) acum = v; else if (i < n - 1) acum += v; return { l, v, ini, fim, tot: i === 0 || i === n - 1 } })
  const max = Math.max(...barras.map(b => Math.max(b.ini, b.fim)), 1), y = v => 10 + (max - v) * (H - pb - 10) / max, x = i => pl + i * (W - pl - 20) / n + ((W - pl - 20) / n - bw) / 2
  return `<svg class="chart" viewBox="0 0 ${W} ${H}">${barras.map((b, i) => `<rect x="${x(i)}" y="${y(Math.max(b.ini, b.fim))}" width="${bw}" height="${Math.max(1, Math.abs(y(b.ini) - y(b.fim)))}" fill="${b.tot ? 'var(--pri)' : b.v >= 0 ? '#C2410C' : '#2A7647'}"><title>${esc(b.l)}: ${esc(din(b.v))}</title></rect>
    <text x="${x(i) + bw / 2}" y="${y(Math.max(b.ini, b.fim)) - 4}" text-anchor="middle" font-size="11" fill="#1D2433">${esc(din(b.v))}</text><text x="${x(i) + bw / 2}" y="${H - 12}" text-anchor="middle" font-size="11" fill="#667085">${esc(b.l)}</text>`).join('')}</svg>`
}

// ───────────── Risco de execução (Monte Carlo)
async function telaRisco(m) {
  if (!CICLO) return semCiclo(m)
  const cam = camadaPadrao()
  const r = await api('GET', `/api/ciclos/${CICLO.id}/risco-execucao?camada=${cam === 'aprovado' ? 'aprovado' : cam}`)
  m.innerHTML = cabCiclo('Risco de execução') + `<p class="lede">Qual a probabilidade de executar o planejado para ${r.ano}? Simulação Monte Carlo com reamostragem (bootstrap) da execução real histórica (realizado ÷ orçado por projeto e ano) publicada pela PMP.</p>` +
    (!r.suficiente ? `<div class="note warn">${esc(r.mensagem)} (amostras: ${r.amostras})</div><p class="muted">Planejado para ${r.ano}: <b>${din(r.planejado)}</b>.</p>` : `
    <div class="cards"><div class="card"><div class="v">${din(r.planejado)}</div><div class="l">Planejado ${r.ano}</div></div><div class="card"><div class="v">${din(r.p50)}</div><div class="l">Execução provável (P50)</div></div>
      <div class="card"><div class="v">${din(r.p10)} – ${din(r.p90)}</div><div class="l">Faixa P10–P90</div></div><div class="card ${r.prob_atingir['1.0'] < .5 ? 'bad' : 'ok'}"><div class="v">${pct(r.prob_atingir['1.0'], 0)}</div><div class="l">Probabilidade de executar 100%</div></div></div>
    <div class="grid2"><div class="panel"><h2>Distribuição simulada (${nf(r.simulacoes)} simulações, ${r.amostras} observações)</h2>${grafico([{ nome: 'Frequência', valores: r.histograma.map(h => h.n), tipo: 'barra' }], r.histograma.map(h => din((h.de + h.ate) / 2)), { barras: true, fmt: v => nf(v) })}</div>
    <div class="panel"><h2>Probabilidade de atingir</h2>${tabela([{ t: '% do planejado', h: x => pct(+x[0], 0) }, { t: 'Valor', n: 1, h: x => din(+x[0] * r.planejado) }, { t: 'Probabilidade', n: 1, h: x => `<b>${pct(x[1], 0)}</b>` }], Object.entries(r.prob_atingir))}</div></div>`)
  ligarCiclo()
}

// ───────────── Integrações
async function telaIntegracoes(m) {
  if (!pmo()) { m.innerHTML = '<h1>Integrações</h1><div class="note">Área do PMO.</div>'; return }
  const r = await api('GET', '/api/integracoes/pendentes')
  m.innerHTML = `<h1>Integrações</h1><p class="lede">Entradas e saídas pelo Data Core do hub: novos projetos da Originação, projetos em execução (tendência) da PMP e a publicação do plano aprovado.</p>
    ${r.modo !== 'hub' ? '<div class="note">Modo isolado: o Data Core é local (útil para testes). No hub, as outras ferramentas enxergam os mesmos registros.</div>' : ''}
    <div class="cards"><div class="card warn"><div class="v">${r.originacao.length}</div><div class="l">Novos projetos da Originação</div></div><div class="card warn"><div class="v">${r.execucao.length}</div><div class="l">Atualizações de projetos em execução</div></div>
    <div class="card"><div class="v">${r.publicacoes.filter(p => p.status === 'publicado').length}</div><div class="l">Publicações recentes</div></div></div>
    <div class="panel"><div class="row"><h2 style="margin:0">Receber</h2><span class="spacer"></span><label class="f">Ciclo de destino<select class="in" id="iCiclo">${opcoes(CICLOS.filter(c => ['preparacao', 'pleitos', 'comite'].includes(c.fase)).map(c => ({ v: c.id, l: c.nome })), CICLO && CICLO.id, 'Automático (pelos anos)')}</select></label><button class="btn pri" id="iSync" ${r.originacao.length + r.execucao.length ? '' : 'disabled'}>Receber agora</button></div>
    ${r.modo === 'hub' ? '<div class="note ok">Recebimento automático: o Plano busca as entradas ao abrir e a cada 2 minutos. O que estiver aqui é o que ainda não tem ciclo de destino aberto.</div>' : ''}
    <p class="small muted">Na preparação, as entradas vão para a base; com pleitos ou comitê abertos, entram como pleito (aparecem como “novos” na comparação).</p>
    <div class="grid2"><div>${tabela([{ t: 'Originação', h: x => esc(x.id_mestre) }, { t: 'Projeto', wrap: 1, k: 'nome' }, { t: V('unidade'), k: 'unidade' }], r.originacao, { vazio: 'Nada pendente.' })}</div>
    <div>${tabela([{ t: 'Carteira', h: x => esc(x.codigo) }, { t: 'Projeto', wrap: 1, k: 'nome' }, { t: V('rodada'), k: 'rodada' }], r.execucao, { vazio: 'Nada pendente.' })}</div></div></div>
    <div class="panel"><div class="row"><h2 style="margin:0">Publicações</h2><span class="spacer"></span>${CICLO && ['aprovado', 'encerrado'].includes(CICLO.fase) ? '<button class="btn" id="iRep">Republicar ciclo aprovado</button>' : ''}</div>
    ${tabela([{ t: 'Quando', h: p => dh(p.em) }, { t: 'Ciclo', k: 'ciclo_id' }, { t: 'Destino', k: 'destino' }, { t: 'Chave', k: 'chave' }, { t: 'Situação', h: p => `<span class="tag ${p.status === 'publicado' ? 'ok' : 'bad'}">${esc(p.status)}</span>` }, { t: 'Detalhe', wrap: 1, k: 'detalhe' }], r.publicacoes, { vazio: 'Nenhuma publicação ainda.', max: 420 })}</div>`
  $('#iSync').onclick = async () => { try { const x = await api('POST', '/api/integracoes/sincronizar', { ciclo_id: $('#iCiclo').value || null }); toast(`Recebidos: ${x.originacao.length} da Originação, ${x.execucao.length} da carteira.`); await carregarCiclos(); K.render() } catch (e) { toast(e.message) } }
  const rp = $('#iRep'); if (rp) rp.onclick = async () => { try { const x = await api('POST', `/api/ciclos/${CICLO.id}/republicar`); toast(`Publicados: ${x.publicados}.`); K.render() } catch (e) { toast(e.message) } }
}

// ───────────── Índices
async function telaIndices(m) {
  const idx = await api('GET', '/api/indices')
  const ini = CICLO ? CICLO.ano_inicio : (st.meta.organizacao.ano_base || 2026), fim = CICLO ? CICLO.ano_fim + 5 : ini + 10
  const anos = []; for (let a = Math.min(ini, ...idx.map(i => i.ano)); a <= Math.max(fim, ...idx.map(i => i.ano)); a++) anos.push(a)
  m.innerHTML = `<h1>Índices de inflação</h1><p class="lede">Usados para converter valores nominais em valores reais (econômicos) no painel. Informe a inflação anual esperada (ex.: 0,045 = 4,5%).</p>
    <div class="panel"><div class="grid3">${anos.map(a => { const i = idx.find(x => x.ano === a); return `<label class="f">${a}<input class="in" data-ano="${a}" type="number" step="0.001" value="${i ? i.inflacao : ''}" ${pmo() ? '' : 'disabled'}></label>` }).join('')}</div>
    ${pmo() ? '<div class="row" style="margin-top:12px"><button class="btn pri" id="xSalvar">Salvar índices</button></div>' : ''}</div>`
  const b = $('#xSalvar'); if (b) b.onclick = async () => { const indices = [...m.querySelectorAll('[data-ano]')].filter(i => i.value !== '').map(i => ({ ano: +i.dataset.ano, inflacao: parseFloat(i.value) })); try { await api('PUT', '/api/indices', { indices }); toast('Índices salvos.') } catch (e) { toast(e.message) } }
}

K.iniciar({
  nome: 'Plano de Investimentos', inicio: 'painel',
  aoIniciar: async () => { await carregarCiclos(); document.title = V('plano_nome') },
  aoSincronizar: async () => { await carregarCiclos() },
  nav: () => [['painel', 'Painel'], ['ciclos', 'Ciclos'], ['carteira', 'Carteira do ciclo'], ['pleitos', 'Pleitos'], ['cenarios', 'Comitê e cenários'], ['comparar', 'Comparação'], ['risco', 'Risco de execução'],
  pmo() && ['integracoes', 'Integrações'], ['indices', 'Índices'], K.eAdmin() && ['usuarios', 'Usuários e perfis']],
  rotas: { painel: telaPainel, ciclos: telaCiclos, carteira: telaCarteira, pleitos: telaPleitos, cenarios: telaCenarios, comparar: telaComparar, risco: telaRisco, integracoes: telaIntegracoes, indices: telaIndices, usuarios: K.telaUsuarios }
})
