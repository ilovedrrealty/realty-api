# Isolated groups native proof

Preparation only until the first reviewed isolated operations ref runs successfully.
No production connections, credentials, real accounts, messages or Storage objects.

The exact product is `4e23cc8b643a4b07f4e14042e530e6271dad9c8f`
(`8093ea7aa9fd8f87a842e2527c715a8c482ac1fc`) and the exact preceding
runtime is `4d21cb3ae2b14475828ff8fbe710b22c34c3a53b`
(`1e3f72978515a58733188caa235cb6ee61190a26`). Exactly eight group paths
and one additive `c4e2a7319b60` revision differ. Existing map/contact source
and the preceding 92-table layout remain part of both sides.

The workflow reuses the accepted `4cf1fdd0ad45db3e1d476c251219a950b8c1a328`
dependency recipe, locked wheels, hosted-event guards and owned cleanup.
PostgreSQL is pinned to 17.6 and Python to its accepted image digest. Runtime
containers share only PostgreSQL's loopback network namespace; provider keys
are absent, workers disabled, source mounts read-only. Only three random,
comment-marked synthetic databases are used. All process output remains in
bounded private scratch logs except explicitly checked aggregate markers.

Finite acceptance:

1. Normal fresh bootstrap plus Alembic creates 99 tables. The seven group
   tables have RLS, zero policies and no PUBLIC/anon/authenticated/service_role
   grants. The group trigger function is invoker-only and has no such grants.
   Empty-only downgrade/reupgrade restores the identical full catalog.
2. Run exactly 24 source cases (4 input, 20 native), no skips or expected
   failures. These include group join/invite/link/photo/comment behavior,
   concurrent link use, audience withdrawal, former-member photo revocation,
   closed-group withdrawal/access revocation, persistent manager roster
   versions and isolation from existing private properties/projects.
3. Bootstrap the actual preceding 92-table runtime. Seed 82 protected synthetic
   owner contacts, 3 public contacts, 86 evidence rows, 46 SQL-NULL hidden
   managed profiles, 9 accepted-shaped jobs/worker, CMS version 7, retained
   listing/inquiry/booking/lead records and two typed floorplan bytea rows.
   No provider sends occur: only the actual immutable enqueue function runs.
4. Upgrade 92 to 99. Fresh and forward full raw catalogs must match. Every
   original column, row, sequence, ACL, policy, index, trigger and function
   remains exact; only the new standalone group function is allowed.
5. Use the actual service for one seven-action private group lifecycle:
   create, direct invite, accept, synthetic image upload, post, photo comment
   and expiring invite-link creation. No old-table values change.
6. Run the preceding runtime with only the exact additive migration copied
   into its otherwise byte-verified Git archive. Normal startup is a no-op;
   nine actual role/API reads and every retained row/catalog must survive.
   A populated group downgrade must fail atomically.
7. Create and restore a full native custom archive in the owned PostgreSQL
   container. Verify all 99 table rows, sequences and both bytea floorplans.
   All eight non-constraint catalog components stay raw-equal. The 529
   constraint identities/types and all server-rendered definitions stay
   equal. Preserve the accepted exact 13 legacy CHECK reparse variations;
   at most four explicitly named new group CHECKs may vary in raw redundant
   Boolean grouping. Native positive BETWEEN/expanded-AND and negative bound/
   AND-OR-precedence controls prevent blind text normalization. Fresh versus
   forward comparison is always raw-exact.
8. Verify terminal process exits and owned container/image/scratch cleanup.

The accepted catalog, row/sequence and deparser-control helper blocks are
copied verbatim from `54266a09109a1a012aa3de0ddf39c8487d4289b3`; their source
and individual hashes are recorded in `accepted-catalog-provenance.json`.
New posts require the captured group version: a private draft cannot acquire a new network audience after a concurrent settings change. Existing immutable private-post receipts still replay safely.

The source-only subset test inventory and archive constraint count are
explicit assertions, not claims of a new successful hosted run.

This proof does not exercise the upcoming groups frontend or restore real
Storage bytes. It does not rerun the unchanged 314/37 native or 68 browser
groups. Existing media custody and broader recovery limits remain separate.
