## Working agreements

- Prefer `uv` when installing dependencies.

## Code style

- Use type annotations for Python code: function and method arguments, return types.
- In Python, use `assert` only in tests. In application and library code,
  use explicit conditionals with appropriate exceptions or established
  fallback behavior for validation and invariant checks.
- For type narrowing, use explicit guards such as `if value is None:`.
  Do not add assertions solely to satisfy a type checker or suppress
  Bandit B101 to permit them.

## Compatibility comments

- Add an inline code comment when compatibility requirements cause non-obvious
  complexity, a fallback, or the use of an older API instead of a modern Python API.
- Name the affected dependency or runtime and the relevant version or version
  range, for example, "Support for pytest 7" or "Support for Python 3.11–3.13".
  Explain the behavior or API limitation that makes the code necessary.
- Keep comments accurate: distinguish behavior shared by supported versions from
  a workaround needed only by older versions. Do not invent version boundaries.
- Update or remove compatibility comments and workarounds when support changes.
