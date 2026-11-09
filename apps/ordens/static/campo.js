/* Modo execução (PWA) — tela de campo do executor: OS do dia, como chegar, iniciar (cronômetro), checklist com fotos,
   materiais, pausa e conclusão com aceite do cliente. Sem sinal, as ações entram numa fila no aparelho e são enviadas
   na ordem quando a conexão volta (com a hora real do toque). */
'use strict'
const { esc, api, st, dh, toast, nf } = K
const $c = s => document.querySelector(s)
const LS = { get: (k, d) => { try { const v = localStorage.getItem('campo:' + k); return v ? JSON.parse(v) : d } catch (e) { return d } }, set: (k, v) => { try { localStorage.setItem('campo:' + k, JSON.stringify(v)) } catch (e) { } } }
const GESTAO = ['gestor', 'supervisor', 'planejador']
const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const hh = s => s ? new Date(s).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : ''

// ── fila offline (IndexedDB: guarda também as fotos)
const DB = (() => {
  let db = null
  const abrir = () => db ? Promise.resolve(db) : new Promise((res, rej) => { const r = indexedDB.open('campo-os', 1); r.onupgradeneeded = () => r.result.createObjectStore('fila', { keyPath: 'id', autoIncrement: true }); r.onsuccess = () => { db = r.result; res(db) }; r.onerror = () => rej(r.error) })
  const tx = async (modo, fn) => { const d = await abrir(); return new Promise((res, rej) => { const t = d.transaction('fila', modo), s = t.objectStore('fila'); const r = fn(s); t.oncomplete = () => res(r && r.result); t.onerror = () => rej(t.error) }) }
  return { add: x => tx('readwrite', s => s.add(x)), todos: () => tx('readonly', s => s.getAll()), del: id => tx('readwrite', s => s.delete(id)) }
})()
let FILA = []
async function recarregarFila() { try { FILA = await DB.todos() } catch (e) { FILA = [] } desenharFila() }
function desenharFila() {
  const b = $c('#cFila'), erros = LS.get('erros', [])
  b.hidden = !FILA.length && !erros.length
  b.innerHTML = (FILA.length ? `<span>⏳ ${FILA.length} ação(ões) aguardando envio${navigator.onLine ? '' : ' (sem conexão)'}</span>${navigator.onLine ? '<button id="cEnv">Enviar agora</button>' : ''}` : '') +
    (erros.length ? `<div class="cerr">⚠ ${erros.length} ação(ões) recusada(s) pelo servidor:<ul>${erros.slice(-4).map(e => `<li>${esc(e.rotulo)} — ${esc(e.msg)}</li>`).join('')}</ul><button id="cLimpa">Entendi</button></div>` : '')
  const e = $c('#cEnv'); if (e) e.onclick = () => enviar(true)
  const l = $c('#cLimpa'); if (l) l.onclick = () => { LS.set('erros', []); desenharFila() }
  $c('#cNet').textContent = navigator.onLine ? '' : 'sem conexão'
}
async function enfileirar(acao) { await DB.add({ ...acao, criado: new Date().toISOString() }); await recarregarFila(); enviar() }
let enviando = false
async function enviar(manual) {
  if (enviando || !navigator.onLine) return
  enviando = true; let mudou = false
  try {
    for (const x of await DB.todos()) {
      try {
        if (x.tipo === 'foto') { const fd = new FormData(); fd.append('arquivo', x.arquivo, x.nome || 'foto.jpg'); fd.append('etapa', x.etapa); if (x.seq) fd.append('checklist_seq', x.seq); await api('POST', `/api/ordens/${encodeURIComponent(x.numero)}/anexos`, fd, true) }
        else await api(x.metodo, x.url, x.corpo)
        await DB.del(x.id); mudou = true
      } catch (e) {
        if (!e.status) break  // rede: tenta depois, mantendo a ordem
        const erros = LS.get('erros', []); erros.push({ rotulo: x.rotulo || x.url, msg: e.message, em: new Date().toISOString() }); LS.set('erros', erros.slice(-20))
        await DB.del(x.id); mudou = true
      }
    }
  } finally { enviando = false }
  await recarregarFila()
  if (mudou && !FILA.length) { await atualizar(); render() } else if (manual && !FILA.length) toast('Tudo enviado.')
}

