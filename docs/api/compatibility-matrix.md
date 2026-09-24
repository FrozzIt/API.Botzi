# Task 0.1 — документальная матрица 64 операций LPTracker

Дата доступа: **2026-09-22**, Europe/Moscow. Это документальная сверка, не доказательство runtime-поведения.

## Легенда

- Статус: **LIVE** — ограниченный runtime-сценарий подтверждён в задаче 0.2, но это не доказательство полного контракта; **DOC** — method+path однозначно совпадают в блоке запроса и/или curl; **DOC+LIVE** — method+path подтверждены, но указанная часть контракта требует live-проверки; **CONFLICT+LIVE** — официальный источник сам себе противоречит; **UNAVAILABLE** — источник недоступен.
- Ownership: `account` — API не даёт проектной привязки; `path/query/body.project` — проект указан прямо; `object.project` — проект выводится из документированной модели объекта; `parent` — требуется проверка родительского объекта/справочника.
- Side effect: `no`, `yes`, `session`.
- Retry: `read≤1` — только безопасное чтение, максимум один ограниченный повтор; `none-after-send` — не повторять автоматически после отправки; `session-special` — координированное управление сессией.
- Предлагаемый release scope не заменяет verification status: `v1`/`deferred` показывает продуктовый объём на review, а `LIVE`/`DOC*` — состояние доказательств метода.
- Неопределённость: `E` — документация даёт пример, но не полную схему/null/лимиты/ошибки; `W` — спорная оболочка/статус ответа; `F` — конфликт имени поля; `P` — конфликт пути; `O` — ownership не доказуем из ответа операции; `S` — расхождение SDK и HTTP; `T` — trailing slash; `—` — специальных расхождений сверх `E` не найдено.

