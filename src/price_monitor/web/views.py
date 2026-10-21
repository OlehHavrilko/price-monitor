from pathlib import Path
from urllib.parse import urlencode, quote, urlsplit

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).parent
STATE_LABELS = {"ok": "Працює", "failed": "Помилка збору", "stopped": "Зупинено", "empty": "Даних ще немає"}
STOCK_LABELS = {"IN_STOCK": "У наявності", "OUT_OF_STOCK": "Немає в наявності", "UNKNOWN": "Невідомо"}
EVENT_LABELS = {"PRICE_CHANGED": "Ціна змінилася", "OUT_OF_STOCK": "Закінчився", "BACK_IN_STOCK": "Знову в наявності",
                "REMOVED": "Видалено з каталогу", "RETURNED": "Повернувся до каталогу",
                "BELOW_THRESHOLD": "Ціна нижче порога", "PRICE_DROP_PERCENT": "Зниження на заданий відсоток",
                "AVAILABILITY_CHANGED": "Наявність змінилася"}


def safe_shop_url(value):
    try:
        url = urlsplit(value or "")
        return value if url.scheme in ("http", "https") and url.netloc else None
    except ValueError:
        return None


env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(default=True))
env.filters["segment"] = lambda v: quote(str(v), safe="")
env.filters["money"] = lambda v: format(v, "f") if v is not None else "Ціну не вказано"
env.filters["shop_url"] = safe_shop_url
env.globals.update(states=STATE_LABELS, stocks=STOCK_LABELS, event_labels=EVENT_LABELS)
templates = Jinja2Templates(env=env)


def render(request, name, status_code=200, **context):
    return templates.TemplateResponse(request=request, name=name, context=context, status_code=status_code)


def pagination(request, total, page, size):
    pages = max(1, (total + size - 1) // size)
    def link(number):
        values = dict(request.query_params)
        values["page"] = str(number)
        return request.url.path + "?" + urlencode(values)
    return dict(page=page, pages=pages, total=total,
                previous=link(page - 1) if page > 1 else None,
                next=link(page + 1) if page < pages else None)
