# Task 0.3 — проверка изоляции сложных объектов

**Статус:** передано на PM/architect review; зависимые subsets заблокированы  
**Live-окно:** 2026-09-22 10:35:13–10:35:31, Europe/Moscow  
**Upstream:** `https://direct.lptracker.ru`  
**Связано с:** [GitHub issue #4](https://github.com/FrozzIt/API.Botzi/issues/4)

## Границы и безопасность

Факт: выполнен один последовательный live-run в двух разрешённых тестовых проектах общего нетестового аккаунта. Secret-файл имел mode `600` и ровно шесть ожидаемых ключей. Login выполнен один раз; token находился только в памяти и был отозван одним logout. Полные ответы, credentials, token, project/object IDs и фактический marker не логировались.

Все созданные сущности имели уникальный marker вида `<probe-marker>` и синтетические адреса в зарезервированном домене `example.invalid`. Существующие объекты не изменялись. Calls, messages, webhooks, files, notifications и реальные staff assignments не выполнялись. Временный probe и bytecode удалены; рядом с secret-файлом остался только исходный mode-600 secret.

Request budget: не более 80; фактически **15**. Запросы выполнялись строго последовательно, с паузой не менее 0,8 секунды и timeout 12 секунд. Redirect, HTML/non-JSON, auth loss и quota error не наблюдались.

## Последовательность запросов

Все ответы имели HTTP 200 и валидный JSON.

| Seq | Сценарий | Top-level | Safe code/class | Результат |
|---:|---|---|---|---|
| 1 | один `POST /login` | success | — | token получен в память |
| 2–3 | C01 no-filter search, project A/B | error | 400 / validation-or-client | без phone/email/MAX-фильтра поиск отклонён в обоих проектах |
| 4–5 | C02 create contact A/B | success | — | два probe contact и два detail созданы |
| 6–7 | C03 parent contact A/B | success | — | `project_id` совпал; созданный detail присутствовал в `details` родителя |
| 8–9 | C06 direct detail A/B | success | — | ID совпал; parent/project поля в result отсутствовали |
| 10 | C07 edit detail A | success | — | `value` принят; response ID/value совпали |
| 11 | C06 readback detail A | success | — | новое значение подтверждено |
| 12 | C08 `DELETE /contact/details/{detail_id}` | error | 400 / validation-or-client | сработал STOP; singular-вариант не пробовался |
| 13–14 | C05 cleanup contact A/B | success | — | оба созданных родителя удалены |
| 15 | один `POST /logout` | success | — | token отозван |

Observed latency в этом коротком окне: 952–1483 ms. Это не performance test и не SLA.

## Ownership verdicts

### Contact details C01/C03/C06–C08

- **Факт, высокая уверенность:** для detail, созданного вместе с новым contact, безопасная привязка получается из C02/C03: родитель вернул ожидаемый `project_id` и detail ID. Proxy может сохранить этот индекс при известном происхождении объекта.
- **Факт, высокая уверенность:** direct C06 result для обоих probe details содержал ID/type/data, но не содержал parent/contact/project field. Один detail ID сам по себе не доказывает project ownership.
- **Факт, высокая уверенность:** C07 plural path с body `value` реально изменил probe detail; readback подтвердил значение.
- **Факт, высокая уверенность:** C01 с одним `project_id` и без contact-фильтра вернул application error 400 в обоих проектах. Следовательно, такой HTTP-запрос не даёт bootstrap полного contact/detail index.
- **Факт, высокая уверенность:** документальный plural C08 path вернул HTTP 200 + JSON `status=error`, code 400. Singular curl path `/contact/detail/{id}` не пробовался после STOP.
- **Ограничение, высокая уверенность:** после ошибки C08 отдельный GET этого detail не выполнялся; поэтому его сохранность сразу после error 400 не доказана чтением. Два parent contact затем были удалены с `status=success`, но каскадное удаление details отдельным GET не подтверждалось. Ledger считает details закрытыми через успешное удаление их единственных probe parents; наличие/отсутствие orphan detail остаётся неподтверждённым runtime.
- **Вывод, высокая уверенность:** bootstrap ownership для существующего до proxy или созданного вне proxy detail, если известен только detail ID, не доказан. До architect decision C06–C08 должны быть заблокированы для такого объекта либо допущены только при наличии ранее сохранённой и актуализированной parent mapping.

### Staff, owner=0 и observers

Subset не достигнут: STOP произошёл до `GET /staff`. Project membership fields, nested sensitive field presence и наличие synthetic/zero staff runtime не проверены. Positive owner/observer scenario не выполнялся: безопасный test staff ID отсутствует. `owner=0` не тестировался, поскольку probe lead не требовался после исключения task-write subset.

### Labels M01–M04

Subset не достигнут: STOP произошёл до label writes. Ни одна label не создавалась. Membership через project list, отсутствие project guard в ID routes и cross-project semantics остаются неподтверждёнными.

### Tasks T01–T05 и transfer

Subset намеренно не запускался. Официальная документация требует реальный `owner_id`; `remind_type=0` отключает reminder, но не гарантирует отсутствие иных уведомлений владельцу при создании/редактировании. Без безопасного test staff ID запрет на реальные notifications нельзя доказуемо соблюсти. Поэтому task visibility, observers, nested profiles и межпроектный transfer остаются заблокированными, а probe lead не создавался.

## Cleanup ledger

| Тип | Создано | Cleanup success | Осталось |
|---|---:|---:|---:|
| contacts | 2 | 2 parent DELETE success | 0 известных активных contacts |
| contact details | 2 | 2 закрыты через parent DELETE success | 0 parent-attached; orphan state не проверен |
| labels | 0 | 0 | 0 |
| tasks | 0 | 0 | 0 |
| leads/deals | 0 | 0 | 0 |

Unknown/ambiguous write outcomes: **0**. C08 вернул однозначный JSON error, но post-error state detail не читался. Cleanup contact DELETE A/B и logout оба завершились JSON success. Никаких повторов write-запросов не выполнялось.

## STOP и влияние на план

STOP reason: `DELETE /contact/details/{detail_id}` вернул документально неразрешённый application error 400 вместо success/404. Консервативный процесс не стал угадывать конфликтующий singular path и перешёл к cleanup/logout. Это остановило staff и labels subsets; повторный login не выполнялся.

Предлагаемый статус задачи: **ARCHITECT REVIEW REQUIRED / BLOCKED dependent subset**. Это предложение, не само-приёмка. Нужны решения по двум границам:

1. допустима ли C06–C08 только для details с proxy-maintained parent mapping, и как обрабатывать уже существующие/out-of-band details без полного HTTP bootstrap;
2. нужен ли отдельный безопасный staff fixture/notification sandbox для owner/observer/tasks, а также новый ограниченный запуск labels/staff после принятия STOP-результата.

Issue #4 не должен закрываться этим PR: acceptance criteria для staff, labels и tasks не достигнуты.
## Rework 1 — singular C08, staff schema и labels

**Live-окно:** 2026-09-22 11:05:25–11:05:49, Europe/Moscow  
**Архитектурное основание:** [issue #4, decision 5773075314](https://github.com/FrozzIt/API.Botzi/issues/4#issuecomment-5773075314)

### Границы запуска

Выполнены только три разрешённых независимых subset: один fresh singular C08 probe, один read-only `GET /staff`, M01–M04 для новых labels в двух тестовых проектах. Task/owner/observer, calls, messages, webhooks, files, notifications, существующие объекты и cross-project mutations не выполнялись.

Secret preflight подтвердил regular file текущего владельца, mode `600` и ровно шесть ожидаемых ключей. Один login и один logout; token хранился только в памяти. Запросы последовательные, пауза не менее 0,8 секунды, timeout 12 секунд, фактически **23 из 40**. Все ответы: HTTP 200, валидный JSON, без redirect/HTML. Фактический marker заменён на `<probe-marker>`; credentials, token, project/object/staff IDs и PII не логировались.

### Redacted request sequence

| Seq | Сценарий | Top-level | Safe error | Результат |
|---:|---|---|---|---|
| 1 | login | success | — | token получен в память |
| 2 | C02 create fresh contact/detail, project A | success | — | один disposable parent/detail создан |
| 3 | C03 mapping before singular | success | — | parent project совпал, detail присутствовал |
| 4 | singular C08 `DELETE /contact/detail/{detail_id}` | error | 404 / not-found | ровно одна попытка, без retry и plural call |
| 5 | C06 detail readback | success | — | detail существует |
| 6 | C03 parent readback | success | — | parent существует, project совпал, detail присутствует |
| 7 | C05 cleanup parent | success | — | disposable parent удалён |
| 8 | S01 `GET /staff` | success | — | schema-only анализ, значения не сохранялись |
| 9–10 | M02 lists A/B before create | success | — | count=0/0, object envelope |
| 11–12 | M01 create label A/B | success | — | по одной новой label в каждом проекте |
| 13–14 | M02 lists A/B after create | success | — | own=true, other=false, count=1/1 |
| 15–16 | M03 edit own label A/B | success | — | response IDs совпали внутри процесса |
| 17–18 | M02 lists A/B after edit | success | — | own present/name matched, other=false |
| 19–20 | M04 delete own label A/B | success | — | обе удаления подтверждены |
| 21–22 | M02 lists A/B after delete | success | — | обе probe labels отсутствуют в обоих списках |
| 23 | logout | success | — | token отозван |

Observed latency 898–1624 ms; это не performance test и не SLA. STOP condition: **нет**. Unknown/ambiguous write outcomes: **0**.

### C08 verdict

- **Факт, высокая уверенность:** trusted mapping свежего detail была доказана C02/C03 до удаления.
- **Факт, высокая уверенность:** единственный singular `DELETE /contact/detail/{detail_id}` вернул HTTP 200 + JSON `status=error`, code 404.
- **Факт, высокая уверенность:** последующие C06 и C03 подтвердили, что detail и parent сохранились, detail оставался в `details` того же project-matched parent. Следовательно, singular route в проверенном окне не удалил свежий detail.
- **Факт, высокая уверенность по двум probe:** plural route ранее дал code 400, singular route дал code 404. Другие URL не подбирались.
- **Архитектурное ограничение сохраняется:** C06–C08 допустимы только при доверенной mapping `detail_id → contact_id → project_id`; pre-existing/out-of-band detail по одному ID получает нейтральный отказ. Рабочий delete route runtime не найден; требуется официальный ответ поставщика.

### Staff verdict

`GET /staff` выполнен в единственной документированной account-scoped форме без project parameter. Result содержал **1** entry. Top-level entry schema keys: `created_at`, `id`, `job`, `last_login_at`, `name`, `type`; classes соответственно integer/string, без сохранения значений.

Project-membership fields `project_id`, `project_ids`, `projects`, `last_project_id` отсутствовали; значения обоих разрешённых projects не наблюдались. Наличие sensitive fields: `name=true`, `job=true`, `email=false`, `phone=false`, `avatar=false`. Entry с `id=0` присутствовал — только boolean-факт; ID других entries не сохранялись.

**Вывод, высокая уверенность:** endpoint account-scoped и не даёт доказуемого project membership. Positive owner/observer assignment не выполнялся. Наличие zero-ID entry не доказывает semantics `owner=0`; task subset остаётся заблокированным до безопасной staff fixture.

### Labels verdict

- **Факт, высокая уверенность:** M01 с документированным `title`, `color`, `project_id` создал по одной новой label в каждом тестовом проекте.
- **Факт, высокая уверенность:** M02 runtime вернул object envelope, а не внешний array из документационного примера.
- **Факт, высокая уверенность:** project-scoped lists до/после create/edit/delete доказали membership: own probe присутствовал только в своём проекте; probe другого проекта отсутствовал.
- **Факт, высокая уверенность:** M03 ID-route успешно изменил только предварительно подтверждённую через M02 own label; list readback подтвердил новое name и неизменную project membership.
- **Факт, высокая уверенность:** M04 удалил обе own labels; последующие project lists подтвердили отсутствие обеих. Cross-project mutation не выполнялась и не требуется для безопасного proxy rule: перед M03/M04 label ID сверяется через M02 разрешённого проекта.

### Cleanup ledger rework-1

| Тип | Создано | Cleanup | Осталось |
|---|---:|---:|---:|
| contact parent | 1 | 1 DELETE success | 0 известных active parents |
| contact detail | 1 | 1 закрыт через parent DELETE | 0 parent-attached; cascade отдельно не читался |
| labels | 2 | 2 DELETE success + list absence | 0 |
| tasks/leads | 0 | 0 | 0 |

Временный probe удалён; в secret-каталоге остался только исходный mode-600 файл.

### Статус передачи

Три разрешённых subset выполнены в согласованном объёме. Предлагаемый статус — **PM/architect review; Task 0.3 остаётся частично заблокированной**. Issue #4 не закрывается: task/owner/observer runtime требует безопасной staff fixture, а provider должен официально подтвердить project-scoped details bootstrap, перенос details между parents и рабочий delete route. Переход к 0.4 и service implementation этим отчётом не разрешается.

## Архитектурное предложение после live-наблюдений — на review

Это решение-кандидат не меняет факты и ledgers выше. Предлагается v1 из 57 операций и deferred-набор `C08`, `S01`, `L11`, `L22`, `T01`, `T04`, `T05`. Условия возврата каждого ID зафиксированы в [реестре](../plans/proxy-api-endpoint-inventory.md#предложение-по-объёму-v1--на-review-pmзаказчика).

Для оставшихся операций граница доступа предлагается следующая: C05 — project precheck без утверждения каскадов; C06–C07 — только trusted mapping и повторная parent check; L01/L06 — без неподтверждённых owner/observers; T02–T03 — project check и redaction вложенных staff fields. Detail, известный только по ID, получает нейтральный отказ. Точный внешний код (`400`, `404` или `501`) остаётся вопросом CRM-контракта в 0.5.

Предложение не означает принятие 0.3 или G0 и не разрешает переход к 0.4 до решения PM.
