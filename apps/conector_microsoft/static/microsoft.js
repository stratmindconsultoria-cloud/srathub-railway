/* Conector Microsoft — interface. */
'use strict'
const { $, esc, api, dh, toast, modal, tabela, st } = K
const adm = () => st.user.perfil === 'admin'
const ESTADO = { erro: ['recusada pela Microsoft', 'bad'], criando: ['criando', 'warn'], criada: ['no Teams', 'pri'], executada: ['executada', 'ok'], cancelada: ['cancelada (resolvida no hub)', ''],
  falha: ['falha', 'bad'], sem_aprovador: ['sem aprovador no Microsoft 365', 'warn'], encerrada: ['encerrada', ''] }
let espera = null

async function telaConta(m) {
  clearInterval(espera)
  const r = await api('GET', '/api/conta'), c = r.conta
  m.innerHTML = `<h1>Minha conta Microsoft</h1><p class="lede">Conecte a sua conta do Microsoft 365 para ver a agenda do hub no Outlook/Teams (e os compromissos do Outlook na agenda) e para receber as aprovações no app <b>Aprovações</b> do Teams.</p>
    ${!r.configurado ? `<div class="note warn">O administrador ainda não informou o ID do aplicativo Microsoft${adm() ? ' — <a href="#/config">abrir Configuração</a>' : ''}.</div>` : ''}
    <div class="panel">${c ? `<div class="row"><span class="tag ok">conectada</span><b>${esc(c.ms_nome || '')}</b><span class="muted">${esc(c.ms_upn || '')}</span><span class="spacer"></span><button class="btn" id="tst">Testar</button><button class="btn bad" id="dsc">Desconectar</button></div>
      <p class="small muted" style="margin:8px 0 0">Conectada em ${dh(c.conectado_em)} · último uso ${dh(c.ultimo_uso)}${r.emissor ? ' · <b>esta conta emite as aprovações no Teams</b>' : ''}</p>
      ${c.confere ? '' : `<div class="note warn small" style="margin-top:8px">O e-mail do seu cadastro no hub (${esc(c.email_hub || 'vazio')}) não é um dos endereços desta conta Microsoft. Até o administrador confirmar que a conta é sua, as aprovações chegam no Teams pelo e-mail do cadastro.</div>`}
      ${c.erro ? `<div class="note bad" style="margin-top:10px">${esc(c.erro)}</div>` : ''}`
      : `<p>Nenhuma conta conectada.</p><button class="btn pri" id="con" ${r.configurado ? '' : 'disabled'}>Conectar conta Microsoft</button><div id="cod"></div>`}</div>
    <div class="panel"><h2>O que acontece</h2><ul class="small" style="margin:0;padding-left:18px;line-height:1.7">
      <li>${r.calendario ? '' : '<s>'}Seus blocos da StratAgenda (inclusive OS) aparecem no Outlook e no Teams como <b>ocupado</b>, com a etiqueta STRAT·HUB. Mude pela agenda do hub.${r.calendario ? '' : '</s> (desligado pelo administrador)'}</li>
      <li>${r.calendario ? '' : '<s>'}Seus compromissos do Outlook entram na agenda do hub como <b>ocupado</b> (os privados aparecem só como “Ocupado”).${r.calendario ? '' : '</s>'}</li>
      <li>${r.aprovacoes ? '' : '<s>'}Aprovações das ferramentas chegam também no Teams, com os mesmos botões. A primeira resposta, em qualquer canal, vale.${r.aprovacoes ? '' : '</s> (desligado pelo administrador)'}</li>
      <li>O acesso fica cifrado no servidor e você desconecta quando quiser.</li></ul></div>`
  const con = $('#con'); if (con) con.onclick = conectar
  const dsc = $('#dsc'); if (dsc) dsc.onclick = async () => { if (!confirm('Desconectar a conta Microsoft? A sincronização para.')) return; await api('DELETE', '/api/conta'); toast('Desconectada.'); K.render() }
  const tst = $('#tst'); if (tst) tst.onclick = async () => { try { const t = await api('POST', '/api/conta/testar'); toast(`OK: ${t.nome} · calendário acessível`) } catch (e) { toast(e.message) } }
}
async function conectar() {
  let d; try { d = await api('POST', '/api/conta/conectar') } catch (e) { toast(e.message); return }
  $('#cod').innerHTML = `<div class="note" style="margin-top:12px"><p style="margin:0 0 8px">1. Abra <a href="${esc(d.endereco)}" target="_blank" rel="noopener"><b>${esc(d.endereco)}</b></a><br>2. Digite o código:</p>
    <div class="row"><code style="font-size:26px;letter-spacing:4px;font-weight:700">${esc(d.codigo)}</code><button class="btn mini" id="cp">Copiar</button></div>
    <p class="small muted" style="margin:8px 0 0">3. Entre com a conta do Microsoft 365 e aceite. Esta tela atualiza sozinha. O código vale ${Math.round(d.expira_em_s / 60)} min.</p></div>`
  $('#cp').onclick = () => navigator.clipboard && navigator.clipboard.writeText(d.codigo).then(() => toast('Código copiado.'))
  try { window.open(d.endereco, '_blank', 'noopener') } catch (e) { }
  clearInterval(espera)
  espera = setInterval(async () => {
    try { const r = await api('POST', '/api/conta/verificar'); if (!r.pendente) { clearInterval(espera); toast('Conta conectada!'); K.render() } }
    catch (e) { clearInterval(espera); toast(e.message); K.render() }
  }, Math.max(5, d.intervalo_s) * 1000)
}

