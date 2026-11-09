/* Agenda das equipes — quadro de replanejamento (semana e dia), arrastar e soltar, redimensionar e desfazer.
   Só planejador, supervisor e gestor movem (o planejamento é dono da escala das OS); técnicos veem. */
'use strict'
const Quadro = (() => {
  const Q = { modo: 'semana', inicio: '', dia: '', pilha: [], equipe: '' }
  const H0 = 6, H1 = 21, PX = 48  // dia: 06h–21h, 48 px por hora (30 min = 24 px)
  const pode = () => GESTAO.includes(st.user.perfil)
  const MOVEIS = ['planejada', 'pausada']
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  const diaDe = s => iso(new Date(s))
  const somaDias = (s, n) => { const d = new Date(s + 'T12:00:00'); d.setDate(d.getDate() + n); return iso(d) }
  const segunda = s => { const d = new Date((s || iso(new Date())) + 'T12:00:00'); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return iso(d) }
  const naHora = (dia, h, mi) => { const d = new Date(dia + 'T00:00:00'); d.setHours(h, mi, 0, 0); return d }
  let poli = false
  function polyfill() {
    if (poli || !window.MobileDragDrop) return
    poli = MobileDragDrop.polyfill({ dragImageTranslateOverride: MobileDragDrop.scrollBehaviourDragImageTranslateOverride, holdToDrag: 250 })
    window.addEventListener('touchmove', () => { }, { passive: false })
  }

  async function tela(m) {
    if (st.user.perfil === 'solicitante') { m.innerHTML = '<h1>Agenda das equipes</h1><div class="note">Área da operação.</div>'; return }
    polyfill()
    if (!EQUIPES.length) await cadastros()
    const semana = Q.modo === 'semana'
    if (semana) Q.inicio = Q.inicio || segunda()
    else Q.dia = Q.dia || iso(new Date())
    const a = await api('GET', `/api/agenda?inicio=${semana ? Q.inicio : Q.dia}&dias=${semana ? 7 : 1}`)
    const conf = a.itens.filter(i => i.conflitos.length)
    const titulo = semana ? `${data(Q.inicio)} a ${data(somaDias(Q.inicio, 6))}` : new Date(Q.dia + 'T12:00:00').toLocaleDateString('pt-BR', { weekday: 'long', day: '2-digit', month: 'long' })
    m.innerHTML = `<div class="row"><h1 style="margin:0">Agenda das equipes</h1><span class="spacer"></span>
      <div class="seg"><button class="btn ${semana ? 'pri' : ''}" data-modo="semana">Semana</button><button class="btn ${semana ? '' : 'pri'}" data-modo="dia">Dia</button></div>
      <button class="btn" id="qAnt">←</button><button class="btn" id="qHoje">Hoje</button><button class="btn" id="qProx">→</button>${pode() ? `<button class="btn" id="qDesf" ${Q.pilha.length ? '' : 'disabled'} title="Desfaz o último movimento">↶ Desfazer</button>` : ''}</div>
      <p class="lede">${esc(titulo)} · ${a.itens.length} execução(ões).${pode() ? ' Arraste para replanejar' + (semana ? ' (outro dia ou outra equipe)' : '; puxe a borda de baixo para mudar a duração') + '. Só OS planejadas ou pausadas se movem.' : ''}</p>
      ${conf.length ? `<div class="note bad">Conflitos de horário: ${conf.map(i => esc(i.numero)).join(', ')} — a mesma equipe está em duas OS no mesmo horário.</div>` : ''}
      <div class="quadro"><div class="qboard">${semana ? semanaHtml(a) : diaHtml(a)}</div>
      ${pode() ? `<aside class="aplanejar" data-drop-ap><h3>A planejar <span class="muted">${a.a_planejar.length}</span></h3>${a.a_planejar.map(o => card(o, true)).join('') || '<p class="small muted">Nenhuma OS aberta.</p>'}</aside>` : ''}</div>
      ${a.equipes.length ? '' : '<div class="note">Cadastre as equipes em Equipes.</div>'}`
    m.querySelectorAll('[data-modo]').forEach(b => b.onclick = () => { Q.modo = b.dataset.modo; if (Q.modo === 'dia' && !Q.dia) Q.dia = semana && Q.inicio <= iso(new Date()) && iso(new Date()) <= somaDias(Q.inicio, 6) ? iso(new Date()) : Q.inicio; K.render() })
    const nav = n => { if (semana) Q.inicio = somaDias(Q.inicio, 7 * n); else Q.dia = somaDias(Q.dia, n); K.render() }
    $('#qAnt').onclick = () => nav(-1); $('#qProx').onclick = () => nav(1); $('#qHoje').onclick = () => { Q.inicio = ''; Q.dia = ''; K.render() }
    const dz = $('#qDesf'); if (dz) dz.onclick = desfazer
    m.querySelectorAll('[data-dia-link]').forEach(x => x.onclick = () => { Q.modo = 'dia'; Q.dia = x.dataset.diaLink; K.render() })
    if (pode()) ligar(m, a, semana)
  }

  function card(i, solto) {
    const mov = pode() && (solto ? i.status === 'aberta' : MOVEIS.includes(i.status))
    const sla = (i.sla || {}).situacao
    return `<div class="qcard ${i.conflitos && i.conflitos.length ? 'conf' : ''} ${esc(i.status)} sla-${esc(sla)}" ${mov ? 'draggable="true"' : ''} data-n="${esc(i.numero)}" data-eq="${esc(i.equipe_id || '')}" data-st="${esc(i.status)}"
      title="${esc(i.numero + ' · ' + i.titulo + ' · ' + (i.status_nome || ''))}">
      <div class="qh">${i.execucao_inicio ? `${hhmm(i.execucao_inicio)}–${hhmm(i.fim_previsto)}` : `${esc(i.numero)}`}${mov ? '' : ' <span class="qlock" title="Não pode ser arrastada nesta situação">🔒</span>'}</div>
      <a class="qt" href="#/os/${encodeURIComponent(i.numero)}" draggable="false">${esc(i.titulo)}</a>
      <div class="qm">${prio(i.prioridade)} ${i.executor ? `<span>👷 ${esc(i.executor)}</span>` : ''}${i.feriado && i.feriado.situacao === 'aguardando_gestor' ? ' <span class="tag warn">feriado: aguardando de acordo</span>' : ''}${sla === 'atrasada' || sla === 'em_risco' ? ' ' + tagSla(i.sla) : ''}</div>
      ${!solto && pode() && MOVEIS.includes(i.status) && Q.modo === 'dia' ? '<div class="qres" title="Arraste para mudar a duração"></div>' : ''}</div>`
  }
  const ferDe = (a, eq, dia) => ((a.feriados || {})[eq] || []).find(f => f.data === dia)

  function semanaHtml(a) {
    const dias = [...Array(7)].map((_, k) => somaDias(Q.inicio, k)), hoje = iso(new Date())
    const ferCab = dia => { const fs = new Set(Object.values(a.feriados || {}).flat().filter(f => f.data === dia).map(f => f.nome)); return fs.size ? `<div class="qfer">🎌 ${esc([...fs].join(', '))}</div>` : '' }
    return `<div class="semana q">${'<div class="cab">Equipe</div>' + dias.map(d => `<div class="cab${d === hoje ? ' hoje' : ''}"><a href="#" data-dia-link="${d}" onclick="return false">${new Date(d + 'T12:00:00').toLocaleDateString('pt-BR', { weekday: 'short', day: '2-digit', month: '2-digit' })}</a>${ferCab(d)}</div>`).join('')}
      ${a.equipes.map(e => `<div class="eq">${esc(e.nome)}<div class="small muted">${e.membros} pessoa(s)${e.unidade ? ' · ' + esc(e.unidade) : ''}</div></div>` + dias.map(d => { const f = ferDe(a, e.id, d); return `<div class="cel${d === hoje ? ' hoje' : ''}${f ? ' fer' : ''}" data-cel data-eq="${esc(e.id)}" data-dia="${d}"${f ? ` title="Feriado: ${esc(f.nome)}"` : ''}>${a.itens.filter(i => i.equipe_id === e.id && diaDe(i.execucao_inicio) === d).map(i => card(i)).join('')}</div>` }).join('')).join('')}</div>`
  }
  function diaHtml(a) {
    const horas = [...Array(H1 - H0)].map((_, k) => H0 + k), alt = (H1 - H0) * PX
    return `<div class="qdia" style="--px:${PX}px"><div class="qhoras"><div class="qcab"></div><div style="position:relative;height:${alt}px">${horas.map(h => `<div class="qhr" style="top:${(h - H0) * PX}px">${String(h).padStart(2, '0')}:00</div>`).join('')}</div></div>
      ${a.equipes.map(e => { const f = ferDe(a, e.id, Q.dia); return `<div class="qcol"><div class="qcab">${esc(e.nome)}${f ? `<div class="qfer">🎌 ${esc(f.nome)}</div>` : ''}</div>
        <div class="qtrilho${f ? ' fer' : ''}" data-trilho data-eq="${esc(e.id)}" style="height:${alt}px">${horas.map(h => `<div class="qlinha" style="top:${(h - H0) * PX}px"></div>`).join('')}
        ${a.itens.filter(i => i.equipe_id === e.id && diaDe(i.execucao_inicio) === Q.dia).map(i => { const ini = new Date(i.execucao_inicio), top = Math.max(0, (ini.getHours() + ini.getMinutes() / 60 - H0) * PX), h = Math.max(PX / 2, (i.duracao_h || 1) * PX)
          return `<div class="qpos" style="top:${top}px;height:${h}px" data-pos="${esc(i.numero)}">${card(i)}</div>` }).join('')}</div></div>` }).join('')}</div>`
  }

  // ── arrastar e soltar
  function ligar(m, a, semana) {
    const itens = Object.fromEntries([...a.itens, ...a.a_planejar].map(i => [i.numero, i]))
    let arr = null  // {numero, offsetY}
    m.querySelectorAll('.qcard[draggable=true]').forEach(c => {
      c.addEventListener('dragstart', e => { const r = c.getBoundingClientRect(); arr = { numero: c.dataset.n, offsetY: (e.clientY || r.top) - r.top }; e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', c.dataset.n) } catch (x) { } c.classList.add('arrastando') })
      c.addEventListener('dragend', () => { c.classList.remove('arrastando'); m.querySelectorAll('.alvo').forEach(x => x.classList.remove('alvo')) })
    })
    const alvos = semana ? m.querySelectorAll('[data-cel]') : m.querySelectorAll('[data-trilho]')
    alvos.forEach(t => {
      t.addEventListener('dragenter', e => { if (arr) { e.preventDefault(); t.classList.add('alvo') } })
      t.addEventListener('dragover', e => { if (arr) { e.preventDefault(); e.dataTransfer.dropEffect = 'move' } })
      t.addEventListener('dragleave', e => { if (!t.contains(e.relatedTarget)) t.classList.remove('alvo') })
      t.addEventListener('drop', async e => {
        e.preventDefault(); t.classList.remove('alvo'); if (!arr) return
        const o = itens[arr.numero], eq = t.dataset.eq; let ini
        if (semana) {
          const antes = o.execucao_inicio ? new Date(o.execucao_inicio) : null
          ini = naHora(t.dataset.dia, antes ? antes.getHours() : 8, antes ? antes.getMinutes() : 0)
        } else {
          const y = e.clientY - t.getBoundingClientRect().top - (arr.offsetY || 0), min = Math.max(0, Math.round(y / (PX / 2)) * 30)
          ini = naHora(Q.dia, H0 + Math.floor(min / 60), min % 60)
        }
        const numero = arr.numero; arr = null
        if (o.status === 'aberta' || (o.equipe_id && eq !== o.equipe_id)) return abrirPlanejar(numero, { equipe_id: eq, inicio: ini.toISOString(), duracao_h: o.duracao_h })
        if (o.execucao_inicio && new Date(o.execucao_inicio).getTime() === ini.getTime()) return
        await mover(o, { execucao_inicio: ini.toISOString() })
      })
    })
    // redimensionar (vista do dia): puxa a borda de baixo, passo de 30 min
    m.querySelectorAll('.qres').forEach(h => h.addEventListener('pointerdown', e => {
      e.preventDefault(); e.stopPropagation()
      const pos = h.closest('.qpos'), c = h.closest('.qcard'), o = itens[c.dataset.n], y0 = e.clientY, h0 = pos.getBoundingClientRect().height
      c.draggable = false; h.setPointerCapture(e.pointerId)
      const novo = ev => Math.max(PX / 2, Math.round((h0 + ev.clientY - y0) / (PX / 2)) * (PX / 2))
      const mv = ev => { pos.style.height = novo(ev) + 'px' }
      const up = async ev => { h.removeEventListener('pointermove', mv); h.removeEventListener('pointerup', up); c.draggable = true; const dur = novo(ev) / PX; if (Math.abs(dur - (o.duracao_h || 1)) >= .25) await mover(o, { duracao_h: dur }); else pos.style.height = h0 + 'px' }
      h.addEventListener('pointermove', mv); h.addEventListener('pointerup', up)
    }))
  }

  // replaneja com as proteções do servidor: SLA pede confirmação, feriado pede ciência, outra equipe abre o planejamento
  async function mover(o, mudanca, desfazendo) {
    const antes = { execucao_inicio: o.execucao_inicio, duracao_h: o.duracao_h }
    let corpo = { ...mudanca, ...(desfazendo ? { confirmar_sla: true, ciencia_feriado: true, motivo: 'Desfeito no quadro' } : {}) }
    for (let tent = 0; tent < 3; tent++) {
      try {
        await api('POST', `/api/ordens/${encodeURIComponent(o.numero)}/replanejar`, corpo)
        if (!desfazendo) { Q.pilha.push({ numero: o.numero, ...antes }); if (Q.pilha.length > 20) Q.pilha.shift() }
        toast(desfazendo ? 'Movimento desfeito.' : `${o.numero} replanejada. A equipe é avisada pela agenda.`)
        return K.render()
      } catch (e) {
        const d = e.data || {}
        if (d.codigo === 'SLA' && !corpo.confirmar_sla) { if (!confirm(`${d.mensagem}\n\nConfirmar o replanejamento mesmo assim?`)) return K.render(); corpo.confirmar_sla = true; continue }
        if (d.codigo === 'FERIADO' && !corpo.ciencia_feriado) { if (!confirm(`${d.mensagem}\nSem escala de feriado: ${(d.sem_escala || []).join(', ')}.\n\nEstou ciente — a OS fica aguardando o de acordo do gestor de serviços.`)) return K.render(); corpo.ciencia_feriado = true; continue }
        if (d.codigo === 'EQUIPE') return abrirPlanejar(o.numero, { inicio: mudanca.execucao_inicio })
        toast(e.message); return K.render()
      }
    }
  }
  async function desfazer() {
    const u = Q.pilha.pop(); if (!u) return
    await mover({ numero: u.numero }, { execucao_inicio: u.execucao_inicio, duracao_h: u.duracao_h }, true)
  }
  async function abrirPlanejar(numero, pre) {
    try { const o = await api('GET', '/api/ordens/' + encodeURIComponent(numero)); const r = await planejar(o, pre); if (!r) K.render() } catch (e) { toast(e.message); K.render() }
  }
  return { tela }
})()
