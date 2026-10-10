# Decision 001: scope v1, безопасные отличия и внешний контракт отказов

**Задача:** 0.5

**Статус:** `PROPOSED — ARCHITECT SCOPE AGREED, AWAITING PM ACCEPTANCE AND G0`

**Дата обновления пакета:** 2026-10-10

**Принимающие стороны:** заказчик продукта, представитель самописной CRM, технический reviewer/архитектор

## 1. Решение, которое требуется принять

Архитектор согласовал состав **52 операции v1 / 12 deferred** и отдельный перенос доставки callback. Этот документ синхронизирует пакет для самостоятельной PM-приёмки и последующего G0, но сам по себе не означает принятие задачи 0.5 или прохождение G0.

Предлагается принять единым пакетом:

1. v1 из **52 операций** и deferred-набор из **12 операций**;
2. условия возврата каждой deferred-операции;
3. безопасные ограничения методов, оставшихся в v1;
4. выбранные решения по блокерам 0.4;
5. внешний error contract;
6. изменения будущих задач, G0/G7 и оценки.

Все 64 ID сохраняются. До отдельной PM-приёмки и `G0 APPROVED` пакет остаётся предложением к выпуску, а не разрешением начинать весь v1.

## 2. Краткая таблица решений

Статус «согласовано архитектором» фиксирует выбранный вариант, но не заменяет PM-приёмку задачи 0.5 и отдельный G0.

| ID | Требуется решение | Варианты | Рекомендация | Кто утверждает | Статус |
|---|---|---|---|---|---|
| D01 | Scope первой версии | 52 v1 / 12 deferred | Применить точный состав раздела 3 | заказчик + CRM + reviewer | согласовано архитектором; PM-приёмка ожидается |
| D02 | Условия возврата deferred | Таблица раздела 4 | Возврат только отдельным scope-решением и после доказательств | заказчик + reviewer | подготовлено к PM-приёмке |
| D03 | Безопасные ограничения 52 методов | Раздел 5 | Без доказанной принадлежности операция не выполняется | заказчик + CRM + reviewer | подготовлено к PM-приёмке |
| D04 | Contact detail bootstrap | Trusted mapping с нейтральным отказом | C06–C07 только по server-side связи `detail → contact → project` и с повторной parent check | заказчик + CRM + reviewer | согласовано архитектором; PM-приёмка ожидается |
| D05 | P05–P08 и существующие подписки | Deferred | Не читать и не менять upstream-подписки в v1 | заказчик + CRM + reviewer | согласовано архитектором; PM-приёмка ожидается |
| D06 | Callback trust и delivery | Deferred вне реестра 64 | Не принимать и не доставлять callback в v1 | заказчик + CRM + reviewer | согласовано архитектором; PM-приёмка ожидается |
| D07 | L10 и происхождение `file_id` | Deferred | Вернуть только после доказанной цепочки `file_id → custom → lead → project` | заказчик + CRM + reviewer | согласовано архитектором; PM-приёмка ожидается |
| D08 | L21/media | Оставить в v1 | Запись выдаётся только безопасно через домен ProxyAPI | заказчик + CRM + reviewer | согласовано архитектором; PM-приёмка ожидается |
| D09 | Внешние ошибки | Для deferred зафиксировать HTTP 200/application 501; 400/404 вынести на отдельную приёмку | Раздел 7 | CRM + заказчик + reviewer | 501 входит в пакет; 400/404 ожидают решения |
| D10 | G0/G7 и оценка | Разделы 9–10 | 36–57 человеко-дней — предварительная оценка полного объёма, не остатка | заказчик + reviewer | подготовлено к PM-приёмке |

## 3. Согласованный архитектором scope 52/12

### v1 — 52 операции

`A01–A02`, `P01–P04`, `P09–P14`, `C01–C07`, `C09–C12`, `V01–V04`, `L01–L09`, `L12–L21`, `T02–T03`, `M01–M04`.

### Deferred — 12 операций

`P05–P08`, `C08`, `L10`, `L11`, `L22`, `S01`, `T01`, `T04`, `T05`.

Доставка callback также deferred, но не входит в подсчёт 64 операций. Deferred означает: route известен реестру, после успешной локальной авторизации возвращает HTTP 200 с application code 501 и не проверяет существование объекта, не обращается к LPTracker и не запускает внутренний login. Deferred не означает удаление ID, источника или накопленных DOC/LIVE-фактов.

