# AI Dev Agent

AI Dev Agent - это MVP FastAPI-сервиса для автоматического ревью merge request.

Сервис получает Jira-задачу и GitLab merge request, загружает описание задачи, MR diff и проектный контекст из `AGENT.md`, отправляет данные в LLM, получает structured JSON с замечаниями и публикует комментарии обратно в GitLab.

Текущая версия сфокусирована на навыке `code_review`.

---

## Что умеет текущая версия

- получать задачу из Jira Cloud;
- получать merge request и diff из GitLab;
- загружать `AGENT.md` из target branch MR;
- формировать prompt для LLM;
- получать strict JSON response от LLM;
- валидировать ответ через Pydantic;
- публиковать inline-комментарии в GitLab;
- делать fallback в обычный MR-комментарий, если inline-привязка невозможна;
- писать application logs и HTTP request logs;
- запускаться через Docker / Docker Compose;
- предоставлять diagnostics endpoints для проверки Jira, GitLab и LLM.

---

## Основной сценарий

```text
Jira task + GitLab MR diff + AGENT.md
        |
        v
AI Dev Agent
        |
        v
LLM review
        |
        v
GitLab comments
```

Ручной запуск ревью:

```text
POST /manual/review
```

---

## Архитектура

Текущий MVP уже разделён на основные слои:

```text
app/
├── main.py
├── api/
│   ├── dependencies.py
│   └── routes/
│       ├── health.py
│       ├── manual/
│       │   └── code_review.py
│       └── diagnostics/
│           ├── gitlab.py
│           ├── jira.py
│           └── llm.py
├── application/
│   └── skills/
│       └── code_review/
├── components/
│   ├── diff/
│   ├── llm/
│   └── review/
├── core/
├── domain/
├── infrastructure/
│   ├── gitlab/
│   └── jira/
└── schemas/
```

Целевая архитектура описана отдельно в [`ARCHITECTURE_TARGET.md`](ARCHITECTURE_TARGET.md).

---

## Требования

Для запуска нужны:

- доступ к GitLab API;
- доступ к Jira Cloud API;
- OpenAI-compatible LLM provider.

---

### Переменные окружения

| Переменная | Назначение |
|---|---|
| `MODEL_LLM` | Модель LLM |
| `BASE_URL` | OpenAI-compatible API endpoint |
| `OPENROUTER_API_KEY` | API key для LLM provider |
| `GITLAB_URL` | URL GitLab instance |
| `GITLAB_TOKEN` | GitLab access token |
| `GITLAB_PROJECT_ID` | ID проекта или path вида `group/project` |
| `AGENT_CONTEXT_PATH` | Путь к файлу проектного контекста в репозитории |
| `JIRA_URL` | URL Jira Cloud instance |
| `JIRA_EMAIL` | Email пользователя Jira |
| `JIRA_API_TOKEN` | Jira API token |
| `LOG_DIR` | Директория логов |
| `LOG_LEVEL` | Уровень логирования |
| `LOG_FILE_NAME` | Имя текущего log file |
| `LOG_BACKUP_DAYS` | Количество дней хранения log files |

---

## GitLab token

GitLab token должен позволять:

- читать проект;
- читать merge requests;
- читать repository files;
- читать diff;
- создавать комментарии в merge request.

Достаточно прав:

```text
api
read_api
read_repository
```

---

## Jira API token

Для Jira Cloud используется Basic Auth:

```text
email + api_token
```

Текущий клиент ожидает Jira Cloud REST API v3:

```text
/rest/api/3
```

Важно: сейчас Jira custom fields захардкожены в `app/infrastructure/jira/client.py`:

```text
customfield_10040   # MR URL
customfield_10039   # Reviewers
customfield_10041   # Task type
```

---

## API

### Health

```text
GET /health
GET /ready
```

---

### Swagger UI

```text
http://127.0.0.1:18010/docs
```

---

### LLM diagnostics

Проверить, что LLM provider доступен:

```text
POST /diagnostics/llm/check
```

Request:

```json
{
  "message": "Проверь, что LLM работает"
}
```

Response:

```json
{
  "ok": true,
  "model": "openai/gpt-4.1-mini",
  "input": "Проверь, что LLM работает",
  "output": "LLM работает."
}
```

---

### Jira diagnostics

Получить задачу из Jira:

```text
GET /diagnostics/jira/task/{issue_key}
```

---

### GitLab diagnostics

Получить MR и diff:

```text
GET /diagnostics/gitlab/mr/{mr_iid}
```

Получить `AGENT.md` или другой файл из репозитория:

```text
GET /diagnostics/gitlab/agent-context?file_path=AGENT.md&ref=main
```

Создать тестовый inline-комментарий:

```text
POST /diagnostics/gitlab/mr/{mr_iid}/inline-comment
```

Request:

```json
{
  "body": "Test comment",
  "new_path": "app/service.py",
  "new_line": 42
}
```

---

### Manual code review

Запустить ревью вручную:

```text
POST /manual/review
```

Request:

```json
{
  "jira_issue_key": "PROJ-123",
  "mr_iid": 7
}
```

---

## Файл проектного контекста

По умолчанию агент загружает из репозитория файл:

```text
AGENT.md
```

Файл берётся из target branch merge request.

`AGENT.md` должен быть коротким и полезным для ревью diff:

- назначение проекта;
- основные директории;
- архитектурные правила;
- важные ограничения;
- типичные ошибки;
- соглашения по API/backend/frontend.

Не стоит превращать `AGENT.md` в большую документацию. Его задача - помочь модели ревьюить конкретный diff.

---

## Как публикуются комментарии

Агент поддерживает режимы публикации:

| Режим | Описание |
|---|---|
| `inline` | Комментарий опубликован на конкретную строку diff |
| `mr_note` | Обычный комментарий в MR |
| `mr_note_fallback` | Inline-привязка не удалась, замечание опубликовано как MR note |
| `failed` | Публикация не удалась |

Для line-level замечаний LLM возвращает не номер строки, а `anchor_text`. Сервис сам ищет этот фрагмент среди добавленных строк diff и вычисляет `new_line`.

---

## Ограничения текущей версии

- нет PostgreSQL;
- нет Qdrant;
- нет RAG по Confluence;
- нет очередей задач;
- нет фоновой обработки;
- нет `Assignment` и `Job`;
- нет webhook endpoint для GitLab/Jira;
- нет авторизации входящих запросов к FastAPI;
- нет защиты от повторной публикации одинаковых комментариев;
- нет хранения истории ревью;
- нет retry-механизма для LLM/GitLab/Jira;
- inline-комментарии ставятся только на добавленные строки diff;
- большие diff могут превышать context window модели;
- Jira custom fields сейчас завязаны на конкретную конфигурацию.

---

## Статус проекта

Текущий статус: рабочий MVP AI code review agent.

Основной end-to-end сценарий:

```text
Jira task + GitLab MR diff + AGENT.md -> LLM review -> GitLab comments
```

Дальнейшее развитие описано в `ARCHITECTURE_TARGET.md`.
