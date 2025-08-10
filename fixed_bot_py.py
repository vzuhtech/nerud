import logging
import asyncio
import json
import os
from datetime import datetime
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
import re

import openai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы для состояний разговора
MATERIAL_SELECTION, QUANTITY_INPUT, ADDRESS_INPUT, CONTACT_INPUT, CONFIRMATION = range(5)

# Конфигурация (используем переменные окружения)
class Config:
    # ИСПРАВЛЕНО: используем os.getenv() правильно
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'T8323533826:AAFD0HsdzXmP-u8eb8Ge2ieQSNE6SZ-WVGU')
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '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')
    MANAGER_CHAT_ID = os.getenv('MANAGER_CHAT_ID', '5806904086')
    
    # Базовые цены за м³ (можно настроить)
    MATERIAL_PRICES = {
        "песок": {"price": 1500, "unit": "м³", "description": "Песок речной мытый"},
        "щебень": {"price": 2000, "unit": "м³", "description": "Щебень гранитный фр. 5-20мм"},
        "земля": {"price": 800, "unit": "м³", "description": "Земля растительная плодородная"},
        "глина": {"price": 1200, "unit": "м³", "description": "Глина для дренажа"},
        "песок_карьерный": {"price": 1200, "unit": "м³", "description": "Песок карьерный"},
        "щебень_известняковый": {"price": 1800, "unit": "м³", "description": "Щебень известняковый"}
    }

@dataclass
class Order:
    user_id: int
    username: str
    material: str
    quantity: float
    unit: str
    address: str
    phone: str
    estimated_price: float
    created_at: str
    ai_recommendation: str = ""

class AIAssistant:
    def __init__(self, api_key: str):
        if api_key and api_key.startswith('sk-'):  # Проверяем валидность ключа
            # Используем новый API OpenAI
            self.client = openai.OpenAI(api_key=api_key)
            self.enabled = True
        else:
            logger.warning("OpenAI API ключ не найден или неверный. ИИ функции работать не будут.")
            self.enabled = False
        
    async def get_material_recommendation(self, user_query: str) -> Dict[str, Any]:
        """Получить рекомендацию ИИ по материалам"""
        if not self.enabled:
            return {
                "recommended_material": "песок",
                "explanation": "Для получения персональных рекомендаций свяжитесь с нашим менеджером.",
                "estimated_quantity": "уточнить"
            }
            
        system_prompt = """
        Ты - эксперт по строительным материалам. Помоги клиенту выбрать подходящий материал.
        Доступные материалы:
        - песок (песок речной мытый для фундаментов, бетона)
        - песок_карьерный (песок карьерный для засыпки, выравнивания)
        - щебень (щебень гранитный для дренажа, фундаментов, дорожек)
        - щебень_известняковый (более дешевый вариант для дренажа)
        - земля (земля растительная для садовых работ, газонов)
        - глина (глина для дренажа, гидроизоляции)
        
        Отвечай кратко и по делу. Рекомендуй конкретный материал и примерное количество.
        Формат ответа в JSON:
        {
            "recommended_material": "название_материала_из_списка_выше",
            "explanation": "краткое объяснение выбора до 100 символов",
            "estimated_quantity": "число от 1 до 50"
        }
        """
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_query}
                ],
                max_tokens=200,
                temperature=0.7
            )
            
            content = response.choices[0].message.content
            
            # Попытка извлечь JSON из ответа
            try:
                # Ищем JSON в тексте
                start = content.find('{')
                end = content.rfind('}') + 1
                if start >= 0 and end > start:
                    json_str = content[start:end]
                    result = json.loads(json_str)
                    
                    # Валидируем материал
                    if result.get("recommended_material") not in Config.MATERIAL_PRICES:
                        result["recommended_material"] = "песок"
                    
                    return result
                else:
                    raise json.JSONDecodeError("JSON not found", content, 0)
                    
            except json.JSONDecodeError:
                # Если не удалось распарсить JSON, возвращаем дефолтные значения
                return {
                    "recommended_material": "песок",
                    "explanation": content[:100] if content else "Рекомендуем песок для большинства строительных работ",
                    "estimated_quantity": "5-10"
                }
                
        except Exception as e:
            logger.error(f"Ошибка при обращении к OpenAI: {e}")
            return {
                "recommended_material": "песок",
                "explanation": "Для консультации обратитесь к менеджеру",
                "estimated_quantity": "уточнить"
            }

