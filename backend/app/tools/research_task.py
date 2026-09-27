"""Инструмент «Задача на исследование».

Срабатывает, когда по вопросу пользователя в базе исследований нет информации
(поиск пуст или модель не сослалась ни на один источник): предлагает завести
задачу на исследование. Если пользователь согласен — поэтапно собирает бриф
(машина состояний в chat.tool_state), проверяет гипотезы по критериям NN/g,
методологическую часть предлагает сам, и после подтверждения саммери
сохраняет задачу MD-файлом в папку tasks/.

Этапы (tool_state.stage):
- collect  — по одному вопросу за раз собираем обязательные поля заказчика;
- proposal — LLM предложил методологическую часть, ждем подтверждения/правок;
- summary  — показано полное саммери, ждем «Создать задачу» или правок.

Полнота брифа проверяется кодом (_next_field), а не мнением LLM: задача
не создастся, пока обязательные поля пусты. Пользователь в любой момент может
уточнить ранний пункт (extraction обновит поле) или написать «отмена».
"""

import json
import re
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import llm, rag
from ..config import settings
from ..db import async_session
from ..models import Chat, Message, utcnow
from ..serializers import message_to_dict
from . import Tool, sse_event

_KEEP = object()  # сентинел «не менять» для mode/state в _finalize

OFFER_TEXT = (
    "Могу помочь завести **задачу на исследование** по этому вопросу: задам несколько "
    "уточняющих вопросов, соберу бриф и сохраню задачу для исследователя. Создаем?"
)

INTRO_TEXT = (
    "Отлично! Я задам несколько коротких вопросов — по одному за раз. В конце покажу "
    "саммери задачи и создам ее только после вашего подтверждения. Любой пункт можно "
    "уточнить в любой момент, а если передумаете — напишите «отмена».\n\n"
    "**Первый вопрос:** что за продукт или фича, в каком контексте планируется "
    "исследование? Если есть макеты или описание предполагаемого решения — приложите ссылку."
)

SUMMARY_HINT = (
    "Если все верно — нажмите «Создать задачу». Любой пункт можно поправить, просто написав сообщение."
)

FIELD_TITLES = {
    "product": "Продукт и контекст",
    "business_goal": "Бизнес-задача",
    "audience": "Целевая аудитория",
    "hypotheses": "Ключевые гипотезы",
    "success_criteria": "Критерий успеха исследования",
    "design_constraints": "Требования к дизайну исследования",
    "main_question": "Главный исследовательский вопрос",
    "research_questions": "Исследовательские вопросы",
    "methodology": "Методология",
    "expected_results": "Ожидаемые результаты и формат отчета",
    "first_step": "Первый шаг",
}
LIST_FIELDS = {"hypotheses", "research_questions"}
# Обязательные поля заказчика — в порядке опроса
REQUIRED_ORDER = ["product", "business_goal", "audience", "hypotheses", "success_criteria"]

QUESTION_HINTS = {
    "product": "Что за продукт или фича, в каком контексте будет исследование? "
    "Если есть макеты или описание решения — приложите ссылку.",
    "business_goal": "Какая бизнес-задача у исследования — какое решение команда примет по его итогам?",
    "audience": "Кто целевая аудитория исследования — какие сегменты пользователей изучаем?",
    "hypotheses": "Какие ключевые гипотезы нужно проверить? Хорошая гипотеза — конкретное, "
    "проверяемое предположение о поведении пользователей (а не «понравится ли»).",
    "success_criteria": "Что будет критерием успеха исследования — по чему поймете, что оно удалось?",
    "design_constraints": "Есть ли критически важные требования к дизайну исследования — сроки, "
    "обязательные сегменты, ограничения? Если нет — так и напишите, пропустим этот пункт.",
}

