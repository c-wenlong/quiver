"""One page file for ``GET /v1/sessions/{id}/preview?path=``.

When a tool call writes an ``.html``, ``.htm`` or ``.svg`` file, the
phone can ask for that file as it is on disk now and draw it in a locked
web view. This module reads it, held to the same folder confinement as
the ``@`` file search (``files.py``):

- ``path`` is relative to the session's working folder. An absolute
  path, a ``..`` part or a NUL is refused before anything touches disk.
- The path is resolved with ``realpath`` and must stay inside the
  resolved working folder, so a symlink pointing out is refused.
- The resolved file is then opened one folder at a time from the root
  with ``O_NOFOLLOW`` (``files.open_dir``), so a part swapped for a
  link after the check can't lead outside, and the open itself refuses
  a final link.
- Both the requested name and the resolved name must carry a preview
  extension, so a ``page.html`` link can't serve another file type.
- Only a regular file is read, at most ``MAX_BYTES``; a FIFO or device
  node is refused without blocking on it.

The hub answers with the file's type, ``Cache-Control: no-store`` and
``CSP`` as defence in depth: the client draws the page with scripts and
network off, and the header keeps a browser that opens the response
itself to the same rules.
"""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass

from swe.remote import files
from swe.remote.files import FilesError

# Big enough for a mockup with inlined images, small enough that a
# phone on a slow link isn't asked to pull a build artefact.
MAX_BYTES = 2 << 20
MAX_PATH_LEN = 4096

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
}

# No scripts, no fetches, no forms, no plugins; inline styles and data:
# images and fonts still draw. ``sandbox`` with no tokens also gives the
# page an opaque origin, so it can't read the hub origin's storage.
CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "font-src data:; media-src data:; base-uri 'none'; "
    "form-action 'none'; sandbox"
)

_FILE_FLAGS = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


@dataclass(frozen=True)
class Preview:
    path: str  # as requested, relative to the working folder
    content_type: str
    body: bytes
    mtime: float


def for_session(
    row: dict,
    *,
    hub_machine_id: str,
    machines: dict[str, dict],
    path: str,
) -> Preview:
    """The route's answer for one catalog row; raises ``FilesError``."""
    cwd = files.local_cwd(
        row, hub_machine_id=hub_machine_id, machines=machines,
        code="preview_unavailable", what="file preview",
    )
    return read(cwd, path)


def content_type(name: str) -> str | None:
    """The response type for a preview file name, None for any other."""
    dot = name.rfind(".")
    if dot <= name.rfind("/"):
        return None
    return TYPES.get(name[dot:].lower())


def read(cwd: str, rel: str) -> Preview:
    rel = str(rel or "")
    if not rel or len(rel) > MAX_PATH_LEN or not files.safe_rel(rel):
        raise FilesError(
            422, "bad_path",
            "path must be relative to the working folder, without ..",
        )
    if content_type(rel) is None:
        raise FilesError(
            415, "unsupported_type", "only .html, .htm and .svg preview",
        )
    root = files.resolve_root(cwd)
    real = files.confine(root, rel)
    if real is None:
        raise FilesError(
            403, "outside_folder", "that path leaves the working folder",
        )
    if real == root:
        raise FilesError(422, "not_a_file", f"not a file: {rel}")
    # The type of what is served: a link inside the folder may name an
    # .svg that is really an .html, and a target that isn't a page
    # file at all is refused.
    ctype = content_type(real)
    if ctype is None:
        raise FilesError(
            415, "unsupported_type", "only .html, .htm and .svg preview",
        )
    body, mtime = _read_inside(root, real, rel)
    return Preview(rel, ctype, body, mtime)


def _read_inside(root: str, real: str, rel: str) -> tuple[bytes, float]:
    """``real`` (a ``confine`` result) read through descriptors opened
    from ``root`` down, none of them through a symlink."""
    sub = real[len(root.rstrip("/")) + 1:]
    parent, _sep, name = sub.rpartition("/")
    dfd = files.open_dir(root, parent)
    if dfd is None:
        raise FilesError(404, "not_found", f"no such file: {rel}")
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=dfd)
    except OSError as exc:
        raise _open_error(exc, rel) from None
    finally:
        os.close(dfd)
    try:
        st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise FilesError(403, "not_readable", f"can't read {rel}") from None
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise FilesError(422, "not_a_file", f"not a file: {rel}")
    if st.st_size > MAX_BYTES:
        os.close(fd)
        raise _too_large()
    with os.fdopen(fd, "rb") as fh:
        try:
            body = fh.read(MAX_BYTES + 1)
        except OSError:
            raise FilesError(403, "not_readable", f"can't read {rel}") from None
    if len(body) > MAX_BYTES:  # grew after the fstat
        raise _too_large()
    return body, st.st_mtime


def _open_error(exc: OSError, rel: str) -> FilesError:
    if exc.errno in (errno.ENOENT, errno.ENOTDIR):
        return FilesError(404, "not_found", f"no such file: {rel}")
    if exc.errno == errno.ELOOP:
        # Swapped for a link between the check and the open.
        return FilesError(
            403, "outside_folder", "that path leaves the working folder",
        )
    return FilesError(403, "not_readable", f"can't read {rel}")


def _too_large() -> FilesError:
    return FilesError(
        413, "too_large", f"file is over the {MAX_BYTES >> 20} MB preview cap",
    )
