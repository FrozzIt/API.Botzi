# Decision 001: scope v1, безопасные отличия и внешний контракт отказов

**Задача:** 0.5

**Статус:** `PROPOSED — AWAITING WRITTEN DECISION`

**Дата пакета:** 2026-09-25

**Принимающие стороны:** заказчик продукта, представитель самописной CRM, технический reviewer/архитектор

## 1. Решение, которое требуется принять

До письменного решения действует исходная база из **64 операций**, полный bootstrap contact details, проверки всех 64 строк, исходные критерии этапа 0/G0/G7 и оценка `50–77` человеко-дней. Этот документ не меняет scope, коды ошибок, OpenAPI, задачи или контрольные точки сам по себе.

Предлагается принять единым пакетом:

1. v1 из **57 операций** и deferred-набор из **7 операций**;
2. условия возврата каждой deferred-операции;
3. безопасные ограничения методов, оставшихся в v1;
4. выбранные решения по блокерам 0.4;
5. внешний error contract;
6. изменения будущих задач, G0/G7 и оценки.

Если хотя бы один обязательный пункт пакета не принят, решение 57/7 не считается состоявшимся. В этом случае действующей базой остаются 64 операции либо стороны письменно утверждают другой scope с новым составом и отдельной оценкой.

## 2. Краткая таблица решений

`Рекомендация` — инженерное предложение, а не утверждённое продуктовое решение.

| ID | Требуется решение | Варианты | Рекомендация | Кто утверждает | Статус |
|---|---|---|---|---|---|
| D01 | Scope первой версии | A: принять 57/7; B: оставить 64; C: утвердить другой состав | A, только вместе с D02–D10; иначе C с новым пересчётом | заказчик + CRM + reviewer | ожидается |
| D02 | Условия возврата 7 deferred | A: принять таблицу раздела 4; B: вернуть на доработку по конкретным ID | A | заказчик + reviewer | ожидается |
| D03 | Безопасные ограничения 57 методов | A: принять раздел 5; B: исключить спорный метод из v1 | A; безопасность не может быть ослаблена | заказчик + CRM + reviewer | ожидается |
| D04 | Contact detail bootstrap | A: trusted mapping с нейтральным отказом; B: полный bootstrap; C: убрать C06–C07 из v1 | A | заказчик + CRM + reviewer | ожидается |
| D05 | P05/P07 и существующие подписки | A: доказать адресность до G0; B: принять local-only контракт без upstream delivery; C: deferred | A; B/C меняют продуктовый контракт или scope | заказчик + CRM + reviewer | ожидается |
| D06 | Callback trust и delete | A: readback для create/update, delete quarantine; B: потребовать доказанную аутентификацию и delete reconciliation; C: deferred callback delivery | B, если CRM требует полный поток; иначе A как явное отличие | заказчик + CRM + reviewer | ожидается |
| D07 | L10 и происхождение `file_id` | A: до G0 доказать provider/parent source и цепочку `file_id → custom → lead → project`; B: deferred L10, scope 56/8 и отдельная оценка | A только при воспроизводимом доказательстве раздела 6; trusted mapping без доказанного источника `file_id` недостаточен, иначе B | заказчик + CRM + reviewer | ожидается |
| D08 | L21/media | A: принять консервативный proxy contract и обязательные тесты; B: потребовать дополнительные fixtures до G0; C: deferred | A | заказчик + CRM + reviewer | ожидается |
| D09 | Внешние `400/404/501` | A: принять предложение раздела 7; B: утвердить другую таблицу | A | CRM + заказчик + reviewer | ожидается |
| D10 | G0/G7 и оценка | A: принять разделы 9–10; B: зафиксировать иные критерии/оценку | A после D01–D09 | заказчик + reviewer | ожидается |

## 3. Предлагаемый scope 57/7

### v1 — 57 операций

