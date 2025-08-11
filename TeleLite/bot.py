import asyncio
import json
import threading
import time
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import requests
import urllib3
from flask import Flask, request

update_types = [
    "message", "edited_message", "channel_post", "edited_channel_post",
    "business_connection", "business_message", "edited_business_message",
    "deleted_business_messages", "message_reaction", "message_reaction_count",
    "inline_query", "chosen_inline_result", "callback_query", "shipping_query",
    "pre_checkout_query", "purchased_paid_media", "poll", "poll_answer",
    "my_chat_member", "chat_member", "chat_join_request", "chat_boost",
    "removed_chat_boost"
]


def safe_print(obj: Any) -> None:
    try:
        print(json.dumps(obj, indent=4, ensure_ascii=False))
    except Exception:
        try:
            print(str(obj))
        except Exception:
            pass


class FilterBase:
    def __call__(self, update: Dict) -> bool:
        return True

    def __and__(self, other):
        return AndFilter(self, other)

    def __or__(self, other):
        return OrFilter(self, other)

    def invert(self):
        return NotFilter(self)


class AndFilter(FilterBase):
    def __init__(self, f1, f2):
        self.f1 = f1
        self.f2 = f2

    def __call__(self, update):
        try:
            return bool(self.f1(update)) and bool(self.f2(update))
        except Exception:
            return False


class OrFilter(FilterBase):
    def __init__(self, f1, f2):
        self.f1 = f1
        self.f2 = f2

    def __call__(self, update):
        try:
            return bool(self.f1(update)) or bool(self.f2(update))
        except Exception:
            return False


class NotFilter(FilterBase):
    def __init__(self, f):
        self.f = f

    def __call__(self, update):
        try:
            return not bool(self.f(update))
        except Exception:
            return False


class FilterWrapper(FilterBase):
    def __init__(self, func: Callable[[Dict], bool]):
        self.func = func

    def __call__(self, update: Dict) -> bool:
        try:
            return bool(self.func(update))
        except Exception:
            return False


class Filters:
    @staticmethod
    def text(*texts: str):
        valid_texts = set(texts)

        def func(update):
            txt = update.get("text") or update.get("caption")
            if txt is None:
                return False
            if not valid_texts:
                return True
            return txt in valid_texts

        return FilterWrapper(func)

    @staticmethod
    def user(*user_ids: int):
        valid_ids = set(user_ids)

        def func(update):
            uid = None
            from_user = update.get("from_user")
            if from_user and isinstance(from_user, dict):
                uid = from_user.get("id")
            return (not valid_ids) or (uid in valid_ids)

        return FilterWrapper(func)

    @staticmethod
    def chat(*chat_ids: int):
        valid_ids = set(chat_ids)

        def func(update):
            cid = None
            chat = update.get("chat")
            if chat and isinstance(chat, dict):
                cid = chat.get("id")
            return (not valid_ids) or (cid in valid_ids)

        return FilterWrapper(func)

    @staticmethod
    def command(*commands: str):
        valid_cmds = set()
        for cmd in commands:
            if cmd.startswith("/"):
                valid_cmds.add(cmd)
            else:
                valid_cmds.add("/" + cmd)
                valid_cmds.add(cmd)

        def func(update):
            entities = update.get("entities", []) or update.get("caption_entities", [])
            text = update.get("text", "") or update.get("caption", "")
            if not text:
                return False
            for e in entities:
                if e.get("type") == "bot_command" and e.get("offset") == 0:
                    command_text = text[0:e.get("length", 0)]
                    if "@" in command_text:
                        command_text = command_text.split("@")[0]
                    if command_text in valid_cmds:
                        return True
            if text in valid_cmds:
                return True
            for cmd in valid_cmds:
                if cmd.startswith("/") and text == cmd:
                    return True
            return False

        return FilterWrapper(func)

    @staticmethod
    def regex(pattern: str):
        import re

        r = re.compile(pattern)

        def func(update):
            text = update.get("text", "") or update.get("caption", "")
            if not text:
                return False
            return bool(r.search(text))

        return FilterWrapper(func)

    @staticmethod
    def has_text():
        def func(update):
            return "text" in update and update["text"] is not None

        return FilterWrapper(func)

    @staticmethod
    def has_photo():
        def func(update):
            return "photo" in update and update["photo"] is not None

        return FilterWrapper(func)

    @staticmethod
    def has_document():
        def func(update):
            return "document" in update and update["document"] is not None

        return FilterWrapper(func)

    @staticmethod
    def has_video():
        def func(update):
            return "video" in update and update["video"] is not None

        return FilterWrapper(func)

    @staticmethod
    def has_audio():
        def func(update):
            return "audio" in update and update["audio"] is not None

        return FilterWrapper(func)

    @staticmethod
    def has_voice():
        def func(update):
            return "voice" in update and update["voice"] is not None

        return FilterWrapper(func)

    @staticmethod
    def edited():
        def func(update):
            return update.get("edit_date") is not None

        return FilterWrapper(func)

    @staticmethod
    def forwarded():
        def func(update):
            return any(update.get(k) is not None for k in ["forward_from", "forward_from_chat", "forward_date"])

        return FilterWrapper(func)

    @staticmethod
    def reply():
        def func(update):
            return update.get("reply_to_message") is not None

        return FilterWrapper(func)


