"""Игровой движок: детерминированный конечный автомат без БД и без формата Алисы.

Узлы сценария лежат в файлах `content/episodes/*.json`. Движок получает снимок состояния
и реплику и возвращает TurnResult — что сказать и что изменить. Сохраняет изменения
сервисный слой. Переход между эпизодами (`next`) обрабатывает мультидвижок MultiEngine.
"""

from __future__ import annotations

from dataclasses import replace

from app.game import intents
from app.game.state import GameContext, TurnResult

# После скольких непонятных ответов подряд включается упрощённая помощь.
SIMPLE_HELP_AT = 3
# После скольких непонятных ответов подряд игра мягко прощается (без решения и без награды),
# чтобы не мучить ребёнка бесконечным вопросом. Прогресс сохраняется.
GIVE_UP_AT = 5
AMBIGUOUS = "__ambiguous__"


class Engine:
    def __init__(self, episode: dict, episode_id: str = "ep1") -> None:
        self.episode = episode
        self.episode_id = episode_id
        self.nodes: dict = episode["nodes"]
        self.texts: dict = episode["texts"]
        self.start: str = episode["start"]

    # ------------------------------------------------------------------ публичный API

    def new_context(self) -> GameContext:
        return GameContext(episode_id=self.episode_id, node=self.start)

    def step(
        self,
        ctx: GameContext,
        utterance: str | None,
        new_session: bool = False,
        dangerous: bool = False,
    ) -> TurnResult:
        if ctx.episode_id != self.episode_id:
            raise RuntimeError(
                f"движок {self.episode_id} не работает с состоянием эпизода {ctx.episode_id}"
            )
        if not self._is_resumable(ctx.node):  # сценарий изменился: возвращаемся в безопасную точку
            ctx = replace(ctx, node="EPISODE_COMPLETE" if ctx.completed else self.start)
        turn = ctx.turn + 1
        text = intents.normalize(utterance)
        node = self.nodes[ctx.node]

        if dangerous:
            return self._stay(ctx, turn, "danger", [self._pick(self.texts["danger"], turn)])
        if intents.is_exit(text):
            return self._finish(ctx, turn, "exit", self._pick(self.texts["exit"], turn))

        # Самый первый ход: ребёнок ещё ничего не слышал, приветствие важнее любой реплики.
        if ctx.node == self.start:
            return self._play(ctx, turn, self.start, kind="story")

        if intents.is_about(text):
            return self._stay(ctx, turn, "about", [self._pick(self.texts["about"], turn)])
        if intents.is_help(text) and not intents.is_hint(text):
            return self._stay(ctx, turn, "help", [self._pick(self.texts["help"], turn)])

        if new_session:
            parts = [
                self._pick(self.texts["resume_prefix"], turn),
                self._text(node.get("resume"), node, ctx.decisions, turn),
            ]
            return self._stay(replace(ctx, unclear_count=0), turn, "resume", parts)

        if intents.is_hint(text):
            options = node.get("hint") or node.get("reask")
            return self._stay(ctx, turn, "hint", [self._pick(options, turn)], include_ask=False)

        matched = self._match(ctx, node, text)
        if matched not in (None, AMBIGUOUS):
            return self._transition(ctx, node, matched, turn)

        if intents.is_repeat(text):
            parts = [self._text(node.get("resume"), node, ctx.decisions, turn)]
            return self._stay(ctx, turn, "repeat", parts)

        return self._unclear(ctx, node, turn)

    def _is_resumable(self, name: str) -> bool:
        """Сохранять и продолжать можно только стартовый узел или узел, который ждёт ответа."""
        node = self.nodes.get(name)
        return node is not None and (name == self.start or bool(node.get("expects")))

    # ------------------------------------------------------------------ распознавание

    def _expectations(self, ctx: GameContext, node: dict) -> dict[str, str]:
        return dict(node.get("expects", {}))

    def _match(self, ctx: GameContext, node: dict, text: str) -> str | None:
        expects = self._expectations(ctx, node)
        found = intents.classify(text, list(expects))
        if len(found) > 1:
            # «да» + «расскажи» ведут в один узел — это не конфликт
            if len({expects[i] for i in found}) == 1:
                return found[0]
            return AMBIGUOUS
        if found:
            return found[0]
        fallback = node.get("fallback")
        if fallback and fallback.get("type") == "binary" and ctx.unclear_count >= SIMPLE_HELP_AT:
            answer = intents.classify(text, ["yes", "no"])
            if len(answer) == 1:
                return fallback[answer[0]]
        return None

    # ------------------------------------------------------------------ переходы

    def _transition(self, ctx: GameContext, node: dict, intent: str, turn: int) -> TurnResult:
        # Переход в следующий эпизод обрабатывает MultiEngine; одиночный движок до него не доходит.
        if self.nodes[node["expects"][intent]].get("next_episode"):
            return self._finish(
                replace(ctx, unclear_count=0), turn, "end", self._pick(node.get("reask"), turn)
            )
        decision = None
        decisions = dict(ctx.decisions)
        decision_cfg = node.get("decision")
        if decision_cfg:
            key = decision_cfg["key"]
            value = decision_cfg["values"][intent]
            if key in decisions:
                # Первое решение не перезаписывается: идём по уже сохранённой ветке.
                existing = decisions[key]
                for candidate, mapped in decision_cfg["values"].items():
                    if mapped == existing:
                        intent, value = candidate, existing
                        break
            else:
                decision = (key, value)
                decisions[key] = value
        target = node["expects"][intent]
        return self._play(
            replace(ctx, decisions=decisions, unclear_count=0),
            turn,
            target,
            kind="story",
            decision=decision,
        )

    def _play(
        self,
        ctx: GameContext,
        turn: int,
        start: str,
        kind: str,
        decision: tuple[str, str] | None = None,
        prefix: list[str] | None = None,
    ) -> TurnResult:
        """Проигрывает цепочку узлов (auto_next) до узла, который ждёт ответа или завершает."""
        parts = list(prefix or [])
        path: list[str] = []
        unlocks: list[str] = []
        meets: list[str] = []
        completed = False
        end_session = False
        persisted = ctx.node
        buttons: list[str] = []
        name: str | None = start

        while name:
            if name in path:
                raise RuntimeError(f"цикл auto_next в узле {name}")
            path.append(name)
            node = self.nodes[name]
            parts.append(self._text(node.get("say"), node, ctx.decisions, turn))
            effects = node.get("effects", {})
            unlocks += effects.get("unlock", [])
            meets += effects.get("meet", [])
            completed = completed or bool(node.get("complete"))
            end_session = end_session or bool(node.get("end_session"))
            if node.get("expects"):
                persisted = name
                buttons = list(node.get("buttons", []))
            if "persist_as" in node:
                persisted = node["persist_as"]
            parts.append(self._pick(node.get("ask"), turn))
            name = node.get("auto_next")

        return TurnResult(
            text=self._join(parts),
            buttons=[] if end_session else buttons,
            end_session=end_session,
            kind=kind,
            node=persisted,
            unclear_count=0,
            turn=turn,
            decision=decision,
            unlocks=unlocks,
            meets=meets,
            completed=completed,
            path=path,
        )

    # ------------------------------------------------------------------ вспомогательные ответы

    def _unclear(self, ctx: GameContext, node: dict, turn: int) -> TurnResult:
        count = min(ctx.unclear_count + 1, GIVE_UP_AT)
        fallback = node.get("fallback") or {}
        # reask и hint написаны так, что уже содержат сам вопрос, поэтому ask не добавляем.
        if count == 1:
            return self._stay(
                replace(ctx, unclear_count=count), turn, "unclear", [self._pick(node["reask"], turn)],
                include_ask=False,
            )
        if count == 2:
            return self._stay(
                replace(ctx, unclear_count=count), turn, "unclear", [self._pick(node["hint"], turn)],
                include_ask=False,
            )

        # Третья неудача подряд: упрощённая помощь. Сюжет не блокируется.
        say = self._pick(fallback.get("say"), turn)
        kind = fallback.get("type")
        if kind == "binary" and count >= GIVE_UP_AT:
            return self._finish(replace(ctx, unclear_count=0), turn, "end", self._pick(fallback["give_up"], turn))
        if kind == "advance":
            return self._play(replace(ctx, unclear_count=0), turn, fallback["to"], kind="story", prefix=[say])
        if kind == "end":
            return self._finish(replace(ctx, unclear_count=0), turn, "end", say)
        buttons = list(fallback.get("buttons", node.get("buttons", [])))
        return self._stay(
            replace(ctx, unclear_count=count), turn, "unclear", [say], include_ask=False, buttons=buttons
        )

    def _stay(
        self,
        ctx: GameContext,
        turn: int,
        kind: str,
        parts: list[str],
        include_ask: bool = True,
        buttons: list[str] | None = None,
    ) -> TurnResult:
        """Остаёмся в текущем узле: ответ + повтор вопроса."""
        node = self.nodes[ctx.node]
        out = list(parts)
        if include_ask:
            out.append(self._pick(node.get("ask"), turn))
        return TurnResult(
            text=self._join(out),
            buttons=list(node.get("buttons", [])) if buttons is None else buttons,
            end_session=False,
            kind=kind,
            node=ctx.node,
            unclear_count=ctx.unclear_count,
            turn=turn,
            path=[ctx.node],
        )

    def _finish(self, ctx: GameContext, turn: int, kind: str, text: str) -> TurnResult:
        return TurnResult(
            text=text,
            buttons=[],
            end_session=True,
            kind=kind,
            node=ctx.node,
            unclear_count=ctx.unclear_count,
            turn=turn,
            path=[ctx.node],
        )

    # ------------------------------------------------------------------ тексты

    @staticmethod
    def _pick(options: list[str] | None, turn: int) -> str:
        """Детерминированный выбор варианта: заранее проверенные тексты, без случайности."""
        if not options:
            return ""
        return options[turn % len(options)]

    def _text(self, options, node: dict, decisions: dict[str, str], turn: int) -> str:
        if isinstance(options, dict):
            value = decisions.get(node.get("by_decision", ""))
            chosen = options.get(value) or options.get("_default") or next(iter(options.values()))
            return self._pick(chosen, turn)
        return self._pick(options, turn)

    @staticmethod
    def _join(parts: list[str]) -> str:
        return " ".join(p.strip() for p in parts if p and p.strip())