`A01–A02`, `P01–P14`, `C01–C07`, `C09–C12`, `V01–V04`, `L01–L10`, `L12–L21`, `T02–T03`, `M01–M04`.

### Deferred — 7 операций

`C08`, `S01`, `L11`, `L22`, `T01`, `T04`, `T05`.

Deferred означает: метод известен реестру, но не считается реализованным v1, не вызывает LPTracker и получает согласованный нейтральный отказ. Deferred не означает удаление ID, источника или накопленных live-фактов.

## 4. Условия возврата deferred-операций

Возврат выполняется отдельным письменным scope-решением: доказательство условия → обновление OpenAPI/клиентской документации → реализация → contract/isolation tests → reviewer acceptance. Одного изменения статуса в реестре недостаточно.

| ID | Причина deferred | Проверяемое условие возврата | Что не считается достаточным |
|---|---|---|---|
| C08 | Оба известных route вернули application errors; безопасное удаление не доказано | Рабочий delete route; для pre-existing/out-of-band details воспроизводима связь `detail → parent → разрешённый project` и повторная parent check перед delete | HTTP success без readback; mapping только по client-supplied detail ID |
| S01 | `GET /staff` account-scoped, project membership отсутствует | Утверждён доказуемый источник membership и правила фильтрации/очистки либо отдельные upstream-аккаунты | `last_project`, staff presence или отсутствие чужих данных в одной выборке |
| L11 | Membership сотрудника и `owner=0` не доказаны | Provider/доверенный server-side справочник даёт membership; приняты `owner=0`, redaction и neutral denial; isolation test пройден | Совпадение staff ID или account-wide S01 |
| L22 | В запросе нет lead/project; blast radius account/chat scoped | Доказана связь MAX-чата с разрешённым проектом и отсутствие эффекта для других проектов либо принята эквивалентная изоляция | Успех одного вызова без проверки других проектов |
| T01 | Нет безопасного staff/notification fixture | Утверждён staff/notification contract; controlled runtime подтверждает проект, owner/observers, visibility и получателей | `remind_type=0` без проверки остальных уведомлений |
| T04 | Не доказаны staff rules, уведомления и transfer semantics | Выполнены условия T01; runtime проверяет старый/новый project и запрещает transfer до write | Post-write обнаружение чужого проекта или отсутствие уведомления в одном случае |
| T05 | Нет безопасного task lifecycle с доказанным проектом | После допуска T01/T04 fixture читается перед delete; delete и readback подтверждены без чужих эффектов | Delete по одному task ID без precheck |

Если принят D07-B, восьмым deferred становится `L10`. Его условие возврата: provider/parent read воспроизводимо выдаёт `file_id`; обезличенный fixture доказывает цепочку `file_id → custom → lead → разрешённый project`; отрицательный fixture подтверждает neutral denial для чужой или недоказанной цепочки; после этого согласованы Base64/MIME/size/error limits и выполнены contract/isolation tests. Client-supplied `file_id`, догадка либо mapping из недоказанного источника условие возврата не выполняют.

## 5. Обязательные ограничения методов, остающихся в v1

Эти правила не являются временными удобствами. Без доказанной принадлежности операция не выполняется и данные не выдаются независимо от принятого scope.

