/* Conector CRM — interface. */
'use strict'
const { $, esc, api, dh, toast, modal, tabela, st } = K
const adm = () => st.user.perfil === 'integrador'
const urlBase = () => location.origin + location.pathname.replace(/\/?(index\.html)?$/, '').replace(/\/$/, '')
const EXEMPLO = { crm_ref: 'T-100', titulo: 'Ar-condicionado sem refrigerar', descricao: 'Sala de reuniões 3', tipo: 'atendimento', prioridade: 'alta', cliente: { nome: 'Cliente Exemplo', documento: '00.000.000/0001-00' }, contato: { nome: 'Maria', telefone: '(11) 99999-0000', email: 'maria@exemplo.com' }, local: { endereco: 'Rua Um, 10', cidade: 'São Paulo', uf: 'SP' }, codigo_projeto: '' }

async function telaInicio(m) {
  const xs = await api('GET', '/api/integracoes')
  m.innerHTML = `<div class="row"><h1 style="margin:0">Integrações com CRM</h1><span class="spacer"></span>${adm() ? '<button class="btn pri" id="nova">+ Nova integração</button>' : ''}</div>
    <p class="lede">O CRM continua sendo o seu CRM. Aqui só conectamos: a <b>solicitação de serviço</b> entra (webhook, busca periódica ou arquivo), vira OS em Ordens de Serviço, e o <b>status da OS</b> volta ao CRM.</p>
    ${tabela([{ t: 'Integração', h: x => `<a href="#/integracao/${encodeURIComponent(x.id)}"><b>${esc(x.nome)}</b></a><div class="small muted">${esc(x.crm || '')} · id ${esc(x.id)}</div>` }, { t: 'Situação', h: x => `<span class="tag ${x.ativo ? 'ok' : ''}">${x.ativo ? 'ativa' : 'desativada'}</span>` },
      { t: 'Entrada', h: x => ['webhook', x.pull_url && 'busca ' + (x.pull_intervalo_min || '?') + ' min'].filter(Boolean).join(' · ') }, { t: 'Saída', h: x => x.webhook_url ? 'webhook' : 'consulta (pull)' },
      { t: 'Recebidas', n: 1, h: x => x.entradas_ok + (x.entradas_erro ? ` <span class="tag bad">${x.entradas_erro} erro(s)</span>` : '') }, { t: 'Status entregues', n: 1, h: x => x.saidas_ok + (x.saidas_pendentes ? ` <span class="tag warn">${x.saidas_pendentes} pendente(s)</span>` : '') }], xs, { vazio: 'Nenhuma integração.' })}
    <div class="panel" style="margin-top:16px"><h2>Como funciona</h2><ol class="small" style="margin:0;padding-left:18px;line-height:1.7">
      <li>Crie a integração e copie a <b>chave</b> (aparece uma vez).</li><li>No CRM ou na automação (n8n, Make, Zapier), envie <code>POST …/entrada/&lt;id&gt;/solicitacoes</code> com o cabeçalho <code>X-Chave</code>.</li>
      <li>Se o CRM não envia no formato neutro, configure o <b>de-para</b> de campos na integração.</li><li>O status volta por webhook (assinado com HMAC) ou o CRM consulta <code>GET …/entrada/&lt;id&gt;/status/&lt;crm_ref&gt;</code>.</li>
      <li>CRMs na nuvem não alcançam um servidor interno: nesse caso use a <b>busca periódica</b> (o servidor busca numa URL do CRM) ou um túnel público só para <code>/entrada</code>.</li></ol></div>`
  const n = $('#nova'); if (n) n.onclick = async () => { const nome = await K.pedir('Nova integração', 'Nome da integração (ex.: CRM comercial).', true); if (!nome) return; try { const r = await api('PUT', '/api/integracoes/nova', { nome }); mostrarChave(r); location.hash = '#/integracao/' + encodeURIComponent(r.id) } catch (e) { toast(e.message) } }
}
function mostrarChave(r) {
  if (!r.chave) return
  const d = modal(`<h2>Chave da integração</h2><p class="muted">Copie agora: ela não será mostrada de novo. Gere outra se perder.</p><pre class="code" id="kk">${esc(r.chave)}</pre>
    <p class="small">Endpoint de entrada:<br><code>${esc(urlBase())}/entrada/${esc(r.id)}/solicitacoes</code></p><div class="row" style="justify-content:flex-end"><button class="btn" id="kc">Copiar</button><button class="btn pri" id="ko">Fechar</button></div>`)
  d.querySelector('#kc').onclick = () => { navigator.clipboard && navigator.clipboard.writeText(r.chave).then(() => toast('Copiada.')) }
  d.querySelector('#ko').onclick = () => d.close()
}
async function telaIntegracao(m, partes) {
  const x = await api('GET', '/api/integracoes/' + encodeURIComponent(partes[0])), aba = partes[1] || 'config'
  const abas = [['config', 'Configuração'], ['mapa', 'De-para de campos'], ['entradas', `Entradas (${x.entradas.length})`], ['saidas', `Saídas de status (${x.saidas.length})`], ['arquivo', 'Contingência: arquivo']]
  m.innerHTML = `<p class="small"><a href="#/inicio">← Integrações</a></p><div class="row"><h1 style="margin:0">${esc(x.nome)}</h1><span class="tag ${x.ativo ? 'ok' : ''}">${x.ativo ? 'ativa' : 'desativada'}</span></div>
    <p class="lede">id <code>${esc(x.id)}</code> · chave ${esc(x.chave_dica || '—')}</p>
    <nav class="tabs">${abas.map(([k, l]) => `<a href="#/integracao/${encodeURIComponent(x.id)}/${k}"${k === aba ? ' aria-current="page"' : ''}>${esc(l)}</a>`).join('')}</nav><div id="aba"></div>`
  const box = $('#aba'), dis = adm() ? '' : 'disabled'
  const salvar = async corpo => { try { const r = await api('PUT', '/api/integracoes/' + encodeURIComponent(x.id), corpo); mostrarChave(r); toast('Salvo.'); K.render() } catch (e) { toast(e.message) } }
  if (aba === 'config') {
    box.innerHTML = `<div class="grid2"><div class="panel"><h2>Geral</h2><div class="grid2"><label class="f">Nome<input class="in" id="cN" value="${esc(x.nome)}" ${dis}></label><label class="f">CRM (rótulo)<input class="in" id="cC" value="${esc(x.crm || '')}" placeholder="ex.: CRM comercial" ${dis}></label></div>
      <label class="row small" style="margin-top:10px"><input type="checkbox" id="cA" ${x.ativo ? 'checked' : ''} ${dis}> Integração ativa</label>
      <h3>Entrada por webhook</h3><pre class="code">POST ${esc(urlBase())}/entrada/${esc(x.id)}/solicitacoes
X-Chave: ${esc(x.chave_dica || '<chave>')}
Content-Type: application/json</pre>${adm() ? '<button class="btn" id="cK">Gerar nova chave</button>' : ''}
      <h3>Busca periódica (o servidor busca no CRM)</h3><div class="grid2"><label class="f">URL que devolve a lista<input class="in" id="cPU" value="${esc(x.pull_url || '')}" placeholder="https://…" ${dis}></label><label class="f">A cada (min)<input class="in" id="cPI" type="number" min="1" value="${esc(x.pull_intervalo_min || '')}" ${dis}></label>
      <label class="f">Token (Bearer) ${x.pull_token ? '— configurado' : ''}<input class="in" id="cPT" type="password" placeholder="${x.pull_token ? 'manter' : ''}" ${dis}></label></div></div>
      <div class="panel"><h2>Saída: status da OS</h2><label class="f">Webhook do CRM/automação (POST JSON)<input class="in" id="cW" value="${esc(x.webhook_url || '')}" placeholder="https://… (vazio = o CRM consulta)" ${dis}></label>
      <label class="f" style="margin-top:8px">Segredo para assinatura HMAC ${x.webhook_segredo ? '— configurado' : ''}<input class="in" id="cS" type="password" placeholder="${x.webhook_segredo ? 'manter' : 'opcional'}" ${dis}></label>
      <p class="small muted">Cabeçalho <code>X-Assinatura: sha256=&lt;hmac do corpo&gt;</code>. Falhas são reenviadas a cada minuto.</p>${adm() && x.webhook_url ? '<button class="btn" id="cT">Enviar evento de teste</button>' : ''}
      <h3>Consulta (pull) pelo CRM</h3><pre class="code">GET ${esc(urlBase())}/entrada/${esc(x.id)}/status/&lt;crm_ref&gt;
GET ${esc(urlBase())}/entrada/${esc(x.id)}/status?desde=&lt;epoch&gt;</pre></div></div>
      ${adm() ? '<div class="row"><button class="btn pri" id="cOk">Salvar configuração</button></div>' : ''}`
    const ok = $('#cOk'); if (ok) ok.onclick = () => salvar({ nome: $('#cN').value, crm: $('#cC').value, ativo: $('#cA').checked, webhook_url: $('#cW').value, pull_url: $('#cPU').value, pull_intervalo_min: $('#cPI').value, ...($('#cS').value ? { webhook_segredo: $('#cS').value } : {}), ...($('#cPT').value ? { pull_token: $('#cPT').value } : {}) })
    const k = $('#cK'); if (k) k.onclick = () => { if (confirm('A chave atual deixa de funcionar. Continuar?')) salvar({ gerar_chave: true }) }
    const t = $('#cT'); if (t) t.onclick = async () => { try { const r = await api('POST', `/api/integracoes/${encodeURIComponent(x.id)}/webhook-teste`); toast('Resposta HTTP ' + r.http_status) } catch (e) { toast(e.message) } }
  } else if (aba === 'mapa') {
    box.innerHTML = `<div class="grid2"><div class="panel"><h2>De-para</h2><p class="small muted">Para cada campo do formato neutro, o caminho no JSON do CRM (com pontos, ex.: <code>properties.subject</code>, <code>contacts.0.email</code>). Vazio = mesmo nome. Padrões preenchem o que o CRM não envia.</p>
      ${tabela([{ t: 'Campo neutro', h: c => `<code>${esc(c)}</code>${['crm_ref', 'titulo'].includes(c) ? ' <span class="tag warn">obrigatório</span>' : ''}` }, { t: 'Caminho no CRM', h: c => `<input class="in" data-mp="${esc(c)}" value="${esc(x.mapeamento[c] || '')}" ${dis}>` }, { t: 'Padrão', h: c => `<input class="in" data-pd="${esc(c)}" value="${esc(x.padroes[c] || '')}" ${dis}>` }], st.meta.campos)}
      ${adm() ? '<div class="row" style="margin-top:10px"><button class="btn pri" id="mOk">Salvar de-para</button></div>' : ''}</div>
      <div class="panel"><h2>Testar com um registro do CRM</h2><textarea class="in" id="mEx" rows="14" style="width:100%;font-family:monospace;font-size:12px">${esc(JSON.stringify(EXEMPLO, null, 2))}</textarea><div class="row" style="margin-top:8px"><button class="btn" id="mT">Ver resultado</button></div><pre class="code" id="mR"></pre></div></div>`
    const coleta = () => { const mp = {}, pd = {}; box.querySelectorAll('[data-mp]').forEach(i => { if (i.value.trim()) mp[i.dataset.mp] = i.value.trim() }); box.querySelectorAll('[data-pd]').forEach(i => { if (i.value.trim()) pd[i.dataset.pd] = i.value.trim() }); return { mapeamento: mp, padroes: pd } }
    const ok = $('#mOk'); if (ok) ok.onclick = () => salvar(coleta())
    $('#mT').onclick = async () => { let ex; try { ex = JSON.parse($('#mEx').value) } catch (e) { $('#mR').textContent = 'JSON inválido.'; return } const r = await api('POST', `/api/integracoes/${encodeURIComponent(x.id)}/testar`, { exemplo: ex, ...coleta() }); $('#mR').textContent = r.ok ? JSON.stringify(r.resultado, null, 2) : '⚠ ' + r.erro }
  } else if (aba === 'entradas') {
    box.innerHTML = tabela([{ t: 'Quando', h: e => dh(e.em) }, { t: 'Canal', k: 'canal' }, { t: 'Ref. CRM', k: 'crm_ref' }, { t: 'Situação', h: e => `<span class="tag ${e.status === 'ok' ? 'ok' : 'bad'}">${esc(e.status)}</span>` }, { t: 'Erro', wrap: 1, k: 'erro' }, { t: '', h: (e, i) => e.payload ? `<button class="btn mini" data-ver="${i}">Ver</button>` : '' }], x.entradas, { vazio: 'Nada recebido ainda.', max: 640 })
    box.querySelectorAll('[data-ver]').forEach(b => b.onclick = () => modal(`<h2>Conteúdo recebido</h2><pre class="code">${esc(JSON.stringify(x.entradas[+b.dataset.ver].payload, null, 2))}</pre>`))
  } else if (aba === 'saidas') {
    box.innerHTML = `${adm() ? '<div class="row" style="margin-bottom:10px"><button class="btn" id="sS">Processar agora</button></div>' : ''}` + tabela([{ t: 'Quando', h: s => dh(s.em) }, { t: 'Ref. CRM', k: 'crm_ref' }, { t: 'OS', k: 'numero_os' }, { t: 'Status', k: 'status_os' }, { t: 'Entrega', h: s => s.enviado_em ? `<span class="tag ok">HTTP ${s.http_status}</span>` : s.erro ? `<span class="tag bad" title="${esc(s.erro)}">falha (${s.tentativas}x)</span>` : '<span class="tag">consulta</span>' }, { t: 'Erro', wrap: 1, k: 'erro' }], x.saidas, { vazio: 'Nenhum status ainda.', max: 640 })
    const s = $('#sS'); if (s) s.onclick = async () => { try { const r = await api('POST', '/api/sincronizar'); toast(`${r.status_enviados_ao_crm} enviado(s), ${r.falhas_de_envio} falha(s).`); K.render() } catch (e) { toast(e.message) } }
  } else {
    box.innerHTML = `<div class="panel"><h2>Contingência: carregar solicitações por arquivo</h2><p class="small muted">Para quando o CRM não consegue enviar. JSON (objeto, lista ou {"solicitacoes": […]}) ou CSV com cabeçalhos do formato neutro (<code>crm_ref, titulo, cliente.nome, contato.telefone…</code>). O de-para da integração também vale aqui.</p>
      ${adm() ? '<label class="btn pri">Escolher arquivo<input type="file" id="aF" accept=".json,.csv" hidden></label>' : '<div class="note">Somente o integrador importa arquivos.</div>'}<pre class="code" id="aR" hidden></pre></div>`
    const f = $('#aF'); if (f) f.onchange = async () => { const fd = new FormData(); fd.append('arquivo', f.files[0]); try { const r = await api('POST', `/api/integracoes/${encodeURIComponent(x.id)}/importar`, fd, true); $('#aR').hidden = false; $('#aR').textContent = `${r.recebidas} recebida(s)` + (r.erros.length ? '\n' + r.erros.map(e => `item ${e.item}: ${e.erro}`).join('\n') : ''); toast(`${r.recebidas} solicitação(ões) recebida(s).`) } catch (e) { toast(e.message) } }
  }
}
async function telaFormato(m) {
  m.innerHTML = `<h1>Formato neutro (${esc(st.meta.formato)})</h1><p class="lede">O mesmo contrato para qualquer CRM. Adaptadores específicos ficam para depois; o de-para cobre a maioria dos casos.</p>
    <div class="grid2"><div class="panel"><h2>Entrada: solicitação de serviço</h2><pre class="code">${esc(JSON.stringify(EXEMPLO, null, 2))}</pre><p class="small muted">Obrigatórios: <code>crm_ref</code> e <code>titulo</code>. Prioridade: baixa, media, alta, urgente (aceita low/medium/high/urgent). Tipo: códigos da ferramenta Ordens de Serviço.</p></div>
    <div class="panel"><h2>Saída: status da OS</h2><pre class="code">${esc(JSON.stringify({ formato: st.meta.formato, evento: 'status_os', crm_ref: 'T-100', numero_os: 'OS-2026-00012', status: 'planejada', status_nome: 'Planejada', sla: 'no_prazo', prazo_sla: '2026-11-02T18:00:00Z', execucao_inicio: '2026-11-02T13:00:00Z', equipe: 'Equipe Elétrica', concluida_em: null, motivo: null, resumo_execucao: null, atualizado_em: '2026-11-01T15:20:00Z', versao: 3 }, null, 2))}</pre><p class="small muted">status: aberta, planejada, em_execucao, pausada, concluida, encerrada, cancelada.</p></div></div>`
}
K.iniciar({
  nome: 'Conector CRM', inicio: 'inicio', subtitulo: 'Integrações',
  nav: () => [['inicio', 'Integrações'], ['formato', 'Formato neutro'], K.eAdmin() && ['usuarios', 'Usuários e perfis']],
  rotas: { inicio: telaInicio, integracao: telaIntegracao, formato: telaFormato, usuarios: K.telaUsuarios }
})
