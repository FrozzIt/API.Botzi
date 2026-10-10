# ProxyAPI: предварительный реестр всего документированного API

Дата проверки: 21–22.09.2026. Дополнение к [основному плану](proxy-api-implementation-plan.md).

Найдено **64 операции method + path**. Это реестр для реализации и проверки, а не заявление о готовой поддержке. Изучены основное оглавление, отдельные страницы и дополнительные методы истории сообщений/звонков. На этапе 0 команда сверяет реестр с работающим API и поставщиком; обнаруженные документированные методы добавляются в объём. Методы внутренних веб-интерфейсов и будущие изменения API автоматически в этот снимок не входят.

## Согласованный архитектором объём v1 — на отдельную PM-приёмку

Архитектор согласовал включение в первую версию **52 операций** и перенос **12 операций**. Пакет ещё требует отдельной PM-приёмки и G0; классификация scope не меняет статус фактической проверки метода. Все 64 ID, их источники и прежние DOC/LIVE-факты ниже сохраняются.

Условия возврата, error contract, влияние на G0/G7, задачи и оценку собраны в [Decision 001](../decisions/001-v1-scope-contract.md). Его статус `PROPOSED — ARCHITECT SCOPE AGREED, AWAITING PM ACCEPTANCE AND G0` не означает приёмку 0.5 или прохождение G0.

- `v1` (52): `A01–A02`, `P01–P04`, `P09–P14`, `C01–C07`, `C09–C12`, `V01–V04`, `L01–L09`, `L12–L21`, `T02–T03`, `M01–M04`.
- `deferred` (12): `P05–P08`, `C08`, `L10`, `L11`, `L22`, `S01`, `T01`, `T04`, `T05`.
- Доставка callback также deferred, но не входит в 64 операции.

| ID | Почему отложен | Условие возврата в scope |
|---|---|---|
| `P05` | Адресная мутация upstream-подписки не доказана | Provider contract или controlled fixture доказывает создание/изменение только нашей регистрации и безопасный readback |
| `P06` | Список может раскрыть чужие upstream-регистрации | Доказана адресная ownership-модель и серверная фильтрация возвращает только настройки клиента |
| `P07` | Адресная мутация callback структуры CRM не доказана | Выполнено условие P05 для отдельного типа подписки и доказано отсутствие изменения чужих регистраций |
| `P08` | Пустой runtime-список не доказывает безопасный будущий read | Выполнено условие P06 для callback структуры CRM |
| `C08` | Оба документированных route дали application errors; рабочее удаление и безопасный bootstrap ownership не доказаны | Подтверждён рабочий delete route; для details, существовавших до proxy или созданных вне proxy, воспроизводимо доказана связь `detail → parent → разрешённый project` и её повторная проверка перед delete |
| `L10` | Provider/parent source `file_id` и полная parent chain не доказаны | Обезличенный fixture воспроизводимо доказывает `file_id → custom → lead → разрешённый project`, negative fixture и Base64/MIME/size/error limits |
| `S01` | Runtime endpoint account-scoped и не содержит project-membership fields | Архитектор утверждает доказуемый источник project membership и правила фильтрации/очистки либо изоляцию upstream-аккаунтов |
| `L11` | Project membership сотрудника и семантика `owner=0` не доказаны | Поставщик или принятый server-side справочник даёт проверяемое членство; архитектор утверждает `owner=0` и нейтральные отказы |
| `L22` | В запросе нет lead/project; область массовой отметки account/token-scoped не доказана | Воспроизводимо доказана связь затрагиваемого MAX-чата с разрешённым проектом и отсутствие эффекта для других проектов либо документирована и принята эквивалентная доказанная изоляция |
| `T01` | Без safe staff fixture не доказаны owner/observers, visibility и отсутствие нежелательных уведомлений | Архитектор утверждает staff/notification contract, после чего ограниченный runtime-тест подтверждает проект и получателей |
| `T04` | Не доказаны staff/observer rules, уведомления и безопасная семантика переноса | Приняты правила staff/notifications; runtime подтверждает project check и запрет межпроектного переноса до записи |
| `T05` | Без безопасного task lifecycle не подтверждена project check перед удалением | После допуска T01/T04 создана безопасная fixture и runtime подтверждает чтение проекта до delete |

