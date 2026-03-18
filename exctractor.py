#!/usr/bin/env python3
# coding: utf-8
"""
simple_md_extractor.py
Простой экстрактор только Markdown (без картинок), с понятными именами файлов/папок и сохранением сессии.
Файлы:
 - modules.txt  (по одной записи: id шага или URL шага)
 - auth_state.json (создаётся/используется автоматически)
Выход:
 - output/<NN>_<slug_урока>/...
"""
import os
import re
import time
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup
from markdownify import markdownify as mdify
from playwright.sync_api import sync_playwright

BASE = "https://buro20.ru"
MODULES_FILE = "modules.txt"
OUTPUT_DIR = "output"
AUTH_STATE = "auth_state.json"
USER_AGENT = "Mozilla/5.0 (compatible)"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def slugify(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    # keep unicode letters, replace non-word with dash
    s = re.sub(r"[^\w\s\-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s)
    s = s.strip("-").lower()
    return s or "untitled"


def normalize_step_input(line: str) -> str:
    s = line.strip()
    if not s:
        return None
    if re.fullmatch(r"\d+", s):
        return f"{BASE}/pl/teach/control/lesson/view?id={s}"
    if s.startswith("/"):
        return urljoin(BASE, s)
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("teach/") or s.startswith("pl/"):
        return urljoin(BASE, s)
    return None


def extract_id_from_url(url: str) -> str:
    try:
        p = urlparse(url)
        q = parse_qs(p.query)
        if "id" in q:
            return q["id"][0]
    except:
        pass
    m = re.search(r"(\d+)(?!.*\d)", url)
    return m.group(1) if m else "unknown"


def get_next_step_link_from_soup(soup: BeautifulSoup):
    # 1) точные ссылки с текстом "Следующий" или "Следующий урок"
    a = soup.find("a", string=lambda t: t and "следующ" in t.lower())
    if a and a.get("href"):
        return a["href"]
    # 2) ссылки, где в тексте встречается "след" и href содержит lesson/view
    for a in soup.select("a[href*='lesson/view']"):
        if "след" in (a.get_text(" ", strip=True) or "").lower() or "next" in (a.get_text(" ", strip=True) or "").lower():
            return a["href"]
    # 3) другие варианты: кнопки с иконкой стрелки
    for a in soup.select("a[href*='lesson/view']"):
        if a.select_one(".fa-angle-right") or a.select_one(".lucide-arrow-right") or "next" in (a.get("aria-label") or "").lower():
            return a["href"]
    return None


def step_title_from_soup(soup: BeautifulSoup):
    # несколько вариантов, берём первое подходящее
    for sel in [".lesson-title-value", "h2.lesson-title-value", ".link.title", ".lite-block-live-wrapper h2", "h1", "h2"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            return el.get_text(strip=True)
    # fallback: meta title
    t = soup.title.string if soup.title else None
    if t:
        return t.strip()
    return None


def page_blocks_to_md(soup: BeautifulSoup):
    # ищем блоки контента, преобразуем в markdown (без картинок)
    blocks = soup.select(".lite-block-live-wrapper")
    if not blocks:
        # fallback-варианты
        candidate = soup.select_one(".lesson-content") or soup.select_one(".lesson-body") or soup.select_one(".content")
        if candidate:
            blocks = [candidate]
    md_parts = []
    for block in blocks:
        # удалить теги <img> и заменять на пустую строку (нам не нужны картинки)
        for img in block.select("img"):
            img.decompose()
        # преобразовать
        html = str(block)
        md = mdify(html, heading_style="ATX")
        if md.strip():
            md_parts.append(md.strip())
    return "\n\n".join(md_parts).strip()


def find_first_step_on_lesson_page(soup: BeautifulSoup, base_url: str):
    # если страница — описание урока (список уроков), попробуем найти ссылку на первый шаг
    # ищем список уроков .lesson-list a[href*='lesson/view']
    first = None
    for a in soup.select(".lesson-list a[href*='lesson/view']"):
        href = a.get("href")
        if href:
            first = href
            break
    if first:
        if first.startswith("/"):
            return urljoin(BASE, first)
        return urljoin(base_url, first)
    # fallback: общий поиск ссылок на lesson/view с data-lesson-id
    for a in soup.select("a[href*='lesson/view?id=']"):
        href = a.get("href")
        if href:
            if href.startswith("/"):
                return urljoin(BASE, href)
            return urljoin(base_url, href)
    return None


def run():
    if not os.path.exists(MODULES_FILE):
        print(f"Создай {MODULES_FILE} с id(ами) или URL стартовых шагов (по одной записи в строке).")
        return
    with open(MODULES_FILE, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    if not lines:
        print("modules.txt пуст.")
        return

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        # попытаемся восстановить auth_state
        if os.path.exists(AUTH_STATE):
            context = browser.new_context(storage_state=AUTH_STATE)
            print("Используем auth_state.json для сессии.")
        else:
            context = browser.new_context()
        page = context.new_page()
        page.set_extra_http_headers({"User-Agent": USER_AGENT})

        # если нет auth_state — попросим зайти вручную и сохраним
        if not os.path.exists(AUTH_STATE):
            page.goto(BASE, timeout=30000)
            print("Открыл браузер. Если нужно, залогинься вручную в окне браузера.")
            input("После успешного входа и загрузки любой страницы нажми Enter...")
            try:
                context.storage_state(path=AUTH_STATE)
                print("Состояние сессии сохранено в", AUTH_STATE)
            except Exception as e:
                print("Не удалось сохранить auth_state:", e)

        for idx, raw in enumerate(lines, start=1):
            start_url = normalize_step_input(raw)
            if not start_url:
                print(f"[{idx}] Пропускаю непонятную строку: {raw}")
                continue

            print(f"[{idx}] Обрабатываю старт: {start_url}")
            try:
                page.goto(start_url, timeout=30000)
                time.sleep(0.5)
            except Exception as e:
                print("  Не удалось открыть стартовый URL:", e)
                continue

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")

            # Если попали на страницу-описание (не шаг) — пытаемся найти первый шаг
            # Признак: нет .lite-block-live-wrapper и есть список уроков
            if not soup.select_one(".lite-block-live-wrapper"):
                first_step = find_first_step_on_lesson_page(soup, start_url)
                if first_step:
                    print("  Стартовый URL оказался страницей урока/списка. Переходим к первому шагу:", first_step)
                    try:
                        page.goto(first_step, timeout=30000)
                        time.sleep(0.4)
                        html = page.content()
                        soup = BeautifulSoup(html, "html.parser")
                    except Exception as e:
                        print("   Ошибка при переходе к первому шагу:", e)
                        continue
                else:
                    print("  Не найдено содержимого шага или ссылка на первый шаг — пропускаю.")
                    continue

            # Получаем заголовок урока для имени папки
            lesson_title = soup.select_one(".lesson-title-value") or soup.select_one("h2.lesson-title-value") or soup.select_one(".link.title") or soup.select_one("h1")
            lesson_title_text = lesson_title.get_text(strip=True) if lesson_title else f"lesson_{extract_id_from_url(start_url)}"
            lesson_slug = slugify(lesson_title_text)
            folder_name = f"{idx:02d}_{lesson_slug}"
            lesson_dir = os.path.join(OUTPUT_DIR, folder_name)
            os.makedirs(lesson_dir, exist_ok=True)
            combined = []

            visited = set()
            cur_url = page.url
            step_no = 0

            while cur_url:
                step_id = extract_id_from_url(cur_url)
                if step_id in visited:
                    print("    цикл обнаружен, выходим:", step_id)
                    break
                visited.add(step_id)
                step_no += 1

                print(f"    -> Открываю шаг: {cur_url}")
                try:
                    page.goto(cur_url, timeout=30000)
                    time.sleep(0.4)
                except Exception as e:
                    print("      ошибка при open:", e)
                    break

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")

                # получаем заголовок шага
                stitle = step_title_from_soup(soup) or f"Step {step_no} ({step_id})"
                st_slug = slugify(stitle)[:60]  # короткий slug
                filename = f"{step_no:02d}_step_{step_no}_{step_id}_{st_slug}.md"
                filepath = os.path.join(lesson_dir, filename)

                # парсим блоки в md (без картинок)
                md_body = page_blocks_to_md(soup)
                # если нет контента — попробуем взять общий текст страницы
                if not md_body.strip():
                    txt = soup.get_text("\n", strip=True)
                    md_body = mdify(f"<div>{txt[:3000]}</div>", heading_style="ATX")

                # сохраняем отдельный md
                with open(filepath, "w", encoding="utf-8") as fh:
                    fh.write(f"# {stitle}\n\n")
                    fh.write(md_body + "\n")
                print(f"      saved: {filepath}")

                combined.append(f"# {stitle}\n\n{md_body}\n\n---\n\n")

                # ищем ссылку Next
                next_href = get_next_step_link_from_soup(soup)
                if not next_href:
                    print("    -> Кнопка 'Следующий' не найдена — конец урока.")
                    break
                # нормализуем
                if next_href.startswith("/"):
                    next_url = urljoin(BASE, next_href)
                else:
                    next_url = urljoin(cur_url, next_href)
                if next_url == cur_url:
                    print("    -> Следующий URL совпадает с текущим — стоп.")
                    break
                cur_url = next_url
                time.sleep(0.25)

            # сохраняем общий lesson.md
            lesson_md_path = os.path.join(lesson_dir, "lesson.md")
            with open(lesson_md_path, "w", encoding="utf-8") as fh:
                fh.write(f"# {lesson_title_text}\n\n")
                fh.write("\n".join(combined))
            print(f"[{idx}] Сохранён урок: {lesson_md_path} (шагов {step_no})")

        try:
            context.close()
        except:
            pass
        try:
            browser.close()
        except:
            pass


if __name__ == "__main__":
    run()