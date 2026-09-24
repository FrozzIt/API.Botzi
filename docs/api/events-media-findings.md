# Task 0.4 — события, подписки и безопасная выдача медиа

**Дата документальной проверки:** 2026-09-24, Europe/Moscow

**Статус:** передано на PM review; документальная часть выполнена, live subsets не запускались

**Объём:** P05–P08, две callback-схемы, L10 и L21. Продуктовый scope 57/7 не изменяется и остаётся решением 0.5.

## 1. Границы проверки и live-журнал

Проверены официальные страницы P05–P08, схемы callback лида и структуры CRM, L10, L21, а также связанные P03/P11. Ниже `DOC` означает факт официальной документации, `LIVE` — результат авторизованного запроса к LPTracker.

**Live API calls: 0.** Защищённый credential-файл текущему окружению недоступен; контролируемый receiver и заранее разрешённые file/call fixtures не переданы. Поэтому нельзя было сначала безопасно прочитать существующие подписки P06/P08, доказать, что запись P05/P07 затронет только нашу регистрацию, либо проверить media без риска утечки исходного URL/PII. P05/P07, callback delivery, L10/L21 и загрузка media не выполнялись. Звонки и уведомления не инициировались.

Следствие, высокая уверенность: существующие подписки не изменялись — ни одного upstream write не было. Официальные страницы читались как документация; это не live-проверка API.

## 2. Callback лида

