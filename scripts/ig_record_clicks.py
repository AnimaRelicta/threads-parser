#!/usr/bin/env python3
"""Рекордер действий для Instagram: открывает его с куками аккаунта и записывает, на что нажимают.

    uv run scripts/ig_record_clicks.py account_001 [--url https://www.instagram.com/garikkharlamov/]
                                       [--net] [--bodies]

Дальше всё делаете руками в окне (профиль -> «···» -> «Об аккаунте» и т. д.). Тексты, на которые
не нажимают (био, счётчики, ссылка в шапке), помечайте: внизу слева панель с меткой
(био / счётчик / ссылка / другое), выберите её и сделайте Alt+клик по нужному тексту. Клик при
этом не срабатывает, элемент подсветится рамкой.

Каждый клик печатается в консоль и пишется в data/recordings/ig_<время>.jsonl и .md: цепочка
элементов от нажатого вверх (тег, role, aria-label, title у svg, текст, data-testid), готовый
селектор Playwright, кусок HTML, для кликов в окне ещё и весь текст окна.

--net     дописывает в запись запросы xhr/fetch к Instagram (метод, статус, адрес): так видно,
          какой эндпоинт отдаёт ленту, профиль, комментарии.
--bodies  (включает --net) сохраняет тела JSON-ответов /api/ и /graphql в
          data/recordings/ig_<время>_net/NNN.json (до 2 МБ каждое). В них бывают ваши данные
          аккаунта: перед отправкой просмотрите файлы.

Закончили: закройте окно браузера или Ctrl+C. Файл .md пришлите целиком.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.async_api import async_playwright  # noqa: E402

from browser.launch import launch_browser, new_context  # noqa: E402
from config import IG, ROOT, load_ig_settings  # noqa: E402
from ig.browser.session import load_cookies  # noqa: E402

BODY_LIMIT = 2_000_000
NET_MARKERS = ("/api/", "/graphql")

# Инъекция в каждую страницу и фрейм: ловим клики в фазе capture, раньше обработчиков сайта.
INIT_JS = r"""
(() => {
  if (window.__rec) return; window.__rec = true;
  const clean = s => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 80);
  const describe = el => {
    const t = el.querySelector && el.querySelector(':scope > title');
    return {
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role'),
      aria: el.getAttribute('aria-label'),
      title: t ? clean(t.textContent) : el.getAttribute('title'),
      testid: el.getAttribute('data-testid') || el.getAttribute('data-pressable-container'),
      href: el.getAttribute('href'),
      id: el.id || null,
      text: clean(el.innerText || el.textContent),
      cls: (el.getAttribute('class') || '').split(/\s+/).slice(0, 3).join('.'),
    };
  };
  const css = el => {
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && parts.length < 6; e = e.parentElement) {
      let s = e.tagName.toLowerCase();
      if (e.id) { parts.unshift(s + '#' + e.id); break; }
      const r = e.getAttribute('role'); if (r) s += `[role="${r}"]`;
      parts.unshift(s);
    }
    return parts.join(' > ');
  };
  const send = (kind, extra) => window.__recSend && window.__recSend(JSON.stringify(
    Object.assign({kind, url: location.href, t: Date.now()}, extra)));
  window.__label = 'био';
  const LABELS = ['био', 'счётчик', 'ссылка', 'другое'];
  const paint = p => p.querySelectorAll('button').forEach(b => {
    b.style.background = b.dataset.l === window.__label ? '#2d6cdf' : '#444'; });
  const ensurePanel = () => {
    if (window.top !== window || !document.body || document.getElementById('__rec_panel')) return;
    const p = document.createElement('div'); p.id = '__rec_panel';
    p.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:2147483647;background:#111;' +
      'color:#fff;font:12px sans-serif;padding:6px;border-radius:8px;opacity:.93';
    p.innerHTML = '<div style="margin-bottom:4px">Alt+клик по элементу = пометить как:</div>';
    for (const l of LABELS) {
      const b = document.createElement('button'); b.textContent = l; b.dataset.l = l;
      b.style.cssText = 'color:#fff;border:0;border-radius:4px;padding:3px 8px;margin-right:4px;cursor:pointer';
      b.onclick = ev => { ev.stopPropagation(); window.__label = l; paint(p); };
      p.appendChild(b);
    }
    document.body.appendChild(p); paint(p);
  };
  setInterval(ensurePanel, 1000);
  window.addEventListener('click', e => {
    if (!e.altKey) return;
    const el = e.target; if (!el || !el.tagName || el.closest('#__rec_panel')) return;
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    let box = el;
    for (let i = 0; i < 12 && box.parentElement; i++) {
      box = box.parentElement; if (box.querySelector('a[href*="/p/"], a[href*="/reel/"]')) break;
    }
    const links = [...new Set([...box.querySelectorAll('a[href]')].map(a => a.href))].slice(0, 20);
    const chain = [];
    for (let x = el; x && x.nodeType === 1 && chain.length < 7; x = x.parentElement)
      chain.push(describe(x));
    const a = el.closest('a');
    send('pick', {
      label: window.__label, text: clean(el.innerText || el.textContent), href: a ? a.href : null,
      chain, css: css(el), html: el.outerHTML.slice(0, 600),
      box_css: css(box), box_text: (box.innerText || '').replace(/\s+/g, ' ').slice(0, 600),
      box_links: links, box_html: box.outerHTML.slice(0, 1500),
    });
    const old = el.style.outline; el.style.outline = '3px solid #2d6cdf';
    setTimeout(() => { el.style.outline = old; }, 700);
  }, true);
  const onClick = e => {
    const el = e.target; if (!el || !el.tagName || el.closest('#__rec_panel')) return;
    const chain = [];
    for (let x = el; x && x.nodeType === 1 && chain.length < 7; x = x.parentElement)
      chain.push(describe(x));
    const btn = el.closest('[role=button],button,a,[role=menuitem],[role=tab],[tabindex]') || el;
    const r = btn.getBoundingClientRect();
    const dlg = el.closest('[role=dialog]');
    send('click', {
      chain, css: css(el), button_css: css(btn),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      html: btn.outerHTML.slice(0, 700),
      in_dialog: !!dlg,
      dialog_text: dlg ? (dlg.innerText || '').replace(/\s+/g, ' ').slice(0, 1500) : null,
    });
  };
  window.addEventListener('click', onClick, true);
  window.addEventListener('keydown', e => {
    if (['Escape', 'Enter', 'Tab'].includes(e.key)) send('key', {key: e.key});
  }, true);
})();
"""


def locator_hint(ev: dict) -> str:
    """Предложение селектора Playwright по цепочке: первое, что отличает элемент."""
    for d in ev["chain"]:
        if d["title"] and d["tag"] in ("svg", "path"):
            return f'svg:has(> title:text-is("{d["title"]}"))'
        if d["aria"]:
            return f'[aria-label="{d["aria"]}"]'
        if d["testid"]:
            return f'[data-testid="{d["testid"]}"]'
        if d["text"] and len(d["text"]) < 40 and d["tag"] in ("span", "div", "a", "button"):
            return f'get_by_text("{d["text"]}", exact=True)'
    return ev["button_css"]


def chain_line(ev: dict) -> str:
    return " <- ".join(
        f"{d['tag']}"
        + (f"[{d['role']}]" if d["role"] else "")
        + (f"(aria={d['aria']})" if d["aria"] else "")
        + (f"(title={d['title']})" if d["title"] else "")
        + (f"«{d['text'][:30]}»" if d["text"] else "")
        for d in ev["chain"][:4]
    )


def render_event(ev: dict, n: int) -> tuple[str, str]:
    """Событие -> (строка в консоль, блок для .md). Для net и key счётчик n не растёт."""
    kind = ev["kind"]
    if kind == "pick":
        console = (
            f"[{n}] ПОМЕТКА «{ev['label']}» | текст: {ev['text'][:60]!r} | href: {ev['href']}\n"
            f"      css: {ev['css']}\n      ссылок в карточке: {len(ev['box_links'])}"
        )
        md = (
            f"## Пометка {n}: {ev['label']}\n- страница: {ev['url']}\n"
            f"- текст элемента: {ev['text']!r}\n- href: {ev['href']}\n"
            f"- css: `{ev['css']}`\n- карточка (css): `{ev['box_css']}`\n"
            f"- текст карточки: {ev['box_text']!r}\n- ссылки карточки: {ev['box_links']}\n"
            f"```html\n{ev['html']}\n```\nКарточка:\n```html\n{ev['box_html']}\n```\n\n"
        )
    elif kind == "key":
        console, md = f"    [клавиша] {ev['key']}", f"- клавиша **{ev['key']}**\n"
    elif kind == "net":
        body = f" -> {ev['body_file']}" if ev.get("body_file") else ""
        line = f"{ev['method']} {ev['status']} {ev['url_req']}{body}"
        console, md = f"    [сеть] {line}", f"- сеть: `{line}`\n"
    else:
        chain, hint = chain_line(ev), locator_hint(ev)
        console = f"[{n}] клик | {chain}\n      селектор: {hint}"
        dialog = f"- текст окна: {ev['dialog_text']!r}\n" if ev.get("dialog_text") else ""
        md = (
            f"## Клик {n}\n- страница: {ev['url']}\n- в окне(dialog): {ev['in_dialog']}\n"
            f"{dialog}- цепочка: {chain}\n- селектор: `{hint}`\n- css: `{ev['css']}`\n"
            f"- кнопка: `{ev['button_css']}` rect={ev['rect']}\n"
            f"```html\n{ev['html']}\n```\n\n"
        )
    return console, md


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("account", nargs="?", default="account_001", help="имя аккаунта (data/ig_cookies)")
    ap.add_argument("--url", default=f"{IG}/", help="с какой страницы начать")
    ap.add_argument("--net", action="store_true", help="записывать запросы xhr/fetch")
    ap.add_argument("--bodies", action="store_true", help="сохранять тела JSON-ответов (+ --net)")
    args = ap.parse_args()
    args.net = args.net or args.bodies

    st = load_ig_settings()
    data = load_cookies(st.cookies_path(args.account))
    if not data:
        raise SystemExit(f"Нет кук аккаунта «{args.account}» ({st.cookies_path(args.account)})")

    out = ROOT / "data" / "recordings"
    out.mkdir(parents=True, exist_ok=True)
    stamp = "ig_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    net_dir = out / f"{stamp}_net"
    jl = (out / f"{stamp}.jsonl").open("w", encoding="utf-8")
    md = (out / f"{stamp}.md").open("w", encoding="utf-8")
    md.write(f"# Запись действий Instagram {stamp}, аккаунт {args.account}\n\n")
    state = {"n": 0, "net": 0}

    def emit(ev: dict) -> None:
        jl.write(json.dumps(ev, ensure_ascii=False) + "\n")
        jl.flush()
        if ev["kind"] in ("click", "pick"):
            state["n"] += 1
        console, block = render_event(ev, state["n"])
        print(console)
        md.write(block)
        md.flush()

    def on_event(_source, raw: str) -> None:
        emit(json.loads(raw))

    async def on_response(resp) -> None:
        req = resp.request
        if req.resource_type not in ("xhr", "fetch") or not any(m in resp.url for m in NET_MARKERS):
            return
        ev = {"kind": "net", "method": req.method, "status": resp.status, "url_req": resp.url,
              "url": resp.frame.url if resp.frame else None, "body_file": None}
        if args.bodies and "json" in (resp.headers.get("content-type") or ""):
            try:
                body = await resp.body()
                if len(body) <= BODY_LIMIT:
                    net_dir.mkdir(exist_ok=True)
                    state["net"] += 1
                    path = net_dir / f"{state['net']:03d}.json"
                    path.write_bytes(body)
                    ev["body_file"] = f"{net_dir.name}/{path.name}"
            except Exception:
                pass  # тело недоступно (редирект, закрытая вкладка): остаётся только строка
        emit(ev)

    async with async_playwright() as p:
        browser = await launch_browser(p, headless=False)
        ctx = await new_context(browser, data)
        await ctx.expose_binding("__recSend", on_event)
        await ctx.add_init_script(INIT_JS)
        page = await ctx.new_page()
        if args.net:
            page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        await page.goto(args.url, wait_until="domcontentloaded")
        print(
            "[*] Запись пошла. Нажимайте как обычно, тексты помечайте Alt+клик (панель слева внизу);\n"
            "    закройте окно браузера, чтобы закончить.\n"
            f"    Файлы: {out}/{stamp}.md и .jsonl"
            + (f", тела ответов: {net_dir}" if args.bodies else "")
        )
        closed = asyncio.Event()
        browser.on("disconnected", lambda *_: closed.set())
        page.on("close", lambda *_: closed.set())
        try:
            await closed.wait()
        except asyncio.CancelledError:
            pass
        if browser.is_connected():
            await browser.close()
    jl.close()
    md.close()
    print(f"[+] Записано действий: {state['n']}. Пришлите {out}/{stamp}.md")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[*] Остановлено")
