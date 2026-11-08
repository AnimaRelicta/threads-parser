#!/usr/bin/env python3
"""Отчёт для заказчика: насколько расширена выборка Threads относительно прошлого файла (~4000 веток).

Запуск:
  uv run build_report.py --posts all-posts.xlsx --old Ветки_threads.xlsx --profiles all-profiles.xlsx --out report.xlsx
  (окно: --months 3 по умолчанию, до даты самого свежего поста; либо явно --from 2026-08-01 --to 2026-10-31)
Оба файла берутся за один и тот же промежуток; всё остальное считается только внутри него.

Логика:
  * страна поста  = страна автора из about_location в all-profiles (KZ/UZ по справочнику core/geo.py,
                    другое место = OTHER; сопоставление по username, без регистра);
  * класс поста   = category_id поста, если он заполнен; иначе category_id автора из all-profiles
                    (в старом файле берётся колонка «Класс»); нет ни там ни там = «не классифицирован»;
  * авторы по числу веток = сколько авторов имеют в выборке не менее 1 / 2 / 3 уникальных веток.
Меняется логика классов — правьте блок НАСТРОЙКИ ниже, остальной код трогать не нужно.
"""
import argparse, sys
from pathlib import Path
import pandas as pd
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# ============================== НАСТРОЙКИ ==============================
HOME_COUNTRY = 'KZ'
MIN_POSTS_LEVELS = (1, 2, 3)   # пороги «автор с не менее N ветками»
# все варианты написания категории (id, code, русское имя) -> каноническое имя
CATEGORY_ALIASES = {
    '1': 'Витрина', 'vitrina': 'Витрина', 'витрина': 'Витрина',
    '2': 'Барахолка', 'baraholka': 'Барахолка', 'барахолка': 'Барахолка',
    '3': 'Перекупщик', 'reseller': 'Перекупщик', 'перекупщик': 'Перекупщик',
    '4': 'Новости', 'news': 'Новости', 'новости': 'Новости',
    '5': 'Домовой чат', 'house_chat': 'Домовой чат', 'домовой чат': 'Домовой чат', 'домовой': 'Домовой чат',
    '6': 'Услуги', 'services': 'Услуги', 'услуги': 'Услуги',
    '7': 'Прочее', 'other': 'Прочее', 'прочее': 'Прочее',
    'не подходит': 'Не подходит', 'неизвестно': 'Неизвестно', 'репосты': 'Репосты',   # только старый файл
}
# ========================================================================

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def read_first(path, prefer=None):
    sheets = pd.read_excel(path, sheet_name=None, dtype=str)
    name = prefer if prefer in sheets else list(sheets)[0]
    df = sheets[name]; df.columns = [str(c).strip() for c in df.columns]
    return df

def canon_cat(s):
    return s.fillna('').astype(str).str.strip().str.lower().map(CATEGORY_ALIASES)

def code_of(df):
    from_url = df['url'].astype(str).str.extract(r'/post/([^/?#\s]+)')[0]
    return df['code'].where(df['code'].notna(), from_url) if 'code' in df else from_url

def ukey(s): return s.astype(str).str.strip().str.lstrip('@').str.lower()

def num(df, col):
    return pd.to_numeric(df[col] if col in df else pd.Series(0, index=df.index), errors='coerce').fillna(0)

def author_counts(d):
    """автор (ukey) -> число уникальных веток (по коду поста)."""
    u = d[d['code_key'].isna() | ~d['code_key'].duplicated()]
    return u.groupby('ukey').size()

ap = argparse.ArgumentParser()
ap.add_argument('--posts', required=True); ap.add_argument('--old', required=True)
ap.add_argument('--profiles', required=True); ap.add_argument('--out', required=True)
ap.add_argument('--months', type=int, default=3, help='длина окна отчёта в месяцах (по умолчанию 3)')
ap.add_argument('--from', dest='date_from', help='начало окна YYYY-MM-DD (по умолчанию конец окна минус --months)')
ap.add_argument('--to', dest='date_to', help='конец окна YYYY-MM-DD включительно (по умолчанию дата самого свежего поста)')
a = ap.parse_args()

