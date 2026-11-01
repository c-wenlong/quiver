"""Shared ``--name=value`` handling for command-line flags.

Every value-taking long flag in the CLI is written ``--name=value`` (a
comma-separated list counts as one value). ``expand_value_flags`` normalises
a raw argv list so handlers only ever see an adjacent ``(name, value)`` pair,
and turns a bare ``--name`` into a helpful error instead of silently
swallowing whatever token happened to follow it.
"""


def expand_value_flags(args, names):
    """Rewrite each ``--name=value`` token in ``args`` to a ``(name, value)`` pair.

    ``names`` is the set of long flags that take a value. A bare ``--name``
    (no ``=``) raises ValueError telling the user the ``=`` form is required.
    Everything else — booleans, short flags, positionals — passes through
    untouched, so handlers keep whatever handling they already have.
    """
    out = []
    for arg in args:
        name, sep, value = arg.partition("=")
        if sep and name in names:
            out += [name, value]
        elif arg in names:
            raise ValueError(f"{arg} takes a value: write it as {arg}=<value>")
        else:
            out.append(arg)
    return out