| ID | Source | Method | Path | Назначение | Ownership evidence | Side effect | Retry | Req/resp uncertainty | Verification status |
|---|---|---|---|---|---|---|---|---|---|
| A01 | [src](https://docs.direct.lptracker.ru/basic/auth/) | POST | `/login` | Получить token | account | session | session-special | LIVE 2026-09-22: один login, JSON success, token получен | LIVE |
| A02 | [src](https://docs.direct.lptracker.ru/basic/auth/) | POST | `/logout` | Удалить token | account | session | none-after-send | LIVE 2026-09-22: logout success; последующий GET получил 401 | LIVE |
| P01 | [src](https://docs.direct.lptracker.ru/project/list/) | GET | `/projects` | Список проектов аккаунта | account; проект выбирает proxy | no | read≤1 | LIVE 2026-09-22: JSON success; 7 проектов, allowed A/B present | LIVE |
| P02 | [src](https://docs.direct.lptracker.ru/project/get/) | GET | `/project/{project_id}` | Проект по ID | path.project | no | read≤1 | LIVE 2026-09-22: путь без trailing slash успешен для A/B; slash-вариант не тестировался | LIVE |
| P03 | [src](https://docs.direct.lptracker.ru/project/custom/) | GET | `/project/{project_id}/customs` | Поля лидов проекта | path.project | no | read≤1 | E | DOC |
| P04 | [src](https://docs.direct.lptracker.ru/project/field/) | GET | `/project/{project_id}/fields` | Поля контактов | path.project | no | read≤1 | E | DOC |
| P05 | [src](https://docs.direct.lptracker.ru/project/callback_url/) | PUT | `/project/{project_id}/callback-url` | Установить webhook лида | path.project | yes | none-after-send | E: доставка/подпись не описаны | DOC+LIVE |
| P06 | [src](https://docs.direct.lptracker.ru/project/callback_url_list/) | GET | `/project/{project_id}/callback-url` | Список webhook лида | path.project | no | read≤1 | E | DOC |
| P07 | [src](https://docs.direct.lptracker.ru/project/project_callback_url/) | PUT | `/project/{project_id}/project-callback-url` | Установить webhook CRM | path.project | yes | none-after-send | E: доставка/подпись не описаны | DOC+LIVE |
| P08 | [src](https://docs.direct.lptracker.ru/project/project_callback_url_list/) | GET | `/project/{project_id}/project-callback-url` | Список webhook CRM | path.project | no | read≤1 | E | DOC |
| P09 | [src](https://docs.direct.lptracker.ru/project/settings/widget/get/) | GET | `/project/{project_id}/widget` | Настройки виджета | path.project | no | read≤1 | E | DOC |
| P10 | [src](https://docs.direct.lptracker.ru/project/settings/widget/edit/) | PUT | `/project/{project_id}/widget` | Изменить настройки виджета | path.project | yes | none-after-send | P: блок запроса PUT, curl без `-X PUT` и без body; полный набор настроек не задан | CONFLICT+LIVE |
| P11 | [src](https://docs.direct.lptracker.ru/funnel/list/) | GET | `/project/{project_id}/funnel` | Шаги воронки | path.project | no | read≤1 | E | DOC |
| P12 | [src](https://docs.direct.lptracker.ru/funnel/get/) | GET | `/project/{project_id}/funnel/{funnel_id}` | Шаг воронки | path.project | no | read≤1 | E | DOC |
| P13 | [src](https://docs.direct.lptracker.ru/autofunnel/list/) | GET | `/project/{project_id}/autofunnels` | Список автоворонок | path.project | no | read≤1 | E | DOC |
| P14 | [src](https://docs.direct.lptracker.ru/autofunnel/edit/) | PATCH | `/project/{project_id}/autofunnels/{autofunnel_id}` | Изменить статус автоворонки | path.project | yes | none-after-send | E: runtime side effects не описаны | DOC+LIVE |
| C01 | [src](https://docs.direct.lptracker.ru/contact/search/) | GET | `/contact/search` | Поиск контактов | query.project | no | read≤1 | LIVE 0.3: `project_id`-only вернул 400 для A/B; SDK all-contact bootstrap не воспроизведён | DOC+LIVE |
| C02 | [src](https://docs.direct.lptracker.ru/contact/create/) | POST | `/contact` | Создать контакт | body.project | yes | none-after-send | LIVE 0.3: два synthetic contacts созданы в A/B; response project/details совпали | LIVE |
| C03 | [src](https://docs.direct.lptracker.ru/contact/get/) | GET | `/contact/{contact_id}` | Контакт по ID | object.project | no | read≤1 | LIVE 0.3: parent reads probes вернули ожидаемые project и detail | LIVE |
| C04 | [src](https://docs.direct.lptracker.ru/contact/edit/) | PUT | `/contact/{contact_id}` | Изменить контакт | object.project | yes | none-after-send | F: `field` в body против `fields` в curl; clear/empty | DOC+LIVE |
| C05 | [src](https://docs.direct.lptracker.ru/contact/delete/) | DELETE | `/contact/{contact_id}` | Удалить контакт | object.project before delete | yes | none-after-send | LIVE 0.3: cleanup DELETE success для обоих probe parents; detail cascade отдельно не читался | LIVE |
| C06 | [src](https://docs.direct.lptracker.ru/contact/detail_get/) | GET | `/contact/details/{detail_id}` | Получить контактные данные | parent mapping; direct result без parent/project | no | read≤1 | LIVE 0.3: direct result без parent/project; mapping доказан только через C02/C03 для новых probes | LIVE |
| C07 | [src](https://docs.direct.lptracker.ru/contact/detail_edit/) | PUT | `/contact/details/{detail_id}` | Изменить контактные данные | parent mapping required | yes | none-after-send | LIVE 0.3: plural PUT с `value` успешен, readback совпал; описание `volume` ошибочно | LIVE |
| C08 | [src](https://docs.direct.lptracker.ru/contact/detail_delete/) | DELETE | `/contact/details/{detail_id}` | Удалить контактные данные | trusted parent mapping required | yes | none-after-send | LIVE 0.3: plural HTTP 200/error 400; singular HTTP 200/error 404; readback подтвердил detail retained; рабочего route нет | CONFLICT+LIVE |
| C09 | [src](https://docs.direct.lptracker.ru/contact/leads_get/) | GET | `/contact/{contact_id}/leads` | Лиды контакта | parent contact; лиды без project в примере | no | read≤1 | O,E | DOC+LIVE |
| C10 | [src](https://docs.direct.lptracker.ru/contact/field_get/) | GET | `/contact/{contact_id}/field/{field_id}` | Поле контакта | parent contact + project fields | no | read≤1 | E | DOC |
| C11 | [src](https://docs.direct.lptracker.ru/contact/field_edit/) | PUT | `/contact/{contact_id}/field/{field_id}` | Изменить поле контакта | parent contact + project fields | yes | none-after-send | P: request `/details/`, curl и SDK `/field/` | CONFLICT+LIVE |
| C12 | [src](https://docs.direct.lptracker.ru/contact/field_delete/) | DELETE | `/contact/{contact_id}/field/{field_id}` | Очистить поле контакта | parent contact + project fields | yes | none-after-send | E | DOC |
| V01 | [src](https://docs.direct.lptracker.ru/view/get/) | GET | `/view/{view_id}` | Просмотр по ID/UUID | object.project | no | read≤1 | E | DOC |
| V02 | [src](https://docs.direct.lptracker.ru/view/create/) | POST | `/view` | Создать просмотр | body.project | yes | none-after-send | E: visitor/real_visitor не формализованы | DOC+LIVE |
| V03 | [src](https://docs.direct.lptracker.ru/view/edit/) | PUT | `/view/{view_id}` | Изменить просмотр | object.project before write | yes | none-after-send | E | DOC |
| V04 | [src](https://docs.direct.lptracker.ru/view/delete/) | DELETE | `/view/{view_id}` | Удалить просмотр | object.project before delete | yes | none-after-send | E: каскад не описан | DOC+LIVE |
| L01 | [src](https://docs.direct.lptracker.ru/lead/create/) | POST | `/lead` | Создать лид/сделку | contact.project or body contact.project | yes | none-after-send | E: вложенные contact/view/custom/payment/owner; callback | DOC+LIVE |
| L02 | [src](https://docs.direct.lptracker.ru/lead/custom_create/) | POST | `/custom/{project_id}/create` | Создать поле лида | path.project | yes | none-after-send | W: пример успеха — сырой объект без global envelope | DOC+LIVE |
| L03 | [src](https://docs.direct.lptracker.ru/lead/custom_add_option/) | PATCH | `/custom/{project_id}/{custom_id}/add-category` | Добавить категории поля | path.project + parent field | yes | none-after-send | E | DOC |
| L04 | [src](https://docs.direct.lptracker.ru/lead/get/) | GET | `/lead/{lead_id}` | Лид по ID | nested contact.project | no | read≤1 | E | DOC |
| L05 | [src](https://docs.direct.lptracker.ru/lead/list/) | GET | `/lead/{project_id}/list` | Список лидов/сделок | path.project | no | read≤1 | W: страница показывает raw array, global docs требуют status | DOC+LIVE |
| L06 | [src](https://docs.direct.lptracker.ru/lead/edit/) | PUT | `/lead/{lead_id}` | Изменить лид | nested contact.project before write | yes | none-after-send | E: custom/map/owner types | DOC+LIVE |
| L07 | [src](https://docs.direct.lptracker.ru/lead/delete/) | DELETE | `/lead/{lead_id}` | Удалить лид | nested contact.project before delete | yes | none-after-send | E: каскад не описан | DOC+LIVE |
| L08 | [src](https://docs.direct.lptracker.ru/lead/call/) | POST | `/lead/{lead_id}/call` | Инициировать звонок | parent lead | yes | none-after-send | E: телефония/дубликаты | DOC+LIVE |
| L09 | [src](https://docs.direct.lptracker.ru/lead/file_upload/) | POST | `/lead/{lead_id}/file` | Загрузить Base64-файл | parent lead + custom field | yes | none-after-send | E: размер/MIME не ограничены | DOC+LIVE |
| L10 | [src](https://docs.direct.lptracker.ru/lead/file_get/) | GET | `/lead/{lead_id}/custom/{custom_id}/file/{file_id}` | Получить Base64-файл | full parent chain in path | no | read≤1 | E | DOC |
| L11 | [src](https://docs.direct.lptracker.ru/lead/owner_put/) | PUT | `/lead/{lead_id}/owner` | Сменить владельца | parent lead + account staff | yes | none-after-send | O: staff project membership/owner=0 не описаны | DOC+LIVE |
| L12 | [src](https://docs.direct.lptracker.ru/lead/funnel_put/) | PUT | `/lead/{lead_id}/funnel` | Сменить шаг | parent lead + project funnel | yes | none-after-send | E: уведомления/side effects | DOC+LIVE |
| L13 | [src](https://docs.direct.lptracker.ru/lead/field_get/) | GET | `/lead/{lead_id}/custom/{field_id}` | Поле лида | parent lead + project custom | no | read≤1 | E | DOC |
| L14 | [src](https://docs.direct.lptracker.ru/lead/field_put/) | PUT | `/lead/{lead_id}/custom/{field_id}` | Изменить поле лида | parent lead + project custom | yes | none-after-send | E: типы value/вложенные IDs | DOC+LIVE |
| L15 | [src](https://docs.direct.lptracker.ru/lead/field_delete/) | DELETE | `/lead/{lead_id}/custom/{field_id}` | Очистить поле лида | parent lead + project custom | yes | none-after-send | E | DOC |
| L16 | [src](https://docs.direct.lptracker.ru/lead/comment_add/) | POST | `/lead/{lead_id}/comment` | Добавить комментарий | parent lead | yes | none-after-send | E: author side effect | DOC+LIVE |
| L17 | [src](https://docs.direct.lptracker.ru/lead/comment_list/) | GET | `/lead/{lead_id}/comments` | Комментарии лида | parent lead | no | read≤1 | O: author account-wide | DOC+LIVE |
| L18 | [src](https://docs.direct.lptracker.ru/lead/payment_add/) | POST | `/lead/{lead_id}/payment` | Добавить платёж | parent lead | yes | none-after-send | E: `sum` example string, model number; duplicate semantics | DOC+LIVE |
| L19 | [src](https://docs.direct.lptracker.ru/lead/message_receive/) | POST | `/lead/messageReceive/{lead_id}` | Добавить входящее сообщение | parent lead | yes | none-after-send | E: messenger/files/дубликаты | DOC+LIVE |
| L20 | [src](https://docs.direct.lptracker.ru/lead/message_history/) | GET | `/lead/chatHistory/{lead_id}` | История чата | parent lead | no | read≤1 | E: без пагинации/полной схемы | DOC+LIVE |
| L21 | [src](https://docs.direct.lptracker.ru/lead/calls_list/) | GET | `/lead/{lead_id}/calls` | Звонки лида | parent lead | no | read≤1 | O,E: account owner и внешняя record URL | DOC+LIVE |
| L22 | [src](https://docs.direct.lptracker.ru/max/message_status/) | POST | `/max/messageStatus` | Отметить доставку/прочтение MAX-сообщений | account/token scoped; lead/project absent | yes | none-after-send | O,E: project isolation и область массовой отметки не доказаны | DOC+LIVE |
| S01 | [src](https://docs.direct.lptracker.ru/staff/list/) | GET | `/staff` | Сотрудники аккаунта | account; project membership absent runtime | no | read≤1 | LIVE 0.3 rework-1: count=1, schema-only; project fields отсутствуют; zero-ID entry present; PII values не сохранялись | LIVE |
| T01 | [src](https://docs.direct.lptracker.ru/task/create/) | POST | `/task` | Создать задачу | body.project + referenced lead/staff/labels | yes | none-after-send | F,O: body `title/text`, описание `task_title/task_text`, curl добавляет `contact_id`; visibility/notifications | DOC+LIVE |
| T02 | [src](https://docs.direct.lptracker.ru/task/get/) | GET | `/task/{task_id}` | Задача по ID | object.project | no | read≤1 | O: вложенные staff fields/URLs | DOC+LIVE |
| T03 | [src](https://docs.direct.lptracker.ru/task/list/) | GET | `/task/{project_id}/list` | Список задач | path.project | no | read≤1 | W: raw array против global status envelope; O staff | DOC+LIVE |
| T04 | [src](https://docs.direct.lptracker.ru/task/edit/) | PUT | `/task/{task_id}` | Изменить задачу | object.project + body.project | yes | none-after-send | F,O: body `title/text`, описание `task_title/task_text`, curl добавляет `contact_id`; перенос/visibility | DOC+LIVE |
| T05 | [src](https://docs.direct.lptracker.ru/task/delete/) | DELETE | `/task/{task_id}` | Удалить задачу | object.project before delete | yes | none-after-send | E | DOC |
| M01 | [src](https://docs.direct.lptracker.ru/label/create/) | POST | `/label` | Создать метку | body.project + M02 list evidence | yes | none-after-send | LIVE 0.3 rework-1: `title` принят; по одной probe label создано в A/B, membership подтверждён M02 | LIVE |
| M02 | [src](https://docs.direct.lptracker.ru/label/list/) | GET | `/label/{project_id}/list` | Список меток | path.project | no | read≤1 | LIVE 0.3 rework-1: runtime object envelope; own probe only, other-project probe absent | LIVE |
| M03 | [src](https://docs.direct.lptracker.ru/label/edit/) | PUT | `/label/{label_id}` | Изменить метку | ID prechecked by M02 project list | yes | none-after-send | LIVE 0.3 rework-1: own A/B edits success; project-list readback name matched и membership сохранилась | LIVE |
| M04 | [src](https://docs.direct.lptracker.ru/label/delete/) | DELETE | `/label/{label_id}` | Удалить метку | ID prechecked by M02 project list | yes | none-after-send | LIVE 0.3 rework-1: own A/B deletes success; project-list readback absence; cleanup complete | LIVE |

## Предложенный release scope — не принят

- `v1` (57): `A01–A02`, `P01–P14`, `C01–C07`, `C09–C12`, `V01–V04`, `L01–L10`, `L12–L21`, `T02–T03`, `M01–M04`.
- `deferred` (7): `C08`, `L11`, `L22`, `S01`, `T01`, `T04`, `T05`.
- До решения 0.5 действующей продуктовой базой остаются все 64 строки и исходные критерии будущих задач; классификация 57/7 не меняет их автоматически.
- Пересечений между наборами нет; вместе они покрывают все 64 строки матрицы. Причины и условия возврата каждого deferred ID зафиксированы в [реестре](../plans/proxy-api-endpoint-inventory.md#предложение-по-объёму-v1--на-review-pmзаказчика).
- Product scope ещё не утверждён, но security-граница обязательна уже сейчас: без доказанной принадлежности операция не выполняется и данные не выдаются. Для `C05`, `C06–C07`, `L01/L06` и `T02–T03` реестр предлагает конкретные правила, требующие письменной приёмки в 0.3; если метод войдёт в v1, принятые проверки изоляции обязательны при реализации. Это не повышает verification status и не превращает неподтверждённое поведение в факт.
- Внешние коды `400/404/501` остаются вариантом для согласования с CRM в задаче 0.5.

## Итог по 64 строкам

- 64 уникальных ID; 64 уникальных нормализованных пары method+path.
- `LIVE`: 14; `DOC`: 18; `DOC+LIVE`: 29; `CONFLICT+LIVE`: 3; `UNAVAILABLE`: 0.
- Три неразрешённых `CONFLICT+LIVE`: P10 (PUT против фактического GET в curl), C08 (`detail/details`; plural runtime error 400, singular runtime error 404 и detail retained), C11 (`details/field`). P02 подтверждён runtime на пути без trailing slash; slash-вариант не проверялся.
- Значение `DOC+LIVE` не опровергает наличие операции: оно означает, что документация подтверждает route, но не позволяет безопасно зафиксировать весь request/response/runtime-контракт.
