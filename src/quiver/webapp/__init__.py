"""The web app: the Mac app's pages as HTML, served by the remote hub.

``static/`` holds the pages (plain HTML, CSS and JS, no build step) and
``api.py`` builds the JSON they read from ``/v1/app/*``. The hub mounts
both, so the web app shares the hub's port, Tailscale Serve address and
paired device credentials. Nothing here touches the native app in
``app/mac/``; see docs/web-app.md.
"""
