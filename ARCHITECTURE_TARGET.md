## Архитектурная идея

Проект строится как assignment-centric modular monolith.

Центральная сущность агента - поручение (`Assignment`).
Поручение описывает работу, которую агент должен выполнить как виртуальный сотрудник разработки.

Code review - только один из навыков агента, а не центр архитектуры.

Основной поток:
External event / command
→ AgentEvent
→ Assignment
→ Job
→ Skill workflow
→ Artifacts / Reports / Comments / Questions

Поддерживаемые manual endpoints могут запускать skill workflow напрямую, минуя Assignment/Job, для разработки, ручного запуска и демонстрации.

## Как использовать этот файл

Этот файл является архитектурным ориентиром для постепенной миграции проекта.

При изменении кода нужно:
1. Сохранять рабочее поведение.
2. Делать изменения маленькими вертикальными шагами.
3. Не создавать пустые будущие модули.
4. Не переписывать весь проект за один раз.
5. Сначала переносить текущий code review workflow в новую структуру.
6. Добавлять Assignment/Job/Event постепенно, не ломая поддерживаемые ручные и диагностические endpoints.


## Основные понятия

### Event

Событие из внешней системы, команды пользователя или ручного API-запроса.

Примеры:
- GitLab MR создан или обновлён;
- агент назначен reviewer;
- пользователь написал комментарий;
- Jira-задача перешла в статус Review;
- пользователь отправил команду в Mattermost.

### Assignment

Поручение агенту.

Примеры:
- провести ревью MR;
- ответить на комментарий;
- проанализировать Jira-задачу;
- подготовить план реализации;
- найти место изменения в кодовой базе;
- написать код и создать MR.

### Job

Технический запуск поручения.

Один `Assignment` может иметь несколько `Job`, например при retry, ошибке интеграции или повторном запуске.

### Skill

Навык агента - это application workflow, а не отдельный микросервис и не plugin runtime.

Навык использует общие компоненты и инфраструктуру:
- GitLab;
- Jira;
- Confluence;
- Mattermost;
- PostgreSQL;
- Qdrant;
- LLM;
- code search;
- RAG;
- notification/communication services.

## Правила для skills

Skill содержит orchestration конкретного рабочего сценария.

Skill может:
- загружать данные через infrastructure clients;
- вызывать components;
- строить prompt;
- вызывать LLM;
- сохранять/возвращать результат;
- инициировать публикацию комментариев или отчётов.

Skill не должен:
- напрямую содержать низкоуровневый HTTP-код для GitLab/Jira;
- содержать SQL-запросы;
- быть отдельным микросервисом;
- иметь собственный plugin runtime;
- дублировать общие компоненты.

## Важные архитектурные правила

- Повторное ревью не является отдельным навыком. Это режим работы `code_review`, который определяется по истории комментариев, review runs и состоянию MR.
- `api/` не содержит бизнес-логику. Он только принимает запросы и передаёт их в application layer.
- `application/` содержит orchestration: routing, jobs, runtime и skill workflows.
- `domain/` содержит внутренние модели агента.
- `components/` содержит переиспользуемые технические возможности, но не владеет workflow целиком.
- `infrastructure/` содержит адаптеры к внешним системам и БД.
- `schemas/` содержит DTO внешнего API, а не доменные модели.
- `manual/` содержит поддерживаемые ручные endpoints для запуска skill workflows напрямую, без `Assignment`, `Job` и webhook-событий. Эти endpoints нужны для разработки, демонстрации, ручного запуска и smoke-test.
- `diagnostics/` содержит поддерживаемые endpoints для проверки внешних интеграций: Jira, GitLab, загрузка AGENT.md, публикация тестовых комментариев. Это не бизнес-логика агента, а операционный API для диагностики.
- Наличие `manual/` и `diagnostics/` не нарушает assignment-centric архитектуру: основной production flow может идти через `Assignment/Job`, но ручные и диагностические entrypoints остаются допустимыми.


## Правило развития проекта

Не создавать все будущие модули сразу.

Целевая структура описывает направление развития, но в код добавляются только те директории и файлы, которые нужны текущему вертикальному сценарию.



## Целевая файловая структура

При необходимости допускается вносить изменения.

