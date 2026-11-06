"""A tiny in-memory todo list, used as the sdlc plugin's test fixture."""

_todos = []


def add(title):
    """Add a todo and return its id."""
    if not title or not title.strip():
        raise ValueError("title must not be empty")
    todo = {"id": len(_todos) + 1, "title": title.strip()}
    _todos.append(todo)
    return todo["id"]


def list_todos():
    """Return all todos, oldest first."""
    return [dict(t) for t in _todos]


def reset():
    _todos.clear()