## 4. Условия возврата deferred-операций

Возврат выполняется отдельным письменным scope-решением: доказательство условия → обновление OpenAPI/клиентской документации → реализация → contract/isolation tests → reviewer acceptance. Одного изменения статуса в реестре недостаточно.

| ID | Причина deferred | Проверяемое условие возврата | Что не считается достаточным |
|---|---|---|---|
| P05 | Адресная мутация upstream-подписки не доказана | Provider contract или controlled fixture доказывает создание/изменение только нашей регистрации и безопасный readback | PUT success без доказательства, какая запись изменена |
| P06 | Upstream-список может раскрыть существующие чужие receiver URL | Принят безопасный источник только клиентских подписок без раскрытия upstream receiver | Фильтрация account-wide ответа после его выдачи клиенту |
| P07 | Адресная мутация callback структуры CRM не доказана | Выполнено условие P05 для отдельного типа подписки и доказано отсутствие изменения чужих регистраций | Пустой список до записи либо PUT без target ID |
| P08 | Пустой runtime-список не доказывает безопасный будущий read | Выполнено условие P06 для callback структуры CRM | Одна пустая выборка A/B |
| C08 | Оба известных route вернули application errors; безопасное удаление не доказано | Рабочий delete route; для pre-existing/out-of-band details воспроизводима связь `detail → parent → разрешённый project` и повторная parent check перед delete | HTTP success без readback; mapping только по client-supplied detail ID |
| L10 | Provider/parent source `file_id` и полная parent chain не доказаны | Обезличенный fixture воспроизводимо доказывает `file_id → custom → lead → разрешённый project`, negative fixture и Base64/MIME/size/error limits | Client-supplied `file_id`, догадка или mapping из недоказанного источника |
| S01 | `GET /staff` account-scoped, project membership отсутствует | Утверждён доказуемый источник membership и правила фильтрации/очистки либо отдельные upstream-аккаунты | `last_project`, staff presence или отсутствие чужих данных в одной выборке |
| L11 | Membership сотрудника и `owner=0` не доказаны | Provider/доверенный server-side справочник даёт membership; приняты `owner=0`, redaction и neutral denial; isolation test пройден | Совпадение staff ID или account-wide S01 |
| L22 | В запросе нет lead/project; blast radius account/chat scoped | Доказана связь MAX-чата с разрешённым проектом и отсутствие эффекта для других проектов либо принята эквивалентная изоляция | Успех одного вызова без проверки других проектов |
| T01 | Нет безопасного staff/notification fixture | Утверждён staff/notification contract; controlled runtime подтверждает проект, owner/observers, visibility и получателей | `remind_type=0` без проверки остальных уведомлений |
| T04 | Не доказаны staff rules, уведомления и transfer semantics | Выполнены условия T01; runtime проверяет старый/новый project и запрещает transfer до write | Post-write обнаружение чужого проекта или отсутствие уведомления в одном случае |
| T05 | Нет безопасного task lifecycle с доказанным проектом | После допуска T01/T04 fixture читается перед delete; delete и readback подтверждены без чужих эффектов | Delete по одному task ID без precheck |

Доставка callback возвращается отдельным решением после доказанного trust contract источника, delete reconciliation, retry/order/event ID, дедупликации, SSRF-защиты и контролируемого end-to-end сценария. Наличие P05–P08 в будущем scope само по себе callback delivery не включает.

## 5. Обязательные ограничения методов, остающихся в v1

Эти правила не являются временными удобствами. Без доказанной принадлежности операция не выполняется и данные не выдаются независимо от принятого scope.

| Методы | Контракт v1 | Проверка при реализации |
|---|---|---|
| C05 | До delete доказать project родителя; каскад не обещать без отдельного теста | project precheck, controlled delete, readback связанных данных |
| C06–C07 | Только trusted server-side mapping `detail → parent → project` и повторная parent check; ID-only detail получает neutral denial | pre-existing, proxy-created и out-of-band cases; stale mapping/reassignment |
| L01/L06 | Неподтверждённые `owner/observers` отклоняются; молчаливое назначение запрещено | negative staff IDs; body не вызывает write до проверки всех ссылок |
| T02–T03 | Доказать project задачи/списка; очистить nested staff/profile fields | foreign IDs, list leakage, main-account/zero-ID, avatar/internal URL redaction |
| L21/media | Не отдавать `record`; выдавать opaque URL нашего домена после повторной проверки прав | SSRF/DNS/redirect, expiry/revocation, Range, headers, size/concurrency |

