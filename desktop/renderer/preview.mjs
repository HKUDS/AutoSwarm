// Side panel for links in replies, as in the Codex app, with browser-like tabs: web pages and
// local HTML and images in an embedded browser (Electron <webview>, locked down by the main
// process), PDFs in Chromium's PDF viewer, Markdown rendered, other text with line numbers.
// It lives outside #app, so the page re-rendering on every update never reloads it, and each
// tab keeps its page alive while another tab is in front.
import { t } from './i18n.mjs';
import { icon } from './icons.mjs';

const WIDTH = 'autoswarm-desk-preview-width';
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const dirOf = file => String(file).replace(/[\\/][^\\/]*$/, '');
// Pages and images go in a locked-down webview; PDFs in an iframe, where Chromium's PDF viewer can draw.
const EMBEDDED = new Set(['web', 'html', 'image']);

/** A link target: a web or e-mail address, or a file path with an optional line (`a.py:42`, `a.py#L42`). */
export function parseTarget(href) {
  let raw = String(href || '').trim();
  if (!raw || raw.startsWith('#') || /^(javascript|data|vbscript):/i.test(raw)) return null;
  if (/^https?:\/\//i.test(raw)) return { kind: 'web', url: raw };
  if (/^mailto:/i.test(raw)) return { kind: 'mail', url: raw };
  if (/^[a-z][\w+.-]+:\/\//i.test(raw) && !/^file:/i.test(raw)) return null;
  if (!/^file:/i.test(raw)) { try { raw = decodeURI(raw); } catch { /* Keep it as written. */ } }
  let path = raw, line = 0;
  const hash = path.match(/#L(\d+)(?:C\d+)?(?:-L?\d+)?$/i);
  if (hash) { line = Number(hash[1]); path = path.slice(0, hash.index); }
  else {
    const suffix = path.match(/^(.{2,}?):(\d+)(?::\d+)?$/);
    if (suffix) { path = suffix[1]; line = Number(suffix[2]); }
  }
  if (!/^file:/i.test(path)) path = path.replace(/#.*$/, '').replace(/^\/([a-z]:[\\/])/i, '$1');
  return path ? { kind: 'file', path, line } : null;
}

const DOCUMENTS = /\.(md|markdown|html?|pdf|txt|csv|json|ipynb|png|jpe?g|svg|webp|docx|xlsx|pptx)$/i;
/** Inline code that names a file: it has a folder, or a document-like extension. */
export function looksLikePath(text) {
  const value = String(text || '').trim();
  if (!value || value.length > 260 || /\s/.test(value) || /^[a-z][\w+.-]*:\/\//i.test(value)) return false;
  const bare = value.replace(/(?::\d+(?::\d+)?|#L\d+)$/, '');
  if (!/^(?:[a-zA-Z]:)?[\w@.~()+\\/-]+\.[A-Za-z][A-Za-z0-9]{0,7}$/.test(bare)) return false;
  return /[\\/]/.test(bare) || DOCUMENTS.test(bare);
}

const hostOf = url => { try { return new URL(url).host || url; } catch { return url; } };
/** The same page or file opens the tab that already shows it. */
const keyOf = item => item.kind === 'web' ? item.url : String(item.path).toLowerCase();
const glyph = item => EMBEDDED.has(item.kind) && item.kind !== 'image' ? 'globe' : item.kind === 'image' ? 'eye' : 'file';

export function createPreview({ desk, markdown, showToast, onLayout = () => {} }) {
  const pane = document.querySelector('#preview');
  let tabs = [], active = null, collapsed = false, sequence = 0;
  const current = () => tabs.find(tab => tab.id === active);

  /** Open a link: in a new tab (or the tab already showing it), or in this tab when clicked inside it. */
  async function open(href, base, { here = false } = {}) {
    const target = parseTarget(href);
    if (!target) return;
    if (target.kind === 'mail') { if (desk.openExternal) await desk.openExternal(target.url).catch(error => showToast(error.message)); return; }
    if (!desk.preview) {  // Browser preview: no main process to read files or host pages.
      if (target.kind === 'web') window.open(target.url, '_blank', 'noopener');
      else showToast(t('文件只能在桌面版中预览'));
      return;
    }
    let item;
    if (target.kind === 'web') item = { kind: 'web', url: target.url, name: hostOf(target.url) };
    else {
      try { item = await desk.preview(target.path, base); } catch (error) { showToast(error.message); return; }
      if (item.kind === 'missing') { showToast(t('找不到 {path}', { path: item.path })); return; }
      if (item.kind === 'folder') { await desk.openPath(item.path).catch(error => showToast(error.message)); return; }
      item.line = target.line;
    }
    const tab = here && current();
    if (tab) { tab.back.push(tab.item); tab.item = item; tab.stale = true; }
    else {
      const existing = tabs.find(other => keyOf(other.item) === keyOf(item));
      if (existing) { if (existing.item.line !== item.line) { existing.item = { ...existing.item, line: item.line }; existing.stale = true; } active = existing.id; }
      else { const fresh = { id: 'tab-' + (++sequence), item, back: [], stale: true }; tabs.push(fresh); active = fresh.id; }
    }
    collapsed = false;
    draw();
  }
  function closeTab(id) {
    const index = tabs.findIndex(tab => tab.id === id);
    if (index < 0) return;
    tabs.splice(index, 1);
    pane.querySelector(`.preview-page[data-tab="${id}"]`)?.remove();
    if (active === id) active = (tabs[index] || tabs[index - 1])?.id || null;
    draw();
  }
  function toggle(show = collapsed) { if (tabs.length) { collapsed = !show; draw(); } }

  function head(tab) {
    const item = tab.item, web = item.kind === 'web', embedded = EMBEDDED.has(item.kind);
    const button = (action, name, label, disabled = false) => `<button type="button" class="icon-button preview-action" data-preview="${action}" title="${esc(label)}" aria-label="${esc(label)}" ${disabled ? 'disabled' : ''}>${icon(name)}</button>`;
    return `${button('back', 'back', t('后退'), !tab.back.length && !embedded)}${embedded ? button('forward', 'chevron', t('前进')) : ''}${button('reload', 'reload', t('重新载入'))}<div class="preview-title"><strong>${esc(item.title || item.name)}</strong><small title="${esc(item.url || item.path)}">${esc(web ? item.url : item.path)}</small></div>${web ? button('external', 'external', t('在浏览器中打开')) : button('open', 'external', t('用默认应用打开'))}${item.path ? button('reveal', 'folder', t('在文件夹中显示')) : ''}${button('collapse', 'panel', t('收起侧栏'))}`;
  }
  function strip() {
    return tabs.map(tab => `<div class="preview-tab ${tab.id === active ? 'is-active' : ''}" role="tab" tabindex="0" aria-selected="${tab.id === active}" data-preview-tab="${tab.id}" title="${esc(tab.item.url || tab.item.path)}"><span class="preview-tab-icon">${icon(glyph(tab.item))}</span><span class="preview-tab-title">${esc(tab.item.title || tab.item.name)}</span><button type="button" class="preview-tab-close" data-preview-close="${tab.id}" title="${esc(t('关闭标签页'))}" aria-label="${esc(t('关闭标签页'))}">${icon('close')}</button></div>`).join('');
  }
  function body(item) {
    if (EMBEDDED.has(item.kind)) return `<webview src="${esc(item.url)}" partition="desk-preview" allowpopups></webview><div class="preview-error" hidden></div>`;
    if (item.kind === 'pdf') return `<iframe class="preview-frame" src="${esc(item.url)}" title="${esc(item.name)}"></iframe>`;
    const truncated = item.truncated ? `<p class="preview-note">${esc(t('文件较大，只显示前 2 MB。'))}</p>` : '';
    if (item.kind === 'markdown') return `<div class="preview-scroll"><div class="preview-doc markdown">${markdown(item.text || '')}</div>${truncated}</div>`;
    if (item.kind === 'text') return `<div class="preview-scroll"><pre class="preview-code">${(item.text || '').split(/\r?\n/).map((line, i) => `<span class="ln ${i + 1 === item.line ? 'is-target' : ''}" data-n="${i + 1}">${esc(line) || ' '}</span>`).join('')}</pre>${truncated}</div>`;
    return `<div class="preview-empty"><p>${esc(t('这个文件不能在 Desk 里预览。'))}</p><button type="button" class="secondary" data-preview="open">${esc(t('用默认应用打开'))}</button></div>`;
  }
  /** A tab's page is built once (and again only when the tab navigates), then kept while hidden. */
  function page(tab) {
    let element = pane.querySelector(`.preview-page[data-tab="${tab.id}"]`);
    if (element && !tab.stale) return element;
    if (!element) { element = document.createElement('div'); element.className = 'preview-page'; element.dataset.tab = tab.id; pane.querySelector('.preview-body').append(element); }
    element.innerHTML = body(tab.item);
    tab.stale = false;
    const view = element.querySelector('webview');
    if (view) {
      const error = element.querySelector('.preview-error');
      const update = () => { refreshChrome(); };
      view.addEventListener('page-title-updated', event => { tab.item.title = event.title; update(); });
      view.addEventListener('did-navigate', event => { tab.item.url = event.url; if (tab.item.kind === 'web') tab.item.name = hostOf(event.url); error.hidden = true; update(); });
      view.addEventListener('did-navigate-in-page', event => { if (event.isMainFrame) { tab.item.url = event.url; update(); } });
      view.addEventListener('did-fail-load', event => {
        if (event.errorCode === -3 || !event.isMainFrame) return;  // -3: a navigation replaced this one.
        error.hidden = false; error.textContent = t('无法打开：{reason}', { reason: event.errorDescription || event.errorCode });
      });
    }
    requestAnimationFrame(() => element.querySelector('.ln.is-target')?.scrollIntoView({ block: 'center' }));
    return element;
  }
  const refreshChrome = () => {
    const tab = current();
    pane.querySelector('.preview-tabs').innerHTML = strip();
    if (tab) pane.querySelector('.preview-head').innerHTML = head(tab);
  };

  function draw() {
    pane.hidden = !tabs.length || collapsed;
    document.body.classList.toggle('has-preview', !pane.hidden);
    if (!pane.querySelector('.preview-body')) pane.innerHTML = `<div class="preview-resize" data-preview-resize title="${esc(t('拖动调整宽度'))}"></div><div class="preview-tabs" role="tablist" aria-label="${esc(t('预览标签页'))}"></div><div class="preview-head"></div><div class="preview-body"></div>`;
    for (const tab of tabs) page(tab).classList.toggle('is-active', tab.id === active);
    refreshChrome();
    pane.querySelector('.preview-tab.is-active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    onLayout();
  }
  const activePage = () => pane.querySelector(`.preview-page[data-tab="${active}"]`);

  pane.addEventListener('click', async event => {
    const close = event.target.closest('[data-preview-close]');
    if (close) { closeTab(close.dataset.previewClose); return; }
    const chosen = event.target.closest('[data-preview-tab]');
    if (chosen) { active = chosen.dataset.previewTab; draw(); return; }
    const control = event.target.closest('[data-preview]'), tab = current();
    if (!control || !tab) return;
    const view = activePage()?.querySelector('webview');
    try {
      switch (control.dataset.preview) {
        case 'back': if (view?.canGoBack()) view.goBack(); else if (tab.back.length) { tab.item = tab.back.pop(); tab.stale = true; draw(); } break;
        case 'forward': if (view?.canGoForward()) view.goForward(); break;
        case 'reload':
          if (view) view.reload();
          else if (tab.item.kind === 'pdf') activePage().querySelector('.preview-frame').src = tab.item.url;
          else if (tab.item.path) { const fresh = await desk.preview(tab.item.path, ''); if (fresh.kind !== 'missing') { tab.item = { ...fresh, line: tab.item.line }; tab.stale = true; draw(); } }
          break;
        case 'external': await desk.openExternal(tab.item.url); break;
        case 'open': await desk.openPath(tab.item.path); break;
        case 'reveal': await desk.showPath(tab.item.path); break;
        case 'collapse': toggle(false); break;
      }
    } catch (error) { showToast(error.message); }
  });
  // The tab strip scrolls sideways with the mouse wheel (its scrollbar is hidden).
  pane.addEventListener('wheel', event => { const strip = event.target.closest('.preview-tabs'); if (strip && !event.shiftKey && Math.abs(event.deltaY) > Math.abs(event.deltaX)) { strip.scrollLeft += event.deltaY; event.preventDefault(); } }, { passive: false });
  // Middle-click closes a tab, as in a browser; Enter or Space selects one from the keyboard.
  pane.addEventListener('auxclick', event => { const tab = event.target.closest('[data-preview-tab]'); if (tab && event.button === 1) { event.preventDefault(); closeTab(tab.dataset.previewTab); } });
  pane.addEventListener('keydown', event => { const tab = event.target.closest('[data-preview-tab]'); if (tab && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); active = tab.dataset.previewTab; draw(); } });
  // Drag the left edge to resize; the width is kept for next time.
  const setWidth = px => pane.style.setProperty('--preview-w', `${Math.round(px)}px`);
  try { const saved = Number(localStorage.getItem(WIDTH)); if (saved) setWidth(saved); } catch { /* Preview without storage. */ }
  pane.addEventListener('pointerdown', event => {
    if (!event.target.closest('[data-preview-resize]')) return;
    event.preventDefault();
    const right = pane.getBoundingClientRect().right;
    document.body.classList.add('is-resizing');
    const move = e => setWidth(Math.min(Math.max(right - e.clientX, 320), window.innerWidth - 420));
    const done = () => {
      document.body.classList.remove('is-resizing');
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', done);
      try { localStorage.setItem(WIDTH, String(Math.round(pane.getBoundingClientRect().width))); } catch { /* Preview without storage. */ }
      onLayout();
    };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', done);
  });
  return {
    open, toggle,
    /** Where relative links inside the panel point: next to the file in the active tab. */
    base: () => current()?.item.path ? dirOf(current().item.path) : '',
    get count() { return tabs.length; },
    get collapsed() { return collapsed; },
  };
}