class ConstructionMaterialsBot:
    def __init__(self):
        self.ai_assistant = AIAssistant(Config.OPENAI_API_KEY)
        self.orders: Dict[int, Order] = {}
        
    def create_material_keyboard(self):
        """Создать клавиатуру для выбора материалов"""
        keyboard = []
        materials = list(Config.MATERIAL_PRICES.keys())
        
        for i in range(0, len(materials), 2):
            row = []
            for j in range(2):
                if i + j < len(materials):
                    material = materials[i + j]
                    price = Config.MATERIAL_PRICES[material]["price"]
                    row.append(InlineKeyboardButton(
                        f"{Config.MATERIAL_PRICES[material]['description']} - {price}₽/м³",
                        callback_data=f"material_{material}"
                    ))
            keyboard.append(row)
            
        if self.ai_assistant.enabled:
            keyboard.append([InlineKeyboardButton("🤖 Консультация ИИ", callback_data="ai_help")])
        keyboard.append([InlineKeyboardButton("👨‍💼 Связаться с менеджером", callback_data="contact_manager")])
        
        return InlineKeyboardMarkup(keyboard)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        welcome_message = """
🏗️ Добро пожаловать в сервис заказа строительных материалов!

Я помогу вам:
• Выбрать подходящий материал
• Рассчитать необходимое количество  
• Оформить заказ с доставкой
• Связаться с менеджером

Что вас интересует?
        """
        
        keyboard = [
            [KeyboardButton("📦 Заказать материалы")],
            [KeyboardButton("💰 Узнать цены"), KeyboardButton("📞 Контакты")],
        ]
        
        if self.ai_assistant.enabled:
            keyboard.append([KeyboardButton("🤖 Помощь ИИ в выборе")])
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(welcome_message, reply_markup=reply_markup)
        return ConversationHandler.END

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        text = update.message.text.lower()
        
        if "заказать" in text or "материал" in text:
            return await self.start_order(update, context)
        elif "цен" in text:
            return await self.show_prices(update, context)
        elif "контакт" in text:
            return await self.show_contacts(update, context)
        elif "помощь" in text or "выбор" in text or "ии" in text:
            return await self.ai_consultation(update, context)
        else:
            # Передаем неопознанный запрос ИИ (если доступно)
            if self.ai_assistant.enabled:
                return await self.ai_consultation(update, context)
            else:
                await update.message.reply_text(
                    "Не понял ваш запрос. Выберите опцию из меню или обратитесь к менеджеру."
                )
                return ConversationHandler.END

    async def start_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Начать процесс заказа"""
        await update.message.reply_text(
            "🛒 Выберите материал из каталога:",
            reply_markup=self.create_material_keyboard()
        )
        return MATERIAL_SELECTION

    async def show_prices(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать прайс-лист"""
        price_text = "💰 **ПРАЙС-ЛИСТ**\n\n"
        
        for material, info in Config.MATERIAL_PRICES.items():
            price_text += f"• {info['description']}\n"
            price_text += f"  💵 {info['price']}₽ за {info['unit']}\n\n"
            
        price_text += "📍 *Цены указаны без учета доставки*\n"
        price_text += "🚚 *Стоимость доставки рассчитывается индивидуально*"
        
        await update.message.reply_text(price_text, parse_mode='Markdown')

    async def show_contacts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать контактную информацию"""
        contact_text = """
📞 **КОНТАКТЫ**

☎️ Телефон: +7 (999) 123-45-67
📧 Email: info@materials.ru
🕐 Режим работы: Пн-Пт 8:00-18:00, Сб 9:00-15:00

