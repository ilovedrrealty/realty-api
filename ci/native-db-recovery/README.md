The fixture runs with PostgreSQL 17.6 in a container with only the loopback interface. It creates its own TLS certificate, databases, rows, login, and recovery key. The production capture entry point and owner filesystem settings are excluded from this branch.

`overlay-manifest.json` binds the exact reviewed input hashes and records the source line ranges copied into the portable modules. `build-overlay.py` recreates that projection from the pinned capture helper, backup envelope format, and schema catalog SQL. It normalizes line endings, omits host-specific declarations and entry points, and replaces the single project identity literal in the catalog.

The fixture verifies the exact snapshot and its public-table coverage after a second connection commits a row. It exercises TLS and the authenticated password pipe, confirms that an empty table appears in the archive, reads the full archive, and restores the bytes recovered with a synthetic key. It checks restored counts, foreign key enforcement, and row visibility under the restored role and policy. Profile and schema drift must both stop the capture.

This fixture uses the updated 23-table contractual scope. It restores 46 synthetic private profiles and verifies the linked synthetic trade source evidence and both trade tables' privacy controls after restore.

The workflow prints aggregate results and cleanup markers. The POSIX fixture does not exercise the Windows terminal, local Windows permissions, the owner’s encryption key, Google Drive, or production recovery. Those results require separate evidence.
