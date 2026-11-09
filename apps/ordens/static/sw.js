/* Service worker do modo execução: guarda a casca da tela para abrir sem sinal. Os dados ficam no aparelho
   (cópia local + fila de ações em campo.js); chamadas à API passam direto. */
const CACHE = 'campo-os-v1.2'
const CASCA = ['campo.html', 'campo.js', 'campo.css', 'kit.js', 'base.css', 'manifest.json', 'icone-192.png', 'icone-512.png']
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(CASCA)).then(() => self.skipWaiting())) })
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith('campo-os-') && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())) })
self.addEventListener('fetch', e => {
  const r = e.request, u = new URL(r.url)
  if (r.method !== 'GET' || u.origin !== location.origin || u.pathname.includes('/api/')) return
  e.respondWith(fetch(r).then(resp => { if (resp.ok && resp.type === 'basic') { const cp = resp.clone(); caches.open(CACHE).then(c => c.put(r, cp)) } return resp })
    .catch(() => caches.match(r, { ignoreSearch: true }).then(x => x || caches.match('campo.html'))))
})