| Методы | Контракт v1 | Проверка при реализации |
|---|---|---|
| C05 | До delete доказать project родителя; каскад не обещать без отдельного теста | project precheck, controlled delete, readback связанных данных |
| C06–C07 | Только trusted server-side mapping `detail → parent → project` и повторная parent check; ID-only detail получает neutral denial | pre-existing, proxy-created и out-of-band cases; stale mapping/reassignment |
| L01/L06 | Неподтверждённые `owner/observers` отклоняются; молчаливое назначение запрещено | negative staff IDs; body не вызывает write до проверки всех ссылок |
| T02–T03 | Доказать project задачи/списка; очистить nested staff/profile fields | foreign IDs, list leakage, main-account/zero-ID, avatar/internal URL redaction |
| P05–P08 | Публично показывать только локальные настройки клиента; не раскрывать upstream receiver/чужие URL | existing subscriptions unchanged; exact ownership before any mutation |
| L10 | `lead`, `custom` и `file` должны образовывать доказанную цепочку; client URL и client-supplied `file_id` не являются источником принадлежности | До допуска реализации — воспроизводимый provider/parent source `file_id`; затем wrong custom/file IDs, project isolation, size/MIME/Base64 limits. Одного server-side mapping недостаточно, если неизвестно, откуда безопасно получен `file_id` |
| L21/media | Не отдавать `record`; выдавать opaque URL нашего домена после повторной проверки прав | SSRF/DNS/redirect, expiry/revocation, Range, headers, size/concurrency |

## 6. Нерешённые вопросы 0.4 и последствия для G0

Единичный live-result доказывает только наблюдавшийся случай. Документальная схема не доказывает runtime-контракт.

| Тема | Подтверждено | Не подтверждено | Варианты решения | Последствие для G0 |
|---|---|---|---|---|
| P05/P07 | P06 A содержит одну существующую lead-подписку; остальные P06/P08 списки пусты; writes не выполнялись | PUT не принимает subscription ID; add/update/replace и empty-url target неизвестны | 1) provider confirmation + isolated controlled write/readback; 2) local-only settings без upstream delivery как принятое отличие; 3) defer P05/P07/callback registration | При варианте 1 G0 ждёт доказательство; вариант 2 требует согласия CRM и меняет promise событий; вариант 3 меняет 57/7 и оценку |
| Callback trust | Payload обеих схем документирован; secret URL не является proof | Signature/headers/mTLS/allowlist, retry/order/event ID; надёжный delete payload/readback | 1) authenticate upstream; 2) untrusted signal + authorized readback для create/update, delete quarantine; 3) defer delivery | Без принятого варианта нельзя утверждать безопасный callback contract и проходить G0 |
| L10 | DOC обещает Base64 response; пять verified file fields не дали structured file ID; L10 не вызывался | Provider/parent source `file_id`, его связь с точными `custom_id`, `lead_id` и разрешённым `project_id`, MIME/size/errors, legacy/out-of-band files | 1) до G0 получить обезличенный воспроизводимый read-only fixture: provider/parent read возвращает `file_id`, а последующие parent reads доказывают цепочку `file_id → custom → lead → project`; negative fixture не позволяет использовать тот же ID вне разрешённой цепочки; 2) если такое доказательство недоступно, defer L10 и принять scope 56/8 с оценкой раздела 10 | Вариант 1 блокирует G0 до evidence; догадка, client-supplied ID или mapping, наполненный из недоказанного источника, не подходят. Вариант 2 меняет scope, OpenAPI, клиентскую документацию, G7 и оценку |
| L21/media | Три owned leads; пять HTTPS records на documented host; один Range вернул 206/Content-Range без redirect | Непустой owner, другие host/records, redirect/error/expiry, реальные MIME, revoke/concurrency | 1) conservative allowlist/no-redirect contract + обязательные stage-5 tests; 2) дополнительные fixtures до G0; 3) defer | Вариант 1 позволяет признать feasibility, но не готовность; failure-path tests остаются gate G5/G7 |

### Рекомендованный пакет по 0.4

- P05/P07: вариант 1; не выполнять upstream write до доказанной адресности.
- Callback: вариант 2 только если CRM письменно принимает отсутствие неподтверждаемого delete; иначе вариант 1/3.
- L10: вариант 1 только после проверяемого доказательства происхождения `file_id` и всей parent/project-цепочки. До этого trusted mapping не делает L10 реализуемым. Если доказательство до G0 недоступно, принять вариант 2: deferred L10, scope 56/8 и отдельную оценку.
- L21: вариант 1; единичный Range остаётся evidence feasibility, а не полного контракта.