# ---------------- загрузка ----------------
new = read_first(a.posts); old = read_first(a.old); prof = read_first(a.profiles, 'all-profiles')
prof_all = prof.copy()   # все профили как есть, для листа «Все авторы»
for df in (new, old):
    df['code_key'] = code_of(df); df['ukey'] = ukey(df['author_username'])
    df['likes_n'] = num(df, 'likes'); df['replies_n'] = num(df, 'reply_count')
    df['date'] = pd.to_datetime(df['date_utc'].astype(str).str[:10], errors='coerce')

# ---------------- единое окно периода для обоих файлов ----------------
win_end = pd.Timestamp(a.date_to) if a.date_to else max(d.date.max() for d in (new, old) if d.date.notna().any())
win_start = pd.Timestamp(a.date_from) if a.date_from else (win_end - pd.DateOffset(months=a.months) + pd.Timedelta(days=1))
WIN = f'{win_start:%d.%m.%Y} – {win_end:%d.%m.%Y}'
before = (len(old), len(new))
old = old[(old.date >= win_start) & (old.date <= win_end)].copy()
new = new[(new.date >= win_start) & (new.date <= win_end)].copy()
print(f'Окно отчёта: {WIN}. Постов в окне: прошлый {len(old)} из {before[0]}, новый {len(new)} из {before[1]} '
      f'(посты без даты и вне окна исключены)')

prof['ukey'] = ukey(prof['username'])
prof = prof.drop_duplicates('ukey')
prof['prof_cat'] = canon_cat(prof['category_id'])
# Колонки about_country в БД больше нет (миграция 019): страна из about_location, как у geo.resolve
sys.path.insert(0, str(Path(__file__).parent / 'scripts'))
from core.geo import find_locations  # noqa: E402


def country_of(place):
    place = '' if pd.isna(place) else str(place).strip()
    if not place:
        return None
    found = find_locations(place)
    return found[0].country if found else 'OTHER'


prof['about_country'] = prof['about_location'].map(country_of) if 'about_location' in prof else None
P = prof.set_index('ukey')

def enrich(df, is_old):
    has_prof = df['ukey'].isin(P.index)
    df['Страна'] = df['ukey'].map(P['about_country']).fillna('не определена').where(has_prof, 'нет профиля')
    col = 'Класс' if is_old else 'category_id'
    own = canon_cat(df[col]) if col in df else pd.Series(index=df.index, dtype=object)
    df['Класс (итог)'] = own.fillna(df['ukey'].map(P['prof_cat'])).fillna('не классифицирован')
    df['KZ'] = df['Страна'].eq(HOME_COUNTRY)
    df['Классифицирован'] = df['Класс (итог)'].ne('не классифицирован')
    df['Страна известна'] = ~df['Страна'].isin(['нет профиля', 'не определена'])
    return df
old = enrich(old, True); new = enrich(new, False)

old_codes, new_codes = set(old['code_key'].dropna()), set(new['code_key'].dropna())
new['Новый пост (не было в прошлом файле)'] = (~new['code_key'].isin(old_codes)).map({True: 'да', False: 'нет'})

# ---------------- метрики ----------------
union_posts = len(old_codes | new_codes); union_auth = len(set(old.ukey) | set(new.ukey))
n_kz_prof = int(prof['about_country'].eq(HOME_COUNTRY).sum())

def lvl_name(n): return f'Авторов с не менее {n} {"веткой" if n == 1 else "ветками"}'

def metrics(d):
    kz = d[d.KZ]; cnt = author_counts(d)
    m = {'Постов (веток) всего': len(d)}
    for n in MIN_POSTS_LEVELS:
        m[lvl_name(n)] = int((cnt >= n).sum())
    m.update({
        'Постов авторов из Казахстана (страна по профилю)': len(kz),
        'Авторов из Казахстана (страна по профилю)': kz.ukey.nunique(),
        'Постов, у которых известна страна автора': int(d['Страна известна'].sum()),
        'Постов с известным классом': int(d['Классифицирован'].sum()),
        'Лайков суммарно': int(d.likes_n.sum()),
        'Комментариев суммарно': int(d.replies_n.sum()),
    })
    return m