// ── dados (com cópia local para abrir sem sinal)
let LISTA = LS.get('lista', []), OS = LS.get('os', {})
async function atualizar() {
  try {
    LISTA = await api('GET', '/api/ordens?minhas=true'); LS.set('lista', LISTA)
    const ativos = LISTA.filter(o => ['planejada', 'em_execucao', 'pausada'].includes(o.status))
    for (const o of ativos) { try { OS[o.numero] = await api('GET', '/api/ordens/' + encodeURIComponent(o.numero)) } catch (e) { } }
    for (const k of Object.keys(OS)) if (!LISTA.some(o => o.numero === k)) delete OS[k]
    LS.set('os', OS); LS.set('atualizado', new Date().toISOString())
  } catch (e) { if (e.status) toast(e.message) }
}
async function obterOS(n) {
  if (navigator.onLine && !FILA.some(x => x.numero === n)) { try { OS[n] = await api('GET', '/api/ordens/' + encodeURIComponent(n)); LS.set('os', OS) } catch (e) { if (e.status === 404 || e.status === 403) throw e } }
  if (!OS[n]) throw new Error('OS não disponível sem conexão. Abra-a uma vez com sinal.')
  return OS[n]
}
const salvarLocal = o => { OS[o.numero] = o; LS.set('os', OS) }

