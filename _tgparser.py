"""
Парсер публичного превью Telegram-канала (t.me/s/<channel>).

Формат постов канала «Твоя работа | MY DAY»:
    <Заголовок> N вакансий от «Компания» / гибрид / З/П до 400.000р
    <цитата-комментарий автора>
    1. <ссылка-должность>
    (З/П до 250.000р).
    2. <ссылка-должность>
    (З/П на ушко).
    ...

Отсюда достаём: компанию из заголовка, и по каждому пункту — название,
URL (спрятан в <a href>), зарплату.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup


@dataclass
class Vacancy:
    post_id: str            # "moskovskayarabota/1234"
    idx: int                # номер пункта в посте
    title: str
    url: Optional[str]
    salary_raw: str = ""    # "до 230.000р" / "на ушко"
    salary_max: Optional[int] = None   # 230000
    company: str = ""
    post_header: str = ""
    jd_inline: str = ""
    contact_label: str = ""

    @property
    def uid(self) -> str:
        return f"{self.post_id}#{self.idx}"


@dataclass
class Post:
    post_id: str
    header: str
    company: str
    text: str
    vacancies: list = field(default_factory=list)


# ---------- вспомогательные регулярки ----------

# «6 ярких вакансий от «2MOOD» / гибрид / З/П до 250.000р»
RE_COMPANY = re.compile(r"от\s*[«\"']([^»\"']+)[»\"']")
# «(З/П до 230.000р).» / «(З/П от 310.000р)» / «(З/П на ушко)»
RE_SALARY = re.compile(r"\(?\s*З/?П\s*([^)\n]+)\)?", re.IGNORECASE)
# число вида 230.000 / 230 000 / 230000
RE_NUM = re.compile(r"(\d[\d\s.,]*\d|\d)")
# начало пункта: «1.» / «2)» в начале строки
RE_ITEM_START = re.compile(r"^\s*(\d{1,2})[.)]\s*")


def _parse_salary(chunk: str):
    """Из «до 230.000р» -> (raw, 230000). Из «на ушко» -> (raw, None)."""
    m = RE_SALARY.search(chunk)
    if not m:
        return "", None
    raw = m.group(1).strip().rstrip(").").strip()
    nums = RE_NUM.findall(raw)
    if not nums:
        return raw, None
    digits = re.sub(r"[^\d]", "", nums[0])
    if not digits:
        return raw, None
    val = int(digits)
    # «250.000» -> 250000; иногда пишут «250» имея в виду тысячи
    if val < 1000:
        val *= 1000
    return raw, val


def _block_to_lines(node):
    """
    Превращает div.tgme_widget_message_text в список строк,
    где каждая строка — список кусков: ('text', str) или ('link', text, url).
    <br> — перенос строки.
    """
    lines = [[]]
    for el in node.descendants:
        name = getattr(el, "name", None)
        if name == "br":
            lines.append([])
        elif name is None:  # NavigableString
            parent = el.parent
            # текст внутри <a> заберём на уровне самого <a>
            if getattr(parent, "name", None) == "a":
                continue
            s = str(el)
            if s.strip():
                lines[-1].append(("text", s))
        elif name == "a":
            href = el.get("href", "")
            lines[-1].append(("link", el.get_text(" ", strip=True), href))
    return lines


def _line_text(line) -> str:
    out = []
    for piece in line:
        out.append(piece[1])
    return " ".join(out).strip()


# ссылки-мусор: хештеги и поисковые запросы внутри Telegram
def _is_junk_link(text: str, href: str) -> bool:
    t = (text or "").strip()
    h = href or ""
    if t.startswith("#"):
        return True
    if "?q=%23" in h or h.startswith("?q="):
        return True
    if "/s/" in h and "?q=" in h:
        return True
    return False


def _clean_title(t: str, from_link: bool = False) -> str:
    """Обрезает хвосты описания, которые прилипают когда у пункта нет ссылки."""
    t = (t or "").replace("\n", " ").strip()
    if not from_link:
        # «Территориальный менеджер сети / подробности. — Ты чувствуешь тренды...»
        t = re.split(r"\s*/\s*подробност", t, flags=re.IGNORECASE)[0]
        t = re.split(r"\s+[—–]\s+", t)[0]
        t = re.split(r"\s*\.\s+", t)[0]
    t = re.sub(r"\s+", " ", t).strip(" /.,:;«»\"'")
    if len(t) > 120:
        t = t[:117].rstrip() + "..."
    return t


def parse_post(msg_div) -> Optional[Post]:
    """Разбирает один div.tgme_widget_message."""
    post_id = msg_div.get("data-post", "").strip()
    if not post_id:
        return None

    text_node = msg_div.select_one(".tgme_widget_message_text")
    if text_node is None:
        return None

    lines = _block_to_lines(text_node)
    flat_text = "\n".join(_line_text(l) for l in lines if _line_text(l))

    header = next((_line_text(l) for l in lines if _line_text(l)), "")
    m = RE_COMPANY.search(header) or RE_COMPANY.search(flat_text)
    company = m.group(1).strip() if m else ""

    post = Post(post_id=post_id, header=header, company=company, text=flat_text)

    # --- собираем пункты 1. 2. 3. ---
    current = None          # (idx, [line, line, ...])
    items = []
    for line in lines:
        txt = _line_text(line)
        if not txt:
            continue
        m_start = RE_ITEM_START.match(txt)
        if m_start:
            if current:
                items.append(current)
            current = (int(m_start.group(1)), [line])
        elif current:
            # строка «(З/П ...)» относится к предыдущему пункту
            current[1].append(line)
    if current:
        items.append(current)

    for idx, item_lines in items:
        # первая ссылка в пункте = вакансия
        title, url = "", None
        for line in item_lines:
            for piece in line:
                if piece[0] == "link":
                    if _is_junk_link(piece[1], piece[2]):
                        continue
                    title, url = _clean_title(piece[1], from_link=True), piece[2]
                    break
            if title:
                break

        joined = " ".join(_line_text(l) for l in item_lines)
        if not title:
            # ссылки нет — берём текст пункта до скобки с З/П
            title = RE_ITEM_START.sub("", joined)
            title = re.split(r"\(?\s*З/?П", title)[0]
            title = _clean_title(title)

        salary_raw, salary_max = _parse_salary(joined)

        if not title or title.startswith("#") or len(title) < 4:
            continue

        post.vacancies.append(
            Vacancy(
                post_id=post_id,
                idx=idx,
                title=title.strip(),
                url=url,
                salary_raw=salary_raw,
                salary_max=salary_max,
                company=company,
                post_header=header,
            )
        )

    return post


def parse_page(html: str) -> list:
    """Разбирает страницу t.me/s/<channel> -> список Post."""
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for div in soup.select(".tgme_widget_message"):
        p = parse_post(div)
        if p and p.vacancies:
            posts.append(p)
    return posts