filters = Filters()


def match_filter(item: Any, filt: Any) -> bool:
    if filt is None:
        return True
    if callable(filt):
        try:
            return bool(filt(item))
        except Exception:
            return False
    if isinstance(filt, dict) and isinstance(item, dict):
        for k, v in filt.items():
            if k not in item:
                return False
            if isinstance(v, dict):
                if not match_filter(item[k], v):
                    return False
            elif item[k] != v:
                return False
        return True
    return False


class Bot:
    def __init__(self, token: str, webhook: Optional[str] = None, host: str = "0.0.0.0", port: int = 5000):
        self.token = token
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.handlers: Dict[str, List[Tuple[Any, Callable]]] = {ut: [] for ut in update_types}
        self.webhook = webhook
        self.app = Flask(__name__)
        self.host = host
        self.port = port
        self._start_event_loop()
        self._register_routes()

    def _start_event_loop(self):
        self.loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _register_routes(self):
        @self.app.route("/alive")
        def handle_alive():
            return "Alive 🕯️", 200

        @self.app.route("/webhook", methods=["POST"])
        def handle_webhook():
            update = request.get_json(silent=True)
            if not update:
                return "No Data", 400
            update_type = self._extract_update_type(update)
            if update_type and update_type in self.handlers:
                data = update.get(update_type, {})
                data = self._fix_reserved_keys(data)
                self._process_handlers(update_type, data)
            return "🚀", 200

    def _extract_update_type(self, update: Dict) -> Optional[str]:
        for ut in update_types:
            if ut in update:
                return ut
        return None

    def _fix_reserved_keys(self, data: Any) -> Any:
        if isinstance(data, dict):
            if "from" in data:
                data["from_user"] = data.pop("from")
            for k, v in list(data.items()):
                if isinstance(v, dict):
                    data[k] = self._fix_reserved_keys(v)
                elif isinstance(v, list):
                    data[k] = [self._fix_reserved_keys(i) if isinstance(i, dict) else i for i in v]
        return data

    def _process_handlers(self, update_type: str, data: Dict):
        for filt, handler in list(self.handlers.get(update_type, [])):
            try:
                ok = filt(data) if callable(filt) else match_filter(data, filt)
                if ok:
                    result = handler(data)
                    if asyncio.iscoroutine(result):
                        asyncio.run_coroutine_threadsafe(result, self.loop)
            except Exception as e:
                safe_print({"handler_error": str(e)})

    def _handler_decorator(self, update_type: str, filter_):
        def decorator(fn):
            self.handlers.setdefault(update_type, []).append((filter_, fn))

            @wraps(fn)
            def wrapper(*args, **kwargs):
                res = fn(*args, **kwargs)
                if asyncio.iscoroutine(res):
                    asyncio.run_coroutine_threadsafe(res, self.loop)
                return res

            return wrapper

        return decorator

    def on_message(self, filter_=None):
        return self._handler_decorator("message", filter_)

    def on_edited_message(self, filter_=None):
        return self._handler_decorator("edited_message", filter_)

    def on_channel_post(self, filter_=None):
        return self._handler_decorator("channel_post", filter_)

    def on_edited_channel_post(self, filter_=None):
        return self._handler_decorator("edited_channel_post", filter_)

    def on_business_connection(self, filter_=None):
        return self._handler_decorator("business_connection", filter_)

    def on_business_message(self, filter_=None):
        return self._handler_decorator("business_message", filter_)

    def on_edited_business_message(self, filter_=None):
        return self._handler_decorator("edited_business_message", filter_)

    def on_deleted_business_messages(self, filter_=None):
        return self._handler_decorator("deleted_business_messages", filter_)

    def on_message_reaction(self, filter_=None):
        return self._handler_decorator("message_reaction", filter_)

    def on_message_reaction_count(self, filter_=None):
        return self._handler_decorator("message_reaction_count", filter_)

    def on_inline_query(self, filter_=None):
        return self._handler_decorator("inline_query", filter_)

    def on_chosen_inline_result(self, filter_=None):
        return self._handler_decorator("chosen_inline_result", filter_)

    def on_callback_query(self, filter_=None):
        return self._handler_decorator("callback_query", filter_)

    def on_shipping_query(self, filter_=None):
        return self._handler_decorator("shipping_query", filter_)

    def on_pre_checkout_query(self, filter_=None):
        return self._handler_decorator("pre_checkout_query", filter_)

    def on_purchased_paid_media(self, filter_=None):
        return self._handler_decorator("purchased_paid_media", filter_)

    def on_poll(self, filter_=None):
        return self._handler_decorator("poll", filter_)

    def on_poll_answer(self, filter_=None):
        return self._handler_decorator("poll_answer", filter_)

    def on_my_chat_member(self, filter_=None):
        return self._handler_decorator("my_chat_member", filter_)

    def on_chat_member(self, filter_=None):
        return self._handler_decorator("chat_member", filter_)

    def on_chat_join_request(self, filter_=None):
        return self._handler_decorator("chat_join_request", filter_)

    def on_chat_boost(self, filter_=None):
        return self._handler_decorator("chat_boost", filter_)

    def on_removed_chat_boost(self, filter_=None):
        return self._handler_decorator("removed_chat_boost", filter_)

    async def __call__(self, method: str, **params):
        import aiohttp
        url = f"{self.api_url}/{method}"
        headers = {"Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=params, headers=headers) as resp:
                data = await resp.json()
                safe_print(data)
                return data

    def run(self, use_polling: bool = True, polling_timeout: int = 100, polling_sleep: float = 0.1):
        if self.webhook and use_polling is False:
            print("Running Flask webhook server...")
            self.app.run(host=self.host, port=self.port)
            return

        if self.webhook and use_polling:
            print("Webhook URL provided but run() called in polling mode. Starting polling instead.")

        print("Running long polling in background thread and Flask server for /alive and /webhook.")
        poll_thread = threading.Thread(
            target=self._polling_loop, args=(polling_timeout, polling_sleep), daemon=True
        )
        poll_thread.start()
        self.app.run(host=self.host, port=self.port)

    def _polling_loop(self, polling_timeout: int, polling_sleep: float):
        offset = 0
        http = urllib3.PoolManager()
        while True:
            try:
                resp = requests.post(
                    f"{self.api_url}/getUpdates",
                    json={"offset": offset, "timeout": polling_timeout, "allowed_updates": update_types},
                    timeout=polling_timeout + 10,
                )
                try:
                    updates = resp.json()
                except Exception:
                    time.sleep(1)
                    continue
                if updates.get("ok"):
                    for upd in updates.get("result", []):
                        offset = max(offset, upd["update_id"] + 1)
                        utype = self._extract_update_type(upd)
                        if not utype:
                            continue
                        data = self._fix_reserved_keys(upd.get(utype, {}))
                        self._process_handlers(utype, data)
            except Exception as e:
                safe_print({"polling_error": str(e)})
                time.sleep(1)
            time.sleep(polling_sleep)