## 6. Нерешённые вопросы 0.4 и последствия для G0

Единичный live-result доказывает только наблюдавшийся случай. Документальная схема не доказывает runtime-контракт.

| Тема | Подтверждено | Не подтверждено | Варианты решения | Последствие для G0 |
|---|---|---|---|---|
| P05–P08 | P06 A содержит одну существующую lead-подписку; остальные P06/P08 списки пусты; writes не выполнялись | PUT не принимает subscription ID; add/update/replace и empty-url target неизвестны | Deferred в v1 | 501/no-upstream проверяется в 7.1; возврат — только по условиям раздела 4 |
| Callback trust | Payload обеих схем документирован; secret URL не является proof | Signature/headers/mTLS/allowlist, retry/order/event ID; надёжный delete payload/readback | Delivery deferred вне 64 | G0 не обещает callback delivery; возврат требует отдельного решения |
| L10 | DOC обещает Base64 response; пять verified file fields не дали structured file ID; L10 не вызывался | Provider/parent source `file_id`, связь с `custom_id`, `lead_id`, `project_id`, MIME/size/errors | Deferred в v1 | 501/no-upstream проверяется в 7.1; evidence сохраняется как условие возврата |
| L21/media | Три owned leads; пять HTTPS records на documented host; один Range вернул 206/Content-Range без redirect | Непустой owner, другие host/records, redirect/error/expiry, реальные MIME, revoke/concurrency | Оставить в v1 с conservative proxy contract | Failure-path tests остаются gate G5/G7; feasibility не равна готовности |

### Зафиксированный для PM-приёмки пакет по 0.4

- P05–P08: deferred; upstream subscription state в v1 не читается и не меняется.
- Callback delivery: deferred отдельно от 64 операций.
- L10: deferred до доказанной цепочки происхождения `file_id` и всей parent/project-цепочки.
- L21: остаётся в v1 только с выдачей записи через домен ProxyAPI; upstream `record` и служебные заголовки клиенту не раскрываются.

## 7. Внешний error contract пакета 0.5

Числа ниже — **application codes** внутри совместимой JSON-оболочки, а не HTTP status. Для deferred поведение 501 входит в согласованный архитектором пакет. Коды 400/404 остаются предложением для отдельной PM/CRM-приёмки и не объявляются утверждённым контрактом.

Предлагаемая форма:

```http
HTTP/1.1 200 OK
Content-Type: application/json
X-Request-ID: <opaque-request-id>
```

```json
{
  "status": "error",
  "errors": [
    {
      "code": 404,
      "message": "Resource is not available"
    }
  ]
}
```

| Application code | Когда использовать | Нейтральное сообщение | Запрещённая детализация |
|---:|---|---|---|
| 400 | Невалидная форма запроса, тип, обязательное поле, conflicting project IDs | `Request is not valid` | Исходный upstream body, внутренний parser/field path, project mapping |
| 404 | Объект отсутствует, чужой либо ownership нельзя доказать; различие наружу не выдаётся | `Resource is not available` | Существование объекта, владелец/project, причина ownership failure |
| 501 | Method известен 64-operation inventory, но deferred/не входит в v1 | `Operation is not available in this release` | Provider blocker, внутренние URL, условия аккаунта, hidden feature state |

Общие правила:

- `400/404` не считаются утверждёнными до отдельной PM/CRM-приёмки. Текущий runtime для части FastAPI validation возвращает application code `422`; это известное расхождение, которое нельзя скрывать в OpenAPI или клиентском контракте.
- Предлагаемый единый `404` используется для foreign/not-found/unproven ownership, чтобы не создавать ID oracle.
- `501` применяется только к route-level deferred method после успешной локальной авторизации и не используется после object lookup.
- Для `501` запрещены проверка существования объекта, исходящий LPTracker-вызов и внутренний login/refresh.
- Если локальная авторизация неуспешна, возвращается нейтральная auth-ошибка; release status метода до аутентификации не раскрывается.
- `400` не заменяет security denial и не используется для раскрытия существования чужого объекта.
- `X-Request-ID` содержит наш opaque ID; upstream IDs и тексты ошибок наружу не переносятся.
- Timeout/unknown write outcome, quota и service failure получают отдельные коды, которые фиксируются до реализации 1.3/3.1; они не должны переиспользовать `400/404/501`.

