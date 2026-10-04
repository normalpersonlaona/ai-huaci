"""
配置读写。

⚠️ 配置文件存在 %APPDATA%\\ai-huaci\\ 下，**不在项目目录里**。
   因为里面有你自己的 API Key —— 将来要把项目开源的话，
   配置放外面就不会被一起传上去。
"""

import json
import os
import copy

APP_NAME = 'ai-huaci'

CONFIG_DIR = os.path.join(
    os.environ.get('APPDATA') or os.path.expanduser('~'), APP_NAME
)
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')


# ── 服务商预设（和浏览器插件那套保持一致，全部 OpenAI 兼容）──

PROVIDERS = [
    {
        'id': 'deepseek',
        'name': 'DeepSeek',
        'baseUrl': 'https://api.deepseek.com/v1',
        'models': ['deepseek-chat', 'deepseek-reasoner'],
        'keyUrl': 'https://platform.deepseek.com/api_keys',
    },
    {
        'id': 'glm',
        'name': '智谱 GLM',
        'baseUrl': 'https://open.bigmodel.cn/api/paas/v4',
        'models': ['glm-4-plus', 'glm-4-flash', 'glm-4v-plus'],
        'keyUrl': 'https://open.bigmodel.cn/usercenter/apikeys',
    },
    {
        'id': 'qwen',
        'name': '通义千问',
        'baseUrl': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'models': ['qwen-plus', 'qwen-max', 'qwen-turbo'],
        'keyUrl': 'https://bailian.console.aliyun.com/',
    },
    {
        'id': 'openai',
        'name': 'OpenAI GPT',
        'baseUrl': 'https://api.openai.com/v1',
        'models': ['gpt-4o', 'gpt-4o-mini'],
        'keyUrl': 'https://platform.openai.com/api-keys',
    },
    {
        'id': 'mimo',
        'name': '小米 MiMo',
        'baseUrl': 'https://api.xiaomimimo.com/v1',
        'models': ['mimo-v2.6-pro', 'mimo-v2.6-flash'],
        'keyUrl': 'https://platform.xiaomimimo.com/#/console/api-keys',
    },
    {
        'id': 'custom',
        'name': '自定义 / 中转',
        'baseUrl': '',
        'models': [],
        'keyUrl': '',
    },
]


def find_provider(pid):
    for p in PROVIDERS:
        if p['id'] == pid:
            return p
    return PROVIDERS[0]


# ── 默认配置 ──

DEFAULT_ACTIONS = [
    {
        'id': 'summarize',
        'label': '总结',
        'prompt': '把下面的内容总结成要点。用列表，不超过 5 条，每条一行。最后用一句话说「所以呢」。',
    },
    {
        'id': 'translate',
        'label': '翻译',
        'prompt': '把下面的内容翻译成中文；如果它已经是中文，就翻译成英文。只输出译文，不要解释、不要加引号。',
    },
    {
        'id': 'explain',
        'label': '是什么？',
        'prompt': '解释下面的内容：它是什么、为什么重要、有什么容易踩的坑。说人话，别堆术语。',
    },
]

DEFAULTS = {
    'provider': 'deepseek',
    'baseUrl': 'https://api.deepseek.com/v1',
    'apiKey': '',
    'model': 'deepseek-chat',
    'actions': DEFAULT_ACTIONS,

    # 浮窗行为
    'minChars': 2,          # 至少选中几个字才弹
    'skipTerminal': True,   # 终端里不弹
    'showDelay': 0,         # 弹窗延迟（毫秒），给某些慢吞吞的软件留时间

    # 请求
    'systemPrompt': '你是一个划词助手。回答要简洁、直接、说人话。回答用 Markdown。',
    'maxTokens': 1500,
    'temperature': 0.6,
    'timeout': 120,
}


class Config:
    def __init__(self):
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    # ── 读写 ──

    def load(self):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                self.data.update(saved)
        except FileNotFoundError:
            pass
        except Exception as e:
            print('[config] 读取失败，用默认值:', e)
        # 迁移：老配置可能缺字段
        for k, v in DEFAULTS.items():
            self.data.setdefault(k, copy.deepcopy(v))
        return self.data

    def save(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = CONFIG_PATH + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG_PATH)
            return True
        except Exception as e:
            print('[config] 保存失败:', e)
            return False

    # ── 便捷访问 ──

    def __getitem__(self, k):
        return self.data.get(k, DEFAULTS.get(k))

    def __setitem__(self, k, v):
        self.data[k] = v

    @property
    def actions(self):
        acts = self.data.get('actions') or []
        # 防止用户把动作全删光了，界面变成空的
        return acts if acts else copy.deepcopy(DEFAULT_ACTIONS)

    def provider(self):
        return find_provider(self.data.get('provider', 'deepseek'))

    def ready(self):
        """配置够不够发请求。返回 (能不能用, 原因)。"""
        if not (self.data.get('apiKey') or '').strip():
            return False, '还没填 API Key'
        if not (self.data.get('baseUrl') or '').strip():
            return False, '还没填接口地址'
        if not (self.data.get('model') or '').strip():
            return False, '还没填模型名'
        return True, ''


if __name__ == '__main__':
    c = Config()
    print('配置文件:', CONFIG_PATH)
    print('存在吗:', os.path.exists(CONFIG_PATH))
    print('服务商:', c['provider'], '| 模型:', c['model'])
    print('动作数:', len(c.actions))
    print('能发请求吗:', c.ready())