Источник схемы: [структура callback лида](https://docs.direct.lptracker.ru/common/webhook/). Документация перечисляет поля, но не приводит полный HTTP request и не описывает метод, `Content-Type`, заголовки аутентификации, подпись или delivery semantics. Пример ниже составлен из документированных полей и обезличен; это не сохранённый runtime payload.

```json
{
  "id": "<lead-id>",
  "contact_id": "<contact-id>",
  "detail_id": "<detail-id>",
  "project_id": "<project-id>",
  "created_at": 1700000000,
  "deal": 0,
  "local_time": "<local-time>",
  "capture_type": "<capture-type>",
  "owner_id": "<owner-id>",
  "stage_id": "<stage-id>",
  "name": "<lead-name>",
  "form": "<form-name>",
  "author": {"id": "<author-id>", "type": 1, "name": "<redacted>", "job": "<redacted>"},
  "custom": [{"id": "<field-id>", "name": "<field-name>", "type": "text", "value": "<redacted>"}],
  "view": {"id": "<view-id>", "project_id": "<project-id>", "source": "<source>"},
  "contact": {
    "id": "<contact-id>",
    "project_id": "<project-id>",
    "contacts": [{"id": "<detail-id>", "type": "email", "data": "<redacted>"}]
  },
  "owner": {"id": "<owner-id>", "type": 1, "last_project": "<project-id>", "name": "<redacted>", "job": "<redacted>"},
  "stage": {"id": "<stage-id>", "name": "<stage-name>"},
  "payments": [{"sum": "<amount>", "purpose_id": "<purpose-id>", "purpose_name": "<redacted>", "category_id": "<category-id>", "category_name": "<redacted>"}],
  "calls_records": [{"time": 1700000000, "linkedid": "<call-id>", "duration": 60, "record": "<redacted-upstream-media-url>", "type": "incomming", "owner_id": "<owner-id>"}],
  "action": "update",
  "action_timestamp": 1700000000000,
  "action_update_fields": "stage.id"
}
```

### Источник и статус полей

| Поля | Официальный источник | Статус | Ограничение |
|---|---|---|---|
| `id`, `contact_id`, `detail_id`, `project_id`, `created_at`, `deal`, `local_time`, `capture_type`, `owner_id`, `stage_id`, `name`, `form` | callback лида | DOC; не LIVE | Типы/nullability и наличие по каждому action не заданы полностью |
| `author.{id,type,name,job}` | callback лида | DOC; не LIVE | Содержит account/staff data; перед выдачей требуется очистка |
| `custom[].{id,name,type,value}` | callback лида | DOC; не LIVE | Для `conv_owner`, `map`, `cats` показаны отдельные формы; полный union не задан |
| `view.{id,project_id,source,campaign,keyword,address,click_time,platform,browser,ip,referrer,lead_from,ym_client_id,ga_client_id}` | callback лида | DOC; не LIVE | Содержит идентификаторы и telemetry/PII; выдача только по согласованной схеме |
| `contact.{id,name,profession,site,created_at,project_id,contacts[].{id,type,data}}` | callback лида | DOC; не LIVE | Contact detail может содержать PII; вложенный `project_id` требует сверки |
| `owner.{id,type,last_project,name,job}` | callback лида | DOC; не LIVE | `last_project` не доказывает текущее членство; служебный профиль не выдаётся сырым |
| `stage.{id,name}` | callback лида | DOC; не LIVE | Принадлежность шага проверяется по P11 разрешённого проекта |
| `payments[].{sum,purpose_id,purpose_name,category_id,category_name}` | callback лида | DOC; не LIVE | Полнота и тип `sum` runtime не подтверждены |
| `calls_records[].{time,linkedid,duration,record,type,owner_id}` | callback лида | DOC; не LIVE | `record` — исходный media URL; наружу не выдаётся, применяется media-контракт ниже |
| `action`, `action_timestamp`, `action_update_fields` | callback лида | DOC; не LIVE | Документированы `create/update/delete`; payload удаления и стабильный event ID не описаны |

## 3. Callback структуры CRM

Источник схемы: [структура callback CRM](https://docs.direct.lptracker.ru/common/project_webhook/). `data` для `custom` ссылается на [P03](https://docs.direct.lptracker.ru/project/custom/), для `funnel` — на [P11](https://docs.direct.lptracker.ru/funnel/list/). Пример обезличен и синтезирован из документации.

```json
{
  "project_id": "<project-id>",
  "type": "custom",
  "data": [
    {"id": "<custom-id>", "name": "<field-name>", "type": "text"}
  ],
  "action": "update",
  "action_timestamp": 1700000000000
}
```

| Поля | Официальный источник | Статус | Ограничение |
|---|---|---|---|
| `project_id`, `type`, `data`, `action`, `action_timestamp` | callback CRM | DOC; не LIVE | Документированы `type=custom|funnel` и `action=create|update|delete`; delete shape не задан |
| `data[].{id,name,type}` при `type=custom` | P03 | DOC; не LIVE | Полный набор field types и payload конкретного action не формализованы callback-страницей |
| `data[].{id,name,notify,in_leads,in_deals}` при `type=funnel` | P11 | DOC; не LIVE | В описании P11 есть опечатка `id_deals`, тогда как пример использует `in_deals` |

## 4. Подтверждение источника и проекта

### Подтверждённые факты

- Callback-страницы документируют payload, но не подпись, MAC, сертификат клиента, allowlist адресов, стабильный upstream event ID или другой криптографический proof. `LIVE`: нет.
- P05/P07 принимают `url` и необязательный `name`; документация допускает несколько webhook и говорит, что пустой `url` отключает callback. Семантика выбора конкретной регистрации не описана.
- Случайный секрет в receiver URL снижает вероятность случайного вызова, но не доказывает отправителя и может утечь через логи/прокси. Он не считается аутентификацией события.

### Обязательная проверка

1. Receiver выбирается только по server-side registration; `project_id` из payload не выбирает tenant и обязан совпасть с ожидаемым проектом.
2. Для `create/update` callback лида событие используется как сигнал: авторизованно прочитать lead, проверить вложенный contact/project и только затем принять данные. Связанные stage/custom/view/staff IDs проверяются отдельно.
3. Для `update` структуры CRM авторизованно прочитать P03 либо P11 разрешённого проекта и сверить актуальное состояние затронутого ID/type.
4. Для `delete` payload сам по себе недостаточен. Нужны ранее сохранённая доказанная mapping объекта к проекту и проверяемая readback/reconciliation отсутствия в разрешённом проекте. Если API не позволяет отличить удаление от недоступности/ошибки или определить удалённый объект, событие не применяется как доказанный факт.
5. При любом расхождении или недоказанной принадлежности событие сохраняется как quarantined/unverified, в CRM не доставляется, данные/медиа не выдаются.

### Блокер

До официального или live-подтверждения способа аутентификации нельзя обещать подлинность upstream callback. Допустимая архитектурная альтернатива — считать callback недоверенным сигналом и подтверждать каждое изменение авторизованным readback; для неподтверждаемого delete это остаётся блокером 0.5/G0.

## 5. P05–P08 и сохранность существующих подписок

| ID | DOC-факт | LIVE | Решение/блокер |
|---|---|---|---|
| P05 | `PUT /project/{project_id}/callback-url`, body `url` + optional `name`, success envelope; пустой `url` отключает callback | Не выполнялся | Нет callback ID в write-route/body; add/update/replace и область empty-url не доказаны |
| P06 | `GET /project/{project_id}/callback-url` возвращает `result[]` с `id`, `name`, `url` | Не выполнялся | Документация показывает upstream URL; публично отдавать можно только локальную клиентскую настройку |
| P07 | `PUT /project/{project_id}/project-callback-url`, та же форма для CRM structure callback | Не выполнялся | Те же неразрешённые semantics адресной мутации |
| P08 | `GET /project/{project_id}/project-callback-url` возвращает `result[]` с `id`, `name`, `url` | Не выполнялся | Ownership конкретной регистрации по ответу не доказан |

Без live read P06/P08 нельзя подтвердить текущее состояние. Даже после read официальная запись P05/P07 не принимает `id`, поэтому документация не доказывает, что можно изменить/отключить только нашу строку. До ответа поставщика или безопасного live-протокола с пустым тестовым проектом upstream write запрещён.

Предложение для будущей реализации: хранить клиентские подписки локально; upstream регистрировать только наш receiver после проверки проекта и отсутствия конфликтующего состояния. Перед каждым write делать P06/P08, сопоставлять только созданную нами регистрацию по сохранённому upstream ID/marker и после write проверять полный список. Если адресная мутация не доказана, P05/P07 остаются заблокированными. Это предложение, не runtime-факт.

## 6. L10 — файл лида

Официальная [страница L10](https://docs.direct.lptracker.ru/lead/file_get/) документирует JSON `{status,result:{id,name,ext,data}}`, где `data` — Base64. Прямой media URL, redirect, headers и Range для L10 не описаны.

Проверяемая цепочка перед выдачей:

1. Авторизованно прочитать `lead_id` и доказать его проект через вложенный contact.
2. Проверить `custom_id` по P03 разрешённого проекта и допустимый file-field type.
3. Выполнить L10 только с уже проверенными `lead_id/custom_id/file_id`; ответ обязан иметь ожидаемую схему и совпадающий `id`.
4. Проверить allowlist расширения/MIME, безопасное имя, лимиты encoded/decoded size и Base64 до возврата через ProxyAPI.
5. Не принимать от клиента внешний URL и не отдавать upstream headers/body при schema drift.

`DOC`: форма ответа и Base64. `LIVE`: нет. Блокеры 0.5/G0: фактические MIME/размеры, error contract, возможность подмены `custom_id/file_id`, потребление памяти и наличие file ID в parent-read модели не подтверждены.

## 7. L21 и media endpoint записей

Официальная [страница L21](https://docs.direct.lptracker.ru/lead/calls_list/) документирует список звонков за текущее и предыдущее полугодие. Элемент содержит `linkedid`, `time`, `duration`, `type`, `disposition`, nullable `record`, `owner_id` и nullable `owner`. Пример показывает, что `record` является URL поставщика, а `owner` может содержать PII. Фактический runtime host, redirects, response headers, MIME, Content-Length и Range не проверены.

Проверяемая цепочка:

1. До L21 авторизованно прочитать lead и доказать проект через contact.
2. Отфильтровать/очистить owner fields; `last_project`/account staff не использовать как доказательство membership.
3. Не отдавать `record` клиенту. Сохранить server-side binding `{provider_account, client, project, lead, linkedid, upstream_record_ref, expiry}` и вернуть непрозрачный URL нашего домена.
4. При media GET повторно проверить client session, config revision, project/lead ownership, expiry/revocation и связь `linkedid` с L21 разрешённого lead.
5. Получать только сохранённый server-side URL с `https` и после live-проверки точного allowlist host. Клиентский URL не принимается. Redirect по умолчанию запрещён; каждый разрешённый переход требует отдельной проверки схемы/host/IP и лимита hops.
6. Наружу разрешены только сформированные нами `Content-Type`, `Content-Length`, `Content-Disposition`, `Accept-Ranges`/`Content-Range`, если их semantics доказаны. `Location`, cookies, server/debug headers и upstream URL не проксируются.
7. Range не обещается до runtime-проверки. Если upstream Range отсутствует, допустимый вариант — ограниченно получить запись в контролируемое хранилище и обслуживать Range оттуда; это отдельное предложение с size/concurrency/retention limits.

Те же правила применяются к `calls_records[].record` в callback лида. `DOC`: наличие record URL и связь `linkedid` между L21/callback. `LIVE`: нет. До проверки host/redirect/headers/Range media endpoint имеет только proposed contract и блокирует выпуск L21/callback media в G0.

## 8. Неподтверждённые гарантии и вопросы 0.5/G0

1. Как LPTracker аутентифицирует callback: подпись/headers/mTLS/allowlist не документированы и не проверены.
2. Каков HTTP method/content type, timeout/ack contract, retry/backoff, порядок, максимальный размер, стабильный event ID и duplicate behavior — не установлено. Нельзя обещать подпись upstream, «ровно один раз», порядок или гарантированный срок доставки.
3. Как безопасно подтвердить delete для lead/custom/funnel, если readback отсутствующего объекта неоднозначен.
4. Можно ли P05/P07 адресно создать, изменить и отключить только нашу регистрацию, не затронув существующие; как `name`, `id` и empty `url` участвуют в выборе.
5. Какие runtime hosts, redirects, headers, MIME, Range и size limits используются для call records.
6. Какие реальные MIME/размеры/error responses и parent links действуют для L10.

Если P05–P08, L10, L21 и callback delivery входят в принятый 0.5 scope, эти вопросы должны быть закрыты до G0 либо методы получают явно согласованный блокер/отличие. Документальная схема сама по себе не доказывает безопасную реализацию.
