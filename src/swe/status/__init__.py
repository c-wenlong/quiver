"""Service status: the official status pages of the services the harnesses
depend on, read politely and cached.

A Python port of the Mac app's ``QuiverCore/Status`` for the web app's
Service status page:

- ``catalog``: the services, their groups, and the health vocabulary.
- ``catalogue``: the add sheet's entries and its search/group filter.
- ``tracked``: the catalogue pages a user added, in
  ``~/.quiver/data/web-status-services.json``.
- ``parsers``: Atlassian Statuspage (and incident.io) ``summary.json`` and
  Google Cloud's ``incidents.json`` into a ``Report``.
- ``poller``: the polling floors, conditional requests, ``Retry-After``
  cooldowns and the ``~/.quiver/cache/web-service-status.json`` cache.

Stdlib only. Keep the rules in step with the Swift ones.
"""
