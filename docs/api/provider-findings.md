# Task 0.1 — сверка публичной API-поверхности LPTracker

**Статус:** на проверке PM/reviewer  
**Дата доступа:** 2026-09-22, Europe/Moscow  
**Матрица:** [compatibility-matrix.md](compatibility-matrix.md)

## Результат

Факт, высокая уверенность: все 64 строки актуализированного реестра имеют доступную официальную страницу на `docs.direct.lptracker.ru`; 64 ID и 64 нормализованные пары method+path уникальны. Первые 63 пары не изменены, L22 — единственное добавление. Недоступных источников нет.

Факт, высокая уверенность: найден **один дополнительный публично документированный HTTP-метод**, отсутствующий в реестре и в главном оглавлении:

- `L22 POST /max/messageStatus` — отметка о доставке/прочтении сообщений MAX; [официальный источник](https://docs.direct.lptracker.ru/max/message_status/). Страница находится в официальном sitemap и соседней навигации, описывает request/response и curl. Операция изменяющая, lead/project в запросе отсутствуют; документация утверждает, что меняются сообщения чатов владельца token. Без live-проверки нельзя доказать tenant/project isolation, фактическую область изменения и безопасную повторяемость.

Вывод, высокая уверенность: реестр актуализирован до 64 строк добавлением L22. Vision требует согласованного перечня и contract tests, но сам список не задаёт; утверждённый план v1.1 и уточнение заказчика сохраняют scope всего публично документированного API.

## Статусы 64 строк

- `DOC` — 22: method+path подтверждены документацией, специальных противоречий пути/полей не обнаружено.
- `DOC+LIVE` — 38: route подтверждён, но существенная часть request/response, ownership либо side effect не доказана документацией.
- `CONFLICT+LIVE` — 4: P02, P10, C08, C11.
- `UNAVAILABLE` — 0.

Документация не является доказательством runtime-поведения. Поэтому `DOC` означает только документальное подтверждение method+path и назначения, не готовый контракт реализации.

## Существенные противоречия и пробелы

1. **C08:** блок «Запрос» — `DELETE contact/details/{id}`, curl — `/contact/detail/{id}`. Факт документации; правильный runtime route неизвестен. Уверенность высокая.
2. **C11:** блок «Запрос» — `PUT /contact/{contact_id}/details/{field_id}`, curl и PHP-SDK — `/contact/{contact_id}/field/{field_id}`. Факт документации; правильный runtime route неизвестен. Уверенность высокая.
3. **P02:** блок «Запрос» содержит завершающий `/`, curl — без него. Нужна проверка нормализации upstream. Уверенность высокая в наличии расхождения, низкая в его runtime-значимости.
4. **P10:** блок запроса объявляет `PUT`, но curl не задаёт `-X PUT` и не передаёт body, то есть фактически показывает GET. Уверенность высокая.
5. **C04:** JSON-тело показывает `field`, curl — `fields`; semantics `clear_contacts`, отсутствующего и пустого значения не подтверждены runtime. Уверенность высокая.
6. **C07:** body/curl используют `value`, описание поля ошибочно говорит `volume`. Уверенность высокая.
7. **M01/M03:** body/curl используют `title`, описание и модель — `name`; ответ возвращает `name`. Уверенность высокая.
8. **T01/T04:** body и ответы используют `title`/`text`, описание — `task_title`/`task_text`; curl дополнительно передаёт неописанный `contact_id`. Уверенность высокая.
9. **M02 и оболочки списков:** M02 показывает внешний массив вокруг `{status,result}`; L05 и T03 показывают raw array; большинство других списков — `{status:"success",result:[...]}`. Это противоречит общей странице, утверждающей, что каждый ответ имеет `status`. Все формы требуют live contract tests. Уверенность высокая.
10. **L02 и глобальная оболочка:** L02 показывает сырой объект успеха без `status`, хотя глобальная страница требует `status` у каждого ответа. Нужна live-проверка. Уверенность высокая.
11. **SDK против HTTP для всех контактов:** PHP-SDK говорит, что пустой `searchOptions` возвращает все контакты проекта; HTTP-страница C01 требует хотя бы один из `phone`, `email`, `max_bot_id`, `max_user_id`. Документация не показывает отдельного HTTP list route. Уверенность высокая в расхождении, низкая в фактической возможности полного HTTP-перечисления.
12. **C01 расширен полями MAX:** текущая HTTP-страница документирует `max_bot_id` и `max_user_id`, которых нет в кратком описании исходного реестра. Это не новая операция, но контракт полей должен быть расширен. Уверенность высокая.

## Ownership и live-зависимости

Следующие утверждения нельзя подтвердить без тестового аккаунта и безопасных синтетических данных:

- какой путь реально работает для C08 и C11; имеет ли значение slash в P02;
- `field` или `fields`, `title` или `name`, task field names, реальные оболочки списков и нестандартная форма L02;
- работает ли HTTP-поиск всех контактов без фильтра и можно ли им выполнить bootstrap detail ownership;
- связь contact detail с родительским контактом/проектом: ответы C06–C08 её не содержат;
- project membership у `GET /staff`, `owner=0`, observers/visibility/уведомления и вложенные профили;
- membership метки при M03/M04: route и модель ответа не содержат project_id;
- каскады удаления, side effects автоворонки/воронки, звонка, платежа, callback и уведомлений;
- реальные request limits, null/empty/omitted semantics, типы дат/ID/сумм, max file size/MIME;
- гарантии, подпись, порядок и повтор webhook; документация описывает payload, но не delivery contract;
- фактическая область `POST /max/messageStatus` и поведение повторной отметки;
- фактические HTTP status/body для ошибок и rate limit: глобальные страницы противоречат друг другу (`все HTTP 200` и `503 при превышении`).

Ни один из этих пунктов не требует перепроектирования на 0.1. Они являются входом для 0.2–0.4 и финальной фиксации контракта 0.5.

## Просмотренные источники и границы полноты

Просмотрены:

- [главное оглавление](https://docs.direct.lptracker.ru/);
- официальный `https://docs.direct.lptracker.ru/sitemap.xml`;
- 63 уникальные operation-страницы, являющиеся источниками 64 строк реестра — полный воспроизводимый перечень находится в колонке Source матрицы;
- страницы [models](https://docs.direct.lptracker.ru/common/models/), [errors](https://docs.direct.lptracker.ru/common/errors/), [common](https://docs.direct.lptracker.ru/common/common/), [lead webhook](https://docs.direct.lptracker.ru/common/webhook/), [CRM webhook](https://docs.direct.lptracker.ru/common/project_webhook/);
- [PHP-SDK methods](https://docs.direct.lptracker.ru/phpsdk/methods/) и [PHP-SDK info](https://docs.direct.lptracker.ru/phpsdk/info/);
- страницы вне главного оглавления, найденные через sitemap/навигацию: L20, L21 и L22.

Ограничение полноты: вывод распространяется только на перечисленные официальные страницы и доступный на дату проверки sitemap/навигацию. Поиск по официальному домену использовался как дополнительный способ обнаружения; он не доказывает отсутствие неиндексированных, внутренних, старых или будущих методов. Внутренние web-endpoints LPTracker не исследовались.

Отдельный факт: sitemap использует `my.lptracker.ru` в `<loc>` и не перечисляет часть страниц, присутствующих в главном оглавлении (callback, widget, funnel/autofunnel); поэтому sitemap не использовался как единственный источник полноты.

## Воспроизводимость

Проверка исходного реестра:

```bash
rg -c '^\| [A-Z][0-9]{2} \|' docs/plans/proxy-api-endpoint-inventory.md
rg '^\| [A-Z][0-9]{2} \|' docs/plans/proxy-api-endpoint-inventory.md \
  | sed -E 's/^\| ([A-Z][0-9]{2}) \| `([^`]+)`.*/\1\t\2/' \
  | sort -u | wc -l
```

Ожидаемый результат обеих проверок для актуализированного реестра: `64`.

Проверка артефакта:

```bash
rg -c '^\| [A-Z][0-9]{2} \|' docs/api/compatibility-matrix.md
rg '^\| [A-Z][0-9]{2} \|' docs/api/compatibility-matrix.md \
  | sed -E 's/^\| ([A-Z][0-9]{2}) \|.*\| (GET|POST|PUT|PATCH|DELETE) \| `([^`]+)`.*/\1\t\2 \3/' \
  | sort -u | wc -l
rg -o 'https?://[^)] ]+' docs/api/compatibility-matrix.md \
  | rg -v '^https://docs\.direct\.lptracker\.ru/'
```

Ожидается: `64`, `64`, затем пустой вывод. HTTP-доступность operation-страниц проверялась чтением; все 63 уникальных источника были доступны. При параллельной проверке в восемь потоков несколько страниц дали транспортный timeout, но при последовательном чтении и через web-клиент открылись; это не интерпретируется как поведение LPTracker API.

## Статус передачи на повторную проверку

Материалы задачи 0.1 переданы PM/reviewer на повторную проверку. Исследовательский отчёт покрывает 64 строки, L22 включён в authoritative registry и contract-test scope, результаты перенесены в `docs/api/compatibility-matrix.md`. Решение о принятии задачи и разрешении начать 0.2 принимает назначенный reviewer; до его письменной отметки следующая задача не начинается.


## Live update 0.2 — 2026-09-22

[Подробный протокол](connection-quota-findings.md) зафиксировал девять последовательных HTTPS-запросов к `direct.lptracker.ru` без business writes.

Подтверждено runtime для ограниченного окна: один login success и token; authenticated `GET /projects`; чтение обоих разрешённых тестовых проектов по пути без trailing slash; logout success; отозванный token отклонён. Unauthenticated, synthetic invalid token, revoked token и неизвестный route вернули HTTP 200, валидный JSON `status=error` и safe codes 401/404. Redirect и HTML не наблюдались.

Это не подтверждает весь request/response-контракт A01/A02/P01/P02. Quota scope, лимит 3 req/s, IP-ограничение, repeated login, token expiry, invalid-password/lockout и альтернативный host не проверялись из-за нетестового аккаунта и консервативного request budget.

## Live update 0.3 — 2026-09-22

[Подробный протокол](isolation-findings.md) фиксирует частичный live-run из 15 последовательных запросов в двух разрешённых тестовых проектах. Созданы два synthetic contact с двумя details; C03 подтвердил project/parent mapping, C06 не вернул parent/project, C07 edit и readback успешны. C01 без фильтра кроме `project_id` вернул application error 400 для обоих проектов.

Plural C08 `DELETE /contact/details/{detail_id}` вернул HTTP 200 + JSON error code 400. Singular curl path не проверялся; post-error detail state отдельным GET не подтверждён. Оба probe parent contacts затем удалены с JSON success и token отозван, но каскад details отдельно не читался.

STOP до `GET /staff` и labels означает, что staff, owner=0, observers, labels и transfer не получили новых live-фактов. Task writes намеренно не выполнялись: без safe test staff ID документация не гарантирует отсутствие notifications реальному owner.

Предлагаемый статус — **ARCHITECT REVIEW REQUIRED / BLOCKED dependent subset**. Для existing/out-of-band detail по одному detail ID безопасный bootstrap ownership не доказан; staff/labels/tasks требуют отдельного решения и разрешённого продолжения. Это предложение, не само-приёмка.
## Live update 0.3 rework-1 — 2026-09-22

[Дополнение протокола](isolation-findings.md#rework-1--singular-c08-staff-schema-и-labels) фиксирует 23 последовательных запроса без STOP и unknown writes.

Fresh mapped detail: singular `DELETE /contact/detail/{detail_id}` вернул HTTP 200 + JSON error 404; C06/C03 readback подтвердил, что detail и parent сохранились и mapping не изменилась. Вместе с ранее наблюдавшимся plural error 400 это не даёт рабочего C08 route; другие URL не проверялись.

`GET /staff` вернул один account-scoped entry без project-membership fields. Сохранены только count, schema keys/classes и booleans sensitive-field presence; PII/IDs не сохранялись. Zero-ID entry присутствовал, но `owner=0` semantics и assignment не тестировались.

M01–M04 подтверждены на двух новых labels: project lists доказали own membership и отсутствие probe другого проекта; edit readback успешен; delete readback подтвердил отсутствие; cleanup complete. M02 runtime envelope был object, не внешний array документационного примера.

Task 0.3 не объявляется завершённой: task/owner/observer subset требует safe staff fixture, а C08/provider bootstrap остаются блокерами. Предлагается повторный PM/architect review без перехода к 0.4.

## Architecture scope proposal — 2026-09-23

После этих наблюдений архитектор предложил для PM/заказчика v1 из 57 операций и deferred-набор из 7: `C08`, `S01`, `L11`, `L22`, `T01`, `T04`, `T05`. Это отдельное решение-кандидат: оно не меняет документальные источники, verification status или live-факты выше и не означает принятие 0.3/G0.

Условия возврата deferred и ограничения C05, C06–C07, L01/L06, T02–T03 описаны в [реестре](../plans/proxy-api-endpoint-inventory.md#предложение-по-объёму-v1--на-review-pmзаказчика). Коды `400/404/501` остаются предметом согласования с CRM в 0.5.
