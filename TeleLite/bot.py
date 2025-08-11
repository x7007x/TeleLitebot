import asyncio
import json
from functools import wraps
from flask import Flask, request
import urllib3

update_types = [
    'message', 'edited_message', 'channel_post', 'edited_channel_post',
    'business_connection', 'business_message', 'edited_business_message',
    'deleted_business_messages', 'message_reaction', 'message_reaction_count',
    'inline_query', 'chosen_inline_result', 'callback_query', 'shipping_query',
    'pre_checkout_query', 'purchased_paid_media', 'poll', 'poll_answer',
    'my_chat_member', 'chat_member', 'chat_join_request', 'chat_boost',
    'removed_chat_boost'
]

def safe_print(obj):
    try:
        print(json.dumps(obj, indent=4, ensure_ascii=False))
    except Exception:
        print(obj)


class FilterBase:
    def __call__(self, update):
        return True

    def __and__(self, other):
        return AndFilter(self, other)

    def __or__(self, other):
        return OrFilter(self, other)

    def __invert__(self):
        return NotFilter(self)


class AndFilter(FilterBase):
    def __init__(self, f1, f2):
        self.f1 = f1
        self.f2 = f2

    def __call__(self, update):
        return self.f1(update) and self.f2(update)


class OrFilter(FilterBase):
    def __init__(self, f1, f2):
        self.f1 = f1
        self.f2 = f2

    def __call__(self, update):
        return self.f1(update) or self.f2(update)


class NotFilter(FilterBase):
    def __init__(self, f):
        self.f = f

    def __call__(self, update):
        return not self.f(update)


class FilterWrapper(FilterBase):
    def __init__(self, func):
        self.func = func

    def __call__(self, update):
        try:
            return self.func(update)
        except Exception:
            return False


class Filters:

    @staticmethod
    def text(*texts):
        valid_texts = set(texts)

        def func(update):
            txt = update.get("text")
            if txt is None:
                return False
            if not valid_texts:
                return True
            return txt in valid_texts

        return FilterWrapper(func)

    @staticmethod
    def user(*user_ids):
        valid_ids = set(user_ids)

        def func(update):
            from_user = update.get("from_user")
            uid = from_user.get("id") if isinstance(from_user, dict) else None
            return (not valid_ids) or (uid in valid_ids)

        return FilterWrapper(func)

    @staticmethod
    def chat(*chat_ids):
        valid_ids = set(chat_ids)

        def func(update):
            chat = update.get("chat")
            cid = chat.get("id") if isinstance(chat, dict) else None
            return (not valid_ids) or (cid in valid_ids)

        return FilterWrapper(func)

    @staticmethod
    def command(*commands):
        valid_cmds = set()

        for cmd in commands:
            if cmd.startswith('/'):
                valid_cmds.add(cmd)
            else:
                valid_cmds.add('/' + cmd)
                valid_cmds.add(cmd)

        def func(update):
            entities = update.get("entities", []) or update.get("caption_entities", [])
            text = update.get("text", "") or update.get("caption", "")
            if not text:
                return False
            for e in entities:
                if e.get("type") == "bot_command" and e.get("offset") == 0:
                    cmd_txt = text[0:e.get("length", 0)]
                    if '@' in cmd_txt:
                        cmd_txt = cmd_txt.split('@')[0]
                    if cmd_txt in valid_cmds:
                        return True
            return text in valid_cmds

        return FilterWrapper(func)

    @staticmethod
    def regex(pattern):
        import re
        r = re.compile(pattern)

        def func(update):
            text = update.get("text", "") or update.get("caption", "")
            return bool(text and r.search(text))

        return FilterWrapper(func)

    @staticmethod
    def has_text():
        return FilterWrapper(lambda u: "text" in u and u["text"] is not None)

    @staticmethod
    def has_photo():
        return FilterWrapper(lambda u: "photo" in u and u["photo"] is not None)

    @staticmethod
    def has_document():
        return FilterWrapper(lambda u: "document" in u and u["document"] is not None)

    @staticmethod
    def has_video():
        return FilterWrapper(lambda u: "video" in u and u["video"] is not None)

    @staticmethod
    def has_audio():
        return FilterWrapper(lambda u: "audio" in u and u["audio"] is not None)

    @staticmethod
    def has_voice():
        return FilterWrapper(lambda u: "voice" in u and u["voice"] is not None)

    @staticmethod
    def edited():
        return FilterWrapper(lambda u: u.get("edit_date") is not None)

    @staticmethod
    def forwarded():
        return FilterWrapper(lambda u: any(u.get(k) is not None for k in ["forward_from", "forward_from_chat", "forward_date"]))

    @staticmethod
    def reply():
        return FilterWrapper(lambda u: u.get("reply_to_message") is not None)


filters = Filters()


def match_filter(item, filt):
    if filt is None:
        return True
    if callable(filt):
        try:
            return filt(item)
        except Exception:
            return False
    if isinstance(filt, dict) and isinstance(item, dict):
        for k, v in filt.items():
            if k not in item or (isinstance(v, dict) and not match_filter(item[k], v)) or (not isinstance(v, dict) and item[k] != v):
                return False
        return True
    return False