## 7. Предложение внешнего error contract

Этот раздел требует отдельной письменной приёмки CRM. Числа ниже — **application codes** внутри совместимой JSON-оболочки, а не вывод из HTTP-кодов live-ответов.

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
| 501 | Method известен 64-operation inventory, но deferred/не входит в принятую v1 | `Operation is not available in this release` | Provider blocker, внутренние URL, условия аккаунта, hidden feature state |

Общие правила:

- До утверждения CRM коды не являются контрактом.
- Один и тот же `404` используется для foreign/not-found/unproven ownership, чтобы не создавать ID oracle.
- `501` применяется только к route-level deferred method и не используется после object lookup.
- Для `501` запрещён исходящий LPTracker-вызов.
- `400` не заменяет security denial и не используется для раскрытия существования чужого объекта.
- `X-Request-ID` содержит наш opaque ID; upstream IDs и тексты ошибок наружу не переносятся.
- Timeout/unknown write outcome, quota и service failure получают отдельные коды, которые фиксируются до реализации 1.3/3.1; они не должны переиспользовать `400/404/501`.

Альтернативы для решения CRM:

1. Реальные HTTP `400/404/501` вместо HTTP 200 — семантически привычнее, но хуже совместимость с наблюдаемой моделью LPTracker.
2. Единый `404` и для deferred — меньше информации, но CRM не отличает временно отсутствующую функцию от объекта.
3. Единый `400` — не рекомендуется: смешивает validation, authorization и release scope.

## 8. Влияние принятия 57/7

Ниже — атомарный change set, который вносится **только после письменного D01–D10** в отдельном commit той же ветки.

| Артефакт | Изменение при принятии 57/7 |
|---|---|
| Реестр | Сохранить все 64 ID/источника; отметить 57 `v1`, 7 `deferred`, ссылку на это решение и условия возврата |
| Compatibility matrix | Не менять DOC/LIVE-факты; scope status хранить отдельно от verification status |
| `docs/api/openapi.yaml` | Создать фактический черновик со всеми 64 method+path для прозрачности: 57 с success/error schemas, 7 с `x-release-status: deferred` и только согласованным `501`; не генерировать upstream calls для deferred. Файл ещё не создан и не считается результатом этого decision-ready пакета |
| `docs/api/client-contract.md` | Создать фактический черновик: таблица 57 доступных операций, отдельный список 7 deferred, ограничения C05/C06–C07/L01/L06/T02–T03 и 0.4, нейтральные errors, отсутствие обещания полной LPTracker compatibility. Файл ещё не создан |
| 2.1 | Добавить neutral denial для detail без trusted mapping и обязательную redaction T02–T03 |
| 2.2 | Заменить полный bootstrap на trusted mapping boundary, только если принят D04-A; pre-existing/out-of-band ID без mapping получает 404 без upstream read |
| 2.3 | Убрать публичный S01; не использовать account-scoped staff как membership; оставить только доказуемые справочники и redaction |
| 3.2 | Реализовать C01–C07/C09–C12; C08 получает 501; C05 cascade проверяется отдельно |
| 3.3 | Исключить L11; L01/L06 отклоняют неподтверждённые owner/observers; остальные lead operations сохраняются по scope |
| 4.2 | Реализовать только T02–T03; T01/T04/T05 получают 501; обязательны project check и nested staff redaction |
| 5.2 | Реализовать L19–L20; L22 получает 501; отдельно проверить вложения и unknown write outcome L19 |
| 7.1 | Contract/e2e coverage для 57; для семи deferred — route registration, одинаковый 501, zero upstream calls, отсутствие утечек; все 64 ID остаются в отчёте |
| Этап 0/G0 | Этап 0 может быть вынесен на G0 только после синхронизации всех артефактов и решений по retained blockers; G0 подтверждает feasibility, не implementation readiness |
| G7 | Может закрыться при 57 реализованных методах и семи проверенных neutral refusals, только если нет открытого isolation/secret/duplicate-action defect в 57 |