async function telaConfig(m) {
  if (!adm()) { m.innerHTML = '<h1>Configuração</h1><div class="note">Área do administrador da integração.</div>'; return }
  const c = await api('GET', '/api/config')
  const passo = (ok, t) => `<li>${ok ? '✅' : '⬜'} ${t}</li>`
  m.innerHTML = `<h1>Configuração</h1><p class="lede">Uma vez por organização. O aplicativo é registrado no Microsoft Entra da empresa (ou o mesmo aplicativo multilocatário, consentido pelo administrador de cada empresa).</p>
    <div class="panel"><h2>Checklist</h2><ul style="list-style:none;padding:0;margin:0;line-height:1.9" class="small">
      ${passo(!!c.client_id, 'ID do aplicativo informado')}${passo(c.contas.length > 0, 'ao menos uma conta conectada')}${passo(!!c.emissor, 'conta emissora das aprovações escolhida (de preferência uma conta de integração, que não aprova nada)')}
      ${passo(c.no_hub, 'rodando no STRAT·HUB')}<li>⬜ no hub: <b>Admin técnico › Ferramentas › Conector Microsoft › Autorizar a agir em nome das pessoas</b> (para executar as respostas do Teams)</li></ul></div>
    <div class="panel"><h2>Aplicativo Microsoft</h2><div class="grid2">
      <label class="f">ID do aplicativo (cliente)<input class="in" id="cid" value="${esc(c.client_id || '')}" placeholder="00000000-0000-0000-0000-000000000000"></label>
      <label class="f">Locatário<input class="in" id="ten" value="${esc(c.tenant || 'organizations')}" title="organizations = qualquer empresa (multilocatário); ou o ID do diretório"></label></div>
      <p class="small muted">Permissões delegadas no registro: ${c.escopos.map(x => `<code>${esc(x)}</code>`).join(' ')} · “Permitir fluxos de clientes públicos” ligado · consentimento do administrador concedido.</p>
      <h3>O que sincroniza</h3><label class="row small"><input type="checkbox" id="cal" ${c.calendario ? 'checked' : ''}> Calendário (agenda ⇄ Outlook/Teams)</label>
      <label class="row small"><input type="checkbox" id="apr" ${c.aprovacoes ? 'checked' : ''}> Aprovações no Teams</label>
      <div class="grid2" style="margin-top:8px"><label class="f">Janela do calendário (dias à frente)<input class="in" id="dias" type="number" min="1" max="60" value="${esc(c.dias_calendario)}"></label>
      <label class="f">Conta emissora das aprovações<select class="in" id="emi"><option value="">—</option>${c.contas.map(x => `<option value="${esc(x.id_hub)}"${x.id_hub === c.emissor ? ' selected' : ''}>${esc(x.nome)} · ${esc(x.ms_upn || '')}</option>`).join('')}</select></label></div>
      <p class="small muted">A API de aprovações da Microsoft só aceita acesso em nome de uma pessoa: as aprovações aparecem no Teams como enviadas pela conta emissora (remetente técnico; o pedido real e quem o fez vão na descrição). Use uma conta de integração, que não aprova nada. Sem conta emissora, as aprovações aguardam.</p>
      <div class="row"><button class="btn pri" id="sv">Salvar</button><button class="btn" id="sy">Sincronizar agora</button></div></div>
    <div class="panel"><h2>Notificações do hub no e-mail e no Teams</h2>
      <p class="small muted">O hub decide quem recebe o quê (preferências de cada pessoa). Este conector entrega pela conta emissora: e-mail (quando o hub não tem SMTP), aviso no feed de atividades do Teams e alerta no canal da equipe. Permissões liberadas para a conta emissora: ${(c.permissoes_liberadas || []).map(x => `<span class="tag ok">${esc(x)}</span>`).join(' ') || '<span class="tag warn">nenhuma opcional</span>'}</p>
      <ul class="small" style="line-height:1.8;margin:6px 0 10px">${Object.entries(c.opcionais || {}).map(([k, v]) => `<li>${(c.permissoes_liberadas || []).includes(k) ? '✅' : '⬜'} <code>${esc(v)}</code> — ${esc({ resposta: 'responder pela agenda às aprovações criadas no Teams', email: 'enviar e-mails do hub', teams_feed: 'avisos no feed de atividades do Teams', canal: 'alertas no canal da equipe' }[k] || k)}</li>`).join('')}</ul>
      <label class="row small"><input type="checkbox" id="nEm" ${c.notif_email ? 'checked' : ''}> E-mail pela conta emissora</label>
      <label class="row small"><input type="checkbox" id="nTe" ${c.notif_teams ? 'checked' : ''}> Feed de atividades do Teams</label>
      <label class="row small"><input type="checkbox" id="nCa" ${c.notif_canal ? 'checked' : ''}> Alertas no canal da equipe</label>
      <label class="f" style="margin-top:8px">Endereço público do hub (https, para a aba do app no Teams)<input class="in" id="nEnd" value="${esc(c.endereco_publico || '')}" placeholder="https://hub.suaempresa.com"></label>
      <h3>App do Teams</h3><p class="small muted">O feed de atividades só funciona com o app instalado para as pessoas. Baixe o pacote e peça ao administrador do Teams para publicar em <b>Centro de administração do Teams › Aplicativos do Teams › Gerenciar aplicativos › Carregar</b> (ou carregue você mesmo, em teste). Depois marque abaixo.</p>
      <div class="row"><button class="btn" id="nPac">Baixar pacote do app do Teams</button><label class="row small"><input type="checkbox" id="nIns" ${c.teams_instalado ? 'checked' : ''}> App publicado e instalado para as pessoas</label></div>
      <h3>Canal de cada ferramenta (alertas de equipe)</h3><div id="nCan" class="small muted">${Object.entries(c.canais || {}).map(([a, v]) => `${esc(a)} → ${esc(v.nome)}`).join('<br>') || 'Nenhum canal escolhido.'}</div>
      <div class="row" style="margin-top:6px"><button class="btn" id="nCar">Escolher canais</button><button class="btn pri" id="nSv">Salvar notificações</button></div></div>
    <div class="panel"><h2>Contas conectadas (${c.contas.length})</h2>${(c.duplicadas || []).length ? `<div class="note warn small">Ambiente de teste: a mesma conta Microsoft (${esc(c.duplicadas.join(', '))}) está conectada por mais de um usuário do hub — os compromissos dos dois vão para o mesmo Outlook. Em produção isso é bloqueado.</div>` : ''}${tabela([{ t: 'Pessoa', k: 'nome' }, { t: 'Conta Microsoft', h: x => esc(x.ms_upn || '') }, { t: 'Conectada', h: x => dh(x.conectado_em) }, { t: 'Último uso', h: x => dh(x.ultimo_uso) }, { t: 'Conferência', wrap: 1, h: x => x.confere ? `<span class="tag ok">${x.confirmada_por ? 'confirmada por ' + esc(x.confirmada_por) : 'confere com o cadastro'}</span>` : `<span class="tag warn">e-mail do hub (${esc(x.email_hub || 'vazio')}) não é desta conta</span> <button class="btn mini" data-conf="${esc(x.id_hub)}">Confirmar que é da pessoa</button>` },
      { t: 'Situação', h: x => x.erro ? `<span class="tag bad">${esc(x.erro)}</span>` : '<span class="tag ok">ok</span>' }], c.contas, { vazio: 'Nenhuma conta conectada.' })}</div>`
  $('#sv').onclick = async () => { try { await api('PUT', '/api/config', { client_id: $('#cid').value, tenant: $('#ten').value, calendario: $('#cal').checked, aprovacoes: $('#apr').checked, dias_calendario: $('#dias').value, emissor: $('#emi').value }); toast('Configuração salva.'); K.render() } catch (e) { toast(e.message) } }
  $('#sy').onclick = () => K.sincronizar(true, true)
  let canais = c.canais || {}
  $('#nPac').onclick = () => K.baixarUrl('/api/teams/pacote', 'strathub_teams.zip').catch(e => toast(e.message))
  $('#nIns').onchange = async e => { try { await api('POST', '/api/teams/instalado', { instalado: e.target.checked }); toast('Anotado.') } catch (x) { toast(x.message) } }
  $('#nCar').onclick = async () => {
    try {
      const r = await api('GET', '/api/teams/canais')
      $('#nCan').innerHTML = r.ferramentas.map(f => `<label class="f">${esc(f.nome)}<select class="in" data-can="${esc(f.id)}"><option value="">— nenhum —</option>${r.canais.map(x => `<option value="${esc(x.team_id + '|' + x.channel_id)}"${canais[f.id] && canais[f.id].channel_id === x.channel_id ? ' selected' : ''}>${esc(x.nome)}</option>`).join('')}</select></label>`).join('') || 'Nenhuma ferramenta instalada.'
      $('#nCan').querySelectorAll('[data-can]').forEach(s => s.onchange = () => { const [t, ch] = s.value.split('|'); if (t) canais[s.dataset.can] = { team_id: t, channel_id: ch, nome: s.selectedOptions[0].textContent }; else delete canais[s.dataset.can] })
    } catch (e) { toast(e.message) }
  }
  $('#nSv').onclick = async () => { try { await api('PUT', '/api/config', { notif_email: $('#nEm').checked, notif_teams: $('#nTe').checked, notif_canal: $('#nCa').checked, endereco_publico: $('#nEnd').value, canais }); toast('Notificações salvas.'); K.render() } catch (e) { toast(e.message) } }
  m.querySelectorAll('[data-conf]').forEach(b => b.onclick = async () => {
    const x = c.contas.find(y => y.id_hub === b.dataset.conf)
    if (!confirm(`Confirmar que a conta Microsoft ${x.ms_upn} é de ${x.nome}?\n\nAs aprovações destinadas a ${x.nome} passam a chegar nesta conta. A confirmação fica na auditoria.`)) return
    try { await api('POST', '/api/contas/' + encodeURIComponent(x.id_hub) + '/confirmar'); toast('Conta confirmada.'); K.render() } catch (e) { toast(e.message) } })
}

