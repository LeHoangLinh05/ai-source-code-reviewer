# AGENTS.md

# Senior Python Engineering Rules

## Mission

Write production-grade Python code.

Optimize for:

1. Correctness
2. Readability
3. Maintainability
4. Simplicity
5. Performance

Never sacrifice readability for cleverness.

---

# Core Principles

Follow:

* KISS
* SOLID
* DRY
* Explicit over implicit
* Composition over inheritance

Assume code will be maintained for years.

Code is read more often than written.

---

# Python Version

Target:

```text
Python 3.11+
```

Use modern syntax.

Prefer:

```python
str | None
list[str]
dict[str, int]
```

Avoid legacy typing syntax unless required.

---

# Architecture Rules

Use:

```text
API
↓
Service
↓
Repository
↓
Database
```

Dependencies must only flow downward.

Never:

```text
Repository -> Service
Database -> API
```

Business logic belongs in services.

Routes must remain thin.

Repositories must contain only persistence logic.

---

# Project Structure

```text
src/
├── api/
├── services/
├── repositories/
├── models/
├── schemas/
├── core/
├── utils/
└── tests/
```

Avoid dumping everything into:

```text
utils.py
helpers.py
common.py
```

Create domain-focused modules.

---

# Naming

Names must describe intent.

Bad:

```python
data
obj
tmp
result2
```

Good:

```python
user_profile
payment_status
invoice_items
```

Boolean variables:

```python
is_active
has_access
can_update
```

Functions must use verbs.

```python
create_user()
validate_token()
send_email()
```

Classes use PascalCase.

```python
UserService
OrderRepository
EmailSender
```

Constants use uppercase.

```python
MAX_RETRIES = 5
```

---

# Functions

Requirements:

* One responsibility
* Small and focused
* Prefer <= 30 lines
* Prefer <= 3 nesting levels

Use early returns.

Good:

```python
def validate_user(user: User) -> bool:
    if not user.is_active:
        return False

    return True
```

Bad:

```python
def validate_user(user):
    if user:
        if user.is_active:
            return True
        else:
            return False
```

Avoid side effects.

Functions should be predictable.

---

# Type Hints

Required for:

* public functions
* service methods
* repository methods
* API boundaries

Good:

```python
def get_user(user_id: int) -> User:
    ...
```

Bad:

```python
def get_user(user_id):
    ...
```

Avoid:

```python
Any
```

unless unavoidable.

Use strict typing.

---

# Dataclasses

Use dataclasses for pure data structures.

```python
from dataclasses import dataclass

@dataclass(slots=True)
class User:
    id: int
    email: str
```

Prefer:

```python
slots=True
```

when appropriate.

---

# Dependency Injection

Always inject dependencies.

Good:

```python
class UserService:
    def __init__(
        self,
        repository: UserRepository,
    ):
        self.repository = repository
```

Bad:

```python
class UserService:
    def __init__(self):
        self.repository = PostgresRepository()
```

Never instantiate infrastructure dependencies inside business services.

---

# Imports

Order:

1. Standard library
2. Third-party
3. Local modules

Example:

```python
from pathlib import Path

from fastapi import APIRouter

from app.services.user import UserService
```

Never:

```python
from module import *
```

---

# Error Handling

Catch specific exceptions only.

Good:

```python
try:
    process()
except ValueError:
    ...
```

Bad:

```python
try:
    process()
except:
    pass
```

Never silently ignore exceptions.

Always preserve debugging information.

Raise meaningful errors.

---

# Logging

Use logging.

Never use print in production code.

Use:

```python
logger = logging.getLogger(__name__)
```

Log:

* failures
* retries
* external API calls
* important state transitions

Never log:

* passwords
* tokens
* secrets
* personal data

---

# FastAPI Rules

Routes must contain no business logic.

Bad:

```python
@router.post("/users")
async def create_user():
    # business logic
```

Good:

```python
@router.post("/users")
async def create_user(
    payload: UserCreate,
    service: UserService,
):
    return await service.create(payload)
```

Use:

* APIRouter
* dependency injection
* pydantic validation

Keep controllers thin.

---

# Pydantic

Use Pydantic v2.

Validate all external input.

Never trust:

* request payloads
* headers
* query params
* files

Use schema models.

Avoid raw dictionaries.

---

# SQLAlchemy

Use SQLAlchemy 2.0 style.

Good:

```python
stmt = select(User)
```

Bad:

```python
session.query(User)
```

Repositories own database access.

Services must not write SQL.

---

# Async Rules

Use async only for I/O.

Do not use async for CPU-heavy work.

Prefer:

```python
asyncio.TaskGroup
```

for concurrent tasks.

Avoid fire-and-forget tasks.

Every async task must be awaited or supervised.

---

# Security

Never hardcode:

* passwords
* API keys
* secrets
* tokens

Use environment variables.

Always validate input.

Always use parameterized queries.

Use:

```python
yaml.safe_load()
```

Never:

```python
yaml.load()
```

Never deserialize untrusted pickle data.


# Configuration Management

Centralize all runtime configuration.

Use a single typed settings module based on Pydantic Settings.

Preferred location:

```text
src/core/config.py
```

Example:

```python
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")
    ai_model: str = Field(default="gpt-4o-mini", alias="AI_MODEL")
    request_timeout_seconds: int = Field(
        default=30,
        alias="REQUEST_TIMEOUT_SECONDS",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Only the configuration module may directly access:

* environment variables
* `.env` files
* `os.getenv`
* `os.environ`

Never read environment variables directly inside:

* API routes
* services
* repositories
* database models
* Celery tasks
* domain logic

Bad:

```python
timeout = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
```

Good:

```python
class ReviewService:
    def __init__(
        self,
        settings: Settings,
    ) -> None:
        self.timeout_seconds = settings.request_timeout_seconds