mo, mn = metrics(old), metrics(new)
kpi = pd.DataFrame({'Показатель': list(mo), 'Прошлый файл': list(mo.values()), 'Сейчас': list(mn.values())})
# накопленная база: прошлый файл -> прошлый + новый без дублей
extra = pd.DataFrame([
    ['Накопленная база: уникальных постов (прошлый + новый, без дублей)', len(old_codes), union_posts],
    ['Накопленная база: уникальных авторов (прошлый + новый)', old.ukey.nunique(), union_auth],
    ['Профилей авторов из Казахстана в базе профилей', old[old.KZ].ukey.nunique(), n_kz_prof],
], columns=kpi.columns)
k0 = 1 + len(MIN_POSTS_LEVELS)
kpi = pd.concat([kpi.iloc[:k0], extra, kpi.iloc[k0:]], ignore_index=True)
kpi['Прирост, абс.'] = kpi['Сейчас'] - kpi['Прошлый файл']
kpi['Рост, раз'] = (kpi['Сейчас'] / kpi['Прошлый файл'].where(kpi['Прошлый файл'] > 0)).round(2)
N_CHART = k0 + len(extra)   # строки-счётчики для диаграммы (посты, авторы по порогам, накопленная база)

fmt_d = lambda x: f'{x:%d.%m.%Y}' if pd.notna(x) else '—'
dates = pd.DataFrame([
    ['Самый старый пост', fmt_d(old.date.min()), fmt_d(new.date.min())],
    ['Самый свежий пост', fmt_d(old.date.max()), fmt_d(new.date.max())],
], columns=['Показатель', 'Прошлый файл', 'Сейчас'])
kpi = pd.concat([kpi, dates], ignore_index=True)

cov = lambda d, c: f"{d[c].mean():.0%}"
notes = {lvl_name(n): f'авторы, у которых в выборке не менее {n} уникальных веток (по коду поста)' for n in MIN_POSTS_LEVELS}
notes.update({
    'Постов, у которых известна страна автора': f"покрытие: прошлый {cov(old,'Страна известна')}, сейчас {cov(new,'Страна известна')}. Остальные авторы без профиля — страна неизвестна, это НЕ «не Казахстан»",
    'Постов с известным классом': f"покрытие: прошлый {cov(old,'Классифицирован')}, сейчас {cov(new,'Классифицирован')}. Остальные посты без класса",
    'Постов авторов из Казахстана (страна по профилю)': 'нижняя граница: страна известна не у всех авторов',
    'Авторов из Казахстана (страна по профилю)': 'нижняя граница',
    'Профилей авторов из Казахстана в базе профилей': 'прошлый = авторы старого файла с KZ-профилем; сейчас = все KZ-профили в all-profiles',
    'Самый старый пост': 'по date_utc, дата без времени',
    'Самый свежий пост': 'по date_utc, дата без времени',
})
kpi['Примечание'] = kpi['Показатель'].map(notes).fillna('')
HEAD = ['Постов (веток) всего'] + [lvl_name(n) for n in MIN_POSTS_LEVELS] + [
    'Накопленная база: уникальных постов (прошлый + новый, без дублей)',
    'Профилей авторов из Казахстана в базе профилей']

overlap = pd.DataFrame([
    ['Постов в новом файле', len(new)],
    ['  из них уже были в прошлом файле', int((new['Новый пост (не было в прошлом файле)'] == 'нет').sum())],
    ['  из них НОВЫХ', int((new['Новый пост (не было в прошлом файле)'] == 'да').sum())],
    ['Постов прошлого файла, которых нет в новом', len(old_codes - new_codes)],
    ['Новых авторов (не было в прошлом файле)', len(set(new.ukey) - set(old.ukey))],
], columns=['Показатель', 'Значение'])

