(() => {
  const dialog = document.querySelector('#settings');
  const content = document.querySelector('#settings-content');
  if (!dialog || !content || !window.desk?.agentSwitches || !window.desk?.agentToggle) return;

  let pendingProvider = '';
  let sequence = 0;
  const isZh = () => document.documentElement.lang.toLowerCase().startsWith('zh');

  function section() {
    let node = content.querySelector('#agent-switches');
    if (node) return node;
    node = document.createElement('div');
    node.id = 'agent-switches';
    node.className = 'settings-section agent-switch-section';
    const anchor = content.querySelector('.appearance');
    if (anchor) anchor.after(node);
    else content.prepend(node);
    return node;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  async function refresh(force = false) {
    if (!dialog.open || (pendingProvider && !force)) return;
    const id = ++sequence;
    const node = section();
    const zh = isZh();
    node.replaceChildren(el('label', '', zh ? 'Agent 工具' : 'Agent tools'), el('p', '', zh
      ? 'OpenCode 是执行任务的 Agent。下方单独显示 Ollama 本地模型。关闭 Agent 会阻止新调用，已启动的会话继续运行。'
      : 'OpenCode is an agent that performs tasks. Local Ollama models appear separately below. Turning off an agent blocks new calls; active sessions continue.'));
    try {
      const result = await window.desk.agentSwitches();
      if (id !== sequence || !dialog.open || !node.isConnected) return;
      if (!result.agents.length) {
        node.append(el('p', 'agent-switch-empty', zh ? '当前没有检测到可用或已接入的 Agent。' : 'No available or configured agents were detected.'));
      }
      for (const agent of result.agents) {
        const row = el('div', 'permission-row agent-switch-row');
        const identity = el('div', 'permission-agent');
        const image = el('img', 'provider-logo');
        image.alt = '';
        image.src = '../assets/agents/' + agent.provider + (['kimi', 'nanobot'].includes(agent.provider) ? '.png' : '.svg');
        image.onerror = () => { image.hidden = true; };
        identity.append(image);
        const identityText = el('span', '', agent.name);
        const status = !agent.enabled
          ? (zh ? '已关闭' : 'Disabled')
          : agent.available
            ? (agent.version || (zh ? '本机可用' : 'Available locally'))
            : (zh ? '已接入，当前不可用' : 'Configured, currently unavailable');
        identityText.append(el('small', '', status));
        if (agent.error) identityText.append(el('small', 'error-text', agent.error));
        identity.append(identityText);

        const control = el('label', 'check-row agent-switch-control');
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = agent.enabled;
        input.disabled = !agent.manageable || !!result.demo;
        input.setAttribute('aria-label', (zh ? '启用 ' : 'Enable ') + agent.name);
        const controlText = el('span', '', agent.enabled ? (zh ? '开启' : 'On') : (zh ? '关闭' : 'Off'));
        if (!agent.manageable) controlText.append(el('small', '', zh ? '此启动方式暂不支持独立开关' : 'This launcher cannot be controlled independently'));
        control.append(input, controlText);
        row.append(identity, control);

        input.addEventListener('change', async () => {
          pendingProvider = agent.provider;
          node.querySelectorAll('input[type="checkbox"]').forEach(control => { control.disabled = true; });
          let failure = null;
          try {
            await window.desk.agentToggle(agent.provider);
          } catch (error) {
            failure = error;
          } finally {
            pendingProvider = '';
          }
          await refresh(true);
          if (failure && node.isConnected) node.append(el('p', 'error-text', failure.message || String(failure)));
        });
        node.append(row);
      }
      node.append(el('label', 'local-model-heading', zh ? '本地模型（Ollama）' : 'Local models (Ollama)'));
      node.append(el('p', '', zh ? '模型由 OpenCode 等 Agent 使用；Agent 开关不会安装或卸载模型。' : 'Agents such as OpenCode use these models. Agent switches do not install or remove models.'));
      if (!result.localModels?.length) {
        node.append(el('p', 'agent-switch-empty', zh ? '未检测到本机已安装的 Ollama 模型。' : 'No installed Ollama models detected.'));
      } else {
        for (const model of result.localModels) {
          const row = el('div', 'permission-row agent-switch-row local-model-row');
          const name = el('span', '', model);
          name.append(el('small', '', zh ? 'Ollama · 已安装在本机' : 'Ollama · installed locally'));
          row.append(name);
          node.append(row);
        }
      }
    } catch (error) {
      if (id === sequence && dialog.open && node.isConnected) node.append(el('p', 'error-text', error.message || String(error)));
    }
  }

  const observer = new MutationObserver(records => {
    if (!dialog.open) return;
    if (records.some(record => record.target === dialog) || !content.querySelector('#agent-switches')) void refresh();
  });
  observer.observe(dialog, { attributes: true, attributeFilter: ['open'] });
  observer.observe(content, { childList: true, subtree: true });
  dialog.addEventListener('close', () => { pendingProvider = ''; sequence++; });
})();
