# Third-party notices

OpenIRC's original source is provided under the repository's MIT license.

Runtime dependencies retain their own licensing terms:

- Argon2-cffi and its bindings: MIT; bundled Argon2 material carries its upstream notices.
- PyQt6: GPL v3 or a commercial Riverbank license. Qt libraries have their own applicable licenses and notices. The desktop extra installs these dependencies separately; OpenIRC's MIT license does not replace their terms.
- Python and its standard library: Python Software Foundation license and included third-party notices.

Development/test tools are separately installed dependencies. Consult their distributed metadata for their licenses. OpenIRC does not include proprietary artwork, implementation source, authentication services, or server libraries from historical IRCX products.

Protocol references are interoperability specifications, not source-code dependencies:

- https://www.rfc-editor.org/rfc/rfc1459
- https://modern.ircdocs.horse/
- https://datatracker.ietf.org/doc/html/draft-pfenning-irc-extensions-04
- https://ircv3.net/specs/extensions/capability-negotiation.html
- https://ircv3.net/specs/extensions/sasl-3.1
