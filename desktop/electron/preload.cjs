const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('desk', {
  platform: process.platform,
  initial: () => ipcRenderer.invoke('desk:initial'),
  chooseHome: () => ipcRenderer.invoke('desk:choose-home'),
  demo: () => ipcRenderer.invoke('desk:demo'),
  reconnect: () => ipcRenderer.invoke('desk:reconnect'),
  choosePython: () => ipcRenderer.invoke('desk:choose-python'),
  reveal: id => ipcRenderer.invoke('desk:reveal', id),
  native: agentId => ipcRenderer.invoke('desk:native', agentId),
  history: (id, before) => ipcRenderer.invoke('desk:history', id, before),
  assistantSend: text => ipcRenderer.invoke('desk:assistant-send', text),
  message: (target, text, mode) => ipcRenderer.invoke('desk:message', target, text, mode),
  assistantSettings: value => ipcRenderer.invoke('desk:assistant-settings', value),
  chooseWorkspace: () => ipcRenderer.invoke('desk:choose-workspace'),
  sessionStart: value => ipcRenderer.invoke('desk:session-start', value),
  sessionWorkspace: () => ipcRenderer.invoke('desk:session-workspace'),
  cancel: target => ipcRenderer.invoke('desk:cancel', target),
  decide: (call, request, decision, scope) => ipcRenderer.invoke('desk:decide', call, request, decision, scope),
  permissions: (provider, mode) => ipcRenderer.invoke('desk:permissions', provider, mode),
  permission: (agentId, mode) => ipcRenderer.invoke('desk:permission', agentId, mode),
  answer: (call, request, response) => ipcRenderer.invoke('desk:answer', call, request, response),
  copy: text => ipcRenderer.invoke('desk:copy', text),
  theme: value => ipcRenderer.invoke('desk:theme', value),
  language: value => ipcRenderer.invoke('desk:language', value),
  zoom: step => ipcRenderer.invoke('desk:zoom', step),
  preview: (target, base) => ipcRenderer.invoke('desk:preview', target, base),
  openExternal: url => ipcRenderer.invoke('desk:open-external', url),
  openPath: file => ipcRenderer.invoke('desk:open-path', file),
  showPath: file => ipcRenderer.invoke('desk:show-path', file),
  window: action => ipcRenderer.send('desk:window', action),
  preferences: value => ipcRenderer.invoke('desk:preferences', value),
  show: target => ipcRenderer.invoke('desk:show', target),
  tray: value => ipcRenderer.invoke('desk:tray', value),
  quit: () => ipcRenderer.invoke('desk:quit'),
  update: action => ipcRenderer.invoke('desk:update', action),
  openRules: () => ipcRenderer.invoke('desk:rules-open'),
  models: (provider, options) => ipcRenderer.invoke('desk:models', provider, options),
  onCatalog: callback => {
    const listener = (_, value) => callback(value);
    ipcRenderer.on('desk:catalog', listener);
    return () => ipcRenderer.removeListener('desk:catalog', listener);
  },
  skills: () => ipcRenderer.invoke('desk:skills'),
  skillAgent: host => ipcRenderer.invoke('desk:skill-agent', host),
  skillInstall: host => ipcRenderer.invoke('desk:skill-install', host),
  onFullScreen: callback => {
    const listener = (_, value) => callback(value === true);
    ipcRenderer.on('desk:fullscreen', listener);
    return () => ipcRenderer.removeListener('desk:fullscreen', listener);
  },
  onNavigate: callback => {
    const listener = (_, target) => callback(target);
    ipcRenderer.on('desk:navigate', listener);
    return () => ipcRenderer.removeListener('desk:navigate', listener);
  },
  subscribe: callback => {
    const listener = (_, frame) => callback(frame);
    ipcRenderer.on('desk:frame', listener);
    return () => ipcRenderer.removeListener('desk:frame', listener);
  }
});
