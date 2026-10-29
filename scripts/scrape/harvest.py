"""Накопление постов из ответов сервера: пул, индекс ленты, выбор дочерних узлов."""

from core.jsonwalk import walk, walk_ordered
from scrape.extract import caption_of, is_video_obj, media_urls, topic_of


class Harvester:
    """Копит объекты постов/ответов из JSON-документов страницы.

    Каждый документ обходится ровно один раз, поэтому повторные проверки
    «сколько ответов уже есть» дёшевы.
    """

    def __init__(self):
        self.pool: dict[str, dict] = {}  # code -> самый полный объект
        self._n = 0

    def _add_doc(self, doc) -> None:
        for d in walk_ordered(doc):  # порядок вставки = порядок выдачи сервера
            code = d.get("code")
            if code and "taken_at" in d:
                prev = self.pool.get(code)
                if prev is None or len(d) > len(prev):
                    self.pool[code] = d

    def feed(self, docs: list) -> None:
        for doc in docs:
            self._add_doc(doc)

    def feed_new(self, live: list) -> None:
        n = len(live)
        for doc in live[self._n : n]:
            self._add_doc(doc)
        self._n = n


THREAD_KEYS = ("text", "likes", "reposts", "quotes", "author_id", "topic")


class FeedIndex:
    """Данные веток из JSON самой ленты профиля: дата, число ответов, видео, текст, счётчики.

    Позволяет отсеять старые ветки и видео, понять, появились ли новые ответы,
    и собрать список веток без открытия страницы ветки.
    """

    def __init__(self):
        self.items: dict[str, dict] = {}
        self._n = 0

    def _add_doc(self, doc) -> None:
        for d in walk(doc):
            code = d.get("code")
            ts = d.get("taken_at")
            if not code or ts is None:
                continue
            info = d.get("text_post_app_info") or {}
            user = d.get("user") if isinstance(d.get("user"), dict) else {}
            item = {
                "ts": ts,
                "replies": info.get("direct_reply_count"),
                "video": is_video_obj(d),
                # поля для быстрого сбора веток (--threads-only), без открытия ветки
                "text": caption_of(d),
                "likes": d.get("like_count"),
                "reposts": info.get("repost_count"),
                "quotes": info.get("quote_count"),
                "author_id": str(user["pk"]) if user.get("pk") is not None else None,
                "topic": topic_of(d),
            }
            prev = self.items.get(code)
            if prev:  # неполный повторный объект не стирает уже известные новые поля
                for k in THREAD_KEYS:
                    if item[k] is None:
                        item[k] = prev.get(k)
            self.items[code] = item

    def feed(self, docs: list) -> None:
        for doc in docs:
            self._add_doc(doc)

    def update(self, live: list) -> None:
        n = len(live)
        for doc in live[self._n : n]:
            self._add_doc(doc)
        self._n = n


def pick_children(
    pool: dict,
    node_code: str,
    node_author: str,
    node_ts,
    ancestors: tuple,
    rejected: dict | None = None,
) -> list[dict]:
    """Прямые ответы на узел среди объектов страницы.

    Отбрасываются: сам узел, его предки (путь, по которому мы пришли), всё,
    что опубликовано раньше узла, не-ответы и ответы на других авторов
    (вложенные ответы, показанные на странице «внутри»; их заберём, когда
    дойдём до их родителя).
    """

    def rej(reason: str) -> None:
        if rejected is not None:
            rejected[reason] = rejected.get(reason, 0) + 1

    out = []
    for code, d in pool.items():
        if code == node_code or code in ancestors:
            continue
        user = d.get("user")
        if not isinstance(user, dict) or not user.get("username"):
            rej("без автора")
            continue
        ts = d.get("taken_at")
        if ts is None:
            rej("без даты")
            continue
        if node_ts is not None and int(ts) < int(node_ts):
            rej("старше узла")
            continue
        info = d.get("text_post_app_info") or {}
        if "reply_to_author" in info:
            rta = info["reply_to_author"]
            if not rta:
                rej("не ответ")
                continue  # это не ответ, а посторонний пост на странице
            rname = rta.get("username") if isinstance(rta, dict) else None
            if rname and rname.lower() != node_author.lower():
                rej("ответ другому автору")
                continue  # ответ на другого автора: чужая ветка
        if not (caption_of(d) or media_urls(d)):
            rej("пустой (нет текста и медиа)")
            continue
        out.append(d)
    return out
