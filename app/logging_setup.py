"""Логирование: единый формат, stdout (Amvera собирает его сама), идентификатор запроса, маскирование.

В журнал не попадают: секрет webhook, токены, полные идентификаторы игроков, речь ребёнка
и тело запроса Алисы. Для сопоставления записей используются request id и короткий псевдоним.
"""

from __future__ import annotations

import logging
import re
import sys
import uuid
from contextvars import ContextVar
from urllib.parse import parse_qsl, urlencode

logger = logging.getLogger("iskorka")

FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"
DEFAULT_LEVEL = "INFO"
_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_HANDLER_MARK = "_iskorka_handler"

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_REQUEST_ID_OK = re.compile(r"[A-Za-z0-9._-]{1,64}")
_SENSITIVE_QUERY = re.compile(r"token|key|secret|pass|auth|sign|code", re.IGNORECASE)
MASK = "***"


def new_request_id(incoming: str | None = None) -> str:
    """Берёт безопасный X-Request-Id от прокси или создаёт свой."""
    if incoming and _REQUEST_ID_OK.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex[:12]


def parse_level(value: str | None) -> tuple[str, str | None]:
    """Возвращает (уровень, предупреждение). Ошибочное значение не отключает журнал: берём INFO."""
    name = (value or DEFAULT_LEVEL).strip().upper()
    if name in _LEVELS:
        return name, None
    return DEFAULT_LEVEL, f"Неизвестный LOG_LEVEL={value!r}: допустимы {sorted(_LEVELS)}; использован {DEFAULT_LEVEL}."


def safe_path(path: str, secret: str = "") -> str:
    """Путь без секрета webhook и без чувствительных query-параметров."""
    base, _, query = path.partition("?")
    if base.startswith("/alice/"):
        base = "/alice/" + MASK  # любой сегмент после /alice/ — секрет или попытка его подобрать
    if secret:
        base = base.replace(secret, MASK)
    if query:
        pairs = [(k, MASK if _SENSITIVE_QUERY.search(k) else v) for k, v in parse_qsl(query, keep_blank_values=True)]
        query = urlencode(pairs, safe="*")
        return f"{base}?{query}"
    return base


def pseudonym(key: str | None) -> str:
    """Короткий псевдоним хэш-ключа игрока для журнала (сам ключ и ID платформы не пишем)."""
    return (key or "-")[:8]


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _AccessLogMasker(logging.Filter):
    """Маскирует секрет в штатных access-сообщениях Uvicorn (аргументы: клиент, метод, путь, версия, код)."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], safe_path(args[2], self.secret), *args[3:])
        return True


def configure_logging(level: str | None = DEFAULT_LEVEL, secret: str = "") -> str:
    """Идемпотентная настройка: один обработчик на stdout у корня и у логгеров Uvicorn."""
    name, warning = parse_level(level)
    formatter = logging.Formatter(FORMAT)

    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(_RequestIdFilter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(name)

    # Uvicorn ставит свои обработчики (propagate=False у access): оставляем их, но маскируем секрет
    # и приводим формат к общему.
    for uv in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(uv)
        for f in [f for f in lg.filters if isinstance(f, _AccessLogMasker)]:
            lg.removeFilter(f)
        if uv == "uvicorn.access":
            lg.addFilter(_AccessLogMasker(secret))
        for h in lg.handlers:
            h.addFilter(_RequestIdFilter())
            h.setFormatter(formatter)

    if warning:
        logger.warning(warning)
    return name