def by(col, name):
    agg = lambda d: d.groupby(col).agg(posts=('code_key', 'size'), authors=('ukey', 'nunique'))
    t = agg(old).join(agg(new), how='outer', lsuffix='_o', rsuffix='_n').fillna(0).astype(int)
    t.columns = ['Постов (прошлый)', 'Авторов (прошлый)', 'Постов (сейчас)', 'Авторов (сейчас)']
    t['Рост постов, раз'] = (t['Постов (сейчас)'] / t['Постов (прошлый)'].where(t['Постов (прошлый)'] > 0)).round(2)
    return t.sort_values('Постов (сейчас)', ascending=False).reset_index().rename(columns={col: name})

cat_t = by('Класс (итог)', 'Класс')
country_t = by('Страна', 'Страна')

wk = lambda d, n: d.dropna(subset=['date']).assign(Неделя=lambda x: x.date.dt.to_period('W').dt.start_time.dt.date).groupby('Неделя').size().rename(n)
weeks = pd.concat([wk(old, 'Постов (прошлый)'), wk(new, 'Постов (сейчас)')], axis=1).fillna(0).astype(int).sort_index().reset_index()
weeks['Неделя'] = weeks['Неделя'].astype(str)

period = lambda d: f"{d.date.min():%d.%m.%Y} – {d.date.max():%d.%m.%Y}" if d.date.notna().any() else '—'
lv = ', '.join(str(n) for n in MIN_POSTS_LEVELS)
method = [
 ['Общее', 'Что сравниваем', 'Прошлый файл (разметка веток) и новый выгруженный массив постов (all-posts) с данными профилей (all-profiles)'],
 ['Общее', 'Окно отчёта', f'{WIN}: один и тот же промежуток для обоих файлов (по умолчанию {a.months} мес. до даты самого свежего поста; задаётся --months, --from, --to). Границы включительно'],
 ['Общее', 'Что попадает в окно', 'Только ветки с date_utc внутри окна; ветки без даты и вне окна исключены из всех метрик, листов и из списка постов. Профили в «Все авторы» не фильтруются, число веток автора считается внутри окна'],
 ['Общее', 'Период прошлого файла', period(old) + ' (внутри окна)'],
 ['Общее', 'Период нового файла', period(new) + ' (внутри окна)'],
 ['Общее', 'Ветка / пост', 'Одна строка файла = одна ветка (запись верхнего уровня). Ветки считаются по коду поста'],
 ['Источники', 'Новый файл (all-posts)', 'Используются колонки: code/url, author_username, date_utc, likes, reply_count, category_id'],
 ['Источники', 'Прошлый файл', 'Используются те же колонки; класс берётся из колонки «Класс». Остальные колонки прошлой разметки на листе «Посты прошлый файл» сохранены как есть'],
 ['Источники', 'Профили (all-profiles)', 'Используются колонки: username, about_location (из неё считается страна), category_id. Профили дедуплицируются по username без регистра и @'],
 ['Сопоставление', 'Постов', 'По коду поста (колонка code или хвост url после /post/)'],
 ['Сопоставление', 'Авторов', 'По username без учёта регистра и без @'],
 ['Сопоставление', 'Накопленная база', 'Уникальные посты/авторы старого и нового файлов вместе; пересечение по коду поста считается один раз'],
 ['Определения', 'Страна автора', f'страна из about_location в all-profiles; «{HOME_COUNTRY}» = Казахстан. «нет профиля» = автор не найден в all-profiles; «не определена» = профиль есть, страна пустая'],
 ['Определения', 'Страна неизвестна', 'Автор без профиля или с пустой страной. Это НЕ «не Казахстан», поэтому показатели по Казахстану — нижняя граница'],
 ['Определения', 'Класс поста', 'category_id поста (id, код или русское имя приводятся к одному названию); если пусто — category_id автора из профиля'],
 ['Определения', 'Не классифицирован', 'Нет класса ни у поста, ни у автора (нет профиля или профиль не классифицирован)'],
 ['Определения', 'Авторы с не менее N ветками', f'Для порогов {lv}: у автора считаются уникальные ветки (по коду поста) в соответствующем файле, затем подсчитывается, у скольких авторов их не менее N. Порог 1 = все уникальные авторы файла'],
 ['Определения', 'Самый старый / самый свежий пост', 'Минимум и максимум date_utc по файлу (дата без времени); посты без даты не учитываются'],
 ['Определения', 'Лайки и комментарии', 'Сумма likes и reply_count по всем веткам файла; пустые значения считаются нулём'],
 ['Расчёты', 'Прирост, абс.', 'Сейчас минус Прошлый файл (для дат не считается)'],
 ['Расчёты', 'Рост, раз', 'Сейчас / Прошлый файл, округлено до сотых; пусто, если в прошлом файле значение 0'],
 ['Расчёты', 'Новые посты', 'Посты нового файла, кода которых нет в прошлом файле (лист «Сводка», блок «Что нового»)'],
 ['Расчёты', 'Динамика', 'Число веток по неделям (неделя начинается с понедельника) отдельно для прошлого и нового файла'],
 ['Листы', 'Сводка', 'Ключевые метрики (в т. ч. авторы по порогам веток, самый старый и свежий пост), что нового, диаграмма'],
 ['Листы', 'По классам / По странам', 'Число постов и авторов по итоговому классу и по стране автора, прошлый файл против нового'],
 ['Листы', 'Динамика', 'Посты по неделям'],
 ['Листы', 'Посты сейчас', 'Новый файл целиком (все исходные колонки) + колонки Страна, Класс (итог), Новый пост'],
 ['Листы', 'Посты прошлый файл', 'Прошлый файл целиком (все исходные колонки) + колонки Страна, Класс (итог)'],
 ['Листы', 'Все авторы', 'Все профили из all-profiles (все исходные колонки) + авторы постов без профиля; число веток автора в новом и прошлом файле; признак «Есть профиль»'],
 ['Ограничения', 'Нижние границы', 'Показатели по Казахстану считаются только по авторам с известной страной; значения в исходных файлах не корректируются'],
]
meth = pd.DataFrame(method, columns=['Раздел', 'Параметр', 'Описание'])

