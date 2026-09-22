# Task 0.2 — подключение, авторизация и безопасный baseline

**Статус:** передано PM/reviewer на проверку  
**Live-окно:** 2026-09-22 10:05:36–10:05:48, Europe/Moscow  
**Upstream:** `https://direct.lptracker.ru`  
**Связано с:** [GitHub issue #2](https://github.com/FrozzIt/API.Botzi/issues/2)

## Границы проверки

Факт: LPTracker-аккаунт не является тестовым; два разрешённых project ID из защищённого локального secret-файла относятся к тестовым проектам. Выполнялись только auth/session operations и безопасные чтения. Business writes, сделки, звонки, файлы, webhooks, invalid-password login, burst и параллельные запросы не выполнялись.

Secret-файл имел mode `600`, принадлежал текущему пользователю и содержал шесть ожидаемых ключей. Значения, token и project IDs не выводились, не сохранялись в отчёт и не передавались в GitHub. Token находился только в памяти процесса и был отозван logout. После проверки временный probe удалён; в каталоге остался только исходный mode-600 secret-файл.

Egress IP не определялся отдельным внешним запросом. Поэтому IP-ограничение и область квоты по IP/account/token/deployment остаются неподтверждёнными.

## Параметры протокола

- Максимальный бюджет: 12 LPTracker-запросов.
- Фактически: **9** запросов.
- Выполнение: строго последовательно.
- Минимальная пауза между запросами: 0,8 секунды.
- Timeout одного запроса: 12 секунд.
- Redirect не разрешался; HTML и не-JSON были STOP conditions.
- Полные response bodies и чувствительные headers не логировались.
- Безопасные метрики: HTTP status, top-level `status`, error code/class, JSON validity, latency, project count и boolean-проверки разрешённых проектов.

## Фактический порядок и результаты

| Seq | Сценарий | HTTP | JSON / top-level | Safe error | Latency | Дополнительный факт |
|---|---|---:|---|---|---:|---|
| R01 | unauthenticated `GET /projects` | 200 | valid / `error` | 401 / auth | 1098 ms | no redirect, no HTML |
| R02 | synthetic invalid token, `GET /projects` | 200 | valid / `error` | 401 / auth | 787 ms | no redirect, no HTML |
| R03 | один real `POST /login` | 200 | valid / `success` | none | 768 ms | непустой token получен в память |
| R04 | authenticated `GET /projects` | 200 | valid / `success` | none | 794 ms | project_count=7; allowed A/B present=true/true |
| R05 | `GET /project/{project_id}`, A | 200 | valid / `success` | none | 801 ms | result object; requested ID matched=true |
| R06 | `GET /project/{project_id}`, B | 200 | valid / `success` | none | 786 ms | result object; requested ID matched=true |
| R07 | безопасный неизвестный GET route | 200 | valid / `error` | 404 / not_found | 779 ms | no redirect, no HTML |
| R08 | `POST /logout` собственного token | 200 | valid / `success` | none | 750 ms | logout подтверждён |
| R09 | `GET /projects` с отозванным token | 200 | valid / `error` | 401 / auth | 767 ms | token больше не принят |

Итоговый процесс завершился без STOP condition: `request_count=9`, `logout_confirmed=true`, `normal_flow_completed=true`.

## Подтверждённые факты

1. **Высокая уверенность:** рабочий endpoint для проверенных операций — HTTPS host `direct.lptracker.ru`; redirect или HTML в девяти сценариях не наблюдались.
2. **Высокая уверенность:** один login вернул JSON success и token; token работал для чтений обоих разрешённых тестовых проектов.
3. **Высокая уверенность:** logout собственного token вернул JSON success; последующий GET с тем же token получил прикладную ошибку 401.
4. **Высокая уверенность:** unauthenticated, invalid-token, revoked-token и unknown-route ошибки вернулись с HTTP 200 и JSON `status=error`.
5. **Средняя уверенность:** наблюдаемый baseline latency для этого короткого окна — min 750 ms, median 786 ms, max 1098 ms. Это не performance test и не SLA.
6. **Высокая уверенность:** authenticated `GET /projects` upstream вернул семь проектов аккаунта, включая оба разрешённых тестовых проекта. Это подтверждает необходимость фильтрации P01 на proxy; число не доказывает доступ клиента к этим проектам.

## Что не подтверждено

- лимит 3 req/s и фактическая реакция на его превышение;
- область квоты: egress IP, account, token или deployment;
- правило одного LPTracker-пользователя на IP и влияние повторного login;
- token lifetime/idle expiry 24 часа;
- поведение invalid-password login и lockout;
- эквивалентность или работоспособность `api.lptracker.ru`;
- заголовки rate limit, request ID поставщика и retry hints;
- malformed JSON/body для write-like endpoints;
- schema/null/empty semantics за пределами наблюдавшихся auth/project ответов;
- tenant isolation внутри upstream: аккаунт видит семь проектов, а ограничение до одного проекта должен обеспечить proxy.

Эти свойства намеренно не проверялись из-за нетестового аккаунта, запрета на burst/repeated login и ограничения задачи безопасными чтениями.

## Безопасное воспроизведение

Перед запуском reviewer проверяет mode и только имена ключей, не значения:

```bash
SECRET_FILE=/secure/path/lptracker.env
stat -f 'mode=%Lp owner=%Su' "$SECRET_FILE"
awk -F= '/^[A-Za-z_][A-Za-z0-9_]*=/{print $1}' "$SECRET_FILE"
```

Live-проверка должна выполняться одним локальным redacting process, который читает следующие env names: `LPTRACKER_TEST_LOGIN`, `LPTRACKER_TEST_PASSWORD`, `LPTRACKER_TEST_SERVICE`, `LPTRACKER_TEST_VERSION`, `LPTRACKER_TEST_PROJECT_A`, `LPTRACKER_TEST_PROJECT_B`. Credentials нельзя подставлять в shell arguments или печатать. Процесс повторяет R01–R09, держит token только в памяти, маскирует project IDs как `{project_id}`, выводит только поля из таблицы выше и гарантирует logout в `finally`.

Минимальные безопасные проверки без real credentials:

```bash
curl --silent --show-error --max-time 12 \
  --header 'Accept: application/json' \
  https://direct.lptracker.ru/projects \
  | jq '{status, error_code: (.errors[0].code // null)}'

curl --silent --show-error --max-time 12 \
  --header 'Accept: application/json' \
  --header 'token: synthetic-invalid-token-for-probe' \
  https://direct.lptracker.ru/projects \
  | jq '{status, error_code: (.errors[0].code // null)}'
```

Real login/logout reproduction допускается только защищённым in-process client; `curl -d` с подстановкой secret env запрещён, потому что credentials могут попасть в process arguments/history.

## Вывод для token manager и limiter

Факт: проверенный lifecycle — login один раз, повторное использование token, logout, последующее отклонение token. Вывод для будущей реализации: login/refresh должен быть координированным, token — внутренним секретом, а logout одного клиентского сеанса не должен транслироваться в общий upstream logout.

До отдельной безопасной проверки квоты limiter остаётся консервативным: не более документированных 3 req/s на всё развёртывание, без утверждения, что эта граница или её область подтверждены runtime.

## Статус передачи

Материалы переданы PM/reviewer. Предлагаемый статус — **ACCEPTED с явно принятыми ограничениями**, поскольку разрешённый безопасный auth/error lifecycle подтверждён, а quota/IP/expiry свойства корректно оставлены неподтверждёнными. Это предложение, не само-приёмка; следующая задача начинается только после письменного решения reviewer.