class MultiEngine:
    """Набор движков по одному на эпизод + переход `next` между ними (пункт 3 спецификации).

    Интерфейс совпадает с Engine (`new_context`, `step`), поэтому GameService не знает,
    сколько эпизодов под капотом. Переход в следующий эпизод — это новый снимок состояния:
    `episode_id` меняется на следующий, а `node` — на его стартовый узел. Все решения
    игрока остаются в БД и доступны новому эпизоду как обратные ссылки.
    """

    # Мягкий ограничитель (открытый вопрос 4): не больше эпизодов за один визит.
    EPISODES_PER_SESSION = 2

    PAUSE_TEXT = [
        "Мы сегодня уже побывали в двух сериях — целое приключение! Давай отдохнём: "
        "долина никуда не денется, а Искорка будет ждать тебя в следующий раз. До встречи!",
        "Здорово получилось! Но сказке нужен перерыв, чтобы чудеса успели улечься. "
        "Приходи завтра — и мы продолжим прямо с нового эпизода. До встречи!",
        "Хватит на сегодня, а? Искорка укроет семечко и светлячка, а ты расскажешь взрослому, "
        "что было в долине. В следующий раз узнаем, что скрипит за холмом. До встречи!",
    ]
    NO_NEXT_TEXT = [
        "Новая серия ещё сочиняется для тебя. А пока Искорка будет ждать у Великого дерева. До встречи!",
        "Продолжение скоро появится! Отдыхай, а когда захочешь — возвращайся, и мы продолжим.",
    ]

    def __init__(self, engines: dict[str, Engine], first_episode_id: str, order: list[str] | None = None) -> None:
        self.engines = engines
        self.first_episode_id = first_episode_id
        self.order = order or list(engines)

    def new_context(self) -> GameContext:
        return self.engines[self.first_episode_id].new_context()

    def engine_for(self, episode_id: str) -> Engine:
        engine = self.engines.get(episode_id)
        if engine is None:
            # профиль указывает на недоступный эпизод — безопасно возвращаемся к первому
            engine = self.engines[self.first_episode_id]
        return engine

    def next_episode_id(self, episode_id: str) -> str | None:
        if episode_id not in self.order:
            return None
        index = self.order.index(episode_id)
        if index + 1 >= len(self.order):
            return None
        candidate = self.order[index + 1]
        return candidate if candidate in self.engines else None

    def step(
        self,
        ctx: GameContext,
        utterance: str | None,
        new_session: bool = False,
        dangerous: bool = False,
    ) -> TurnResult:
        engine = self.engine_for(ctx.episode_id)
        result = engine.step(
            replace(ctx, episode_id=engine.episode_id), utterance, new_session, dangerous
        )
        if "NEXT_EPISODE" not in result.path:
            return result

        nxt = self.next_episode_id(engine.episode_id)
        turn = result.turn
        if nxt is None:
            text = Engine._pick(self.NO_NEXT_TEXT, turn)
            return replace(
                result, text=text, buttons=[], end_session=True,
                kind="end", node=result.node, next_episode_id=None,
            )
        if ctx.episodes_started >= self.EPISODES_PER_SESSION:
            text = Engine._pick(self.PAUSE_TEXT, turn)
            return replace(
                result, text=text, buttons=[], end_session=True,
                kind="pause", node=result.node, next_episode_id=None,
            )
        target = self.engines[nxt]
        prefix = target.new_context()
        prefix.turn = turn
        prefix.episodes_started = ctx.episodes_started + 1
        started = target.step(prefix, "", new_session=False)
        return replace(started, next_episode_id=nxt)
