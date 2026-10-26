"""Игровой движок: детерминированный конечный автомат без БД и без формата Алисы.

Узлы сценария лежат в файлах `content/episodes/*.json`. Движок получает снимок состояния
и реплику и возвращает TurnResult — что сказать и что изменить. Сохраняет изменения
сервисный слой. Переход между эпизодами (`next`) обрабатывает мультидвижок MultiEngine.
"""

from __future__ import annotations

from dataclasses import replace

from app.content import DEFAULT_EPISODE, episode_ids, episode_order, load_episode, next_episode_id
from app.game import intents
from app.game.state import GameContext, TurnResult

# После скольких непонятных ответов подряд включается упрощённая помощь.
SIMPLE_HELP_AT = 3
# После скольких непонятных ответов подряд игра мягко прощается (без решения и без награды),
# чтобы не мучить ребёнка бесконечным вопросом. Прогресс сохраняется.
GIVE_UP_AT = 5
AMBIGUOUS = "__ambiguous__"


class Engine:
    # Тексты для перехода между эпизодами (пункт 3 и открытый вопрос 4 спецификации).
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
        next_engine: "Engine | None" = None,
        episodes_left: int | None = None,
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

        matched_intent = self._match(ctx, node, text)

        # Самый первый ход: ребёнок ещё ничего не слышал, приветствие важнее любой реплики.
        # Проверка идёт до перехода «ещё»: на старте узла `expects` может совпасть случайно.
        if ctx.node == self.start:
            started = self._play(ctx, turn, self.start, kind="story")
            return self._with_recap(started, ctx, turn)

        # Эпизод пройден, а ребёнок просит «ещё» — переход к следующему эпизоду (пункт 3).
        if ctx.completed and not new_session and "next" in self._expectations(ctx, node):
            wants_more = bool(intents.classify(text, ["next"])) or matched_intent == "next"
            if wants_more:
                if next_engine is not None and (episodes_left is None or episodes_left > 0):
                    return self._start_next(next_engine, ctx, turn)
                # Продолжения нет или лимит визита исчерпан — мягко прощаемся (открытый вопрос 4).
                if next_engine is not None:
                    return self._finish(ctx, turn, "pause", self._pick(self.PAUSE_TEXT, turn))
                return self._finish(ctx, turn, "end", self._pick(self.NO_NEXT_TEXT, turn))
            # «да» в хабе — это «расскажи ещё раз», а не переход к новой серии.
            # Проверка идёт до глобальных about/help/resume-веток, иначе односложное
            # «да» съедается resume-текстом и ребёнок зацикливается в хабе.
            if matched_intent in ("yes", "replay"):
                return self._transition(ctx, node, matched_intent, turn)

        if intents.is_about(text):
            return self._stay(ctx, turn, "about", [self._pick(self.texts["about"], turn)])
        if intents.is_help(text) and not intents.is_hint(text):
            return self._stay(ctx, turn, "help", [self._pick(self.texts["help"], turn)])

        if new_session:
            # Пройденный эпизод: в новом визите ребёнок возвращается к хабу завершения,
            # и resume-текст должен описывать реальные последствия выбора (ветку), а не
            # нейтральное «долина стала добрее».
            if ctx.completed and node.get("expects"):
                recap = self._recap(ctx, turn)
                consequence = self._text(node.get("resume"), node, ctx.decisions, turn)
                parts = [self._pick(self.texts["resume_prefix"], turn)]
                if recap:
                    parts.append(recap)
                parts.append(consequence)
                return self._stay(replace(ctx, unclear_count=0), turn, "resume", parts)
            parts = [
                self._pick(self.texts["resume_prefix"], turn),
                self._text(node.get("resume"), node, ctx.decisions, turn),
            ]
            return self._stay(replace(ctx, unclear_count=0), turn, "resume", parts)

        if intents.is_hint(text):
            options = node.get("hint") or node.get("reask")
            return self._stay(ctx, turn, "hint", [self._pick(options, turn)], include_ask=False)

        if matched_intent not in (None, AMBIGUOUS):
            return self._transition(ctx, node, matched_intent, turn)

        if intents.is_repeat(text):
            parts = [self._recap(ctx, turn), self._text(node.get("resume"), node, ctx.decisions, turn)]
            return self._stay(ctx, turn, "repeat", parts)

        return self._unclear(ctx, node, turn)

    # ------------------------------------------------------------------ «в прошлый раз» (пункт 6)

    def _recap(self, ctx: GameContext, turn: int) -> str:
        """Одно предложение «в прошлый раз», собранное из сохранённых решений и наград.

        Речь ребёнка не хранится — пересказ строится только по решениям (`decisions`)
        и наградам (`items`), которые относятся к предыдущему эпизоду (ключи и предметы
        помечены в world.json полем `episode`). Для первого эпизода возвращаем пусто.

        Дедупликация: пересказ нужен только при переходе между эпизодами внутри одного
        визита (`episodes_started > 1`). При возвращении ребёнка в уже начатую серию
        («с возвращением!» + resume-текст узла) история прошлого уже рассказана —
        второй пересказ прозвучал бы дважды.
        """
        prev_id = self.episode.get("prev")
        if not prev_id or ctx.episodes_started <= 1:
            return ""
        if not ctx.decisions and not ctx.items:
            return ""
        # Фрагменты пересказа по решениям предыдущего эпизода.
        fragments: list[str] = []
        if ctx.decisions.get("seed_place") == "garden":
            fragments.append("вы посадили семечко в саду")
        elif ctx.decisions.get("seed_place") == "stream":
            fragments.append("вы посадили семечко у ручья")
        if "firefly" in ctx.items:
            fragments.append("светлячок поселился в Великом дереве")
        if not fragments:
            return ""
        return "В прошлый раз " + ", а ещё ".join(fragments) + "."

    def _with_recap(self, result: TurnResult, ctx: GameContext, turn: int) -> TurnResult:
        """Ставит пересказ «в прошлый раз» перед первым репликой нового эпизода."""
        recap = self._recap(ctx, turn)
        if not recap or not result.text:
            return result
        return replace(result, text=Engine._join([recap, result.text]))

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
            # «хочу ещё» распознаётся и как «да», и как «next»: если ребёнок просит
            # продолжение, а эпизод уже пройден — важнее переход к новой серии (пункт 3).
            if set(found) == {"yes", "next"} and ctx.completed:
                return "next"
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
            # Динамический `say` (разветвление по `by_decision`) сам описывает последствия
            # выбора — статический пересказ поверх него дал бы двойное «в прошлый раз».
            if not isinstance(node.get("say"), dict):
                parts.insert(0, self._recap(ctx, turn)) if self._recap(ctx, turn) else None
            effects = node.get("effects", {})
            unlocks += effects.get("unlock", [])
            meets += effects.get("meet", [])
            # Сессию закрываем только на явном end_session. Узел с complete=true и expects
            # — это хаб завершения: он прощается в следующей реплике («ещё» / «нет»),
            # поэтому гасит end_session у предшественника (FINALE). Флаг complete при этом
            # остаётся: эпизод фактически пройден (решение принято, награды выданы), и БД
            # должен зафиксировать это уже сейчас — иначе падение процесса между финалом и
            # ответом в хабе потеряло бы прогресс. Сам хаб остаётся resumable-точкой, а
            # переход к новой серии возможен и в новом визите (ctx.completed из БД).
            if node.get("expects"):
                end_session = False
            else:
                end_session = end_session or bool(node.get("end_session"))
            completed = completed or bool(node.get("complete"))
            if node.get("expects"):
                persisted = name
                buttons = list(node.get("buttons", []))
            if "persist_as" in node:
                persisted = node["persist_as"]
            parts.append(self._pick(node.get("ask"), turn))
            name = node.get("auto_next")

        return TurnResult(
            text=self._join(parts),
            # Кнопки остаются живыми, пока сессия продолжается: хаб EPISODE_COMPLETE —
            # обычный ожидающий узел, и «Ещё»/«Нет» должны быть доступны сразу.
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

    def _start_next(self, target: "Engine", ctx: GameContext, turn: int) -> TurnResult:
        """Начинает следующий эпизод: переносим решения и награды (пункт 3 и пункт 6 спецификации).

        Стартовый узел целевого эпизода проигрывается как самый первый ход — с приветствием
        серии и динамическим пересказом «в прошлый раз» (он же гасит двойной recap: повторный
        пересказ в хабе завершения больше не печатается).
        """
        prefix = target.new_context()
        prefix.turn = turn
        prefix.episodes_started = ctx.episodes_started + 1
        prefix.decisions = dict(ctx.decisions)
        prefix.items = set(ctx.items)
        prefix.met = set(ctx.met)
        # Переход внутри одного визита: целевой эпизод встречает ребёнка приветствием серии.
        # Реплика не передаётся — иначе она совпала бы с ожиданием стартового узла и «съела»
        # приветствие (ребёнок получил бы вопрос без вступления).
        started = target.step(prefix, None, new_session=True)
        return replace(started, kind="next_episode", next_episode_id=target.episode_id)

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

    def _transition_node(self) -> dict | None:
        """Узел-мишень перехода `next` (например NEXT_EPISODE). MultiEngine подменяет его ответ."""
        for node in self.nodes.values():
            if node.get("next_episode"):
                return node
        return None

    # ------------------------------------------------------------------ тексты

    @staticmethod
    def _pick(options: list[str] | None, turn: int) -> str:
        """Детерминированный выбор варианта: заранее проверенные тексты, без случайности."""
        if not options:
            return ""
        return options[turn % len(options)]

    def _text(self, options, node: dict, decisions: dict[str, str], turn: int) -> str:
        if isinstance(options, dict):
            # Ключи «_default»/«_none» — служебные: они не значения решений.
            variants = {k: v for k, v in options.items() if not k.startswith("_")}
            value = decisions.get(node.get("by_decision", ""))
            if value is None:
                chosen = options.get("_none") or options.get("_default")
            else:
                chosen = variants.get(value) or options.get("_default")
            if not chosen:  # решение вышло за пределы развилки — берём любой объявленный вариант
                chosen = next(iter(variants.values()), None) or next(iter(options.values()))
            return self._pick(chosen, turn)
        return self._pick(options, turn)

    @staticmethod
    def _join(parts: list[str]) -> str:
        return " ".join(p.strip() for p in parts if p and p.strip())


class MultiEngine:
    """Набор движков по одному на эпизод + переход `next` между ними (пункт 3 спецификации).

    Интерфейс совпадает с Engine (`new_context`, `step`), поэтому GameService не знает,
    сколько эпизодов под капотом. Сам переход реализован в Engine.step: сюда приходит
    только «следующий движок» и остаток лимита эпизодов за визит (открытый вопрос 4).
    """

    # Мягкий ограничитель (открытый вопрос 4): не больше эпизодов за один визит.
    EPISODES_PER_SESSION = 2

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

    def next_engine(self, episode_id: str) -> Engine | None:
        nxt = next_episode_id(episode_id)
        if nxt is None:
            return None
        return self.engines.get(nxt)

    def step(
        self,
        ctx: GameContext,
        utterance: str | None,
        new_session: bool = False,
        dangerous: bool = False,
    ) -> TurnResult:
        engine = self.engine_for(ctx.episode_id)
        episodes_left = max(self.EPISODES_PER_SESSION - ctx.episodes_started, 0)
        return engine.step(
            replace(ctx, episode_id=engine.episode_id),
            utterance,
            new_session=new_session,
            dangerous=dangerous,
            next_engine=self.next_engine(engine.episode_id),
            episodes_left=episodes_left,
        )


def build_multi_engine() -> MultiEngine:
    """Собирает мультидвижок по series.json: один Engine на каждый написанный эпизод."""
    engines: dict[str, Engine] = {}
    for eid in episode_ids():
        engines[eid] = Engine(load_episode(eid), eid)
    order = [eid for eid in episode_order() if eid in engines]
    first = order[0] if order else DEFAULT_EPISODE
    return MultiEngine(engines, first_episode_id=first, order=order or list(engines))