📍 Адрес склада: г. Москва, ул. Складская, 1

🚚 Доставка по Москве и области
⚡ Срочная доставка в день заказа
        """
        await update.message.reply_text(contact_text, parse_mode='Markdown')

    async def ai_consultation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Консультация с ИИ"""
        if not self.ai_assistant.enabled:
            await update.message.reply_text(
                "🤖 ИИ консультант временно недоступен.\n"
                "📞 Обратитесь к нашему менеджеру для персональной консультации!"
            )
            return ConversationHandler.END
            
        user_query = update.message.text
        
        await update.message.reply_text("🤖 Анализирую ваш запрос...")
        
        recommendation = await self.ai_assistant.get_material_recommendation(user_query)
        
        material_info = Config.MATERIAL_PRICES.get(recommendation['recommended_material'], 
                                                   Config.MATERIAL_PRICES['песок'])
        
        response_text = f"""
🤖 **РЕКОМЕНДАЦИЯ ИИ**

📦 **Материал:** {material_info['description']}
💰 **Цена:** {material_info['price']}₽/{material_info['unit']}

💡 **Обоснование:** {recommendation['explanation']}

📏 **Количество:** {recommendation['estimated_quantity']} {material_info['unit']}

Хотите оформить заказ на рекомендованный материал?
        """
        
        keyboard = [
            [InlineKeyboardButton("✅ Заказать", callback_data=f"order_{recommendation['recommended_material']}")],
            [InlineKeyboardButton("🔄 Другой материал", callback_data="show_materials")],
            [InlineKeyboardButton("👨‍💼 Связаться с менеджером", callback_data="contact_manager")]
        ]
        
        await update.message.reply_text(
            response_text, 
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        
        return MATERIAL_SELECTION

    async def handle_material_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка выбора материала"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "ai_help":
            await query.edit_message_text(
                "🤖 Опишите вашу задачу:\n\n"
                "Например: 'Нужен материал для фундамента дома 10х12 метров' или "
                "'Хочу сделать дренаж участка'"
            )
            return MATERIAL_SELECTION
            
        elif query.data == "contact_manager":
            manager_text = """
👨‍💼 **СВЯЗЬ С МЕНЕДЖЕРОМ**

📞 Телефон: +7 (999) 123-45-67
📧 Email: manager@materials.ru
💬 Telegram: @materials_manager

🕐 Время работы: Пн-Пт 8:00-18:00

Менеджер поможет с:
• Консультацией по материалам
• Расчетом точного количества
• Специальными предложениями
• Срочными заказами
            """
            await query.edit_message_text(manager_text, parse_mode='Markdown')
            return ConversationHandler.END
            
        elif query.data.startswith("material_") or query.data.startswith("order_"):
            material = query.data.split("_", 1)[1]
            
            if material in Config.MATERIAL_PRICES:
                context.user_data['selected_material'] = material
                material_info = Config.MATERIAL_PRICES[material]
                
                await query.edit_message_text(
                    f"✅ Выбран: {material_info['description']}\n"
                    f"💰 Цена: {material_info['price']}₽ за {material_info['unit']}\n\n"
                    f"📏 Укажите необходимое количество в {material_info['unit']}:"
                )
                return QUANTITY_INPUT
                
        elif query.data == "show_materials":
            await query.edit_message_text(
                "🛒 Выберите материал из каталога:",
                reply_markup=self.create_material_keyboard()
            )
            return MATERIAL_SELECTION

    async def handle_quantity_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ввода количества"""
        try:
            quantity = float(re.sub(r'[^\d.,]', '', update.message.text.replace(',', '.')))
            
            if quantity <= 0:
                await update.message.reply_text("❌ Количество должно быть больше 0. Попробуйте еще раз:")
                return QUANTITY_INPUT
                
            if quantity > 1000:
                await update.message.reply_text("❌ Слишком большое количество. Обратитесь к менеджеру для крупных заказов.")
                return QUANTITY_INPUT
                
            context.user_data['quantity'] = quantity
            
            # Рассчитать примерную стоимость
            material = context.user_data['selected_material']
            price_per_unit = Config.MATERIAL_PRICES[material]['price']
            total_price = quantity * price_per_unit
            
            context.user_data['estimated_price'] = total_price
            
            await update.message.reply_text(
                f"📦 Количество: {quantity} {Config.MATERIAL_PRICES[material]['unit']}\n"
                f"💰 Примерная стоимость: {total_price:,.0f}₽ (без учета доставки)\n\n"
                f"📍 Укажите адрес доставки:"
            )
            return ADDRESS_INPUT
            
        except ValueError:
            await update.message.reply_text(
                "❌ Пожалуйста, укажите количество числом (например: 5 или 10.5):"
            )
            return QUANTITY_INPUT

    async def handle_address_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ввода адреса"""
        address = update.message.text.strip()
        
        if len(address) < 10:
            await update.message.reply_text(
                "❌ Пожалуйста, укажите полный адрес доставки\n"
                "(город, улица, дом):"
            )
            return ADDRESS_INPUT
            
        context.user_data['address'] = address
        
        await update.message.reply_text(
            f"📍 Адрес доставки: {address}\n\n"
            f"📞 Укажите ваш номер телефона для связи:"
        )
        return CONTACT_INPUT

    async def handle_contact_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ввода контакта"""
        phone = update.message.text.strip()
        
        # Простая валидация телефона
        phone_pattern = re.compile(r'[\+]?[0-9\s\-\(\)]{10,}')
        if not phone_pattern.match(phone):
            await update.message.reply_text(
                "❌ Пожалуйста, укажите корректный номер телефона\n"
                "(например: +7 999 123-45-67 или 8 999 123 45 67):"
            )
            return CONTACT_INPUT
            
        context.user_data['phone'] = phone
        
        # Создать объект заказа
        order = Order(
            user_id=update.effective_user.id,
            username=update.effective_user.username or "Не указан",
            material=context.user_data['selected_material'],
            quantity=context.user_data['quantity'],
            unit=Config.MATERIAL_PRICES[context.user_data['selected_material']]['unit'],
            address=context.user_data['address'],
            phone=phone,
            estimated_price=context.user_data['estimated_price'],
            created_at=datetime.now().strftime("%d.%m.%Y %H:%M")
        )
        
        # Показать подтверждение заказа
        material_info = Config.MATERIAL_PRICES[order.material]
        confirmation_text = f"""
📋 **ПОДТВЕРЖДЕНИЕ ЗАКАЗА**

👤 Заказчик: @{order.username}
📦 Материал: {material_info['description']}
📏 Количество: {order.quantity} {order.unit}
💰 Примерная стоимость: {order.estimated_price:,.0f}₽
📍 Адрес доставки: {order.address}
📞 Телефон: {order.phone}

⚠️ *Итоговая стоимость может измениться с учетом доставки*

Подтверждаете заказ?
        """
        
        keyboard = [
            [InlineKeyboardButton("✅ Подтвердить заказ", callback_data="confirm_order")],
            [InlineKeyboardButton("❌ Отменить", callback_data="cancel_order")]
        ]
        
        self.orders[update.effective_user.id] = order
        
        await update.message.reply_text(
            confirmation_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return CONFIRMATION

    async def handle_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка подтверждения заказа"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        
        if query.data == "confirm_order" and user_id in self.orders:
            order = self.orders[user_id]
            
            # Отправить заказ менеджеру
            await self.send_order_to_manager(order, context.application)
            
            # Генерируем номер заказа
            order_number = str(order.user_id) + str(int(datetime.now().timestamp()))[-6:]
            
            # Уведомить клиента
            await query.edit_message_text(
                "✅ **ЗАКАЗ ПРИНЯТ!**\n\n"
                f"🆔 Номер заказа: #{order_number}\n\n"
                "📞 Менеджер свяжется с вами в ближайшее время для уточнения деталей доставки.\n\n"
                "⏰ Обычно это происходит в течение 30 минут в рабочее время.\n\n"
                "Спасибо за обращение! 🙏",
                parse_mode='Markdown'
            )
            
            # Очистить данные заказа
            del self.orders[user_id]
            context.user_data.clear()
            
        elif query.data == "cancel_order":
            await query.edit_message_text("❌ Заказ отменен. Если нужна помощь - обращайтесь!")
            if user_id in self.orders:
                del self.orders[user_id]
            context.user_data.clear()
            
        return ConversationHandler.END

    async def send_order_to_manager(self, order: Order, application):
        """Отправить заказ менеджеру"""
        try:
            material_info = Config.MATERIAL_PRICES[order.material]
            order_number = str(order.user_id) + str(int(datetime.now().timestamp()))[-6:]
            
            manager_message = f"""
🔔 **НОВЫЙ ЗАКАЗ #{order_number}**

👤 **Клиент:** @{order.username} (ID: {order.user_id})
📞 **Телефон:** {order.phone}
📅 **Дата заказа:** {order.created_at}

📦 **Материал:** {material_info['description']}
📏 **Количество:** {order.quantity} {order.unit}
💰 **Примерная стоимость:** {order.estimated_price:,.0f}₽

📍 **Адрес доставки:**
{order.address}

⚡ **ТРЕБУЕТСЯ СВЯЗАТЬСЯ С КЛИЕНТОМ!**
            """
            
            # Отправить сообщение менеджеру (если настроен MANAGER_CHAT_ID)
            if Config.MANAGER_CHAT_ID:
                try:
                    await application.bot.send_message(
                        chat_id=Config.MANAGER_CHAT_ID,
                        text=manager_message,
                        parse_mode='Markdown'
                    )
                    logger.info(f"Заказ #{order_number} отправлен менеджеру")
                except Exception as e:
                    logger.error(f"Ошибка отправки заказа менеджеру: {e}")
            else:
                logger.warning("MANAGER_CHAT_ID не настроен, заказ не отправлен менеджеру")
                
        except Exception as e:
            logger.error(f"Ошибка обработки заказа: {e}")

def main():
    """Основная функция запуска бота"""
    
    # Проверяем наличие токена
    if not Config.TELEGRAM_BOT_TOKEN or Config.TELEGRAM_BOT_TOKEN == 'your_bot_token_here':
        logger.error("TELEGRAM_BOT_TOKEN не установлен! Добавьте его в переменные окружения Railway.")
        return
    
    try:
        # Создаем экземпляр бота
        bot = ConstructionMaterialsBot()
        
        # Создаем приложение
        application = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
        
        # Настраиваем ConversationHandler
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("start", bot.start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text_message)
            ],
            states={
                MATERIAL_SELECTION: [
                    CallbackQueryHandler(bot.handle_material_selection),
                    MessageHandler(filters.TEXT, bot.ai_consultation)
                ],
                QUANTITY_INPUT: [MessageHandler(filters.TEXT, bot.handle_quantity_input)],
                ADDRESS_INPUT: [MessageHandler(filters.TEXT, bot.handle_address_input)],
                CONTACT_INPUT: [MessageHandler(filters.TEXT, bot.handle_contact_input)],
                CONFIRMATION: [CallbackQueryHandler(bot.handle_confirmation)]
            },
            fallbacks=[CommandHandler("start", bot.start)]
        )
        
        # Добавляем обработчики
        application.add_handler(conv_handler)
        
        # Логируем запуск
        logger.info("Бот успешно запущен!")
        logger.info(f"OpenAI API: {'включен' if bot.ai_assistant.enabled else 'выключен'}")
        logger.info(f"Manager Chat ID: {Config.MANAGER_CHAT_ID}")
        
        # Запускаем бота
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
    except Exception as e:
        logger.error(f"Критическая ошибка запуска бота: {e}")
        raise

if __name__ == '__main__':
    main()