```text
app/                                     # Основной пакет приложения
├── main.py                              # Точка входа FastAPI: создание app, подключение роутов, middleware
│
├── api/                                 # HTTP/API слой: входные точки во внутреннюю систему агента
│   ├── routes/                          # Группы FastAPI endpoint'ов по назначению
│   │   ├── manual/                      # Ручные endpoints для запуска agent workflows без Assignment/Job/Event
│   │   │   └── code_review.py           # Ручной запуск code_review workflow: POST /manual/review
│   │   ├── diagnostics/                 # Поддерживаемые диагностические endpoints для проверки интеграций
│   │   │   ├── jira.py                  # Проверка Jira API: GET /diagnostics/jira/task/{issue_key}
│   │   │   └── gitlab.py                # Проверка GitLab API: MR, AGENT.md, inline-comment endpoints
│   │   ├── assignments.py               # Ручное создание и просмотр поручений агенту
│   │   ├── jobs.py                      # Просмотр, повторный запуск и контроль технических job
│   │   ├── health.py                    # Проверка живости сервиса: /health, /ready
│   │   ├── webhooks_gitlab.py           # Приём событий GitLab: MR, комментарии, reviewer, commits
│   │   ├── webhooks_jira.py             # Приём событий Jira: переходы статусов, обновления задач
│   │   └── webhooks_mattermost.py       # Приём команд и сообщений из Mattermost
│   └── dependencies.py                  # FastAPI Depends: сборка settings, клиентов, сервисов, workflow
│
├── core/                                # Базовая инфраструктура приложения, не связанная с доменной логикой
│   ├── config.py                        # Настройки приложения и переменные окружения
│   ├── errors.py                        # Общие исключения и обработка ошибок
│   ├── logging.py                       # Настройка логирования
│   └── security.py                      # Проверка webhook secret, подписи, auth/security helpers
│
├── domain/                              # Внутренние доменные модели агента
│   ├── events.py                        # События: GitLab/Jira/Mattermost/manual trigger во внутреннем формате
│   ├── assignments.py                   # Поручения агенту: что нужно сделать как виртуальному сотруднику
│   ├── jobs.py                          # Технические запуски поручений: queued/running/succeeded/failed
│   ├── conversations.py                 # Диалоги, thread'ы, вопросы и ответы с пользователями
│   ├── artifacts.py                     # Результаты работы агента: планы, патчи, отчёты, summaries
│   ├── reports.py                       # Статусные отчёты агента: начал, завершил, заблокирован, нужна помощь
│   └── reviews.py                       # Доменные сущности ревью: finding, review run, published comment
│
├── application/                         # Application layer: orchestration, маршрутизация и выполнение сценариев
│   ├── event_router.py                  # Преобразует входные события в решение: реагировать или игнорировать
│   ├── assignment_router.py             # Решает, какое поручение создать из события или команды
│   ├── job_runner.py                    # Запускает job, обновляет статус, обрабатывает ошибки и retry
│   ├── agent_runtime.py                 # Общая среда выполнения агента: запуск workflow, коммуникация, состояние
│   │
│   └── skills/                          # Навыки агента как application workflow, не микросервисы
│       ├── registry.py                  # Реестр навыков: skill_name -> workflow class/factory
│       │
│       ├── code_review/                 # Навык ревью кода: первичное ревью, follow-up, проверка старых comments
│       │   ├── workflow.py              # Основной сценарий ревью: загрузить контекст, вызвать LLM, опубликовать результат
│       │   ├── prompts.py               # Промпты именно для code review
│       │   ├── schemas.py               # Input/output схемы навыка и структурированный ответ LLM для ревью
│       │   └── context_builder.py       # Сбор контекста для ревью: task, MR, diff, AGENT.md, snippets
│       │
│       ├── comment_reply/               # Навык ответа на комментарии пользователей
│       │   ├── workflow.py              # Сценарий ответа в discussion/thread с учётом исходного контекста
│       │   ├── prompts.py               # Промпты для объяснений и ответов на вопросы
│       │   └── schemas.py               # Схемы входа/выхода для ответа на комментарий
│       │
│       ├── task_analysis/               # Навык анализа Jira-задачи
│       │   ├── workflow.py              # Проверка полноты задачи, рисков, противоречий, открытых вопросов
│       │   ├── prompts.py               # Промпты для анализа требований
│       │   └── schemas.py               # Схемы результата анализа задачи
│       │
│       ├── implementation_planning/     # Навык подготовки плана реализации
│       │   ├── workflow.py              # Поиск релевантного кода/документации и генерация плана изменений
│       │   ├── prompts.py               # Промпты для планирования реализации
│       │   └── schemas.py               # Схемы плана, рисков, файлов и шагов реализации
│       │
│       ├── code_implementation/         # Навык написания кода и создания MR
│       │   ├── workflow.py              # Сценарий: создать ветку, изменить файлы, commit, открыть MR
│       │   ├── prompts.py               # Промпты для генерации/исправления кода
│       │   └── schemas.py               # Схемы патчей, изменений, результата генерации
│       │
│       └── codebase_consultation/       # Навык консультации по кодовой базе через чат
│           ├── workflow.py              # Поиск по коду/RAG и ответ разработчику с источниками
│           ├── prompts.py               # Промпты для объяснения кода и поиска решений
│           └── schemas.py               # Схемы вопроса, ответа, источников и найденных фрагментов
│
├── components/                          # Переиспользуемые технические компоненты, не владеющие workflow целиком
│   ├── diff/                            # Парсинг, разбиение, анализ и локализация строк diff
│   ├── llm/                             # Общий клиент LLM, structured output, retry, лимиты
│   ├── context/                         # Общие структуры context pack и сбор reusable-контекста
│   ├── rag/                             # Индексация и поиск по Confluence/документации через Qdrant
│   ├── code_search/                     # Поиск по репозиторию: grep, symbols, usages, similar implementations
│   ├── repository/                      # Работа с локальным mirror/checkout репозитория
│   ├── review/                          # Общие review-компоненты: normalizer, deduplicator, publisher
│   ├── communication/                   # Диалоговая логика агента: вопросы людям, ответы в thread, ожидание input
│   └── notifications/                   # Простые уведомления: started, completed, failed, blocked
│
├── infrastructure/                      # Адаптеры к внешним системам и хранилищам
│   ├── db/                              # SQLAlchemy/session/models/migrations для PostgreSQL
│   ├── repositories/                    # Репозитории БД: assignments, jobs, events, reviews, conversations
│   ├── gitlab/                          # GitLab API client: MR, diff, files, comments, branches, commits
│   ├── jira/                            # Jira API client: задачи, статусы, поля, комментарии
│   ├── confluence/                      # Confluence API client: страницы, spaces, обновления документации
│   ├── mattermost/                      # Mattermost API client: сообщения, threads, slash commands
│   └── qdrant/                          # Qdrant client: коллекции, embeddings, vector search
│
└── schemas/                             # Общие DTO для внешнего API, не доменные модели
    ├── api.py                           # Request/response схемы FastAPI endpoint'ов
    └── common.py                        # Общие API-схемы: pagination, errors, status response
```
