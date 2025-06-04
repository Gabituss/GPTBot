import time
import logging
import asyncio
from aiogram import Dispatcher, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message as AiogramMessage, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode, ChatAction
from aiogram.exceptions import TelegramBadRequest
from langchain_llm7 import ChatLLM7
from langchain_core.messages import HumanMessage, AIMessage
from db import User, Dialog, DialogMessage, get_or_create_user_and_dialog
from chatgpt_md_converter import telegram_format
import config

logger = logging.getLogger(__name__)
user_locks = {}

MODEL_SELECT_CALLBACK_PREFIX = "select_model_"

def setup_handlers(dp: Dispatcher, bot_object: Bot):
    @dp.message(CommandStart())
    async def start_handler(message: AiogramMessage):
        logger.info(f"User {message.from_user.id} started the bot.")
        user, dialog = await get_or_create_user_and_dialog(message.from_user.id, message.from_user.username)
        logger.info(f"User {user.id} (dialog {dialog.id}) session started/continued. Model: {dialog.model_used}")
        await message.answer(f"Привет! Напиши мне что-нибудь. Модель: {dialog.model_used}.\nДля сброса диалога: /reset\nДля выбора модели: /select")

    @dp.message(Command(commands=["reset"]))
    async def reset_handler(message: AiogramMessage):
        logger.info(f"User {message.from_user.id} requested dialog reset.")
        user, _ = await get_or_create_user_and_dialog(message.from_user.id, message.from_user.username)
        
        new_dialog = await Dialog.create(user=user, model_used=user.model) 
        user.current_dialog = new_dialog
        await user.save(update_fields=['current_dialog_id'])
        logger.info(f"User {user.id} started new dialog {new_dialog.id} with model {new_dialog.model_used}")
        await message.answer(f"Новый диалог начат. Модель: {new_dialog.model_used}")

    @dp.message(Command(commands=["select"]))
    async def select_model_handler(message: AiogramMessage):
        user, _ = await get_or_create_user_and_dialog(message.from_user.id, message.from_user.username)
        buttons = []
        for model_key, model_details in config.models.items():
            display_name = model_details.get("name", model_key) 
            text = f"✅ {display_name}" if user.model == model_key else display_name
            buttons.append([InlineKeyboardButton(text=text, callback_data=f"{MODEL_SELECT_CALLBACK_PREFIX}{model_key}")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer("Выберите нейросеть. Внимание: выбор новой модели сбросит текущий диалог!", reply_markup=keyboard)

    @dp.callback_query(F.data.startswith(MODEL_SELECT_CALLBACK_PREFIX))
    async def process_model_selection(callback_query: CallbackQuery):
        await callback_query.answer() 
        selected_model_key = callback_query.data[len(MODEL_SELECT_CALLBACK_PREFIX):]
        user_id = callback_query.from_user.id
        username = callback_query.from_user.username

        user, _ = await get_or_create_user_and_dialog(user_id, username)

        if selected_model_key in config.models:
            if user.model == selected_model_key:
                await bot_object.send_message(user_id, f"Модель {config.models[selected_model_key].get('name', selected_model_key)} уже выбрана.")
                await callback_query.message.edit_reply_markup(reply_markup=None)
                return

            user.model = selected_model_key 
            new_dialog = await Dialog.create(user=user, model_used=selected_model_key) 
            user.current_dialog = new_dialog
            await user.save(update_fields=['model', 'current_dialog_id'])
            
            logger.info(f"User {user.id} selected model {selected_model_key} and started new dialog {new_dialog.id}")
            await bot_object.send_message(user_id, f"Выбрана модель: {config.models[selected_model_key].get('name', selected_model_key)}. Диалог сброшен.")
            await callback_query.message.edit_reply_markup(reply_markup=None)
        else:
            logger.warning(f"User {user_id} tried to select invalid model key: {selected_model_key}")
            await bot_object.send_message(user_id, "Выбрана некорректная модель.")

    @dp.message(F.text)
    async def handle_message(message: AiogramMessage):
        user, dialog = await get_or_create_user_and_dialog(message.from_user.id, message.from_user.username)
        user_id = user.id
        current_dialog_id = dialog.id

        lock = user_locks.setdefault(user_id, asyncio.Lock())
        if lock.locked():
            await message.answer("Дождитесь завершения предыдущего запроса.")
            return
        
        async with lock:
            user_text = message.text
            logger.info(f"Received message from {user_id} (dialog {current_dialog_id}, model {dialog.model_used}): {user_text}")

            await DialogMessage.create(dialog=dialog, text=user_text, sender='user')
            
            await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

            history_messages = await DialogMessage.filter(dialog=dialog).order_by('timestamp')
            llm_context = []
            for msg_db in history_messages:
                if msg_db.sender == 'user':
                    llm_context.append(HumanMessage(content=msg_db.text))
                elif msg_db.sender == 'bot':
                    llm_context.append(AIMessage(content=msg_db.text))
            
            model_id_to_use = config.models.get(dialog.model_used, config.models.get("gpt-4.1", {}))['id']
            if not model_id_to_use:
                 logger.error(f"Model ID for '{dialog.model_used}' or default 'gpt-4.1' not found in config.py for user {user_id}")
                 await message.answer("Ошибка конфигурации модели. Обратитесь к администратору.")
                 return

            llm = ChatLLM7(
                model=model_id_to_use,
                temperature=1,
                stop=["\n", "Observation:"],
                max_tokens=10000,
                timeout=40,
                streaming=True,
            )

            msg_to_edit = await message.answer(f"Думаю ({dialog.model_used})...")
            last_update_time = time.time()  
            bot_response_text = ""
            
            try:
                for chunk in llm.stream(llm_context):
                    bot_response_text += chunk.content
                    current_time = time.time()
                    if current_time - last_update_time >= 0.25:
                        formatted_text = telegram_format(bot_response_text)
                        try:
                            await msg_to_edit.edit_text(formatted_text, parse_mode=ParseMode.HTML)
                            last_update_time = current_time 
                        except TelegramBadRequest:
                            pass 
                
                final_formatted_text = telegram_format(bot_response_text)
                try:
                    await msg_to_edit.edit_text(final_formatted_text, parse_mode=ParseMode.HTML)
                except TelegramBadRequest:
                    pass

                if bot_response_text.strip():
                    await DialogMessage.create(dialog=dialog, text=bot_response_text, sender='bot')
            
            except Exception as e:
                logger.error(f"Error while processing message for user {user_id} (dialog {current_dialog_id}): {e}", exc_info=True)
                await msg_to_edit.edit_text("Произошла ошибка при обработке вашего запроса.") 