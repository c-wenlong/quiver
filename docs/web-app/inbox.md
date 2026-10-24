# Inbox

The Inbox page (sidebar id `inbox`) shares its `gh` cache, setup card and
styles with Repositories, so both are documented together in
[repositories.md](repositories.md#inbox): the `GET /v1/app/inbox` payload,
the `POST /v1/app/inbox/read` write (one row, or every notification after
an inline confirm), whose `gh` login it uses (the hub machine's), and
what differs from the Mac app.