EXTRACT_PROMPT = """Ты — парсер брифа задачи на UX-исследование. По текущему брифу, последним \
сообщениям диалога и НОВОМУ сообщению пользователя определи, что пользователь сообщил.

Ответь ТОЛЬКО валидным JSON без пояснений:
{"updates": {}, "cancel": false, "confirm": false, "skip_optional": false}

Поля, которые могут появиться в updates:
- product: строка — продукт и контекст исследования (что за продукт/фича, ссылки на макеты)
- business_goal: строка — бизнес-задача (какое решение примут по итогам исследования)
- audience: строка — целевая аудитория
- hypotheses: список строк — ключевые гипотезы
- success_criteria: строка — критерий успеха исследования
- design_constraints: строка — критичные требования к дизайну исследования (сроки, сегменты, ограничения)
- main_question: строка — главный исследовательский вопрос
- research_questions: список строк — исследовательские вопросы
- methodology: строка — методология
- expected_results: строка — ожидаемые результаты и формат отчета
- first_step: строка — первый шаг

Правила:
1. В updates включай ТОЛЬКО поля, о которых пользователь реально сообщил что-то новое в НОВОМ \
сообщении. Ничего не выдумывай и не переписывай остальные поля.
2. Пользователь может уточнять любой уже заполненный пункт — тогда верни итоговое значение поля \
целиком: старое значение, аккуратно доработанное правкой пользователя.
3. Для списковых полей (hypotheses, research_questions) всегда возвращай полный итоговый список.
4. cancel: true — только если пользователь явно отказывается заводить задачу («отмена», «не надо», «передумал»).
5. confirm: true — ТОЛЬКО если сообщение является явным согласием с последним предложением \
ассистента («да», «ок», «подходит», «согласен», «все верно») без правок. Если в сообщении есть \
отрицание («не», «нет») или критика («хрень», «не то», «плохо», «не согласен») — confirm: false.
6. skip_optional: true — если пользователь говорит, что особых требований к дизайну исследования \
нет, или просит пропустить этот пункт."""

HYPOTHESES_PROMPT = """Ты — методолог UX-исследований. Оцени гипотезы по критериям качества (NN/g):
1. Конкретность: гипотеза про определенное поведение/восприятие определенных пользователей, а не «всем понравится».
2. Проверяемость: исследование может подтвердить или опровергнуть ее (фальсифицируема).
3. Это утверждение-предположение о пользователях/продукте, а не вопрос и не пожелание.
4. Действенность: из результата проверки следует понятное продуктовое решение.

Будь умеренно строгим: не придирайся к стилю, отклоняй только реально непроверяемые или размытые гипотезы.

Ответь ТОЛЬКО валидным JSON:
{"ok": true, "feedback": "", "suggestions": []}
- ok: false, если хотя бы одна гипотеза не проходит критерии
- feedback: коротко и доброжелательно — что именно не так с каждой слабой гипотезой
- suggestions: улучшенные формулировки ВСЕХ гипотез (слабые — переписанные, хорошие — без изменений). \
Каждая формулировка — УТВЕРЖДЕНИЕ, а не вопрос: без вопросительного знака."""

ASK_PROMPT = """Ты — дружелюбный ассистент-ресерчер, собираешь бриф задачи на исследование в чате.
Напиши ОДНУ короткую фразу: подтверди, что именно зафиксировал из последнего ответа пользователя \
(например: «Записал: целевая аудитория — клиенты 25–45 лет»). Если зафиксировать ничего не \
удалось — одной фразой мягко скажи, что не получилось соотнести ответ с брифом. \
НЕ задавай вопросов и ничего не добавляй: следующий вопрос система задаст сама. \
Ничего не выдумывай про содержание брифа."""

PROPOSE_PROMPT = """Ты — методолог UX-исследований. На основе собранного брифа предложи \
методологическую часть задачи. Опирайся ТОЛЬКО на поля брифа: продукт, бизнес-задачу, целевую \
аудиторию, гипотезы и критерий успеха. Не упоминай никакие другие продукты и бренды, \
кроме указанных в брифе.

Ответь ТОЛЬКО валидным JSON:
{"main_question": "...", "research_questions": ["..."], "methodology": "...", "expected_results": "...", "first_step": "..."}
- main_question: ОДИН главный исследовательский вопрос про продукт из брифа, \
вытекающий из бизнес-задачи и гипотез
- research_questions: 3–6 исследовательских вопросов, декомпозирующих главный \
(вопросы к исследованию, НЕ вопросы респондентам)
- methodology: подходящий метод и одной фразой почему (юзабилити-тест, глубинные интервью, опрос, \
дневниковое исследование и т.п.)
- expected_results: какие результаты и в каком формате отчета ожидать
- first_step: конкретный первый шаг исследователя
Пиши по-русски, коротко и конкретно."""

# Поля, у которых проверяется содержательная конкретика (кроме гипотез — у тех своя проверка).
# Максимум один переспрос: дальше фиксируем как есть с пометкой «уточнить с заказчиком».
FIELD_QUALITY = {
    "business_goal": "Должно быть понятно, какое бизнес-решение примут по итогам исследования. "
    "Плохо: «заработать больше», «поднять доход». "
    "Хорошо: «решим, выводить ли фичу X в прод», «выберем, какой из двух флоу развивать».",
    "audience": "Должны быть названы сегменты или хотя бы 1–2 критерия отбора респондентов. "
    "Плохо: «все пользователи», «и бедные и богатые». "
    "Хорошо: «клиенты 25–45, пользующиеся приложением ежедневно».",
    "success_criteria": "Должно быть понятно, что даст исследование и как поймем, что оно удалось. "
    "Плохо: «когда все хорошо будет». "
    "Хорошо: «получим ответ, снижает ли фича X отток» или «список барьеров использования».",
}

