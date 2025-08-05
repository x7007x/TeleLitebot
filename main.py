import asyncio
from TeleLite import Bot, filters

bot = Bot("8083493226:AAGCSHnkc3dQUS5iuQlnAiaqOvjpyVnZZtQ")

@bot.on_message(filters.text("hello"))
async def greet_user(message):
    await bot("sendMessage", chat_id=message["chat"]["id"], text="Welcome!")

@bot.on_message(filters.text("ping") | filters.text("pong"))
async def pong_ping(message):
    await bot("sendMessage", chat_id=message["chat"]["id"], text="pong!")

@bot.on_message(~filters.text("ignore me"))
async def all_but_ignore(message):
    print("Message:", message.get("text"))

@bot.on_message(filters.command("start", "help"))
async def welcome(message):
    keyboard = {
        "keyboard": [
            [{"text": "Say Hello"}, {"text": "Send Photo"}],
            [{"text": "Make Payment"}, {"text": "Star Rating"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }
    await bot("sendMessage", chat_id=message["chat"]["id"], text="Welcome! Use the keyboard below:", reply_markup=keyboard)

@bot.on_message(filters.regex(r"^\d+$"))
async def number_message(message):
    await bot("sendMessage", chat_id=message["chat"]["id"], text="You sent a number!")

@bot.on_message(filters.has_photo() | filters.has_video())
async def media_received(message):
    await bot("sendMessage", chat_id=message["chat"]["id"], text="Nice media!")

@bot.on_callback_query()
async def handle_callback(callback):
    data = callback.get("data")
    if data == "like":
        await bot("answerCallbackQuery", callback_query_id=callback["id"], text="You liked 👍")
    elif data == "dislike":
        await bot("answerCallbackQuery", callback_query_id=callback["id"], text="You disliked 👎")

@bot.on_message(filters.edited())
async def edited_message(message):
    print("Edited message detected:", message.get("text"))

@bot.on_message(filters.text("Make Payment"))
async def payment_request(message):
    invoice = {
        "chat_id": message["chat"]["id"],
        "title": "Sample Product",
        "description": "This is a sample payment invoice.",
        "payload": "sample_payload",
        "provider_token": "PROVIDER_PAYMENT_TOKEN",
        "start_parameter": "start",
        "currency": "USD",
        "prices": [{"label": "Product", "amount": 1000}]
    }
    await bot("sendInvoice", **invoice)

@bot.on_pre_checkout_query()
async def checkout_handler(query):
    await bot("answerPreCheckoutQuery", pre_checkout_query_id=query["id"], ok=True)

@bot.on_poll()
async def poll_update(poll):
    print("Poll updated:", poll)

@bot.on_message(filters.text("Star Rating"))
async def star_rating(message):
    inline_keyboard = {
        "inline_keyboard": [
            [{"text": "⭐", "callback_data": "rate_1"},
             {"text": "⭐⭐", "callback_data": "rate_2"},
             {"text": "⭐⭐⭐", "callback_data": "rate_3"},
             {"text": "⭐⭐⭐⭐", "callback_data": "rate_4"},
             {"text": "⭐⭐⭐⭐⭐", "callback_data": "rate_5"}]
        ]
    }
    await bot("sendMessage", chat_id=message["chat"]["id"], text="Please rate:", reply_markup=inline_keyboard)

@bot.on_callback_query()
async def rating_response(callback):
    rating = callback["data"].split("_")[1]
    await bot("answerCallbackQuery", callback_query_id=callback["id"], text=f"Thank you for rating: {rating}⭐")
    await bot("editMessageText", 
              chat_id=callback["message"]["chat"]["id"], 
              message_id=callback["message"]["message_id"], 
              text=f"Your rating: {rating}⭐")

bot.run(webhook=True)