# ---------------- запись Excel ----------------
wb = Workbook(); HDR = PatternFill('solid', fgColor='1F4E78'); KPI_FILL = PatternFill('solid', fgColor='E2EFDA')
def put(ws, df, r0=1, c0=1, title=None, fmt=True):
    if title:
        ws.cell(r0, c0, title).font = Font(bold=True, size=13); r0 += 1
    for j, col in enumerate(df.columns):
        c = ws.cell(r0, c0 + j, col); c.font = Font(bold=True, color='FFFFFF'); c.fill = HDR
        c.alignment = Alignment(wrap_text=True, vertical='center')
    for i, row in enumerate(df.itertuples(index=False), 1):
        for j, v in enumerate(row):
            if not isinstance(v, (list, tuple)) and pd.isna(v): v = None
            if hasattr(v, 'item'): v = v.item()
            if isinstance(v, str): v = ILLEGAL_CHARACTERS_RE.sub('', v)[:32767]   # лимит Excel и недопустимые символы
            c = ws.cell(r0 + i, c0 + j, v)
            if isinstance(v, str) and v.startswith('='): c.data_type = 's'   # текст, а не формула
            if fmt and isinstance(v, (int, float)) and not isinstance(v, bool): c.number_format = '#,##0.##'
    return r0, r0 + len(df)
def widths(ws, mx=60, cap=200):
    for i, col in enumerate(ws.columns, 1):
        w = max((len(str(c.value)) for c in list(col)[:cap] if c.value is not None), default=8)
        ws.column_dimensions[get_column_letter(i)].width = min(max(w + 2, 10), mx)

ws = wb.active; ws.title = 'Сводка'
ws['A1'] = 'Расширение выборки Threads: прошлый файл vs текущая выгрузка'; ws['A1'].font = Font(bold=True, size=15)
ws['A2'] = f"Окно отчёта: {WIN} · Прошлый файл: {period(old)} · Новый: {period(new)}"
r = 4
for k in HEAD:
    row = kpi[kpi['Показатель'] == k].iloc[0]
    ws.cell(r, 1, k).font = Font(bold=True)
    ws.cell(r, 2, f"{int(row['Прошлый файл']):,} → {int(row['Сейчас']):,}".replace(',', ' '))
    ws.cell(r, 3, f"×{row['Рост, раз']}" if pd.notna(row['Рост, раз']) else '—').font = Font(bold=True, size=13, color='2E7D32')
    for c in range(1, 4): ws.cell(r, c).fill = KPI_FILL
    r += 1