### Статусы scope и безопасности

Scope 52/12 согласован архитектором, но ещё не принят PM и не прошёл G0. Независимо от scope действует обязательная security-граница: если принадлежность проекту не доказана, операция не выполняется и данные не выдаются.

Ниже приведены предложенные в 0.3 конкретные безопасные правила. Они требуют отдельной письменной приёмки PM/архитектора в 0.3. Для методов, которые затем будут включены в v1, принятые правила или согласованный эквивалент доказанной изоляции обязательны при реализации:

- Если `C05` войдёт в v1, перед удалением проверяется проект. Успешный cleanup родителя не доказывает все каскадные эффекты; они остаются отдельной contract-проверкой.
- Если `C06–C07` войдут в v1, они разрешены только для доверенной server-side mapping `detail → parent → project` с повторной проверкой родителя. Detail, известный только по собственному ID, не выдаётся и не изменяется.
- Если `L01/L06` войдут в v1, они не принимают неподтверждённое назначение `owner` или `observers`: такие поля отклоняются либо исключаются только по письменно принятому контракту.
- Если `T02–T03` войдут в v1, до выпуска обязательны проверка проекта и очистка вложенных staff fields.
- Для deferred после успешной локальной авторизации зафиксированы HTTP 200, JSON `status:error`, application code `501` и сообщение `Operation is not available in this release`. Отказ выполняется без проверки существования объекта и без обращений к LPTracker, включая внутренний login.
- Внешние application-коды `400/404` ещё не утверждены; текущая framework-валидация возвращает application `422`, и это расхождение требует отдельного решения.

Уверенность в наличии перечисленных операций в документации — высокая. В точности спорных путей и фактических форматах — средняя или низкая там, где отмечено расхождение. Каждый документированный метод должен получить тест успешного сценария, ошибок, изоляции проекта и отсутствия утечек. Непроверенный/временно закрытый метод не засчитывается как реализованный.

Во всех строках проект определяется по клиентскому конфигу. `project_id` из запроса лишь проверяется на совпадение. Объекты и вложенные ссылки также проверяются: наличие разрешённого проекта в URL само по себе недостаточно.

## Авторизация — 2 операции

