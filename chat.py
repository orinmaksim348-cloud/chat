import flet as ft
from dataclasses import dataclass
from datetime import datetime
import os
import threading

from inceptionai import Inception

# ---------- Модель данных ----------
@dataclass
class Message:
    user: str
    text: str
    timestamp: str
    type: str = "message"        # message / system / bot
    target_session_id: str | None = None

# ---------- Общая история (в памяти процесса, достаточно для бесплатного тарифа) ----------
HISTORY: list[dict] = []
HISTORY_LIMIT = 50

def push_history(msg: dict) -> None:
    global HISTORY
    HISTORY.append(msg)
    HISTORY = HISTORY[-HISTORY_LIMIT:]

# ---------- Клиент Inception ----------
def build_inception_client() -> Inception | None:
    key = os.getenv("INCEPTION_API_KEY")
    if not key:
        return None
    return Inception(api_key=key)

def ask_bot(question: str) -> str:
    """Синхронный вызов; в Flet следует запускать в отдельном потоке, чтобы не блокировать UI."""
    client = build_inception_client()
    if not client:
        return "⚠️ Бот не настроен: отсутствует INCEPTION_API_KEY."
    try:
        completion = client.chat.completions.create(
            model="mercury-3",
            messages=[{"role": "user", "content": question}],
            reasoning_effort="low",          # для чата достаточно low, быстрее
            temperature=0.7,
            max_completion_tokens=1024,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ Ошибка бота: {e}"

# ---------- Основное приложение ----------
def main(page: ft.Page) -> None:
    page.title = "Flet Chat + Bot"
    page.padding = 0
    page.theme_mode = ft.ThemeMode.DARK

    user_name: list[str | None] = [None]
    my_session_id = page.session.id

    # UI
    chat_list = ft.ListView(expand=True, spacing=10, padding=20, auto_scroll=True)
    message_input = ft.TextField(
        hint_text="Введите сообщение…", expand=True,
        border_color=ft.Colors.BLUE_400, autofocus=True,
    )
    bot_indicator = ft.Text("", size=11, color=ft.Colors.AMBER_400, italic=True)

    def add_message_to_ui(msg: Message) -> None:
        # Фильтрация приватных сообщений: если есть target_session_id и это не я — не показывать
        if msg.target_session_id and msg.target_session_id != my_session_id:
            return

        is_own = (msg.user == user_name[0]) and msg.type == "message"
        is_bot = msg.type == "bot"
        is_system = msg.type == "system"

        if is_system:
            chat_list.controls.append(
                ft.Container(
                    content=ft.Text(msg.text, size=11, color=ft.Colors.GREY_500, italic=True),
                    alignment=ft.Alignment.CENTER,
                    padding=4,
                )
            )
            return

        color = (
            ft.Colors.GREEN_900 if is_own
            else ft.Colors.AMBER_900 if is_bot
            else ft.Colors.GREY_900
        )
        name_color = (
            ft.Colors.GREEN_400 if is_own
            else ft.Colors.AMBER_400 if is_bot
            else ft.Colors.BLUE_400
        )

        chat_list.controls.append(
            ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Text(msg.user, weight=ft.FontWeight.BOLD, size=12, color=name_color),
                        ft.Text(msg.timestamp, size=10, color=ft.Colors.GREY_500),
                    ], spacing=8),
                    ft.Text(msg.text, size=14, color=ft.Colors.WHITE_70),
                ], spacing=2),
                bgcolor=color,
                padding=10,
                border_radius=8,
                width=page.width * 0.7 if page.width else 300,
                alignment=ft.Alignment.CENTER_RIGHT if is_own else ft.Alignment.CENTER_LEFT,
            )
        )

    def on_message(msg_data: dict) -> None:
        msg = Message(**msg_data)
        add_message_to_ui(msg)
        # 如果是 bot 消息，清除指示器（仅当自己是接收者时）
        if msg.type == "bot" and (not msg.target_session_id or msg.target_session_id == my_session_id):
            bot_indicator.value = ""
        page.update()

    page.pubsub.subscribe(on_message)

    # ---------- Отправка ----------
    def send_message(e: ft.ControlEvent) -> None:
        text = (message_input.value or "").strip()
        if not text or not user_name[0]:
            return

        payload = {
            "user": user_name[0],
            "text": text,
            "timestamp": datetime.now().strftime("%H:%M"),
            "type": "message",
            "target_session_id": None,
        }
        push_history(payload)
        page.pubsub.send_all(payload)
        message_input.value = ""
        page.update()

        # 若以 @bot 开头，异步调用 Inception
        if text.lower().startswith("@bot"):
            question = text[4:].strip()
            if question:
                bot_indicator.value = "🤔 Бот думает…"
                page.update()
                def worker():
                    answer = ask_bot(question)
                    bot_payload = {
                        "user": "Bot",
                        "text": answer,
                        "timestamp": datetime.now().strftime("%H:%M"),
                        "type": "bot",
                        "target_session_id": None,
                    }
                    push_history(bot_payload)
                    # 从工作线程调用 pubsub 是线程安全的
                    page.pubsub.send_all(bot_payload)
                threading.Thread(target=worker, daemon=True).start()

    message_input.on_submit = send_message

    # ---------- Вход ----------
    name_field = ft.TextField(label="Ваше имя", autofocus=True, width=300)
    enter_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Вход в чат"),
        content=ft.Column([name_field], tight=True),
        actions=[],
    )

    def enter_chat(e: ft.ControlEvent) -> None:
        name = (name_field.value or "").strip()
        if not name:
            return
        user_name[0] = name
        page.pop_dialog()

        # Системное сообщение о входе
        sys_msg = {
            "user": "Система",
            "text": f"{name} присоединился к чату",
            "timestamp": datetime.now().strftime("%H:%M"),
            "type": "system",
            "target_session_id": None,
        }
        page.pubsub.send_all(sys_msg)

        # Показать историю (только этому новому пользователю, локально)
        for h in HISTORY[-HISTORY_LIMIT:]:
            add_message_to_ui(Message(**h))
        page.update()

    enter_dialog.actions = [ft.Button("Войти", on_click=enter_chat)]
    page.show_dialog(enter_dialog)

    # ---------- Раскладка ----------
    page.add(
        ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.CHAT, color=ft.Colors.GREEN_400),
                    ft.Text("Flet Chat", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    ft.Container(expand=True),
                    ft.Text("Введите @bot вопрос — ответит ИИ", size=11, color=ft.Colors.GREY_500),
                ]),
                ft.Divider(color=ft.Colors.GREY_700),
                chat_list,
                bot_indicator,
                ft.Row([
                    message_input,
                    ft.IconButton(ft.Icons.SEND, icon_color=ft.Colors.GREEN, on_click=send_message),
                ]),
            ], expand=True),
            expand=True,
            padding=10,
        )
    )

if __name__ == "__main__":
    ft.run(main, view=ft.AppView.WEB_BROWSER)