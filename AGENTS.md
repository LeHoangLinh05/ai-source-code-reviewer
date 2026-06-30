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
ruff format .
```

Enable:

* pyflakes
* pycodestyle
* bugbear
* isort
* pyupgrade
* simplify

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
* Type hints added
* Architecture rules respected
* Code is understandable without explanation

```
```