Если D04-B сохраняет полный bootstrap, строка 2.2 не меняется, а оценка берётся из соответствующего варианта раздела 10.

## 9. Критерии G0 и G7 при принятии предложения

### G0 — отдельная точка после 0.5

G0 не пройден этим документом. Для вынесения на G0 одновременно нужны:

1. письменные D01–D10 с именем/ролью/датой;
2. синхронные артефакты: `docs/plans/proxy-api-endpoint-inventory.md`, scope-блок `docs/api/compatibility-matrix.md`, фактический валидируемый черновик `docs/api/openapi.yaml`, фактический черновик `docs/api/client-contract.md`, обновлённые `docs/plans/proxy-api-tasks.md` и `docs/plans/proxy-api-implementation-plan.md`; два черновика сейчас отсутствуют, поэтому этот пункт ещё не выполнен;
3. принятые safe rules C05, C06–C07, L01/L06, T02–T03;
4. для P05/P07, callback и L21 выбран вариант раздела 6 и зафиксировано, что блокирует G0, а что переносится в implementation gate; для L10 до G0 либо приложено воспроизводимое доказательство `file_id → custom → lead → project`, либо письменно принят deferred L10 со scope 56/8, обновлёнными артефактами и оценкой раздела 10;
5. для каждого deferred утверждены return condition и требование `501` без upstream call; runtime-проверка этого требования остаётся задачей 7.1;
6. reviewer/архитектор отдельно ставит `G0 APPROVED`; принятие 0.5 не заменяет эту отметку.

### G7 — техническое разрешение на пилот

При 57/7 G7 может закрыться, когда:

- все 57 имеют success/error/isolation/field contract tests;
- семь deferred имеют contract tests нейтрального `501`, zero upstream calls и отсутствие data leakage;
- ни один retained blocker раздела 6 не маскируется как реализованный метод;
- OpenAPI и клиентская документация совпадают с runtime;
- открытый defect изоляции, секрета, неизвестного повтора записи или media SSRF блокирует G7 независимо от числа реализованных методов.

## 10. Условный пересчёт оценки

Исходные `50–77` человеко-дней относятся только к базе 64 и остаются действующей оценкой до решения. Они не являются оценкой 57/7.

### Если приняты 57/7 и D04-A trusted mapping

| Этап | База 64 | Предложение 57/7 | Причина изменения |
|---|---:|---:|---|
| 0 | 5–8 | 5–8 | Решения/evidence нужны независимо от scope |
| 1 | 4–6 | 4–6 | Без изменения |
| 2 | 6–10 | 5–8 | Нет публичного S01 и полного bootstrap; mapping/redaction остаются |
| 3 | 6–9 | 5–8 | Нет C08/L11; retained guards остаются |
| 4 | 6–9 | 4–6 | T01/T04/T05 deferred; остаются T02–T03, labels/settings |
| 5 | 5–8 | 4–7 | L22 deferred; files/messages/calls/media остаются |
| 6 | 6–9 | 6–9 | P05–P08/callback остаются в 57 и не упрощаются |
| 7 | 8–12 | 6–9 | 57 full tests + 7 neutral-refusal tests |
| 8 | 4–6 | 4–6 | Pilot/release без изменения |
| **Итого** | **50–77** | **43–67** | Снижение на 7–10 человеко-дней |

Ориентир: один backend-разработчик — примерно 9–14 рабочих недель. Ориентир для двух — примерно 6–10 календарных недель — допустим только если reviewer отдельно разрешил параллельное выполнение конкретных задач и принял их зависимости согласно процессу плана. При последовательной приёмке срок для двух разработчиков как ориентир не приводится и должен быть пересчитан по разрешённому графику. Это предварительная оценка, не обязательство.

### Если приняты 57/7, но сохранён полный bootstrap D04-B