Открытые альтернативы для решения CRM по 400/404:

1. Сохранить HTTP 200 и согласовать application `400/404`, приведя текущий application `422` к выбранному validation-коду.
2. Оставить application `422` для framework validation и использовать `400` только для доменной валидации; это различие должно быть явно принято и описано.
3. Реальные HTTP `400/404` — семантически привычнее, но хуже совместимы с наблюдаемой моделью LPTracker. Deferred 501 в любом случае остаётся внутри HTTP 200.

## 8. Влияние согласованного scope 52/12

Ниже — единый change set пакета 0.5. Он фиксирует решение архитектора, но не означает PM-приёмку 0.5 или прохождение G0.

| Артефакт / задача | Согласованное изменение |
|---|---|
| Реестр | Сохранить все 64 ID и исходные DOC/LIVE-факты; отметить 52 `v1`, 12 `deferred`, ссылку на это решение и условия возврата |
| Compatibility matrix | Не менять DOC/LIVE-факты; scope status хранить отдельно от verification status |
| `docs/api/openapi.yaml` | Содержать все 64 method+path: 52 `v1` с осторожными черновыми схемами и 12 `deferred` с `x-release-status: deferred` и только согласованным `501`; непроверенные upstream-схемы не объявлять подтверждёнными |
| `docs/api/client-contract.md` | Кратко описать 52 доступные операции, 12 deferred, отдельно deferred-доставку callback, ограничения изоляции и фиксированный neutral refusal |
| 2.1 | Сохранить neutral denial для detail без trusted mapping и обязательную redaction T02–T03 |
| 2.2 | Для C06–C07 использовать только доверенную серверную связь `detail → contact → project` и повторную проверку родителя; без доказанной связи отказать нейтрально до upstream-вызова |
| 2.3 | Не включать публичный S01; не использовать account-scoped staff как доказательство membership |
| 3.2 | Реализовать C01–C07/C09–C12; C08 получает фиксированный `501`; C05 cascade проверяется отдельно |
| 3.3 | Исключить L11; L01/L06 отклоняют неподтверждённых owner/observers; остальные lead operations сохраняются по scope |
| 4.2 | Реализовать только T02–T03; T01/T04/T05 получают фиксированный `501`; обязательны project check и nested staff redaction |
| 5.1 | Реализовать L09; L10 исключить из активной реализации и вернуть фиксированный `501` |
| 5.2 | Реализовать L19–L20; L22 получает фиксированный `501`; отдельно проверить вложения и unknown write outcome L19 |
| 5.3 | Реализовать L08 и L21; L21 выдаёт записи только через безопасный домен ProxyAPI; доставку callback не реализовывать |
| Этап 6 | Отложить целиком: P05–P08 получают фиксированный `501`, доставка callback остаётся вне 64 операций и не реализуется |
| 7.1 | Проверить success/error/isolation/field contract для 52; для 12 deferred — route registration, одинаковый `501`, zero upstream calls, отсутствие утечек; все 64 ID остаются в отчёте |
| Этап 0/G0 | Вынести пакет на отдельную PM-приёмку и затем на G0; этот документ сам по себе не закрывает ни одну из точек |
| G7 | Может закрыться при 52 реализованных методах и 12 проверенных neutral refusals, только если нет открытого isolation/secret/duplicate-action defect в v1 |

## 9. Критерии G0 и G7 для согласованного scope

### G0 — отдельная точка после 0.5

G0 не пройден этим документом. Для вынесения на G0 одновременно нужны:

1. письменная фиксация архитектором состава 52/12 и решений D05–D08; пакет 0.5 отдельно принят PM;
2. синхронные артефакты: `docs/plans/proxy-api-endpoint-inventory.md`, scope-блок `docs/api/compatibility-matrix.md`, валидируемый черновик `docs/api/openapi.yaml`, черновик `docs/api/client-contract.md`, обновлённые `docs/plans/proxy-api-tasks.md` и `docs/plans/proxy-api-implementation-plan.md`;
3. принятые safe rules C05, C06–C07, L01/L06, T02–T03;
4. P05–P08, L10 и доставка callback отмечены deferred; для L21 зафиксирована безопасная выдача только через домен ProxyAPI;
5. для каждого deferred утверждены return condition и требование `501` без upstream call; runtime-проверка этого требования остаётся задачей 7.1;
6. reviewer/архитектор отдельно ставит `G0 APPROVED`; принятие 0.5 не заменяет эту отметку.