FIELD_QUALITY_PROMPT = """Ты — методолог UX-исследований, помогаешь заказчику заполнить бриф. \
Оцени, достаточно ли конкретен ответ заказчика, чтобы исследователь мог по нему работать. \
Будь снисходителен к стилю и разговорным формулировкам — важна только содержательная конкретика.

Поле «{title}». {criteria}

Значение от заказчика: {value}
Остальной бриф (для контекста): {context}

Ответь ТОЛЬКО валидным JSON:
{{"ok": true, "clarify": ""}}
- ok: false — ТОЛЬКО если по значению реально нельзя работать (по смыслу пустое или предельно общее)
- clarify: при ok=false — ОДИН доброжелательный уточняющий вопрос заказчику, \
который поможет конкретизировать (можно с примером формата ответа)"""

# Отрицание в сообщении — не считаем его согласием, что бы ни решил экстрактор
_NEGATION_RE = re.compile(r"\b(не|нет|неа)\b|хрень|фигня|ерунда|бред", re.IGNORECASE)


# ---------- состояние и чистые функции ----------

def _new_state(question: str, query: str) -> dict:
    return {
        "tool": "research_task",
        "stage": "collect",
        "origin_question": question,
        "origin_query": query,
        "hypotheses_ok": False,
        "skipped_optional": False,
        "pending_hypotheses": None,
        "pending_clarify": None,
        "clarify_retries": {},
        "needs_clarification": [],
        "brief": {
            "product": "",
            "business_goal": "",
            "audience": "",
            "hypotheses": [],
            "success_criteria": "",
            "design_constraints": "",
            "main_question": "",
            "research_questions": [],
            "methodology": "",
            "expected_results": "",
            "first_step": "",
        },
    }


def _merge_updates(brief: dict, updates: dict) -> list[str]:
    """Вносит извлеченные обновления в бриф, возвращает список изменившихся полей."""
    changed = []
    for key, value in (updates or {}).items():
        if key not in FIELD_TITLES:
            continue
        if key in LIST_FIELDS:
            if isinstance(value, str):
                value = [v.strip(" -–—•") for v in value.split("\n")]
            if not isinstance(value, list):
                continue
            value = [str(v).strip() for v in value if str(v).strip()]
        else:
            value = str(value).strip()
        if not value:
            continue
        if brief.get(key) != value:
            brief[key] = value
            changed.append(key)
    return changed


def _next_field(state: dict) -> str | None:
    """Следующее незаполненное поле заказчика; None — сбор завершен."""
    brief = state["brief"]
    for key in REQUIRED_ORDER:
        if key == "hypotheses":
            if not brief.get("hypotheses") or not state.get("hypotheses_ok"):
                return "hypotheses"
        elif not (brief.get(key) or "").strip():
            return key
    if not (brief.get("design_constraints") or "").strip() and not state.get("skipped_optional"):
        return "design_constraints"
    return None


def _bullet(items: list) -> str:
    return "\n".join(f"- {i}" for i in items)


def _render_proposal(brief: dict) -> str:
    return "\n\n".join(
        [
            f"**{FIELD_TITLES['main_question']}:** {brief.get('main_question') or '—'}",
            f"**{FIELD_TITLES['research_questions']}:**\n"
            + (_bullet(brief.get("research_questions") or []) or "—"),
            f"**{FIELD_TITLES['methodology']}:** {brief.get('methodology') or '—'}",
            f"**{FIELD_TITLES['expected_results']}:** {brief.get('expected_results') or '—'}",
            f"**{FIELD_TITLES['first_step']}:** {brief.get('first_step') or '—'}",
        ]
    )


def _flag(state: dict, key: str) -> str:
    return " ⚠️ (уточнить перед стартом)" if key in (state.get("needs_clarification") or []) else ""


