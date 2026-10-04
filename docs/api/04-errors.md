Every error answers with an HTTP status and one JSON shape: a stable
`code` to act on in your program, a `message` in plain words (the same words
the panels show), `fields` when particular fields are at fault, and a
`reference` that identifies the request in the server log.

**Act on the `code`, not the message** — messages may be reworded; codes never
change within a version. Errors are grouped below by kind; each endpoint's
page lists only the errors that endpoint can return.
