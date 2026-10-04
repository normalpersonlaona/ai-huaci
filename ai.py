"""
AI 调用 —— 只用标准库 urllib，不装任何第三方包。

全部走 OpenAI 兼容协议：POST {baseUrl}/chat/completions

流式的解析方式：服务端按 SSE 一坨一坨地吐
    data: {"choices":[{"delta":{"content":"你"}}]}
    data: {"choices":[{"delta":{"content":"好"}}]}
    data: [DONE]
我们按行读，把 content 拼起来，每拿到一小段就回调一次。
"""

import json
import urllib.request
import urllib.error


def _endpoint(base_url, path):
    return base_url.rstrip('/') + path


def _headers(api_key):
    return {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + api_key,
        'Accept': 'text/event-stream',
    }


def stream_chat(cfg, user_text, action, on_delta, on_done, on_error, should_stop=None):
    """
    流式对话。**这个函数是阻塞的，要在工作线程里调用。**

    on_delta(str)    —— 每收到一小段文字调一次（累计全文）
    on_done(str)     —— 正常结束，参数是完整文本
    on_error(str)    —— 出错，参数是给人看的中文说明
    should_stop()    —— 返回 True 就中断（用户把浮窗关了）
    """
    if should_stop is None:
        should_stop = lambda: False

    base = (cfg['baseUrl'] or '').strip()
    key = (cfg['apiKey'] or '').strip()
    model = (cfg['model'] or '').strip()

    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': cfg['systemPrompt']},
            {'role': 'user', 'content': f"{action.get('prompt', '')}\n\n{user_text}"},
        ],
        'stream': True,
        'temperature': cfg['temperature'],
        'max_tokens': cfg['maxTokens'],
    }

    req = urllib.request.Request(
        _endpoint(base, '/chat/completions'),
        data=json.dumps(body).encode('utf-8'),
        headers=_headers(key),
        method='POST',
    )

    acc = []
    try:
        with urllib.request.urlopen(req, timeout=cfg['timeout']) as resp:
            for raw in resp:
                if should_stop():
                    return
                line = raw.decode('utf-8', 'replace').strip()
                if not line or not line.startswith('data:'):
                    continue
                payload = line[5:].strip()
                if payload == '[DONE]':
                    break
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                if obj.get('error'):
                    err = obj['error']
                    on_error(str(err.get('message') or err))
                    return

                choices = obj.get('choices') or []
                if not choices:
                    continue
                delta = choices[0].get('delta') or {}
                piece = delta.get('content')
                if piece:
                    acc.append(piece)
                    on_delta(''.join(acc))

        if not should_stop():
            on_done(''.join(acc) or '（模型没返回内容）')

    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')[:300]
        except Exception:
            pass
        on_error(_explain_http(e.code, detail))
    except urllib.error.URLError as e:
        on_error(f'连不上接口：{e.reason}\n检查一下接口地址对不对、网络通不通。')
    except TimeoutError:
        on_error(f'等了 {cfg["timeout"]} 秒还没响应，超时了。')
    except Exception as e:
        on_error(f'出错了：{e}')


def _explain_http(code, detail):
    hint = {
        401: 'API Key 不对，或者没填。去设置里检查一下。',
        403: 'Key 没权限访问这个模型，或者账号被限制了。',
        404: '接口地址或模型名不对。检查 base_url 和模型名。',
        429: '请求太频繁，或者余额用完了。',
        500: '服务商那边出错了，等会儿再试。',
        502: '服务商网关错误，等会儿再试。',
        503: '服务商暂时不可用，等会儿再试。',
    }.get(code, '')
    msg = f'HTTP {code}'
    if hint:
        msg += f' —— {hint}'
    if detail:
        msg += f'\n\n服务商返回：{detail}'
    return msg


def list_models(base_url, api_key, timeout=20):
    """
    拉一下这家有哪些模型（走 /models 接口）。
    返回 (模型名列表, 错误说明)，成功时第二个是空串。
    """
    req = urllib.request.Request(
        _endpoint(base_url, '/models'),
        headers={'Authorization': 'Bearer ' + api_key},
        method='GET',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            obj = json.loads(resp.read().decode('utf-8', 'replace'))
        arr = obj.get('data') if isinstance(obj, dict) else None
        if not isinstance(arr, list):
            arr = obj.get('models') if isinstance(obj, dict) else None
        if not isinstance(arr, list):
            return [], '返回格式看不懂'
        ids = []
        for m in arr:
            mid = m if isinstance(m, str) else (m or {}).get('id')
            if mid:
                ids.append(str(mid))
        return sorted(set(ids)), ''
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')[:200]
        except Exception:
            pass
        return [], _explain_http(e.code, detail)
    except Exception as e:
        return [], f'拉取失败：{e}'
