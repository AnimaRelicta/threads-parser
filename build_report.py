#!/usr/bin/env python3
"""Отчёт для заказчика: насколько расширена выборка Threads относительно прошлого файла (~4000 веток).

Запуск:
  uv run build_report.py --posts all-posts.xlsx --old Ветки_threads.xlsx --profiles all-profiles.xlsx --out report.xlsx

Логика (единая, не зависит от того, классифицированы ли посты):
  * страна поста  = about_country автора из all-profiles (сопоставление по username, без регистра);
  * класс поста   = category_id поста, если он заполнен; иначе category_id автора из all-profiles;
  * «Подходит»    = да, если класс из PASS_CLASSES (Барахолка, Витрина, Перекупщик, Домовой чат, Услуги);
                    нет — для Новости / Прочее (и старых Не подходит, Неизвестно, Репосты);
                    «не классифицирован» — если класса нет ни у поста, ни у автора. Это НЕ «нет»:
                    такие посты в «подходящих» не считаются, но и не записываются в «неподходящие»;
  * старый файл: если есть колонка «Подходит», берётся она (ручная/прошлая разметка), иначе считается по классу.
Меняется логика — правьте блок НАСТРОЙКИ ниже, остальной код трогать не нужно.
"""
import argparse, re, sys
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# ============================== НАСТРОЙКИ ==============================
HOME_COUNTRY = 'KZ'
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
PASS_CLASSES = {'Барахолка', 'Витрина', 'Перекупщик', 'Домовой чат', 'Услуги'}
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

def city_of(s):
    first = s.fillna('').astype(str).str.split(',').str[0].str.strip()
    first = first.str.replace(r'^[A-Z]{2}\s*', '', regex=True).str.strip()
    return first.replace('', '(город не указан)')

ap = argparse.ArgumentParser()
ap.add_argument('--posts', required=True); ap.add_argument('--old', required=True)
ap.add_argument('--profiles', required=True); ap.add_argument('--out', required=True)
a = ap.parse_args()

# ---------------- загрузка ----------------
new = read_first(a.posts); old = read_first(a.old); prof = read_first(a.profiles, 'all-profiles')
for df in (new, old):
    df['code_key'] = code_of(df); df['ukey'] = ukey(df['author_username'])
    df['likes_n'] = pd.to_numeric(df.get('likes'), errors='coerce').fillna(0)
    df['replies_n'] = pd.to_numeric(df.get('reply_count'), errors='coerce').fillna(0)
    df['date'] = pd.to_datetime(df['date_utc'].astype(str).str[:10], errors='coerce')

prof['ukey'] = ukey(prof['username'])
prof = prof.drop_duplicates('ukey')
prof['prof_cat'] = canon_cat(prof['category_id'])
prof['city'] = city_of(prof.get('text_location'))
P = prof.set_index('ukey')

def enrich(df, is_old):
    has_prof = df['ukey'].isin(P.index)
    df['Страна'] = df['ukey'].map(P['about_country']).fillna('не определена').where(has_prof, 'нет профиля')
    df['Город автора'] = df['ukey'].map(P['city'])
    df['Подписчиков у автора'] = pd.to_numeric(df['ukey'].map(P['followers_count']), errors='coerce')
    own = canon_cat(df['Класс']) if is_old else canon_cat(df.get('category_id', pd.Series(index=df.index, dtype=str)))
    df['Класс (итог)'] = own.fillna(df['ukey'].map(P['prof_cat'])).fillna('не классифицирован')
    derived = df['Класс (итог)'].isin(PASS_CLASSES).map({True: 'да', False: 'нет'}).where(df['Класс (итог)'] != 'не классифицирован', 'не классифицирован')
    if is_old and 'Подходит' in df:
        df['Подходит (итог)'] = df['Подходит'].str.strip().str.lower().where(df['Подходит'].notna(), derived)
    else:
        df['Подходит (итог)'] = derived
    df['KZ'] = df['Страна'].eq(HOME_COUNTRY)
    df['Классифицирован'] = df['Класс (итог)'].ne('не классифицирован')
    df['Страна известна'] = ~df['Страна'].isin(['нет профиля', 'не определена'])
    df['OK'] = df['Подходит (итог)'].eq('да')
    return df
