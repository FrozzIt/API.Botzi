# ProxyAPI: отклонения и результат выполнения плана

## Отклонения и причины

План и реестр исходно содержали 63 операции. При сверке официальной публичной документации LPTracker выявлена дополнительная операция L22 `POST /max/messageStatus`.

Изменение объёма с 63 до 64 операций выполнено по прямому правилу плана: при обнаружении нового документированного метода сначала расширяются реестр и контракт. Архитектура, порядок этапов, зависимости и критерии приёмки не менялись.

Runtime-контракт и tenant/project isolation L22 документацией не подтверждены. Их проверка переносится в предусмотренные этапом 0 live-проверки.

## Результат выполнения

Задача 0.1 документально сверена. Зафиксированы 64 уникальных ID и 64 уникальные пары method+path: 22 операции со статусом `DOC`, 38 — `DOC+LIVE`, 4 — `CONFLICT+LIVE`.

Live API не вызывался, credentials не использовались, runtime-поведение не подтверждалось. Следующий шаг разрешён только после письменной приёмки задачи 0.1 PM/reviewer.


### Задача 0.2 — подключение и квоты

22.09.2026 выполнен консервативный live-протокол из девяти последовательных HTTPS-запросов к `direct.lptracker.ru` без business writes. Подтверждены: один login, использование token для `GET /projects` и обоих разрешённых тестовых проектов, logout и отклонение отозванного token; прикладные ошибки unauthenticated/invalid/revoked token и unknown route пришли как HTTP 200 + JSON error.

Документальный лимит 3 req/s не проверялся burst-тестом и не считается runtime-фактом. Область квоты, egress IP, правило одного пользователя на IP, repeated login, token expiry и invalid-password/lockout не проверялись из-за нетестового аккаунта и ограничения безопасным request budget.

Результат передан PM/reviewer. Следующая задача разрешена только после письменной приёмки 0.2.

### Задача 0.3 — изоляция сложных объектов

22.09.2026 выполнен один частичный live-run из 15 последовательных запросов. C01 без contact-фильтра получил error 400 в обоих тестовых проектах. Для двух новых synthetic contacts подтверждены parent project/detail mapping, отсутствие parent/project в direct C06, успешный C07 edit/readback. Plural C08 route вернул HTTP 200 + JSON error 400; singular route не пробовался.

После STOP оба probe parent contacts удалены с JSON success и выполнен logout. Отдельный post-error GET detail и проверка каскада после parent delete не выполнялись. Staff и labels subsets не достигнуты; task/lead subset не запускался из-за недоказанной notification safety без safe test staff ID.

Результат передан как **ARCHITECT REVIEW REQUIRED / BLOCKED dependent subset**. До решения по parent mapping/bootstrap details и безопасному staff/task fixture зависимая часть C06–C08/T01–T04 не может считаться закрытой; issue #4 остаётся открытым.
#### Rework 1 по архитектурному решению issue #4

22.09.2026 выполнены только разрешённые независимые subsets: fresh singular C08, schema-only `GET /staff`, lifecycle двух новых labels. Протокол: 23 последовательных запроса, один login/logout, unknown writes=0, STOP отсутствовал.

Singular C08 вернул JSON error 404; C06/C03 readback доказал, что detail и parent сохранились. Staff endpoint account-scoped и не вернул project-membership fields. Labels M01–M04 созданы, подтверждены project lists, изменены, удалены и подтверждённо отсутствуют после cleanup в обоих тестовых проектах.

Архитектура и порядок плана не менялись. Task/owner/observer runtime не выполнялся и остаётся заблокированным до safe staff fixture. Для C08 и out-of-band details нужен официальный ответ поставщика. Результат передан на повторный PM/architect review; 0.4 и реализация не начинаются, issue #4 остаётся открытым.

#### Предложение архитектора по scope v1 — 23.09.2026

Получено предложение для review: первая версия содержит 57 операций, а `C08`, `S01`, `L11`, `L22`, `T01`, `T04`, `T05` откладываются. Это не принятие задачи 0.3, продуктового scope или G0. Все 64 операции и исходные live-факты сохраняются; причины и условия возврата deferred зафиксированы в реестре.

В предложенном v1 остаётся `C05` с project precheck; успешный cleanup не доказывает все каскады. `C06–C07` допускаются только по доверенной server-side mapping detail→parent→project с повторной проверкой parent; detail только по ID получает нейтральный отказ. В `L01/L06` запрещено неподтверждённое назначение owner/observers. `T02–T03` требуют project check и очистки вложенных staff fields.

Коды `400/404/501` не утверждены: их должен согласовать CRM-контракт в 0.5. Порядок `0.3 → 0.4 → 0.5 → G0 → 1.1` не меняется.