Этап 2 остаётся `6–10`, итог — **44–69 человеко-дней**. Ориентир для одного разработчика — 9–14 рабочих недель. Диапазон для двух — примерно 6–10 календарных недель — применим только к отдельно разрешённым параллельным задачам; при последовательной приёмке он не является ориентиром.

### Если L10 deferred: scope 56/8

Если доказательство D07-A недоступно до G0, `L10` переносится в deferred: v1 содержит 56 операций, deferred-набор — `C08`, `S01`, `L10`, `L11`, `L22`, `T01`, `T04`, `T05`. Этап 5 предварительно меняется с `4–7` на `3–6` человеко-дней: остаются регистрация route, neutral `501` без upstream call и тест отсутствия утечки, но исключаются получение/Base64, доказательство ownership и limits/error tests L10.

- при D04-A trusted mapping итог — **42–66 человеко-дней**;
- при D04-B full bootstrap итог — **43–68 человеко-дней**.

Уверенность этой дельты низкая до доказательства остальных блокеров 0.4. Для одного разработчика календарный диапазон остаётся ориентировочно 9–14 рабочих недель из-за округления и внешних ожиданий. Срок для двух разработчиков не приводится без отдельно разрешённого перечня параллельных задач.

Все условные оценки:

- не включают ожидание provider/PM/reviewer;
- не включают возврат семи deferred в будущий release;
- предполагают, что решения раздела 6 не требуют новой provider feature, отдельного upstream-аккаунта или переработки продукта;
- пересчитываются снова, если P05/P07, callback delivery, L10 или L21 выводятся из 57 либо получают новый обязательный механизм.

## 11. Письменная фиксация решения

Каждая сторона заполняет свою строку. Ссылка на комментарий/issue допустима вместо подписи, если в ней явно перечислены D01–D10.

| Сторона | Решение | Имя/роль | Дата | Ссылка/замечания |
|---|---|---|---|---|
| Заказчик | ожидается | — | — | — |
| Самописная CRM | ожидается | — | — | — |
| Reviewer/архитектор | ожидается | — | — | — |

Шаблон:

```text
Decision 001 / Task 0.5
D01 scope: A / B / C: ...
D02 deferred return conditions: accepted / changes: ...
D03 retained safety rules: accepted / changes: ...
D04 details: A / B / C
D05 subscriptions: A / B / C
D06 callbacks: A / B / C
D07 L10: A / B
D08 L21/media: A / B / C
D09 error contract: A / B, changes: ...
D10 G0/G7/estimate: A / B, changes: ...
Name, role, date, link
```

## 12. Как применить решение

После письменного ответа в той же ветке выполняется отдельный commit:

1. заменить `PROPOSED` на `ACCEPTED` или `REJECTED`, сохранить ссылки и даты;
2. атомарно обновить inventory, matrix scope block, implementation plan, tasks, vision cross-reference и estimate; создать и согласовать фактические `docs/api/openapi.yaml` и `docs/api/client-contract.md`, не подменяя их описанием policy;
3. проверить 64 ID, принятую сумму `57 + 7 = 64` либо `56 + 8 = 64`, отсутствие пересечений и неизменность verification facts;
4. выполнить `git diff --check` и проверку ссылок;
5. передать PM на повторную проверку;
6. не начинать 1.1 и не ставить G0 до отдельного `G0 APPROVED`.

## 13. Источники решения

- [Реестр 64 и предложение 57/7](../plans/proxy-api-endpoint-inventory.md)
- [Матрица совместимости](../api/compatibility-matrix.md)
- [Изоляция сложных объектов](../api/isolation-findings.md)
- [События и медиа](../api/events-media-findings.md)
- [Основной план](../plans/proxy-api-implementation-plan.md)
- [Очередь задач и точки G0–G8](../plans/proxy-api-tasks.md)
- [Vision](../../lptracker_api_proxy_vision.md)