old = enrich(old, True); new = enrich(new, False)

old_codes, new_codes = set(old['code_key'].dropna()), set(new['code_key'].dropna())
new['Новый пост (не было в прошлом файле)'] = ~new['code_key'].isin(old_codes)

# ---------------- метрики ----------------
allpost_keys = old['code_key'].dropna().tolist() + new['code_key'].dropna().tolist()
union_posts = len(set(allpost_keys)); union_auth = len(set(old.ukey) | set(new.ukey))
n_kz_prof = int(prof['about_country'].eq(HOME_COUNTRY).sum())

def metrics(d):
    ok = d[d.OK]; kz = d[d.KZ]; okkz = d[d.OK & d.KZ]
    return {
        'Постов (веток) всего': len(d),
        'Уникальных авторов': d.ukey.nunique(),
        'Постов авторов из Казахстана (страна по профилю)': len(kz),
        'Авторов из Казахстана (страна по профилю)': kz.ukey.nunique(),
        'Постов, у которых известна страна автора': int(d['Страна известна'].sum()),
        'Постов с известным классом': int(d['Классифицирован'].sum()),
        'Подходящих постов (только среди классифицированных)': len(ok),
        'Подходящих авторов (только среди классифицированных)': ok.ukey.nunique(),
        'Подходящих постов из Казахстана': len(okkz),
        'Лайков суммарно': int(d.likes_n.sum()),
        'Комментариев суммарно': int(d.replies_n.sum()),
    }
mo, mn = metrics(old), metrics(new)
kpi = pd.DataFrame({'Показатель': list(mo), 'Прошлый файл': list(mo.values()), 'Сейчас': list(mn.values())})
# строки про накопленную базу: прошлый файл -> прошлый + новый без дублей
extra = pd.DataFrame([
    ['Накопленная база: уникальных постов (прошлый + новый, без дублей)', len(old), union_posts],
    ['Накопленная база: уникальных авторов (прошлый + новый)', old.ukey.nunique(), union_auth],
    ['Профилей авторов из Казахстана в базе профилей', old[old.KZ].ukey.nunique(), n_kz_prof],
], columns=kpi.columns)
kpi = pd.concat([kpi.iloc[:2], extra, kpi.iloc[2:]], ignore_index=True)
kpi['Прирост, абс.'] = kpi['Сейчас'] - kpi['Прошлый файл']
kpi['Рост, раз'] = (kpi['Сейчас'] / kpi['Прошлый файл'].where(kpi['Прошлый файл'] > 0)).round(2)
cov = lambda d, c: f"{d[c].mean():.0%}"
notes = {
    'Постов, у которых известна страна автора': f"покрытие: прошлый {cov(old,'Страна известна')}, сейчас {cov(new,'Страна известна')}. Остальные авторы без профиля — страна неизвестна, это НЕ «не Казахстан»",
    'Постов с известным классом': f"покрытие: прошлый {cov(old,'Классифицирован')}, сейчас {cov(new,'Классифицирован')}. Посты не классифицированы — сравнивать «подходящие» корректно нельзя",
    'Подходящих постов (только среди классифицированных)': 'нижняя граница; в новом файле большая часть постов ещё без класса',
    'Подходящих авторов (только среди классифицированных)': 'нижняя граница',
    'Постов авторов из Казахстана (страна по профилю)': 'нижняя граница: страна известна не у всех авторов',
    'Авторов из Казахстана (страна по профилю)': 'нижняя граница',
    'Подходящих постов из Казахстана': 'нижняя граница',
    'Профилей авторов из Казахстана в базе профилей': 'прошлый = авторы старого файла с KZ-профилем; сейчас = все KZ-профили в all-profiles',
}
kpi['Примечание'] = kpi['Показатель'].map(notes).fillna('')
HEAD = ['Постов (веток) всего', 'Уникальных авторов',
        'Накопленная база: уникальных постов (прошлый + новый, без дублей)',
        'Профилей авторов из Казахстана в базе профилей']