| ID | Метод и путь | Обработка |
|---|---|---|
| A01 | `POST /login` | Только наша авторизация; [источник](https://docs.direct.lptracker.ru/basic/auth/) |
| A02 | `POST /logout` | Отзыв нашей сессии; внутренний logout не выполнять; [источник](https://docs.direct.lptracker.ru/basic/auth/) |

## Проекты, настройки и воронки — 14 операций

| ID | Метод и путь | Проверка/особенность |
|---|---|---|
| P01 | `GET /projects` | Вернуть список ровно из одного разрешённого проекта; [источник](https://docs.direct.lptracker.ru/project/list/) |
| P02 | `GET /project/{project_id}` | Только назначенный проект; [источник](https://docs.direct.lptracker.ru/project/get/) |
| P03 | `GET /project/{project_id}/customs` | Поля только этого проекта; [источник](https://docs.direct.lptracker.ru/project/custom/) |
| P04 | `GET /project/{project_id}/fields` | Поля контактов этого проекта; [источник](https://docs.direct.lptracker.ru/project/field/) |
| P05 | `PUT /project/{project_id}/callback-url` | Локальная настройка клиента + наш relay; [источник](https://docs.direct.lptracker.ru/project/callback_url/) |
| P06 | `GET /project/{project_id}/callback-url` | Возвращать клиентские настройки, не внутренний receiver; [источник](https://docs.direct.lptracker.ru/project/callback_url_list/) |
| P07 | `PUT /project/{project_id}/project-callback-url` | Relay событий структуры CRM; [источник](https://docs.direct.lptracker.ru/project/project_callback_url/) |
| P08 | `GET /project/{project_id}/project-callback-url` | Только локальные подписки данного клиента; [источник](https://docs.direct.lptracker.ru/project/project_callback_url_list/) |
| P09 | `GET /project/{project_id}/widget` | Проверка проекта и полей ответа; [источник](https://docs.direct.lptracker.ru/project/settings/widget/get/) |
| P10 | `PUT /project/{project_id}/widget` | Настройки влияют на поведение проекта; [источник](https://docs.direct.lptracker.ru/project/settings/widget/edit/) |
| P11 | `GET /project/{project_id}/funnel` | Справочник шагов; [источник](https://docs.direct.lptracker.ru/funnel/list/) |
| P12 | `GET /project/{project_id}/funnel/{funnel_id}` | Проверить также принадлежность шага; [источник](https://docs.direct.lptracker.ru/funnel/get/) |
| P13 | `GET /project/{project_id}/autofunnels` | Только проектные автоворонки; [источник](https://docs.direct.lptracker.ru/autofunnel/list/) |
| P14 | `PATCH /project/{project_id}/autofunnels/{autofunnel_id}` | Проверить принадлежность и побочные эффекты смены состояния; [источник](https://docs.direct.lptracker.ru/autofunnel/edit/) |

## Контакты — 12 операций

| ID | Метод и путь | Проверка/особенность |
|---|---|---|
| C01 | `GET /contact/search` | Проект в query, массивы телефонов/email; [источник](https://docs.direct.lptracker.ru/contact/search/) |
| C02 | `POST /contact` | Проект в body, поля и контактные данные; [источник](https://docs.direct.lptracker.ru/contact/create/) |
| C03 | `GET /contact/{contact_id}` | Проект из проверенного объекта; [источник](https://docs.direct.lptracker.ru/contact/get/) |
| C04 | `PUT /contact/{contact_id}` | Проверить семантику `clear_contacts`, пустых значений, `field`/`fields`; [источник](https://docs.direct.lptracker.ru/contact/edit/) |
| C05 | `DELETE /contact/{contact_id}` | Проверить владение и каскадные эффекты удаления; [источник](https://docs.direct.lptracker.ru/contact/delete/) |
| C06 | `GET /contact/details/{detail_id}` | В примере ответа нет проекта/родителя: нужен доказанный индекс принадлежности; [источник](https://docs.direct.lptracker.ru/contact/detail_get/) |
| C07 | `PUT /contact/details/{detail_id}` | Перед записью проверить detail через его контакт; [источник](https://docs.direct.lptracker.ru/contact/detail_edit/) |
| C08 | `DELETE /contact/details/{detail_id}` | Спорный путь: пример использует `/contact/detail/{id}`; подтвердить; [источник](https://docs.direct.lptracker.ru/contact/detail_delete/) |
| C09 | `GET /contact/{contact_id}/leads` | Контакт и каждый возвращённый лид принадлежат проекту; [источник](https://docs.direct.lptracker.ru/contact/leads_get/) |
| C10 | `GET /contact/{contact_id}/field/{field_id}` | Контакт + принадлежность поля проекту; [источник](https://docs.direct.lptracker.ru/contact/field_get/) |
| C11 | `PUT /contact/{contact_id}/field/{field_id}` | Путь взят из примера; блок «Запрос» указывает `/details/`; подтвердить; [источник](https://docs.direct.lptracker.ru/contact/field_edit/) |
| C12 | `DELETE /contact/{contact_id}/field/{field_id}` | Очистка значения; проверить контакт и поле; [источник](https://docs.direct.lptracker.ru/contact/field_delete/) |

## Просмотры — 4 операции

| ID | Метод и путь | Проверка/особенность |
|---|---|---|
| V01 | `GET /view/{view_id}` | Проверить project_id объекта; [источник](https://docs.direct.lptracker.ru/view/get/) |
| V02 | `POST /view` | Проект в body; [источник](https://docs.direct.lptracker.ru/view/create/) |
| V03 | `PUT /view/{view_id}` | Предварительно проверить проект; [источник](https://docs.direct.lptracker.ru/view/edit/) |
| V04 | `DELETE /view/{view_id}` | Проверить проект и эффекты на связанные данные; [источник](https://docs.direct.lptracker.ru/view/delete/) |

## Лиды и связанные операции — 22 операции

| ID | Метод и путь | Проверка/особенность |
|---|---|---|
| L01 | `POST /lead` | Проверить контакт/вложенный контакт, просмотр, шаг, owner, поля, платежи; callback запускает звонок; [источник](https://docs.direct.lptracker.ru/lead/create/) |
| L02 | `POST /custom/{project_id}/create` | Создание поля проекта; [источник](https://docs.direct.lptracker.ru/lead/custom_create/) |
| L03 | `PATCH /custom/{project_id}/{custom_id}/add-category` | Проверить принадлежность поля; [источник](https://docs.direct.lptracker.ru/lead/custom_add_option/) |
| L04 | `GET /lead/{lead_id}` | Проект из вложенного контакта; [источник](https://docs.direct.lptracker.ru/lead/get/) |
| L05 | `GET /lead/{project_id}/list` | Пагинация, сортировка, фильтры, лиды/сделки; [источник](https://docs.direct.lptracker.ru/lead/list/) |
| L06 | `PUT /lead/{lead_id}` | Лид и связанные сущности; [источник](https://docs.direct.lptracker.ru/lead/edit/) |
| L07 | `DELETE /lead/{lead_id}` | Проверка до удаления; [источник](https://docs.direct.lptracker.ru/lead/delete/) |
| L08 | `POST /lead/{lead_id}/call` | Реальный звонок, без слепого повтора; [источник](https://docs.direct.lptracker.ru/lead/call/) |
| L09 | `POST /lead/{lead_id}/file` | JSON/Base64, поле и лид, размер файла; [источник](https://docs.direct.lptracker.ru/lead/file_upload/) |
| L10 | `GET /lead/{lead_id}/custom/{custom_id}/file/{file_id}` | Проверить всю цепочку, ответ с Base64; [источник](https://docs.direct.lptracker.ru/lead/file_get/) |
| L11 | `PUT /lead/{lead_id}/owner` | Лид + разрешённый сотрудник; отдельно owner=0; [источник](https://docs.direct.lptracker.ru/lead/owner_put/) |
| L12 | `PUT /lead/{lead_id}/funnel` | Лид и шаг одного проекта; [источник](https://docs.direct.lptracker.ru/lead/funnel_put/) |
| L13 | `GET /lead/{lead_id}/custom/{field_id}` | Проверить лид и поле; [источник](https://docs.direct.lptracker.ru/lead/field_get/) |
| L14 | `PUT /lead/{lead_id}/custom/{field_id}` | Проверить также ссылки внутри значения; [источник](https://docs.direct.lptracker.ru/lead/field_put/) |
| L15 | `DELETE /lead/{lead_id}/custom/{field_id}` | Очистка значения, включая зависимости его типа; [источник](https://docs.direct.lptracker.ru/lead/field_delete/) |
| L16 | `POST /lead/{lead_id}/comment` | Лид и данные автора в ответе; [источник](https://docs.direct.lptracker.ru/lead/comment_add/) |
| L17 | `GET /lead/{lead_id}/comments` | Проверить лид, не раскрывать профиль главного аккаунта; [источник](https://docs.direct.lptracker.ru/lead/comment_list/) |
| L18 | `POST /lead/{lead_id}/payment` | Изменяющая операция; проверка лида и дублей; [источник](https://docs.direct.lptracker.ru/lead/payment_add/) |
| L19 | `POST /lead/messageReceive/{lead_id}` | Вложения Base64, данные мессенджера, проверка лида; [источник](https://docs.direct.lptracker.ru/lead/message_receive/) |
| L20 | `GET /lead/chatHistory/{lead_id}` | Проверить лид и вложенные сведения об автоворонке; [источник](https://docs.direct.lptracker.ru/lead/message_history/) |
| L21 | `GET /lead/{lead_id}/calls` | Проверить лид; record перенаправляется через наш media endpoint; [источник](https://docs.direct.lptracker.ru/lead/calls_list/) |
| L22 | `POST /max/messageStatus` | Account/token scoped; lead/project в запросе отсутствуют, до live-проверки изоляция не доказана; side effect=yes; retry=none-after-send; [источник](https://docs.direct.lptracker.ru/max/message_status/) |

## Сотрудники — 1 операция

| ID | Метод и путь | Проверка/особенность |
|---|---|---|
| S01 | `GET /staff` | Account-wide метод без project_id: вернуть только назначенных клиенту сотрудников, скрыть профиль главного аккаунта; [источник](https://docs.direct.lptracker.ru/staff/list/) |

## Задачи — 5 операций

| ID | Метод и путь | Проверка/особенность |
|---|---|---|
| T01 | `POST /task` | Проект, лид, owner, observers, labels, режим visibility и уведомления; [источник](https://docs.direct.lptracker.ru/task/create/) |
| T02 | `GET /task/{task_id}` | Проект задачи и вложенные профили; [источник](https://docs.direct.lptracker.ru/task/get/) |
| T03 | `GET /task/{project_id}/list` | Проект, фильтры, пагинация, вложенные last_project_id и avatar; [источник](https://docs.direct.lptracker.ru/task/list/) |
| T04 | `PUT /task/{task_id}` | Проект до изменения + новый проект в body; межпроектный перенос запрещён; [источник](https://docs.direct.lptracker.ru/task/edit/) |
| T05 | `DELETE /task/{task_id}` | Проверить проект до удаления; [источник](https://docs.direct.lptracker.ru/task/delete/) |

## Метки — 4 операции

| ID | Метод и путь | Проверка/особенность |
|---|---|---|
| M01 | `POST /label` | Проект в body; уточнить `title`/`name`; [источник](https://docs.direct.lptracker.ru/label/create/) |
| M02 | `GET /label/{project_id}/list` | Только проектный список; уточнить оболочку ответа; [источник](https://docs.direct.lptracker.ru/label/list/) |
| M03 | `PUT /label/{label_id}` | Проверить ID по полному списку меток разрешённого проекта; [источник](https://docs.direct.lptracker.ru/label/edit/) |
| M04 | `DELETE /label/{label_id}` | Та же проверка, учесть использование в задачах; [источник](https://docs.direct.lptracker.ru/label/delete/) |

## Как превратить реестр в контракт

Для каждой строки команда добавляет: проверенный upstream-путь; схемы запроса/ответа; точную семантику пустых значений и типов; правила проекта и связанных объектов; разрешённые преобразования; число upstream-вызовов; политику повторов; тесты; статус проверки на реальном аккаунте. Прикладные ошибки внутри HTTP 200 проверяются для каждой группы.

Спорные пути C08/C11 нельзя закрыть догадкой или автоматическим повтором записи на альтернативном URL. Их подтверждают заранее на синтетических данных. Описание SDK утверждает возможность поиска всех контактов без фильтра, тогда как страница HTTP-поиска требует фильтр: это особенно важно для первичного заполнения индекса принадлежности. [SDK](https://docs.direct.lptracker.ru/phpsdk/methods/), [HTTP-поиск](https://docs.direct.lptracker.ru/contact/search/)

Каждая строка должна иметь доказанную безопасную реализацию. Если без дополнительных возможностей поставщика это невозможно, фиксируется блокер полной совместимости; запрет метода защищает данные, но не означает выполнение требования «весь API».
