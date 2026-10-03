"""
gregory/editor_history.py

Who made a change, for the history tables that carry ``EditorHistoryMixin``.

The ``stamp_editor_on_history`` signal (gregory/signals.py) asks
``current_editor()`` for the answer. There are two ways to supply it:

  - ``editing_as(user, via)``: an explicit scope. Used by code that acts for a
    person without that person being ``request.user`` -- the MCP editor
    endpoints run as the editor the service credential vouches for.
  - The request that simple-history's middleware stashes: an API key means
    ``via="api_key"``, a signed-in user means ``via="admin"`` when the request
    is an admin page (or whatever the view put in ``request.editor_via``).

A change made outside any request (a management command, the shell) has no
editor and stays blank, which is how a pipeline write differs from a person's.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import NamedTuple, Optional

_explicit_editor: ContextVar = ContextVar("gregory_history_editor", default=None)


class Editor(NamedTuple):
	user: Optional[object]
	label: str
	via: str


def editor_label(user) -> str:
	"""Name and email as they are now, for the audit trail to keep."""
	name = (user.get_full_name() or user.get_username() or "").strip()
	email = (getattr(user, "email", "") or "").strip()
	return (f"{name} <{email}>" if email else name)[:300]


@contextmanager
def editing_as(user, via: str):
	"""Attribute history rows written inside the block to ``user`` via ``via``."""
	token = _explicit_editor.set(Editor(user, editor_label(user), via))
	try:
		yield
	finally:
		_explicit_editor.reset(token)


def current_editor(request) -> Optional[Editor]:
	"""The editor for a history row being written now, or None.

	``request`` is the one simple-history's middleware stashed, or None.
	"""
	explicit = _explicit_editor.get()
	if explicit is not None:
		return explicit
	if request is None:
		return None
	# A SimpleLazyObject (ApiKeyMiddleware) wrapping None is never `is None`;
	# truthiness is what sees through it.
	if getattr(request, "api_access_scheme", None):
		return Editor(None, "", "api_key")
	user = getattr(request, "user", None)
	if user is None or not user.is_authenticated:
		return None
	via = getattr(request, "editor_via", "")
	if not via:
		match = getattr(request, "resolver_match", None)
		via = "admin" if match is not None and "admin" in match.namespaces else ""
	return Editor(user, editor_label(user), via)