N_CHART = 7   # первые 7 строк = счётчики без охвата/классов/лайков

overlap = pd.DataFrame([
    ['Постов в новом файле', len(new)],
    ['  из них уже были в прошлом файле', int((~new['Новый пост (не было в прошлом файле)']).sum())],
    ['  из них НОВЫХ', int(new['Новый пост (не было в прошлом файле)'].sum())],
    ['Постов прошлого файла, которых нет в новом', len(old_codes - new_codes)],
    ['Новых авторов (не было в прошлом файле)', len(set(new.ukey) - set(old.ukey))],
    ['Новых подходящих авторов из Казахстана', len(set(new[new.OK & new.KZ].ukey) - set(old.ukey))],
], columns=['Показатель', 'Значение'])

def by(col, name):
    def agg(d):
        g = d.groupby(col).agg(posts=('code_key', 'size'), authors=('ukey', 'nunique'))
        return g
    o, n = agg(old), agg(new)
    t = o.join(n, how='outer', lsuffix='_o', rsuffix='_n').fillna(0).astype(int)
    t.columns = ['Постов (прошлый)', 'Авторов (прошлый)', 'Постов (сейчас)', 'Авторов (сейчас)']
    t['Рост постов, раз'] = (t['Постов (сейчас)'] / t['Постов (прошлый)'].where(t['Постов (прошлый)'] > 0)).round(2)
    return t.sort_values('Постов (сейчас)', ascending=False).reset_index().rename(columns={col: name})

cat_t = by('Класс (итог)', 'Класс')
country_t = by('Страна', 'Страна')
kzok_o, kzok_n = old[old.KZ & old.OK], new[new.KZ & new.OK]
city_t = (lambda o, n: o.join(n, how='outer', lsuffix='_o', rsuffix='_n').fillna(0).astype(int))(
    kzok_o.groupby('Город автора').agg(p=('code_key', 'size'), a=('ukey', 'nunique')),
    kzok_n.groupby('Город автора').agg(p=('code_key', 'size'), a=('ukey', 'nunique')))
city_t.columns = ['Постов (прошлый)', 'Авторов (прошлый)', 'Постов (сейчас)', 'Авторов (сейчас)']
city_t = city_t.sort_values('Постов (сейчас)', ascending=False).reset_index().rename(columns={'Город автора': 'Город (подходящие, KZ)'})

wk = lambda d, n: d.dropna(subset=['date']).assign(Неделя=lambda x: x.date.dt.to_period('W').dt.start_time.dt.date).groupby('Неделя').size().rename(n)
weeks = pd.concat([wk(old, 'Постов (прошлый)'), wk(new, 'Постов (сейчас)')], axis=1).fillna(0).astype(int).sort_index().reset_index()
weeks['Неделя'] = weeks['Неделя'].astype(str)

top_auth = (new[new.OK & new.KZ].groupby('ukey').agg(Автор=('author_username', 'first'), Постов=('code_key', 'size'),
            Класс=('Класс (итог)', 'first'), Город=('Город автора', 'first'), Подписчиков=('Подписчиков у автора', 'first'),
            Лайков=('likes_n', 'sum')).sort_values('Постов', ascending=False).head(50).reset_index(drop=True))