def _render_summary(state: dict) -> str:
    brief = state["brief"]
    if (brief.get("design_constraints") or "").strip():
        constraints = brief["design_constraints"]
    elif state.get("skipped_optional"):
        constraints = "нет особых требований"
    else:
        constraints = "—"
    return "\n\n".join(
        [
            f"**{FIELD_TITLES['product']}:** {brief.get('product') or '—'}{_flag(state, 'product')}",
            f"**{FIELD_TITLES['business_goal']}:** {brief.get('business_goal') or '—'}"
            f"{_flag(state, 'business_goal')}",
            f"**{FIELD_TITLES['audience']}:** {brief.get('audience') or '—'}{_flag(state, 'audience')}",
            f"**{FIELD_TITLES['hypotheses']}:**\n" + (_bullet(brief.get("hypotheses") or []) or "—"),
            f"**{FIELD_TITLES['success_criteria']}:** {brief.get('success_criteria') or '—'}"
            f"{_flag(state, 'success_criteria')}",
            f"**{FIELD_TITLES['design_constraints']}:** {constraints}",
            _render_proposal(brief),
        ]
    )


def _hypotheses_feedback_reply(check: dict) -> str:
    parts = [
        "Прежде чем идти дальше, давайте доработаем гипотезы — сейчас их будет сложно проверить исследованием."
    ]
    if (check.get("feedback") or "").strip():
        parts.append(check["feedback"].strip())
    suggestions = check.get("suggestions") or []
    if suggestions:
        parts.append(
            "Предлагаю такие формулировки:\n"
            + "\n".join(f"{i}. {s}" for i, s in enumerate(suggestions, 1))
        )
        parts.append("Если согласны — напишите «да», либо пришлите свои варианты.")
    else:
        parts.append(
            "Попробуйте переформулировать: конкретное, проверяемое предположение "
            "о поведении пользователей, из которого следует продуктовое решение."
        )
    return "\n\n".join(parts)


_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
        "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
        "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
        "я": "ya",
    }
)


def _slug(text: str) -> str:
    text = text.lower().translate(_TRANSLIT)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:60].strip("-") or "task"


def _write_task_file(title: str, md: str) -> Path:
    tasks_dir = Path(settings.tasks_dir)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    base = f"{date.today().isoformat()}-{_slug(title)}"
    path = tasks_dir / f"{base}.md"
    n = 2
    while path.exists():
        path = tasks_dir / f"{base}-{n}.md"
        n += 1
    path.write_text(md, encoding="utf-8")
    return path


def _render_task_md(
    title: str, chat_title: str, state: dict, user_email: str, related: list[dict]
) -> str:
    brief = state["brief"]

    def val(key: str) -> str:
        text = (brief.get(key) or "—") if key not in LIST_FIELDS else (
            _bullet(brief.get(key) or []) or "—"
        )
        if key in (state.get("needs_clarification") or []):
            text += "\n\n> ⚠️ Сформулировано общо — уточнить перед стартом исследования."
        return text

    constraints = (brief.get("design_constraints") or "").strip() or "Особых требований не заявлено."
    if related:
        related_block = "\n".join(
            f"- «{r['title']}» (проведено {r['date']}) — изучить перед стартом, чтобы не дублировать"
            for r in related
        )
    else:
        related_block = "Похожих исследований в базе не найдено."
    sections = [
        f"# Задача на исследование: {title}",
        "",
        f"- **Дата создания:** {date.today().isoformat()}",
        f"- **Автор:** {user_email}",
        f"- **Чат-источник:** «{chat_title}»",
        f"- **Исходный вопрос:** {state.get('origin_question') or '—'}",
        "",
        "## Контекст и продукт", "", val("product"), "",
        "## Бизнес-задача", "", val("business_goal"), "",
        "## Целевая аудитория", "", val("audience"), "",
        "## Главный исследовательский вопрос", "", val("main_question"), "",
        "## Ключевые гипотезы", "", val("hypotheses"), "",
        "## Исследовательские вопросы", "", val("research_questions"), "",
        "## Методология", "", val("methodology"), "",
        "## Критически важные требования к дизайну исследования", "", constraints, "",
        "## Ожидаемые результаты и формат отчета", "", val("expected_results"), "",
        "## Критерий успеха", "", val("success_criteria"), "",
        "## Первый шаг", "", val("first_step"), "",
        "## Связанные исследования в базе", "", related_block,
    ]
    return "\n".join(sections) + "\n"


def _proposal_actions() -> list[dict]:
    return [{"id": "research_task:accept_proposal", "label": "Все подходит", "data": {}}]


def _summary_actions() -> list[dict]:
    return [
        {"id": "research_task:create", "label": "Создать задачу", "data": {}},
        {"id": "research_task:cancel", "label": "Не создавать", "data": {}},
    ]


async def _llm_json(system: str, user: str, fallback: dict, temperature: float = 0.0) -> dict:
    raw = await llm.chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
    )
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return fallback
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return fallback
    return {**fallback, **{k: v for k, v in parsed.items() if k in fallback}}


