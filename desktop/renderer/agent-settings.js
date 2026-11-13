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

  function sourceName(source, zh, agents = []) {
    const key = source.key || '';
    if (key.startsWith('local:')) {
      const model = source.models?.[0] || key.slice(6);
      return /^deepseek/i.test(model)
        ? (zh ? `DeepSeek · ${model}（本机）` : `DeepSeek · ${model} (local)`)
        : (zh ? `${model}（Ollama 本机模型）` : `${model} (local Ollama model)`);
    }
    if (key === 'chatgpt') return 'ChatGPT';
    if (key === 'claude') return 'Claude';
    if (key === 'deepseek') return zh ? 'DeepSeek 在线模型' : 'DeepSeek hosted models';
    if (key === 'provider:opencode') return zh ? 'OpenCode 模型服务' : 'OpenCode model service';
    if (key.startsWith('provider:')) {
      const value = key.slice(9);
      return value ? value[0].toUpperCase() + value.slice(1) : value;
    }
    if (key.startsWith('agent:')) {
      const id = key.slice(6);
      return agents.find(agent => agent.provider === id)?.name || id;
    }
    return source.name || key;
  }

  async function refresh(force = false) {
    if (!dialog.open || (pendingProvider && !force)) return;
    const id = ++sequence;
    const node = section();
    const zh = isZh();
    node.replaceChildren(el('label', '', zh ? 'Agent 工具' : 'Agent tools'), el('p', '', zh
      ? 'OpenCode、DeepSeek Harness 等各自是独立工具。关闭某个工具只阻止 AutoSwarm 新建该工具的会话，已启动的会话继续运行。'
      : 'OpenCode, DeepSeek Harness and other tools are independent. Turning one off blocks new AutoSwarm sessions with it; active sessions continue.'));
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
      node.append(el('label', 'local-model-heading', zh ? '模型与服务' : 'Models and providers'));
      node.append(el('p', '', zh
        ? 'DeepSeek、Claude、ChatGPT、本地模型等按各自名称独立控制。这里的开关只控制 AutoSwarm Desk 新任务能否使用，不修改 OpenCode 或其他工具的原生设置，也不卸载模型；已启动的会话继续运行。'
        : 'DeepSeek, Claude, ChatGPT and local models are controlled under their own names. These switches control new AutoSwarm Desk tasks only; they do not edit native agent settings or uninstall models. Active sessions continue.'));
      if (!result.modelSources?.length) {
        node.append(el('p', 'agent-switch-empty', zh ? '尚未检测到模型服务。打开一次会话的模型列表后，这里会显示该 Agent 已报告的模型。' : 'No model providers detected yet. Open an agent model list once to show the models it reports here.'));
      } else {
        for (const source of result.modelSources) {
          const row = el('div', 'permission-row agent-switch-row model-source-row');
          const identity = el('div', 'permission-agent');
          const name = sourceName(source, zh, result.agents);
          const title = el('span', '', name);
          const agents = (source.agents || []).map(id => result.agents.find(agent => agent.provider === id)?.name || id);
          const details = [];
          if (source.installed) details.push(zh ? 'Ollama · 已安装在本机' : 'Ollama · installed locally');
          else if (source.key.startsWith('local:')) details.push(zh ? 'Ollama 模型路由' : 'Ollama model route');
          if (agents.length) details.push((zh ? '可由 ' : 'Available through ') + [...new Set(agents)].join('、'));
          if (source.models?.length) details.push(source.models.slice(0, 3).join('、') + (source.models.length > 3 ? (zh ? ` 等 ${source.models.length} 个` : ` and ${source.models.length - 3} more`) : ''));
          title.append(el('small', '', details.join(' · ') || (zh ? '已检测到' : 'Detected')));
          identity.append(title);
          const control = el('label', 'check-row agent-switch-control');
          const input = document.createElement('input');
          input.type = 'checkbox';
          input.checked = source.enabled;
          input.disabled = !!result.demo;
          input.setAttribute('aria-label', (zh ? '启用 ' : 'Enable ') + name);
          const controlText = el('span', '', source.enabled ? (zh ? '开启' : 'On') : (zh ? '关闭' : 'Off'));
          control.append(input, controlText);
          row.append(identity, control);
          input.addEventListener('change', async () => {
            pendingProvider = 'model:' + source.key;
            node.querySelectorAll('input[type="checkbox"]').forEach(item => { item.disabled = true; });
            let failure = null;
            try { await window.desk.modelSourceToggle(source.key); }
            catch (error) { failure = error; }
            finally { pendingProvider = ''; }
            await refresh(true);
            if (failure && node.isConnected) node.append(el('p', 'error-text', failure.message || String(failure)));
          });
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