```

Configuration must be:

* typed
* validated at application startup
* injected into dependent components
* defined in one place
* documented in `.env.example`

Do not duplicate environment variable names or default values across modules.

Required configuration must fail fast at startup.

Do not silently use fallback values for:

* secrets
* database connections
* authentication configuration
* external service credentials

Do not place values in environment variables merely because they are literals.

Use environment variables only for values that may vary between deployments or contain sensitive data.

---

# Constants and Magic Values

Do not embed unexplained literals directly in business logic.

Magic values include:

* numeric limits
* timeout durations
* retry counts
* cache TTL values
* queue names
* Redis key prefixes
* HTTP header names
* repeated status strings
* repeated error codes
* file size limits
* model names
* external URLs

Bad:

```python
if attempt >= 3:
    ...

await asyncio.sleep(5)

if review.status == "processing":
    ...

redis.setex(f"review:{review_id}", 3600, result)
```

Good:

```python
MAX_REVIEW_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5
REVIEW_CACHE_TTL_SECONDS = 60 * 60
REVIEW_CACHE_PREFIX = "review"
```

Use enums for closed sets of domain values.

```python
from enum import StrEnum


class ReviewStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
```

Then use:

```python
if review.status is ReviewStatus.PROCESSING:
    ...
```

Keep constants close to their domain.

Prefer domain-focused modules such as:

```text
src/reviews/constants.py
src/reviews/enums.py
src/core/config.py
```

Do not create one global `constants.py` containing unrelated values.

A literal may remain inline when its meaning is obvious, local, and unlikely to change independently.

Acceptable examples:

```python
items[0]
value + 1
range(3)
```

Do not extract constants mechanically when doing so reduces readability.

---

# Repeated Identifiers and Aliases

Do not repeat infrastructure identifiers across modules.

Centralize repeated:

* environment variable names
* Redis key prefixes
* Celery queue names
* event names
* HTTP header names
* database constraint names
* external endpoint paths
* storage bucket names
* collection names

Bad:

```python
redis.get(f"review-result:{review_id}")
redis.delete(f"review-result:{review_id}")
```

Good:

```python
REVIEW_RESULT_KEY_PREFIX = "review-result"


def build_review_result_key(review_id: UUID) -> str:
    return f"{REVIEW_RESULT_KEY_PREFIX}:{review_id}"
```

Create a helper only when formatting, validation, or construction logic is reused.

Do not create aliases that merely rename a literal without adding domain meaning.

Bad:

```python
THREE = 3
NAME = "processing"
```

Good:

```python
MAX_REVIEW_ATTEMPTS = 3
PROCESSING_STATUS = "processing"
```

Prefer enums over standalone string constants when the value belongs to a closed domain set.

---

# Testing

Framework:

```text
pytest
```

Test behavior.

Do not test implementation details.

Structure:

```text
tests/
├── unit/
├── integration/
└── e2e/
```

Use fixtures.

Mock:

* external APIs
* queues
* email services
* payment providers

Avoid excessive mocking.

---

# Code Smells

Refactor immediately when you see:

* God classes
* Duplicate code
* Deep nesting
* Long parameter lists
* Hidden side effects
* Circular imports
* Large files

Avoid overengineering.

Do not introduce abstractions before they are needed.

Apply Rule of Three.

---

# Clean Code

Remove:

* dead code
* commented code
* unused imports
* unused variables

Replace magic values.

Bad:

```python
if retries > 7:
```

Good:

```python
MAX_RETRIES = 7

if retries > MAX_RETRIES:
```

Code should be understandable without comments.

Comments explain WHY.

Code explains WHAT.

---

# Ruff

Code must pass:

```bash
ruff check .
ruff format --check .
```

Enable at minimum:

* `E` — pycodestyle errors
* `F` — pyflakes
* `I` — isort
* `B` — bugbear
* `UP` — pyupgrade
* `SIM` — simplify
* `PL` — pylint rules
* `S` — security rules
* `RUF` — Ruff-specific rules

Enable `PLR2004` to detect magic values in comparisons.

Configure justified ignores explicitly in `pyproject.toml`.

Do not add blanket `noqa` comments to bypass violations.

Static analysis does not detect every architectural magic value or duplicated identifier.
AI review and code review must still check for repeated strings, configuration access, and misplaced constants.

---

# Mypy

Code must pass:

```bash
mypy .
```

Requirements:

* no implicit Any
* strict mode preferred
* typed public interfaces

---

# Pre-Commit

Required hooks:

* ruff
* mypy
* pytest

No commit should bypass validation.

---

# AI Agent Constraints

Never:

* create unnecessary abstractions
* introduce patterns without justification
* create factories for a single implementation
* create interfaces with only one implementation
* create utility classes full of static methods

Prefer simpler solutions first.

When solving a problem:

1. Use existing code patterns.
2. Reuse existing modules.
3. Minimize new files.
4. Minimize complexity.
5. Explain tradeoffs.

Do not rewrite working code without a reason.

Do not perform large refactors unless explicitly requested.

---

# Definition of Done

A task is complete only if:

* Code runs
* Tests pass
* Ruff passes
* Mypy passes
* No duplicated logic
* No dead code
* No secrets exposed
* Runtime configuration is centralized and typed
* No direct environment access outside the configuration layer
* No unexplained magic values or duplicated infrastructure identifiers
* Type hints added
* Architecture rules respected
* Code is understandable without explanation
