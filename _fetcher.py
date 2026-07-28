import logging

import requests

from config import UA

log = logging.getLogger("fetcher")


def fetch_channel_html(channel: str, timeout: int = 20) -> str:
    """
    Тянет публичное превью канала. Никакой авторизации, никакого юзербота —
    обычная HTML-страница, которую Telegram отдаёт всем.
    """
    url = f"https://t.me/s/{channel}"
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.9"},
                     timeout=timeout)
    r.raise_for_status()
    return r.text
