#!/usr/bin/env python3
"""
discover_promo_transfer.py

1) Берёт cookie-файлы из ./cookies (формат: dict name->value или selenium-style list)
2) GET https://pwonline.ru/promo_items.php с headers, похожими на браузер (из вашего cURL)
3) Сохраняет страницу и пытается найти элементы подарков и возможные endpoint'ы перевода
4) Результат сохраняет в out/<account>.gifts_candidates.json
"""
import os, json, re, time
from bs4 import BeautifulSoup
import requests
from urllib.parse import urljoin

# --- Настройки ---
BASE = "https://pwonline.ru"
PAGE = "/promo_items.php"
FULL_URL = BASE + PAGE

COOKIES_DIR = "./cookies"
OUT_DIR = "./out"
os.makedirs(OUT_DIR, exist_ok=True)

# headers взяты и упрощены из вашего cURL — достаточно для requests
COMMON_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": FULL_URL,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    # "Sec-Fetch-*", "sec-ch-ua" и т.п. обычно не нужны для requests
}

# --- Вспомогательные функции ---
def load_cookie_dict(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        out = {}
        for item in data:
            name = item.get("name") or item.get("Name") or item.get("key")
            value = item.get("value") or item.get("Value")
            if name and value:
                out[name] = value
        return out
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    raise ValueError("Unsupported cookie file format: " + repr(type(data)))

def session_from_cookies(cdict):
    s = requests.Session()
    s.headers.update(COMMON_HEADERS)
    s.cookies.update(cdict)
    return s

def find_gift_elements(html):
    soup = BeautifulSoup(html, "html.parser")
    # Попробуем несколько вариантов селекторов; собираем кандидаты.
    selectors = [".chest_input_block", ".item_input_block"]
    candidates = []
    for sel in selectors:
        els = soup.select(sel)
        if els:
            for e in els:
                candidates.append({"selector": sel, "html": str(e)})
    return candidates

def parse_chest_page(url, session):
    """
    Получаем страницу сундука и собираем все предметы внутри
    """
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []

    # все блоки item_input_block — это отдельные предметы
    
    for block in soup.find_all("div", class_="item_input_block"):
        inputs = block.find_all("input", class_="promo_all_item_box")
        ans ="n"
        for inp in inputs:
            item_id = inp.get("value")
            item_type = inp.get("type")
            label_tag = block.find("label")
            item_name = label_tag.get_text(strip=True) if label_tag else ""
            if item_type == "radio":
                ans = input(f"Добавить предмет '{item_name}'? [y/N]: ").strip().lower()
                if ans == "y":
                    items.append({"id": item_id, "name": item_name})
                    return items
            


def discover_transfer_from_element(elem_html, s):
    """
    Парсим элемент подарка и пытаемся найти:
      - form (action + inputs)
      - <a href> с параметрами
      - button/input с onclick="..." (в т.ч. AJAX)
      - data-attributes (data-gift-id)
    Возвращаем список кандидатов: [{type, method, url, payload_example, notes}, ...]
    """
    soup = BeautifulSoup(elem_html, "html.parser")
    out = []
    # 2) <a href>
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # ignore anchors
        if href.startswith("#"):
            continue
        items = parse_chest_page(urljoin(FULL_URL, href), s)
        payload = {}
        for item in items:
            payload.setdefault("chest_items[]", [])
            payload["chest_items[]"].append(item["id"])

    # --- POST или GET на активацию выбранных предметов ---
        if payload:
        # requests умеет автоматически ставить multiple checkbox как list
            print(f"Активация выбранных предметов: {payload}")
            r2 = s.post(urljoin(FULL_URL, href), data=payload, allow_redirects=False)
            print(f"Статус: {r2.status_code}")

    # 3) buttons / inputs with onclick -> sometimes contain JS: location='...'
    onclicks = []
    tag = soup.find()
    gid = None
    gid = tag.find("input", {"name": "cart_items[]"})
    if gid:
        gid = gid.get("value")
    if gid:
        out.append({
            "type": "gift_id_hint",
            "method": "post",
            "url": None,
            "payload_example": {"gift_id": gid},
            "notes": "found possible gift id attribute"
        })

    # attach collected onclicks as note
    return out
    
def parse_character_selector(html):
    soup = BeautifulSoup(html, "html.parser")
    script_tag = soup.find("script", string=re.compile(r"var\s+shards\s*="))
    if not script_tag:
        print("⚠️ Персонажи не найдены (нет var shards).")
        return None

    # Ищем JSON внутри var shards
    match = re.search(r"var\s+shards\s*=\s*(\{.*\})\s*</script>", script_tag.decode_contents(), re.DOTALL)
    if not match:
        # Если конец </script> мешает, можно просто искать все {...}
        match = re.search(r"var\s+shards\s*=\s*(\{.*\})", script_tag.decode_contents(), re.DOTALL)

    if not match:
        print("⚠️ Не удалось извлечь JSON shards.")
        return None

    shards_json = match.group(1)
    try:
        data = json.loads(shards_json)
    except json.JSONDecodeError as e:
        print("❌ Ошибка при парсинге JSON:", e)
        return None

    characters = []
    for shard_id, shard in data.items():
        shard_name = shard["name"]
        for acc_id, acc in shard["accounts"].items():
            for char in acc["chars"]:
                char_id = f"{acc_id}_{shard_id}_{char['id']}"
                char_name = f"{char['name']} ({char['occupation']}, уровень:{char['level']})"
                characters.append({
                    "id": char_id,
                    "display": char_name,
                    "server": shard_name
                })

    # Если персонаж один — сразу возвращаем
    if len(characters) == 1:
        c = characters[0]
        print(f"✨ Единственный персонаж: {c['display']} [{c['id']}]")
        return c["id"]

    # Если их несколько — спрашиваем
    print("Доступные персонажи:")
    for i, c in enumerate(characters, start=1):
        print(f"{i}. {c['display']} [{c['id']}] на сервере {c['server']}")

    for c in characters:
        answer = input(f"Оставить {c['display']}? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            print(f"✅ Выбран {c['display']}")
            return c["id"]

    print("❌ Никто не выбран.")
    return None

def transfer_items(s, results, acc_info, max_total=30, batch_size=10):
    """
    Отправляет предметы на перевод в игру за выбранного персонажа.
    - s: requests.Session с куками
    - results: финальный словарь с candidates
    - acc_info: строка вида "153705720_2_4237200" — выбранный персонаж
    - max_total: максимум предметов за этот запуск
    - batch_size: количество предметов в одной отправке
    """
    # собираем все item_id из candidates
    item_ids = []
    for cand in results["candidates"]:
        for disc in cand["discoveries"]:
            if disc.get("type") == "gift_id_hint" and disc.get("payload_example"):
                item_ids.append(disc["payload_example"]["gift_id"])
    
    if not item_ids:
        print("⚠️ Не найдено предметов для перевода")
        return

    # лимитируем до max_total
    item_ids = item_ids[:max_total]

    # делим на батчи
    for i in range(0, len(item_ids), batch_size):
        batch = item_ids[i:i + batch_size]
        payload = {"do": "process", "cart_items[]": batch, "acc_info": acc_info}
        try:
            r = s.post(FULL_URL, data=payload, timeout=10, allow_redirects=False)
            r.raise_for_status()
            print(f"✅ Переведено предметов: {len(batch)} / {len(item_ids)} для персонажа {acc_info}")
        except Exception as e:
            print("❌ Ошибка при переводе:", e)

def get_game_account_from_pin_page(s):
    """
    Парсит страницу /pin/ и возвращает game_account из hidden поля.
    """
    url = "https://pwonline.ru/pin/"
    r = s.get(url, timeout=10)
    r.raise_for_status()
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    hidden_input = soup.select_one("form[action='/pin.php?do=activate'] input[name='game_account']")
    if hidden_input:
        return hidden_input.get("value")
    else:
        print("⚠️ Не удалось найти hidden game_account")
        return None

def activate_promo_pin(s, promo_code):
    """
    Применяет промокод (PIN) на странице /pin.php?do=activate.
    - s: requests.Session с куками
    - promo_code: строка промокода
    - game_account_id: id аккаунта/персонажа (как в поле game_account)
    """
    url = "https://pwonline.ru/pin.php?do=activate"
    
    game_account_id = get_game_account_from_pin_page(s)
    payload = {
        "pin": promo_code,
        "game_account": str(game_account_id)
    }

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://pwonline.ru",
        "Referer": "https://pwonline.ru/pin/",
        "User-Agent": COMMON_HEADERS["User-Agent"],
    }

    try:
        r = s.post(url, data=payload, headers=headers, timeout=10, allow_redirects=False)
    except Exception as e:
        print("❌ Ошибка при применении промокода:", e)
# --- Main ---
def process_cookie_file(cookie_file, PROMO_CODE):
    cdict = load_cookie_dict(cookie_file)
    acct = os.path.splitext(os.path.basename(cookie_file))[0]
    s = session_from_cookies(cdict)
#    print(PROMO_CODE)
    if (PROMO_CODE != ""):
        activate_promo_pin(s, PROMO_CODE)
    while True:
        print(f"[{acct}] GET {FULL_URL} ...")
        r = s.get(FULL_URL, timeout=15)
        r.raise_for_status()
        html = r.text

        # save page
        page_path = os.path.join(OUT_DIR, f"{acct}.page.html")
        with open(page_path, "w", encoding="utf-8") as f:
            f.write(html)

        # ищем кандидатов
        candidates = find_gift_elements(html)
        results = {
            "account": acct,
            "url": FULL_URL,
            "status_code": r.status_code,
            "found": len(candidates),
            "candidates": []
        }

        for i, cand in enumerate(candidates):
            elem_html = cand["html"]
            # save element to file для ручной проверки
            with open(os.path.join(OUT_DIR, f"{acct}.gift.{i}.html"), "w", encoding="utf-8") as f:
                f.write(elem_html)
            discoveries = discover_transfer_from_element(elem_html, s)
            results["candidates"].append({
                "index": i,
                "selector_used": cand["selector"],
                "short_html": (elem_html[:400] + "...") if len(elem_html) > 400 else elem_html,
                "discoveries": discoveries
            })

        # выводим кандидатов
#        print(json.dumps(results["candidates"], ensure_ascii=False, indent=2))

        # проверяем, есть ли ещё сундуки
        soup = BeautifulSoup(html, "html.parser")
        remaining_chests = soup.select(".chest_input_block")
        if remaining_chests:
            print(f"⚠️ Найдены сундуки ({len(remaining_chests)}), обновляем страницу...")
            time.sleep(1)  # небольшой таймаут перед повтором
            continue  # повторяем цикл
        else:
            print("Сундуков больше нет, выходим.")
            break  # больше нет сундуков, выходим

    
    acc_info = parse_character_selector(html)
    # save JSON
    transfer_items(s, results, acc_info)
#    out_path = os.path.join(OUT_DIR, f"{acct}.gifts_candidates.json")
#    with open(out_path, "w", encoding="utf-8") as f:
#        json.dump(results, f, ensure_ascii=False, indent=2)
#    print(f"[{acct}] saved {out_path}")
    return 

def main():
    if not os.path.isdir(COOKIES_DIR):
        print("Cookies dir not found:", COOKIES_DIR)
        return
    files = [os.path.join(COOKIES_DIR,f) for f in os.listdir(COOKIES_DIR) if f.lower().endswith(".json")]
    if not files:
        print("no cookie files")
        return
    PROMO_CODE = input("Введите промокод: ").strip()
    for fn in files:
        try:
            process_cookie_file(fn, PROMO_CODE)
        except Exception as e:
            print("Error processing", fn, e)
        time.sleep(1)

if __name__ == "__main__":
    main()