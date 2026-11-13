// Classic scripts work from file:// too. Give actionable feedback if the
// bundled app is missing or fails before it renders, instead of an endless wait.
(() => {
  // Paint the last chosen appearance before the app bundle runs, so a dark
  // window does not flash white. The desktop preference file stays the source
  // of truth; this cached value only covers the first frame.
  try {
    const choice = localStorage.getItem('autoswarm-desk-theme') || 'system';
    const dark = choice === 'dark' || (choice === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  } catch { document.documentElement.dataset.theme = 'light'; }
  function failed() {
    const loading = document.querySelector('#app .loading');
    if (!loading) return;
    loading.replaceChildren();
    const title = document.createElement('h2');
    title.textContent = '界面未能启动';
    const message = document.createElement('p');
    message.textContent = '请保留完整 desktop 文件夹后刷新页面，或打开 dist / win-unpacked 中的 AutoSwarm Desk.exe。';
    loading.append(title, message);
  }
  window.addEventListener('error', failed, true);
  window.addEventListener('unhandledrejection', failed);
  setTimeout(failed, 10000);
})();
