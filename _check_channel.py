"""
Проверка №1. Запусти ЭТО первым, до всего остального.

Отвечает на единственный вопрос: отдаёт ли Telegram публичное превью канала
и видит ли парсер вакансии. Ничего не пишет в БД, ничего не шлёт.

    python3 check_channel.py
    python3 check_channel.py другойканал
"""
import sys

from fetcher import fetch_channel_html
from tgparser import parse_page
from config import CHANNEL

channel = sys.argv[1] if len(sys.argv) > 1 else CHANNEL

print(f"Тяну https://t.me/s/{channel} ...")
try:
    html = fetch_channel_html(channel)
except Exception as e:
    print(f"\n❌ НЕ СКАЧАЛОСЬ: {e}")
    print("Возможные причины: канал приватный, опечатка в имени, сеть/блокировка.")
    sys.exit(1)

print(f"✅ Страница получена, {len(html)} символов")

posts = parse_page(html)
if not posts:
    print("\n⚠️  Страница есть, но постов с вакансиями не распознано.")
    print("Скорее всего изменилась вёрстка или формат постов — пришли мне кусок HTML.")
    snippet = html[:1500]
    print("\n--- первые 1500 символов ---\n", snippet)
    sys.exit(2)

total = sum(len(p.vacancies) for p in posts)
print(f"✅ Распознано постов: {len(posts)}, вакансий всего: {total}\n")

for p in posts[-5:]:
    print(f"=== {p.post_id} | компания: {p.company or '—'}")
    print(f"    {p.header[:80]}")
    for v in p.vacancies:
        url = (v.url or "нет ссылки")[:60]
        print(f"    {v.idx}. {v.title}  [{v.salary_raw or 'з/п н/у'}]  {url}")
    print()

print("Готово. Если названия и ссылки на месте — можно запускать сервис.")