period = lambda d: f"{d.date.min():%d.%m.%Y} – {d.date.max():%d.%m.%Y}" if d.date.notna().any() else '—'
method = [
 ['Что сравниваем', 'Прошлый файл (разметка веток) и новый выгруженный массив постов'],
 ['Период прошлого файла', period(old)], ['Период нового файла', period(new)],
 ['Страна автора', f'about_country из all-profiles; «{HOME_COUNTRY}» = Казахстан; нет профиля = автор не найден в all-profiles'],
 ['Класс поста', 'category_id поста; если пусто — category_id автора (профиля)'],
 ['Подходит', 'Да для: ' + ', '.join(sorted(PASS_CLASSES)) + '. Нет для Новости, Прочее (в прошлом файле: Не подходит, Неизвестно, Репосты)'],
 ['Не классифицирован', 'Нет класса ни у поста, ни у автора (нет профиля или профиль не классифицирован). Не считается ни «подходит», ни «не подходит»; показатели «подходящих» — нижняя граница'],
 ['Страна неизвестна', 'Автор не найден в all-profiles или у профиля пустая страна. Не считается «не Казахстан»'],
 ['Накопленная база', 'Уникальные посты/авторы старого и нового файлов вместе (пересечение по коду поста учтено один раз)'],
 ['Прошлый файл', 'Если в нём есть колонка «Подходит» — используется она'],
 ['Сопоставление постов', 'По коду поста (code / хвост url после /post/)'],
 ['Сопоставление авторов', 'По username без учёта регистра'],
]
meth = pd.DataFrame(method, columns=['Параметр', 'Описание'])

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
            if pd.isna(v) if not isinstance(v, (list, tuple)) else False: v = None
            if hasattr(v, 'item'): v = v.item()
            c = ws.cell(r0 + i, c0 + j, v)
            if fmt and isinstance(v, (int, float)): c.number_format = '#,##0.##'
    return r0, r0 + len(df)
def widths(ws, mx=60, cap=200):
    for i, col in enumerate(ws.columns, 1):
        w = max((len(str(c.value)) for c in list(col)[:cap] if c.value is not None), default=8)
        ws.column_dimensions[get_column_letter(i)].width = min(max(w + 2, 10), mx)

ws = wb.active; ws.title = 'Сводка'
ws['A1'] = 'Расширение выборки Threads: прошлый файл vs текущая выгрузка'; ws['A1'].font = Font(bold=True, size=15)
ws['A2'] = f"Прошлый файл: {period(old)} · Новый: {period(new)}"
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

def sheet(name, df, title=None, chart=None, **kw):
    w = wb.create_sheet(name); h0, h1 = put(w, df, 1, 1, title); w.freeze_panes = w.cell(h0 + 1, 1)
    widths(w, **kw)
    if chart:
        c = BarChart() if chart[0] == 'bar' else LineChart(); c.title = title or name; c.height, c.width = 9, 22
        c.add_data(Reference(w, min_col=chart[1], max_col=chart[2], min_row=h0, max_row=min(h1, h0 + chart[3])), titles_from_data=True)
        c.set_categories(Reference(w, min_col=1, min_row=h0 + 1, max_row=min(h1, h0 + chart[3])))
        w.add_chart(c, f'A{h1 + 3}')
    return w
sheet('По классам', cat_t, 'Посты по классам')
sheet('По странам', country_t, 'Посты по странам авторов')
sheet('Города KZ', city_t, 'Подходящие посты из Казахстана по городам', ('bar', 2, 4, 15))
sheet('Динамика', weeks, 'Посты по неделям', ('line', 2, 3, 60))
sheet('Топ авторов KZ', top_auth, 'Топ-50 подходящих авторов из Казахстана (сейчас)')
sheet('Методика', meth, 'Методика расчёта', mx=110)

det_cols = ['code_key', 'url', 'author_username', 'date_utc', 'likes', 'reply_count', 'Страна', 'Город автора', 'Класс (итог)', 'Подходит (итог)']
d_new = new[det_cols + ['Новый пост (не было в прошлом файле)', 'filter_status']].rename(columns={'code_key': 'code'})
d_old = old[det_cols].rename(columns={'code_key': 'code'})
for nm, d in (('Посты сейчас', d_new), ('Посты прошлый файл', d_old)):
    w = wb.create_sheet(nm); put(w, d.astype(object).where(d.notna(), None), fmt=False)
    w.freeze_panes = 'A2'; w.auto_filter.ref = w.dimensions; widths(w, 50)
wb.save(a.out)
print(kpi.to_string(index=False)); print(overlap.to_string(index=False)); print('saved', a.out)