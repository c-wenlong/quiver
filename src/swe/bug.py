"""``swe bug``: open a GitHub issue form with this machine's versions filled in.

The query names are the field ids in ``.github/ISSUE_TEMPLATE`` and ``area``
is one of the forms' dropdown options word for word. The Mac app builds the
same links (``app/mac/Sources/SWECore/Support/IssueReport.swift``), so a
change to a form's ids or options changes both. Reads no config, like
``swe version``, so it still works when ``~/.swe`` is what is broken.
"""

import platform
import webbrowser
from urllib.parse import quote, urlencode

from swe import __version__
from swe.console import c
from swe.json_output import emit, error_stream, take_json_flag

REPOSITORY = "https://github.com/c-wenlong/quiver"
CLI_AREA = "CLI (swe)"
TEMPLATES = {"bug": "bug_report.yml", "feature": "feature_request.yml"}


def os_line() -> str:
    """"macOS 26.1", the distro's own name on Linux, else system and release."""
    system = platform.system()
    if system == "Darwin":
        release = platform.mac_ver()[0]
        return f"macOS {release}" if release else "macOS"
    if system == "Linux":
        try:
            name = platform.freedesktop_os_release().get("PRETTY_NAME")
        except OSError:
            name = None
        return name or f"Linux {platform.release()}"
    return f"{system} {platform.release()}".strip()


def version_line() -> str:
    return f"swe {__version__} · Python {platform.python_version()}"


def issue_url(kind: str, version: str, os_name: str) -> str:
    """The new-issue form for ``kind``, area picked. A bug also gets the
    version and OS fields; a feature request has neither."""
    query = {"template": TEMPLATES[kind], "area": CLI_AREA}
    if kind == "bug":
        query.update(version=version, os=os_name)
    # quote, not quote_plus: GitHub reads "+" as a space, so a literal
    # "+" must go out as %2B and a space as %20.
    return f"{REPOSITORY}/issues/new?{urlencode(query, quote_via=quote)}"


def cmd_bug(args):
    args, json_view = take_json_flag(args)
    kind = "bug"
    print_only = False
    for arg in args:
        if arg == "--feature":
            kind = "feature"
        elif arg == "--print":
            print_only = True
        else:
            print(c("red", f"Unexpected arg: {arg}"), file=error_stream(json_view))
            return 1
    version, os_name = version_line(), os_line()
    url = issue_url(kind, version, os_name)
    if json_view:
        return emit({"kind": kind, "url": url, "version": version, "os": os_name})
    if print_only or not webbrowser.open(url):
        print(url)
        return 0
    label = "feature request" if kind == "feature" else "bug report"
    print(f"Opened a {label} in your browser, with {version} and {os_name} filled in.")
    print(c("dim", "Nothing is sent until you submit the form. Add `swe doctor` output if it shows a problem."))
    return 0