class Bot:
    def __init__(self, token, webhook=None):
        self.token = token
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.handlers = {ut: [] for ut in update_types}
        self.webhook = webhook
        self.app = Flask(__name__)
        self.http = urllib3.PoolManager()

        @self.app.route('/alive')
        def handle_alive():
            return 'Alive 🕯️', 200

        @self.app.route('/webhook', methods=['POST'])
        def handle_webhook():
            update = request.get_json()
            if not update:
                return 'No Data', 400
            update_type = self._extract_update_type(update)
            if update_type in self.handlers:
                data = self._fix_reserved_keys(update.get(update_type, {}))
                self._process_handlers(update_type, data)
            return '🚀', 200

    def _extract_update_type(self, update):
        return next((ut for ut in update_types if ut in update), None)

    def _fix_reserved_keys(self, data):
        if isinstance(data, dict):
            if 'from' in data:
                data['from_user'] = data.pop('from')
            for k, v in data.items():
                if isinstance(v, dict):
                    data[k] = self._fix_reserved_keys(v)
                elif isinstance(v, list):
                    data[k] = [self._fix_reserved_keys(i) if isinstance(i, dict) else i for i in v]
        return data

    def _process_handlers(self, update_type, data):
        for filt, handler in self.handlers.get(update_type, []):
            try:
                if filt is None or filt(data):
                    result = handler(data)
                    if asyncio.iscoroutine(result):
                        asyncio.run(result)
            except Exception as e:
                print(f"Handler error: {e}")

    def _handler_decorator(self, update_type, filter_):
        def decorator(fn):
            self.handlers[update_type].append((filter_, fn))

            @wraps(fn)
            def wrapper(*args, **kwargs):
                res = fn(*args, **kwargs)
                return asyncio.run(res) if asyncio.iscoroutine(res) else res

            return wrapper
        return decorator

    def on_message(self, filter_=None): return self._handler_decorator('message', filter_)
    def on_edited_message(self, filter_=None): return self._handler_decorator('edited_message', filter_)
    def on_channel_post(self, filter_=None): return self._handler_decorator('channel_post', filter_)
    def on_edited_channel_post(self, filter_=None): return self._handler_decorator('edited_channel_post', filter_)
    def on_business_connection(self, filter_=None): return self._handler_decorator('business_connection', filter_)
    def on_business_message(self, filter_=None): return self._handler_decorator('business_message', filter_)
    def on_edited_business_message(self, filter_=None): return self._handler_decorator('edited_business_message', filter_)
    def on_deleted_business_messages(self, filter_=None): return self._handler_decorator('deleted_business_messages', filter_)
    def on_message_reaction(self, filter_=None): return self._handler_decorator('message_reaction', filter_)
    def on_message_reaction_count(self, filter_=None): return self._handler_decorator('message_reaction_count', filter_)
    def on_inline_query(self, filter_=None): return self._handler_decorator('inline_query', filter_)
    def on_chosen_inline_result(self, filter_=None): return self._handler_decorator('chosen_inline_result', filter_)
    def on_callback_query(self, filter_=None): return self._handler_decorator('callback_query', filter_)
    def on_shipping_query(self, filter_=None): return self._handler_decorator('shipping_query', filter_)
    def on_pre_checkout_query(self, filter_=None): return self._handler_decorator('pre_checkout_query', filter_)
    def on_purchased_paid_media(self, filter_=None): return self._handler_decorator('purchased_paid_media', filter_)
    def on_poll(self, filter_=None): return self._handler_decorator('poll', filter_)
    def on_poll_answer(self, filter_=None): return self._handler_decorator('poll_answer', filter_)
    def on_my_chat_member(self, filter_=None): return self._handler_decorator('my_chat_member', filter_)
    def on_chat_member(self, filter_=None): return self._handler_decorator('chat_member', filter_)
    def on_chat_join_request(self, filter_=None): return self._handler_decorator('chat_join_request', filter_)
    def on_chat_boost(self, filter_=None): return self._handler_decorator('chat_boost', filter_)
    def on_removed_chat_boost(self, filter_=None): return self._handler_decorator('removed_chat_boost', filter_)

    async def __call__(self, method: str, **params):
        url = f"{self.api_url}/{method}"
        payload = json.dumps(params).encode('utf-8')
        response = self.http.request('POST', url, body=payload, headers={'Content-Type': 'application/json'})
        result = json.loads(response.data.decode('utf-8'))
        safe_print(result)
        return result

    def run(self):
        if self.webhook:
            print("Running Flask webhook server...")
            self.app.run(host='0.0.0.0', port=5000)
        else:
            print("Running long polling...")
            from time import sleep
            offset = 0
            while True:
                payload = json.dumps({"offset": offset, "timeout": 100, "allowed_updates": update_types})
                response = self.http.request('POST', f"{self.api_url}/getUpdates", body=payload, headers={'Content-Type': 'application/json'})
                try:
                    updates = json.loads(response.data.decode('utf-8'))
                except Exception:
                    sleep(1)
                    continue
                if updates.get('ok'):
                    for upd in updates['result']:
                        offset = max(offset, upd['update_id'] + 1)
                        utype = self._extract_update_type(upd)
                        if not utype:
                            continue
                        data = self._fix_reserved_keys(upd.get(utype, {}))
                        self._process_handlers(utype, data)
                sleep(0)