// ── telas
function render() {
  const h = (location.hash || '#/').slice(2).split('/').map(decodeURIComponent)
  clearInterval(render.t)
  if (h[0] === 'os' && h[1]) return telaOS(h[1]).catch(e => { $c('#c').innerHTML = `<p><a href="#/">← Minhas OS</a></p><div class="note bad">${esc(e.message)}</div>` })
  telaLista()
}
function telaLista() {
  const hoje = iso(new Date()), lim = iso(new Date(Date.now() + 7 * 864e5))
  const ativos = LISTA.filter(o => ['planejada', 'em_execucao', 'pausada'].includes(o.status))
  const agora = ativos.filter(o => o.status !== 'planejada' || (o.execucao_inicio && iso(new Date(o.execucao_inicio)) <= hoje)).sort((a, b) => (a.execucao_inicio || '').localeCompare(b.execucao_inicio || ''))
  const prox = ativos.filter(o => !agora.includes(o) && o.execucao_inicio && iso(new Date(o.execucao_inicio)) <= lim).sort((a, b) => a.execucao_inicio.localeCompare(b.execucao_inicio))
  const aguard = LISTA.filter(o => o.status === 'concluida' && o.aprovacao === 'pendente')
  const at = LS.get('atualizado')
  const card = o => `<a class="cos ${esc(o.status)}" href="#/os/${encodeURIComponent(o.numero)}"><div class="cos-h"><b>${o.execucao_inicio ? hh(o.execucao_inicio) : '—'}</b><span>${esc(o.numero)}</span><span class="tag ${o.status === 'em_execucao' ? 'ok' : o.status === 'pausada' ? 'warn' : 'pri'}">${esc(o.status_nome)}</span></div>
    <div class="cos-t">${esc(o.titulo)}</div><div class="cos-m">${esc(o.local || o.unidade || '')}${o.executor ? ' · executor ' + esc(o.executor) : ''}</div></a>`
  $c('#c').innerHTML = `<h1>Olá, ${esc((st.user.nome || '').split(' ')[0])}</h1><p class="muted small">${at ? 'Atualizado ' + dh(at) : ''}${navigator.onLine ? ' · <a href="#" id="cAtu">atualizar</a>' : ' · sem conexão: mostrando a última cópia'}</p>
    <h2>Hoje</h2>${agora.map(card).join('') || '<p class="muted">Nenhuma OS para hoje.</p>'}
    ${prox.length ? `<h2>Próximos dias</h2>${prox.map(o => card(o).replace('<b>', `<b>${new Date(o.execucao_inicio).toLocaleDateString('pt-BR', { weekday: 'short', day: '2-digit' })} `)).join('')}` : ''}
    ${aguard.length ? `<h2>Aguardando aprovação</h2>${aguard.map(o => `<div class="cos concluida"><div class="cos-t">${esc(o.numero)} · ${esc(o.titulo)}</div></div>`).join('')}` : ''}`
  const a = $c('#cAtu'); if (a) a.onclick = async e => { e.preventDefault(); await enviar(); await atualizar(); render() }
}
const ETAPA = { antes: 'do antes', depois: 'do depois' }
const fotosDe = (o, filtro) => o.anexos.filter(filtro).length + FILA.filter(x => x.tipo === 'foto' && x.numero === o.numero && filtro({ etapa: x.etapa, checklist_seq: x.seq ? Number(x.seq) : null })).length
async function telaOS(n) {
  const o = await obterOS(n), r = o.regras || {}, ex = o.eu.executor || (GESTAO.includes(st.user.perfil) && o.eu.pode_executar)
  const tel = String(o.contato || '').replace(/[^\d+]/g, ''), destino = o.local || o.unidade || ''
  const exe = (o.participantes.find(p => p.executor) || {}).nome
  const fer = o.feriado && o.feriado.situacao === 'aguardando_gestor'
  const pend = FILA.filter(x => x.numero === n).length
  let h = `<p><a href="#/">← Minhas OS</a></p><div class="chead"><div class="small">${esc(o.numero)} · ${esc(o.tipo_nome)} · ${esc(o.prioridade_nome)}</div><h1>${esc(o.titulo)}</h1>
    <div>${o.execucao_inicio ? dh(o.execucao_inicio) + (o.duracao_h ? ` · ${nf(o.duracao_h, 1)} h` : '') : ''}</div><div class="small">${esc(destino)}${o.ativo ? ' · ' + esc(o.ativo) : ''}</div>
    <div class="cbtns">${destino ? `<a class="cbtn" target="_blank" rel="noopener" href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(destino)}">🧭 Como chegar</a>` : ''}${tel.length >= 8 ? `<a class="cbtn" href="tel:${esc(tel)}">📞 Ligar</a>` : ''}</div></div>
    ${pend ? `<div class="note small">⏳ ${pend} ação(ões) desta OS aguardando envio.</div>` : ''}
    <section class="csec"><h2>Equipe</h2><p>${o.participantes.map(p => esc(p.nome) + (p.executor ? ' <span class="tag pri">executor</span>' : '')).join(', ') || '—'}</p>${o.descricao ? `<h2>Escopo</h2><p>${esc(o.descricao)}</p>` : ''}${o.contato ? `<p class="small">Contato: ${esc(o.contato)}</p>` : ''}
    ${r.orientacoes ? `<div class="note warn">⚠ ${esc(r.orientacoes)}</div>` : ''}${o.anexos_modelo.map(a => `<a href="#" class="small" data-axm="${esc(a.id)}" data-nome="${esc(a.nome)}">📎 ${esc(a.nome)}</a> `).join('')}</section>`
  if (!ex) {
    h += `<div class="note">Você acompanha esta OS. Quem registra a execução é ${esc(exe || 'o executor')}.</div><section class="csec"><h2>Checklist</h2>${o.itens_checklist.map(i => `<div class="cck${i.feito ? ' feito' : ''}">${i.feito ? '✔' : '○'} ${esc(i.item)}</div>`).join('')}</section>`
    $c('#c').innerHTML = h; ligarAnexos(); return
  }
  if (['planejada', 'pausada'].includes(o.status)) {
    const fa = fotosDe(o, a => a.etapa === 'antes')
    h += `<section class="csec"><h2>${o.status === 'pausada' ? 'Retomar' : 'Antes de começar'}</h2>${o.status === 'pausada' && o.motivo ? `<div class="note warn">${o.aprovacao === 'devolvida' ? 'Devolvida pelo supervisor' : 'Pausada'}: ${esc(o.motivo)}</div>` : ''}
      ${r.fotos_antes_min ? `<p>📷 Fotos do antes: <b>${fa}/${r.fotos_antes_min}</b></p>${botaoFoto('antes')}` : ''}
      ${fer ? '<div class="note warn">Execução em feriado aguardando o de acordo do gestor de serviços. Ainda não é possível iniciar.</div>' : `<button class="cbig ok" id="cIni" ${r.fotos_antes_min && fa < r.fotos_antes_min && o.status === 'planejada' ? 'disabled' : ''}>▶ ${o.status === 'pausada' ? 'Retomar' : 'Iniciar'} execução</button>${r.fotos_antes_min && fa < r.fotos_antes_min && o.status === 'planejada' ? '<p class="small muted">Tire as fotos do antes para iniciar.</p>' : ''}`}</section>`
  }
  if (o.status === 'em_execucao') {
    h += `<section class="csec crono"><div>⏱ Em execução há <b id="cT">—</b></div><button class="cbtn" id="cPau">⏸ Pausar</button></section>`
    h += `<section class="csec"><h2>Checklist</h2>${o.itens_checklist.map(i => itemHtml(o, i)).join('') || '<p class="muted">Sem itens.</p>'}</section>`
    if (o.materiais.length) h += `<section class="csec"><h2>Materiais</h2>${o.materiais.map(m => `<div class="cmat"><div><b>${esc(m.descricao)}</b><div class="small muted">previsto ${nf(m.qtd_prevista, 2)} ${esc(m.unidade_medida || '')} · ${esc(st.meta.status_material[m.status])}${m.status === 'consumido' ? ` (${nf(m.qtd_real, 2)})` : ''}</div></div>
      ${['a_separar', 'separado'].includes(m.status) ? `<div class="cbtns"><button class="cbtn" data-mt="${esc(m.id)}" data-q="tudo">Usei tudo</button><button class="cbtn" data-mt="${esc(m.id)}" data-q="x">Usei…</button><button class="cbtn" data-mt="${esc(m.id)}" data-q="dev">Devolvi</button></div>` : ''}</div>`).join('')}</section>`
    const fd = fotosDe(o, a => a.etapa === 'depois')
    h += `<section class="csec"><h2>Concluir</h2>${r.fotos_depois_min ? `<p>📷 Fotos do depois: <b>${fd}/${r.fotos_depois_min}</b></p>` : ''}${botaoFoto('depois')}
      <label class="f">Relato do que foi feito<textarea class="in" id="cRel" rows="3"></textarea></label>
      <label class="f">Aceite do cliente — nome de quem recebeu${r.exige_aceite ? ' (obrigatório)' : ' (opcional)'}<input class="in" id="cAce"></label>
      <label class="row small"><input type="checkbox" id="cConf"> O cliente confirmou que o serviço foi realizado</label>
      <div id="cFalta"></div><button class="cbig ok" id="cConc">✔ Concluir OS</button><p class="small muted">${r.exige_aprovacao ? 'Depois vai para aprovação do supervisor.' : 'Esta OS encerra ao concluir.'}</p></section>`
  }
  if (o.status === 'concluida') h += `<div class="note">Concluída${o.aprovacao === 'pendente' ? ' · aguardando aprovação do supervisor' : ''}.</div>`
  if (o.status === 'encerrada') h += '<div class="note">OS encerrada. Obrigado!</div>'
  $c('#c').innerHTML = h
  ligarAnexos()
  ligarFotos(o)
  const ini = $c('#cIni'); if (ini) ini.onclick = () => acao(o, 'iniciar', {}, 'Iniciar', x => { x.status = 'em_execucao'; x.cronometro_inicio = new Date().toISOString(); x.cronometro_ativo = true })
  if (o.status === 'em_execucao') {
    const t0 = new Date(o.cronometro_inicio || o.iniciada_em || Date.now()).getTime()
    const tick = () => { const s = Math.max(0, (Date.now() - t0) / 1000), el = $c('#cT'); if (el) el.textContent = `${Math.floor(s / 3600)}h ${String(Math.floor(s % 3600 / 60)).padStart(2, '0')}min` }
    tick(); render.t = setInterval(tick, 20000)
    $c('#cPau').onclick = () => pausar(o)
    document.querySelectorAll('[data-feito]').forEach(b => b.onchange = () => salvarItem(o, Number(b.dataset.feito)))
    document.querySelectorAll('[data-resp],[data-obs]').forEach(b => b.onchange = () => salvarItem(o, Number(b.dataset.resp || b.dataset.obs), true))
    document.querySelectorAll('[data-mt]').forEach(b => b.onclick = () => material(o, b.dataset.mt, b.dataset.q))
    $c('#cConc').onclick = () => concluir(o)
  }
}
const botaoFoto = (etapa, seq) => `<label class="cbtn foto">📷 Foto ${seq ? '' : ETAPA[etapa] || ''}<input type="file" accept="image/*" capture="environment" data-ft="${etapa}" ${seq ? `data-seq="${seq}"` : ''} hidden></label>`
function itemHtml(o, i) {
  const nf_ = fotosDe(o, a => a.checklist_seq === i.seq), v = i.resposta ?? ''
  const resp = i.resposta_tipo === 'simnao' ? `<div class="cbtns"><label class="cop"><input type="radio" name="r${i.seq}" data-resp="${i.seq}" value="sim" ${v === 'sim' ? 'checked' : ''}> Sim</label><label class="cop"><input type="radio" name="r${i.seq}" data-resp="${i.seq}" value="nao" ${v === 'nao' ? 'checked' : ''}> Não</label></div>`
    : i.resposta_tipo === 'numero' ? `<div class="row"><input class="in" inputmode="decimal" data-resp="${i.seq}" value="${esc(v)}" style="width:120px" placeholder="0,0"> ${esc(i.unidade || '')}</div>`
      : i.resposta_tipo === 'texto' ? `<input class="in" data-resp="${i.seq}" value="${esc(v)}" placeholder="resposta" style="width:100%">` : ''
  return `<div class="cck${i.feito ? ' feito' : ''}"><label class="cckh"><input type="checkbox" data-feito="${i.seq}" ${i.feito ? 'checked' : ''}><span>${esc(i.item)}${i.obrigatorio ? '' : ' <span class="tag">opcional</span>'}</span></label>
    ${resp}${i.foto_min ? `<div class="small">📷 ${nf_}/${i.foto_min} ${botaoFoto('item', i.seq)}</div>` : `<div class="small">${botaoFoto('item', i.seq)}</div>`}
    <input class="in cobs" data-obs="${i.seq}" value="${esc(i.obs || '')}" placeholder="observação (opcional)"></div>`
}
function ligarAnexos() { document.querySelectorAll('[data-axm]').forEach(a => a.onclick = ev => { ev.preventDefault(); K.baixarUrl('/api/modelo-anexos/' + a.dataset.axm, a.dataset.nome).catch(x => toast(x.message)) }) }
function ligarFotos(o) {
  document.querySelectorAll('[data-ft]').forEach(inp => inp.onchange = async () => {
    const f = inp.files[0]; if (!f) return
    const arq = await reduzir(f), etapa = inp.dataset.ft, seq = inp.dataset.seq || null
    await enfileirar({ tipo: 'foto', numero: o.numero, etapa, seq, arquivo: arq, nome: arq.name || 'foto.jpg', rotulo: `Foto ${etapa}${seq ? ' (item ' + seq + ')' : ''} · ${o.numero}` })
    toast('Foto registrada.'); render()
  })
}
async function reduzir(arq) {
  if (!arq || !/^image\//.test(arq.type) || arq.size < 400 * 1024) return arq
  try { const img = await createImageBitmap(arq), k = Math.min(1, 1600 / Math.max(img.width, img.height)), c = document.createElement('canvas'); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k); c.getContext('2d').drawImage(img, 0, 0, c.width, c.height); const b = await new Promise(r => c.toBlob(r, 'image/jpeg', .82)); return b ? new File([b], 'foto.jpg', { type: 'image/jpeg' }) : arq } catch (e) { return arq }
}
async function acao(o, nome, corpo, rotulo, local) {
  await enfileirar({ tipo: 'json', numero: o.numero, metodo: 'POST', url: `/api/ordens/${encodeURIComponent(o.numero)}/acoes/${nome}`, corpo: { ...corpo, em: new Date().toISOString() }, rotulo: `${rotulo} · ${o.numero}` })
  if (local) { local(o); salvarLocal(o) }
  render()
}
function salvarItem(o, seq, soTexto) {
  const i = o.itens_checklist.find(x => x.seq === seq), box = document.querySelector(`[data-feito="${seq}"]`).closest('.cck')
  const rr = box.querySelector('[data-resp]:checked') || box.querySelector('input[data-resp]:not([type=radio])'), resp = rr ? rr.value.trim() : null
  const feito = box.querySelector('[data-feito]').checked, obs = box.querySelector('[data-obs]').value
  if (feito && !i.feito) {
    if (i.resposta_tipo !== 'check' && !resp) { toast('Responda o item antes de marcar como feito.'); box.querySelector('[data-feito]').checked = false; return }
    if (i.resposta_tipo === 'numero' && resp && isNaN(Number(resp.replace(',', '.')))) { toast('Informe um número.'); box.querySelector('[data-feito]').checked = false; return }
    if (i.foto_min && fotosDe(o, a => a.checklist_seq === seq) < i.foto_min) { toast(`Este item pede ${i.foto_min} foto(s).`); box.querySelector('[data-feito]').checked = false; return }
  }
  Object.assign(i, { feito: feito ? 1 : 0, obs, ...(rr ? { resposta: resp } : {}) }); salvarLocal(o)
  enfileirar({ tipo: 'json', numero: o.numero, metodo: 'PUT', url: `/api/ordens/${encodeURIComponent(o.numero)}/checklist`, corpo: { itens: [{ seq, feito, obs, ...(rr ? { resposta: resp } : {}) }], parcial: true }, rotulo: `Checklist “${i.item}” · ${o.numero}` })
  if (!soTexto) box.classList.toggle('feito', feito)
}
function material(o, id, q) {
  const m = o.materiais.find(x => x.id === id); let corpo
  if (q === 'tudo') corpo = { status: 'consumido', qtd_real: m.qtd_prevista }
  else if (q === 'dev') corpo = { status: 'devolvido' }
  else { const v = prompt(`Quanto usou de ${m.descricao}? (${m.unidade_medida || 'un'})`, m.qtd_prevista); if (v == null) return; const n = Number(String(v).replace(',', '.')); if (!(n >= 0)) return toast('Quantidade inválida.'); corpo = { status: 'consumido', qtd_real: n } }
  Object.assign(m, { status: corpo.status, qtd_real: corpo.qtd_real || 0 }); salvarLocal(o)
  enfileirar({ tipo: 'json', numero: o.numero, metodo: 'PUT', url: `/api/ordens/${encodeURIComponent(o.numero)}/materiais/${id}`, corpo, rotulo: `Material ${m.descricao} · ${o.numero}` }); render()
}
function pausar(o) {
  const d = K.modal(`<h2>Pausar</h2><p class="muted">Por quê?</p><div class="cbtns col">${st.meta.motivos_pausa.map(x => `<button class="cbtn" data-mot="${esc(x)}">${esc(x)}</button>`).join('')}</div><button class="btn" id="pzC" style="margin-top:10px">Voltar</button>`)
  d.querySelectorAll('[data-mot]').forEach(b => b.onclick = () => { d.close(); acao(o, 'pausar', { motivo: b.dataset.mot }, 'Pausar', x => { x.status = 'pausada'; x.motivo = b.dataset.mot; x.cronometro_inicio = null }) })
  d.querySelector('#pzC').onclick = () => d.close()
}
function concluir(o) {
  const r = o.regras || {}, falta = []
  o.itens_checklist.filter(i => i.obrigatorio).forEach(i => { if (!i.feito) falta.push(i.item) })
  if (o.materiais.some(m => ['a_separar', 'separado'].includes(m.status))) falta.push('dar baixa nos materiais (usei / devolvi)')
  if ((r.fotos_depois_min || 0) > fotosDe(o, a => a.etapa === 'depois')) falta.push(`${r.fotos_depois_min} foto(s) do depois`)
  const nome = $c('#cAce').value.trim()
  if (r.exige_aceite && !nome) falta.push('nome de quem recebeu (aceite)')
  if (nome && !$c('#cConf').checked) falta.push('confirmação do cliente')
  if (falta.length) { $c('#cFalta').innerHTML = `<div class="note bad small">Falta: ${falta.map(esc).join('; ')}</div>`; return }
  if (!confirm('Concluir a OS agora?')) return
  acao(o, 'concluir', { resumo: $c('#cRel').value, aceite_nome: nome }, 'Concluir', x => { x.status = r.exige_aprovacao ? 'concluida' : 'encerrada'; x.aprovacao = r.exige_aprovacao ? 'pendente' : null })
  toast(navigator.onLine ? 'OS concluída.' : 'Conclusão registrada; será enviada quando houver sinal.')
}

// ── início: quem é o usuário (hub ou demonstração) e service worker
async function iniciar() {
  try { st.meta = await api('GET', '/api/meta'); LS.set('meta', st.meta) } catch (e) { st.meta = LS.get('meta', null) }
  if (!st.meta) { $c('#c').innerHTML = '<h1>Sem conexão</h1><p class="muted">Abra o modo execução uma vez com sinal para usar depois sem conexão.</p>'; return }
  if (st.meta.modo === 'hub') {
    try { const s = await api('GET', '/api/sessao'); st.user = s.usuario || s.real; LS.set('user', st.user) } catch (e) { st.user = LS.get('user', st.user) }
  } else {
    let us = []; try { us = await api('GET', '/api/usuarios'); LS.set('usuarios', us) } catch (e) { us = LS.get('usuarios', []) }
    us = us.filter(u => u.origem !== 'hub').sort((a, b) => (a.perfil === 'tecnico' ? 0 : 1) - (b.perfil === 'tecnico' ? 0 : 1))
    const salvo = LS.get('uid'); st.user = us.find(u => u.id === salvo) || us[0] || st.user
    const sel = $c('#cUser'); sel.hidden = false
    sel.innerHTML = us.map(u => `<option value="${esc(u.id)}"${u.id === st.user.id ? ' selected' : ''}>${esc(u.nome)}</option>`).join('')
    sel.onchange = async () => { st.user = us.find(u => u.id === sel.value); LS.set('uid', st.user.id); await atualizar(); render() }
  }
  if (st.user.id !== LS.get('dono')) { LISTA = []; OS = {}; LS.set('lista', []); LS.set('os', {}); LS.set('dono', st.user.id) }
  await recarregarFila()
  if (navigator.onLine) { await enviar(); await atualizar() }
  window.addEventListener('hashchange', () => { render(); window.scrollTo(0, 0) })
  window.addEventListener('online', () => { desenharFila(); enviar() }); window.addEventListener('offline', desenharFila)
  setInterval(() => enviar(), 30000)
  render()
  if ('serviceWorker' in navigator && (location.protocol === 'https:' || ['localhost', '127.0.0.1'].includes(location.hostname))) navigator.serviceWorker.register('sw.js').catch(() => { })
}
iniciar()