async function telaAtividade(m) {
  const a = await api('GET', '/api/atividade')
  const sy = a.sincronizacao, ap = (sy && sy.aprovacoes) || {}, tm = (sy && sy.teams) || {}
  const errTeams = Object.entries(tm).filter(([, v]) => typeof v === 'string')
  m.innerHTML = `<h1>Atividade</h1><p class="lede">${a.eventos} compromisso(s) da agenda no Outlook · ${a.ocupados} compromisso(s) do Outlook na agenda.</p>
    ${adm() && sy ? `<div class="panel"><h2>Última sincronização · ${dh(sy.em)}</h2>
      ${ap.aviso ? `<div class="note warn">${esc(ap.aviso)}</div>` : ''}${ap.erro ? `<div class="note bad">Aprovações (ferramentas → Teams): ${esc(ap.erro)}</div>` : ''}
      ${!ap.aviso && !ap.erro ? `<p class="small">Ferramentas → Teams: ${ap.pendencias_lidas ?? 0} pendência(s) lida(s) da agenda · ${ap.criadas || 0} criada(s) · ${ap.executadas || 0} executada(s) · ${ap.canceladas || 0} cancelada(s) · ${ap.falhas || 0} com falha</p>` : ''}
      <p class="small">Teams → agenda: ${Object.entries(tm).filter(([, v]) => typeof v !== 'string').map(([k, v]) => `${esc(k)}: ${v} aberta(s)`).join(' · ') || '—'}</p>
      ${errTeams.map(([k, v]) => `<div class="note bad small">Leitura do Teams (${esc(k)}): ${esc(v)}</div>`).join('')}</div>` : ''}
    ${adm() ? `<div class="panel"><h2>Aprovações no Teams</h2>${tabela([{ t: 'Pendência', wrap: 1, h: x => `<b>${esc(x.titulo || x.pendencia)}</b><div class="small muted">${esc(x.app || '')}</div>` },
      { t: 'Aprovadores', wrap: 1, h: x => esc(x.aprovadores.map(p => p.nome || p.email).join(', ') || '—') }, { t: 'Situação', h: x => { const [l, k] = ESTADO[x.estado] || [x.estado, '']; return `<span class="tag ${k}">${esc(l)}</span>` } },
      { t: 'Resposta', wrap: 1, h: x => x.resposta ? `${esc(x.resposta)} · ${esc(x.respondido_por || '')}${x.comentario ? `<div class="small muted">“${esc(x.comentario)}”</div>` : ''}` : '—' },
      { t: 'Detalhe', wrap: 1, h: x => x.erro ? `<span class="small" style="color:var(--bad)">${esc(x.erro)}</span>` : '' }, { t: 'Atualizada', h: x => dh(x.atualizado_em) }], a.aprovacoes, { vazio: 'Nenhuma aprovação enviada ao Teams ainda.' })}</div>` : ''}`
}

K.iniciar({
  nome: 'Conector Microsoft', inicio: 'conta', subtitulo: 'Microsoft 365',
  nav: () => [['conta', 'Minha conta'], ['atividade', 'Atividade'], adm() && ['config', 'Configuração'], K.eAdmin() && ['usuarios', 'Usuários e perfis']],
  rotas: { conta: telaConta, atividade: telaAtividade, config: telaConfig, usuarios: K.telaUsuarios }
})
