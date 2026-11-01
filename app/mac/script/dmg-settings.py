# dmgbuild settings for SWE's install window: the app on the left, an
# Applications link on the right, the background from
# make-dmg-background.swift behind them. make-dmg.sh passes the paths:
#
#   dmgbuild -s dmg-settings.py -D app=... -D background=... SWE out.dmg
#
# The window and icon geometry must match make-dmg-background.swift.
import os.path

app = defines["app"]  # noqa: F821 (dmgbuild injects `defines`)
app_name = os.path.basename(app)

format = "UDZO"
files = [app]
symlinks = {"Applications": "/Applications"}
background = defines["background"]  # noqa: F821
# dmgbuild copies the background in as .background.tiff; the dot alone
# does not keep Finder from drawing it in the window, the hidden flag does.
hide = [".background.tiff"]

# 640x400pt of content; dmgbuild's bounds include the title bar.
window_rect = ((200, 120), (640, 428))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False

icon_size = 112
# No hide_extensions: dmgbuild sets it with SetFile, and the FinderInfo
# that writes onto the bundle fails codesign --strict. Finder hides .app
# by default anyway.
text_size = 13
arrange_by = None
icon_locations = {
    app_name: (170, 200),
    "Applications": (470, 200),
}
