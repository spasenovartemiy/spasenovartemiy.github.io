"""
Второй парсер: формат "одна вакансия на пост" (каналы вроде @moskvarabota).

Пост выглядит так:
    В компанию требуется:
    <b>HR Generalist</b>
    от 300 000 ₽
    Обязанности: ...
    ...
    Контакт: @username  /  t.me/...  /  +7...

Ссылки на внешнюю вакансию нет — отклик идёт рекрутёру напрямую.
Поэтому:
  - JD берём из самого текста поста (он полный),
  - вместо url кладём контакт для отклика.
"""
import re

from bs4 import BeautifulSoup

from tgparser import Vacancy, Post   # переиспользуем датаклассы


RE_SALARY = re.compile(
    r"(от|до)?\s*([\d][\d\s.,]*\d)\s*(?:₽|руб|р\.?|тыс|000)", re.IGNORECASE)
RE_CONTACT_USER = re.compile(r"(?<![\w/])@([A-Za-z][A-Za-z0-9_]{4,31})")
RE_CONTACT_TME = re.compile(r"https?://t\.me/([A-Za-z][A-Za-z0-9_]{4,31})")
RE_PHONE = re.compile(r"(\+7[\d\s\-()]{9,14}\d)")

# заголовки-триггеры: "требуется", "в команду", "ищем", "вакансия"
RE_TRIGGER = re.compile(
    r"требует|ищем|в команду|вакансия|приглашаем|открыт[аы]? вакан", re.IGNORECASE)

# служебные @-имена, которые НЕ являются контактом для отклика
STOP_USERS = {"moskvarabota", "moskovskayarabota", "podrabotnikmd"}


def _text_with_breaks(node):
    for br in node.find_all("br"):
        br.replace_with("\n")
    return node.get_text().strip()


def _extract_title(text, soup_node):
    """Название — первый <b> после триггера, либо первая жирная строка."""
    bolds = [b.get_text(" ", strip=True) for b in soup_node.find_all("b")]
    bolds = [b for b in bolds if b and len(b) > 2]
    # выкидываем служебные ("Обязанности:", "Требования:" и т.п.)
    bad = re.compile(r"обязанност|требовани|услови|мы предлага|график|зарплат|"
                     r"что мы|о компан|адрес|контакт", re.IGNORECASE)
    for b in bolds:
        if not bad.search(b) and not RE_SALARY.search(b):
            return b.strip(" :।")
    # запасной вариант — первая непустая строка
    for line in text.split("\n"):
        line = line.strip()
        if line and not RE_TRIGGER.search(line):
            return line[:90]
    return ""


def _extract_salary(text):
    m = RE_SALARY.search(text)
    if not m:
        return "", None
    raw = m.group(0).strip()
    digits = re.sub(r"[^\d]", "", m.group(2))
    val = int(digits) if digits else None
    if val and val < 1000:
        val *= 1000
    return raw, val


def _extract_contact(text):
    """Возвращает (label, url) для кнопки отклика."""
    m = RE_CONTACT_TME.search(text)
    if m and m.group(1).lower() not in STOP_USERS:
        return "@" + m.group(1), "https://t.me/" + m.group(1)
    for m in RE_CONTACT_USER.finditer(text):
        if m.group(1).lower() not in STOP_USERS:
            return "@" + m.group(1), "https://t.me/" + m.group(1)
    m = RE_PHONE.search(text)
    if m:
        phone = m.group(1)
        return phone, "tel:" + re.sub(r"[^\d+]", "", phone)
    return "", None


def parse_post_single(msg_div):
    post_id = msg_div.get("data-post", "").strip()
    if not post_id:
        return None
    node = msg_div.select_one(".tgme_widget_message_text")
    if node is None:
        return None

    text = _text_with_breaks(node)
    if not RE_TRIGGER.search(text) and "₽" not in text:
        return None   # не похоже на вакансию

    title = _extract_title(text, node)
    if not title or len(title) < 3:
        return None

    salary_raw, salary_max = _extract_salary(text)
    contact_label, contact_url = _extract_contact(text)

    post = Post(post_id=post_id, header=title, company="", text=text)
    v = Vacancy(
        post_id=post_id, idx=1, title=title, url=contact_url,
        salary_raw=salary_raw, salary_max=salary_max,
        company="", post_header=title,
    )
    # прячем полный текст поста и метку контакта в поля, которые дальше используем
    v.jd_inline = text[:5000]
    v.contact_label = contact_label
    post.vacancies.append(v)
    return post


def parse_page_single(html):
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for div in soup.select(".tgme_widget_message"):
        p = parse_post_single(div)
        if p and p.vacancies:
            posts.append(p)
    return posts
