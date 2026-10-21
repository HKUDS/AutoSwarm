"""Independent SDK peer: native histories/config live outside AutoSwarm records."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

config_path = None

class Config:
    def __init__(self, value):
        self.value = value
        self.agents = NS(defaults=NS(workspace=value['workspace'], model=value.get('model','native-default'), model_preset=None,
                                      provider='native-provider', reasoning_effort=value.get('effort')))
        self.tools = NS(restrict_to_workspace=value.get('restricted',True))
    def resolve_preset(self):
        return self.agents.defaults

def set_config_path(path):
    global config_path
    config_path = path

def load_config(path):
    return Config(json.loads(Path(path).read_text(encoding='utf-8')))

def resolve_config_env_vars(config, **kwargs):
    return config

class ToolRegistry:
    pass

class MCPProvider:
    @classmethod
    def from_config(cls, config, tools):
        return cls()

class AgentLoop:
    @classmethod
    def from_config(cls, config, **kwargs):
        return NS(config=config)

class Sessions:
    def __init__(self, root):
        self.root = Path(root)/'native-sessions'
        self.root.mkdir(parents=True,exist_ok=True)
    def path(self,key):
        return self.root/(key.replace(':','_')+'.json')
    def get(self,key):
        p=self.path(key)
        return json.loads(p.read_text()) if p.exists() else None
    async def ingest(self,key,messages,metadata):
        value=self.get(key) or {}
        value.update(metadata)
        self.save(key,value)
    def save(self,key,value):
        self.path(key).write_text(json.dumps(value),encoding='utf-8')

class Stream:
    def __init__(self,bot,text,key,channel):
        self.bot,self.text,self.key,self.channel=bot,text,key,channel
        self.cancelled=False
    async def stream_events(self):
        yield NS(type='tool.started',tool_call_id='native-tool',name='fixture-native-tool')
        gate = self.text.partition('gate:')[2].splitlines()[0] if self.text.startswith('gate:') else ''
        while gate and not Path(gate).exists() and not self.cancelled:
            await asyncio.sleep(.01)
        if self.cancelled:
            raise asyncio.CancelledError()
    async def wait(self):
        if self.cancelled:
            raise asyncio.CancelledError()
        state=self.bot.sessions.get(self.key)
        if self.text.startswith('remember:'):
            state['memory']=self.text[len('remember:'):]
            self.bot.sessions.save(self.key,state)
        content=state.get('memory','') if self.text=='recall' else self.text
        if self.text=='inspect-native':
            config=self.bot.config.agents.defaults
            content=json.dumps({'workspace':config.workspace,'model':config.model,'effort':config.reasoning_effort,
                                 'project':state['workspace_scope'],'channel':self.channel},ensure_ascii=False)
        return NS(content=content, error='native provider rejected' if self.text=='error' else None,
                  stop_reason='max_iterations' if self.text=='incomplete' else 'completed')
    async def aclose(self):
        self.cancelled=True

class Nanobot:
    def __init__(self,loop,config,mcp_provider):
        self.config=config
        self.sessions=Sessions(config.agents.defaults.workspace)
        self.runtime=NS(workspace=Path(config.agents.defaults.workspace))
    async def run_streamed(self,text,session_key,channel,chat_id):
        return Stream(self,text,session_key,channel)
    async def aclose(self):
        pass

def validate_workspace_scope_payload(raw,default_workspace,default_restrict_to_workspace):
    return NS(metadata=lambda:{'project_path':raw['project_path'],
                               'access_mode':raw.get('access_mode', 'restricted' if default_restrict_to_workspace else 'full')})

def image_gen_provider_configs(config):
    return {}

def create_file_edit_activity_hook(*args):
    pass

for name in ['nanobot.nanobot','nanobot.agent','nanobot.agent.loop','nanobot.agent.hooks',
             'nanobot.agent.tools','nanobot.agent.tools.mcp','nanobot.agent.tools.registry',
             'nanobot.config','nanobot.config.loader','nanobot.providers','nanobot.providers.image_generation',
             'nanobot.security','nanobot.security.workspace_access']:
    sys.modules[name]=sys.modules[__name__]