### G7 — техническое разрешение на пилот

При 52/12 G7 может закрыться, когда:

- все 52 имеют success/error/isolation/field contract tests;
- 12 deferred имеют contract tests нейтрального `501`, zero upstream calls и отсутствие data leakage;
- ни один retained blocker раздела 6 не маскируется как реализованный метод;
- доставка callback не заявляется как часть v1 и не учитывается в 64 операциях;
- OpenAPI и клиентская документация совпадают с runtime;
- открытый defect изоляции, секрета, неизвестного повтора записи или media SSRF блокирует G7 независимо от числа реализованных методов.

## 10. Предварительная оценка

Оценка архитектора для **полного согласованного объёма 52 v1 / 12 deferred** составляет **36–57 человеко-дней**. Это оценка полного объёма пакета, включая уже принятые задачи, а не оценка оставшейся работы и не календарное обязательство.

Из уже выполненного и принятого нельзя корректно вычесть человеко-дни без журнала фактических затрат и единой границы приёмки. Поэтому оставшийся срок в этом решении не публикуется. Для его получения нужны фактические затраты по принятым задачам, повторная оценка незакрытых задач и учёт последовательных PM/reviewer-gates.

Диапазон 36–57:

- не включает ожидание provider/PM/reviewer;
- не включает будущий возврат 12 deferred и доставку callback;
- предполагает, что C06–C07 используют доверенную серверную связь, а L21 не требует выдачи upstream URL клиенту;
- пересчитывается, если меняется scope или обязательный механизм безопасности.

Уверенность в применимости диапазона к полному объёму — средняя; уверенность в любой оценке остатка без фактических трудозатрат — низкая.

## 11. Письменная фиксация решения

Каждая сторона заполняет свою строку. Ссылка на комментарий/issue допустима вместо подписи, если в ней явно перечислены D01–D10.

| Сторона | Решение | Имя/роль | Дата | Ссылка/замечания |
|---|---|---|---|---|
| Заказчик / PM | ожидается | — | — | Отдельная приёмка пакета 0.5 ещё не выполнена |
| Самописная CRM | ожидается | — | — | Клиентский контракт требует подтверждения |
| Reviewer/архитектор | scope согласован | — | — | 52 v1 / 12 deferred; отдельный `G0 APPROVED` отсутствует |

Шаблон:

```text
Decision 001 / Task 0.5
D01 scope: 52 v1 / 12 deferred
D02 deferred return conditions: accepted / changes: ...
D03 retained safety rules: accepted / changes: ...
D04 details: trusted server mapping + parent re-check
D05 P05-P08: deferred
D06 callback delivery: deferred outside 64 operations
D07 L10: deferred
D08 L21/media: v1 through safe ProxyAPI domain only
D09 error contract: deferred HTTP 200 + application 501 accepted; 400/404 pending
D10 G0/G7/estimate: accepted / changes: ...
Name, role, date, link
```

## 12. Как применить решение

После отдельной PM-приёмки в той же ветке выполняется отдельный commit:

1. заменить `PROPOSED` на `ACCEPTED` или `REJECTED`, сохранить ссылки и даты;
2. сохранить синхронность inventory, matrix scope block, implementation plan, tasks, vision cross-reference, estimate, `docs/api/openapi.yaml` и `docs/api/client-contract.md`;
3. проверить 64 ID, сумму `52 + 12 = 64`, отсутствие пересечений и неизменность verification facts;
4. выполнить `git diff --check` и проверку ссылок;
5. передать пакет на отдельный G0;
6. не начинать 1.1 и не ставить G0 до отдельного `G0 APPROVED`.

## 13. Источники решения

- [Реестр 64 и scope 52/12](../plans/proxy-api-endpoint-inventory.md)
- [Матрица совместимости](../api/compatibility-matrix.md)
- [Изоляция сложных объектов](../api/isolation-findings.md)
- [События и медиа](../api/events-media-findings.md)
- [Основной план](../plans/proxy-api-implementation-plan.md)
- [Очередь задач и точки G0–G8](../plans/proxy-api-tasks.md)
- [Vision](../../lptracker_api_proxy_vision.md)