h0, h1 = put(ws, kpi, r + 1, 1, 'Ключевые метрики')
kpi_hdr, kpi_end = h0, h1
r2 = h1 + 3; put(ws, overlap, r2, 1, 'Что нового относительно прошлого файла')
ch = BarChart(); ch.type = 'bar'; ch.title = 'Прошлый файл vs сейчас'; ch.height, ch.width = 9, 22
ch.add_data(Reference(ws, min_col=2, max_col=3, min_row=kpi_hdr, max_row=kpi_hdr + N_CHART), titles_from_data=True)
ch.set_categories(Reference(ws, min_col=1, min_row=kpi_hdr + 1, max_row=kpi_hdr + N_CHART))
ws.add_chart(ch, 'H4')
widths(ws, 55)
ws.column_dimensions['A'].width = 52

def sheet(name, df, title=None, chart=None, wrap=False, **kw):
    w = wb.create_sheet(name); h0, h1 = put(w, df, 1, 1, title); w.freeze_panes = w.cell(h0 + 1, 1)
    widths(w, **kw)
    if wrap:
        for row in w.iter_rows(min_row=h0 + 1):
            for c in row: c.alignment = Alignment(wrap_text=True, vertical='top')
    if chart:
        c = BarChart() if chart[0] == 'bar' else LineChart(); c.title = title or name; c.height, c.width = 9, 22
        c.add_data(Reference(w, min_col=chart[1], max_col=chart[2], min_row=h0, max_row=min(h1, h0 + chart[3])), titles_from_data=True)
        c.set_categories(Reference(w, min_col=1, min_row=h0 + 1, max_row=min(h1, h0 + chart[3])))
        w.add_chart(c, f'A{h1 + 3}')
    return w
sheet('По классам', cat_t, 'Посты по классам')
sheet('По странам', country_t, 'Посты по странам авторов')
sheet('Динамика', weeks, 'Посты по неделям', ('line', 2, 3, 60))
wm = sheet('Методика', meth, 'Методика расчёта', wrap=True, mx=110)
wm.column_dimensions['A'].width = 16; wm.column_dimensions['B'].width = 38

# ---- полные данные: исходные колонки + расчётные, без служебных ----
HELPERS = ['code_key', 'ukey', 'likes_n', 'replies_n', 'date', 'KZ', 'Классифицирован', 'Страна известна']
def full(d): return d.drop(columns=[c for c in HELPERS if c in d.columns])

# все авторы: все профили + авторы постов без профиля, с числом веток
cnt_new, cnt_old = author_counts(new), author_counts(old)
authors = prof_all.copy(); k = ukey(authors['username'])
authors['Есть профиль'] = 'да'
missing = sorted((set(cnt_new.index) | set(cnt_old.index)) - set(k))
name_of = pd.concat([new, old]).drop_duplicates('ukey').set_index('ukey')['author_username']
authors = pd.concat([authors, pd.DataFrame({'username': [name_of[u] for u in missing], 'Есть профиль': 'нет'})], ignore_index=True)
k = ukey(authors['username'])
authors['Постов сейчас'] = k.map(cnt_new).fillna(0).astype(int)
authors['Постов прошлый файл'] = k.map(cnt_old).fillna(0).astype(int)

for nm, d in (('Посты сейчас', full(new)), ('Посты прошлый файл', full(old)), ('Все авторы', authors)):
    w = wb.create_sheet(nm); put(w, d.astype(object).where(d.notna(), None), fmt=False)
    w.freeze_panes = 'A2'; w.auto_filter.ref = w.dimensions; widths(w, 50)
wb.save(a.out)
print(kpi.to_string(index=False)); print(overlap.to_string(index=False)); print('saved', a.out)