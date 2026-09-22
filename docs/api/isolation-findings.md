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