# ---------- сам инструмент ----------

class ResearchTaskTool(Tool):
    name = "research_task"
    mode = "research_task_brief"

    def check_trigger(self, question: str, search_query: str, has_info: bool) -> dict | None:
        if has_info:
            return None
        return {
            "text": OFFER_TEXT,
            "actions": [
                {
                    "id": "research_task:start",
                    "label": "Да, создадим",
                    "data": {"question": question, "query": search_query},
                },
                {"id": "research_task:decline", "label": "Нет, спасибо", "data": {}},
            ],
        }

    # ----- обработка кнопок -----

    async def handle_action(self, chat_id, action: dict, user_email: str):
        async with async_session() as session:
            chat = await session.scalar(select(Chat).where(Chat.id == chat_id))
            if chat is None:
                yield sse_event("error", {"detail": "Чат не найден."})
                return
            state = dict(chat.tool_state) if chat.tool_state else None
            chat_title = chat.title
            # Клик по кнопке фиксируем как реплику пользователя — история остается связной
            user_msg = Message(chat_id=chat_id, role="user", content=action.get("label") or "…")
            session.add(user_msg)
            await session.commit()
        yield sse_event("user_message", {"id": user_msg.id})

        action_id = action.get("id") or ""
        data = action.get("data") or {}

        if action_id == "research_task:start":
            state = _new_state(str(data.get("question") or ""), str(data.get("query") or ""))
            async for ev in self._emit(chat_id, INTRO_TEXT, mode=self.mode, state=state):
                yield ev
        elif action_id == "research_task:decline":
            async for ev in self._emit(
                chat_id, "Хорошо! Продолжаю работать с базой исследований — задавайте вопросы."
            ):
                yield ev
        elif action_id == "research_task:accept_proposal":
            if not state:
                async for ev in self._emit(chat_id, "Брифинг уже завершен — эта кнопка неактуальна."):
                    yield ev
                return
            state["stage"] = "summary"
            reply = "Вот полное саммери задачи:\n\n" + _render_summary(state) + "\n\n" + SUMMARY_HINT
            async for ev in self._emit(chat_id, reply, actions=_summary_actions(), state=state):
                yield ev
        elif action_id == "research_task:create":
            if not state:
                async for ev in self._emit(chat_id, "Брифинг уже завершен — эта кнопка неактуальна."):
                    yield ev
                return
            reply = await self._create_task(chat_title, state, user_email)
            async for ev in self._emit(chat_id, reply, mode="normal", state=None):
                yield ev
        elif action_id == "research_task:cancel":
            async for ev in self._emit(
                chat_id,
                "Хорошо, задачу заводить не будем. Возвращаюсь к обычному поиску по базе.",
                mode="normal",
                state=None,
            ):
                yield ev
        else:
            async for ev in self._emit(chat_id, "Эта кнопка уже неактуальна."):
                yield ev

    # ----- обработка сообщений в режиме брифинга -----

    async def handle_message(self, chat_id, question: str, user_email: str):
        async with async_session() as session:
            chat = await session.scalar(select(Chat).where(Chat.id == chat_id))
            if chat is None:
                yield sse_event("error", {"detail": "Чат не найден."})
                return
            state = dict(chat.tool_state) if chat.tool_state else None
            chat_title = chat.title
            recent = (
                await session.scalars(
                    select(Message)
                    .where(Message.chat_id == chat_id)
                    .order_by(Message.id.desc())
                    .limit(6)
                )
            ).all()
            dialog = "\n".join(
                f"{'Ассистент' if m.role == 'assistant' else 'Пользователь'}: {m.content[:600]}"
                for m in reversed(recent)
            )
            user_msg = Message(chat_id=chat_id, role="user", content=question)
            session.add(user_msg)
            await session.commit()
        yield sse_event("user_message", {"id": user_msg.id})

        if not state:
            # Рассинхрон: режим включен, а состояния нет — возвращаем чат в обычный режим
            async for ev in self._emit(
                chat_id,
                "Брифинг был прерван. Возвращаюсь к обычному поиску по базе — задайте вопрос еще раз.",
                mode="normal",
                state=None,
            ):
                yield ev
            return

        extraction = await self._extract(state, dialog, question)
        if extraction["cancel"]:
            async for ev in self._emit(
                chat_id,
                "Хорошо, задачу заводить не будем. Возвращаюсь к обычному поиску по базе.",
                mode="normal",
                state=None,
            ):
                yield ev
            return

        updates = extraction.get("updates") or {}
        # Эхо-фильтр: на отказ/сомнение модель иногда записывает НАШИ же предложенные
        # гипотезы как правку пользователя — не считаем это его формулировками
        pending_h = state.get("pending_hypotheses")
        if pending_h and isinstance(updates.get("hypotheses"), list):
            if [str(h).strip() for h in updates["hypotheses"]] == [str(h).strip() for h in pending_h]:
                updates.pop("hypotheses")
        changed = _merge_updates(state["brief"], updates)
        if extraction["skip_optional"]:
            state["skipped_optional"] = True
        # Отрицание/критика в сообщении — не согласие, что бы ни решил экстрактор
        confirm = bool(extraction["confirm"]) and not _NEGATION_RE.search(question)

        # Гипотезы: не пропускаем плохо сформированные (NN/g)
        if "hypotheses" in changed:
            state["pending_hypotheses"] = None
            state["hypotheses_ok"] = False
            check = await self._validate_hypotheses(state)
            if check.get("ok"):
                state["hypotheses_ok"] = True
            else:
                state["pending_hypotheses"] = check.get("suggestions") or None
                async for ev in self._emit(
                    chat_id, _hypotheses_feedback_reply(check), state=state
                ):
                    yield ev
                return
        elif confirm and state.get("pending_hypotheses"):
            # Пользователь согласился с предложенными формулировками гипотез
            state["brief"]["hypotheses"] = state["pending_hypotheses"]
            state["pending_hypotheses"] = None
            state["hypotheses_ok"] = True
            changed.append("hypotheses")
            confirm = False  # согласие потрачено на гипотезы, а не на этап
        elif state.get("pending_hypotheses") and not changed:
            # Ни согласия, ни своих формулировок — считаем отказом от предложений
            state["pending_hypotheses"] = None
            async for ev in self._emit(
                chat_id,
                "Хорошо, эти формулировки не подходят. Тогда сформулируйте гипотезы своими "
                "словами: конкретное, проверяемое предположение о поведении пользователей — "
                "что именно и у кого мы ожидаем увидеть?",
                state=state,
            ):
                yield ev
            return

        stage = state.get("stage") or "collect"
        actions = None
        set_mode = _KEEP
        set_state: object = state
        flag_prefix = ""

        if stage == "collect":
            # Проверка конкретики ответов заказчика (бизнес-задача, ЦА, критерий успеха):
            # один переспрос, дальше фиксируем как есть с пометкой «уточнить с заказчиком»
            pending = state.get("pending_clarify")
            quality_field = next((f for f in changed if f in FIELD_QUALITY), None)
            if pending and quality_field != pending:
                flags = state.setdefault("needs_clarification", [])
                if pending not in flags:
                    flags.append(pending)
                state["pending_clarify"] = None
                flag_prefix = (
                    f"Хорошо, оставляю поле «{FIELD_TITLES[pending].lower()}» как есть — "
                    "в задаче отмечу, что пункт стоит уточнить перед стартом исследования.\n\n"
                )
            elif quality_field:
                check = await self._validate_field(state, quality_field)
                if check.get("ok"):
                    state["pending_clarify"] = None
                else:
                    retries = state.setdefault("clarify_retries", {})
                    if retries.get(quality_field, 0) >= 1:
                        flags = state.setdefault("needs_clarification", [])
                        if quality_field not in flags:
                            flags.append(quality_field)
                        state["pending_clarify"] = None
                        flag_prefix = (
                            "Понимаю. Зафиксирую как есть и отмечу в задаче, что пункт "
                            "стоит уточнить перед стартом исследования.\n\n"
                        )
                    else:
                        retries[quality_field] = retries.get(quality_field, 0) + 1
                        state["pending_clarify"] = quality_field
                        clarify = (check.get("clarify") or "").strip() or (
                            f"Можете чуть конкретнее? {FIELD_TITLES[quality_field]} — "
                            "важная часть брифа, по ней исследователь будет работать."
                        )
                        async for ev in self._emit(chat_id, clarify, state=state):
                            yield ev
                        return

            nxt = _next_field(state)
            if nxt is None:
                proposals = await self._propose(state)
                _merge_updates(state["brief"], proposals)
                state["stage"] = "proposal"
                reply = (
                    flag_prefix
                    + "Спасибо, основная часть собрана! Остальные пункты брифа предлагаю так:\n\n"
                    + _render_proposal(state["brief"])
                    + "\n\nЕсли что-то не так — просто напишите правки."
                )
                actions = _proposal_actions()
            else:
                reply = flag_prefix + await self._ask_reply(state, nxt, question, changed)
        elif stage == "proposal":
            if confirm and not changed:
                state["stage"] = "summary"
                reply = "Вот полное саммери задачи:\n\n" + _render_summary(state) + "\n\n" + SUMMARY_HINT
                actions = _summary_actions()
            elif changed:
                reply = (
                    "Обновил. Текущая версия:\n\n"
                    + _render_proposal(state["brief"])
                    + "\n\nПодтвердите или продолжайте править."
                )
                actions = _proposal_actions()
            else:
                reply = (
                    "Не совсем понял, что поправить. Подтвердите предложение кнопкой "
                    "или напишите правку конкретного пункта."
                )
                actions = _proposal_actions()
        else:  # summary
            if confirm and not changed:
                reply = await self._create_task(chat_title, state, user_email)
                set_mode = "normal"
                set_state = None
            elif changed:
                reply = "Обновил саммери:\n\n" + _render_summary(state) + "\n\n" + SUMMARY_HINT
                actions = _summary_actions()
            else:
                reply = "Подтвердите создание задачи кнопкой, напишите правки или «отмена»."
                actions = _summary_actions()

        async for ev in self._emit(chat_id, reply, actions=actions, mode=set_mode, state=set_state):
            yield ev

    # ----- LLM-шаги -----

    async def _extract(self, state: dict, dialog: str, question: str) -> dict:
        parts = [f"Текущий бриф (JSON): {json.dumps(state['brief'], ensure_ascii=False)}"]
        if state.get("pending_hypotheses"):
            parts.append(
                "Ассистент только что предложил формулировки гипотез: "
                + json.dumps(state["pending_hypotheses"], ensure_ascii=False)
            )
        parts.append(f"Этап брифинга: {state.get('stage')}")
        if dialog:
            parts.append(f"Последние сообщения диалога:\n{dialog}")
        parts.append(f"НОВОЕ сообщение пользователя: {question}")
        fallback = {"updates": {}, "cancel": False, "confirm": False, "skip_optional": False}
        return await _llm_json(EXTRACT_PROMPT, "\n\n".join(parts), fallback)

    async def _validate_hypotheses(self, state: dict) -> dict:
        brief = state["brief"]
        user = (
            f"Гипотезы:\n{_bullet(brief.get('hypotheses') or [])}\n\n"
            f"Продукт и контекст: {brief.get('product') or '—'}\n"
            f"Бизнес-задача: {brief.get('business_goal') or '—'}"
        )
        # fail-open: сломанный JSON валидатора не должен блокировать пользователя
        fallback = {"ok": True, "feedback": "", "suggestions": []}
        check = await _llm_json(HYPOTHESES_PROMPT, user, fallback)
        # Собственные предложения тоже проверяем: гипотеза — утверждение, не вопрос
        check["suggestions"] = [
            s.strip() for s in (check.get("suggestions") or [])
            if isinstance(s, str) and s.strip() and "?" not in s
        ]
        return check

    async def _validate_field(self, state: dict, field: str) -> dict:
        """Мягкая проверка конкретики поля брифа: один уточняющий вопрос при ok=false."""
        brief = state["brief"]
        context = {
            k: brief.get(k)
            for k in ("product", "business_goal", "audience", "success_criteria")
            if k != field and brief.get(k)
        }
        prompt = FIELD_QUALITY_PROMPT.format(
            title=FIELD_TITLES[field],
            criteria=FIELD_QUALITY[field],
            value=brief.get(field) or "",
            context=json.dumps(context, ensure_ascii=False),
        )
        # fail-open: сломанный валидатор не должен блокировать брифинг
        fallback = {"ok": True, "clarify": ""}
        return await _llm_json(prompt, "Оцени значение поля.", fallback)

    async def _ask_reply(self, state: dict, field: str, question: str, changed: list[str]) -> str:
        """Фраза-подтверждение — от LLM, сам следующий вопрос — детерминированно кодом."""
        hint = QUESTION_HINTS[field]
        if changed:
            note = "Из последнего сообщения зафиксировано: " + ", ".join(
                FIELD_TITLES[k].lower() for k in changed if k in FIELD_TITLES
            )
        else:
            note = "Из последнего сообщения не удалось извлечь ничего нового."
        ack = "Принято!"
        try:
            text = (
                await llm.chat_completion(
                    [
                        {"role": "system", "content": ASK_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"Бриф: {json.dumps(state['brief'], ensure_ascii=False)}\n\n{note}\n\n"
                                f"Последний ответ пользователя: {question}"
                            ),
                        },
                    ],
                    temperature=0.3,
                )
            ).strip()
            # Страховка: модель могла все же задать свой вопрос — оставляем только подтверждение
            if text and "?" not in text:
                ack = text
        except llm.LLMError:
            pass
        return f"{ack}\n\n{hint}"

    async def _propose(self, state: dict) -> dict:
        brief = state["brief"]
        user = json.dumps(
            {
                "продукт_и_контекст": brief.get("product"),
                "бизнес_задача": brief.get("business_goal"),
                "целевая_аудитория": brief.get("audience"),
                "гипотезы": brief.get("hypotheses"),
                "критерий_успеха": brief.get("success_criteria"),
                "требования_к_дизайну": brief.get("design_constraints"),
            },
            ensure_ascii=False,
        )
        fallback = {
            "main_question": "",
            "research_questions": [],
            "methodology": "",
            "expected_results": "",
            "first_step": "",
        }
        result = await _llm_json(PROPOSE_PROMPT, user, fallback, temperature=0.3)
        if not (result.get("main_question") or "").strip():
            # Совсем пустое предложение — лучше честная ошибка, чем пустой бриф
            raise llm.LLMError("модель не смогла предложить методологическую часть брифа")
        return result

    # ----- создание задачи -----

    async def _create_task(self, chat_title: str, state: dict, user_email: str) -> str:
        title = await self._task_title(state)
        related = await self._find_related(state)
        md = _render_task_md(title, chat_title, state, user_email, related)
        path = _write_task_file(title, md)
        reply = (
            f"**Задача создана:** `tasks/{path.name}`\n\n"
            "Внутри — контекст продукта, ЦА, гипотезы, исследовательские вопросы и методология: "
            "все, что нужно исследователю для старта."
        )
        if related:
            reply += (
                "\n\nВ базе нашлись связанные исследования — я указал их в задаче, "
                "чтобы исследователь не дублировал уже сделанное."
            )
        return reply

    async def _task_title(self, state: dict) -> str:
        brief = state["brief"]
        try:
            title = (
                await llm.chat_completion(
                    [
                        {
                            "role": "system",
                            "content": "Придумай короткое название задачи на исследование (3–7 слов) "
                            "про указанный продукт. Ответь только названием, без кавычек и точки в конце.",
                        },
                        {
                            "role": "user",
                            "content": f"Продукт: {brief.get('product') or ''}\n"
                            f"Главный вопрос: {brief.get('main_question') or ''}"[:800],
                        },
                    ],
                    temperature=0.3,
                )
            ).strip().strip('"«»').strip()
            if title:
                return title[:100]
        except llm.LLMError:
            pass
        return (brief.get("main_question") or state.get("origin_question") or "Задача на исследование")[:100]

    async def _find_related(self, state: dict) -> list[dict]:
        """Ищет в базе похожие прошлые исследования для секции «Связанные исследования»."""
        try:
            brief = state["brief"]
            query = " ".join(
                filter(
                    None,
                    [
                        state.get("origin_query") or state.get("origin_question"),
                        brief.get("main_question"),
                        " ".join(brief.get("hypotheses") or []),
                    ],
                )
            )[:2000]
            if not query.strip():
                return []
            vectors, embedding_model = await llm.embed_texts([query])
            async with async_session() as session:
                sources = await rag.search_chunks(session, vectors[0], embedding_model)
            seen: set = set()
            related = []
            for s in sorted(sources, key=lambda x: -x["similarity"]):
                if s["study_id"] in seen:
                    continue
                seen.add(s["study_id"])
                related.append(
                    {"title": s["study_title"], "date": s["conducted_at"].isoformat()}
                )
                if len(related) >= 3:
                    break
            return related
        except Exception:  # noqa: BLE001 — связанные исследования не критичны для задачи
            return []

    # ----- служебное -----

    async def _emit(self, chat_id, reply: str, actions: list | None = None, mode=_KEEP, state=_KEEP):
        """Сохраняет ответ ассистента (и изменения чата), отдает token + done."""
        yield sse_event("token", {"t": reply})
        async with async_session() as session:
            chat = await session.scalar(select(Chat).where(Chat.id == chat_id))
            if chat is not None:
                if mode is not _KEEP:
                    chat.mode = mode
                if state is not _KEEP:
                    chat.tool_state = state
                chat.updated_at = utcnow()
            msg = Message(chat_id=chat_id, role="assistant", content=reply, actions=actions)
            session.add(msg)
            await session.commit()
            full = await session.scalar(
                select(Message).where(Message.id == msg.id).options(selectinload(Message.citations))
            )
        yield sse_event("done", {"message": message_to_dict(full)})
