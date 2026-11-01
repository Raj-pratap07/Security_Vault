FROZEN DEMO FILES
=================
Do not edit these. They are the versions that were verified working.

  demo_one_pc.py                 One-PC demo shown in the VS Code terminal (1 share fails, 2 and 3 rebuild).
                                 Run from the project root as:  python -m scripts.demo_one_pc --live
  two_pc_demo.py                 Alice (authorized) and the attacker, for PC1.
  share_holders_original.py      Bob and Carol, original version (deal, open bob, open carol, cleanup).
  share_holders_fetch_version.py Later version where Bob/Carol ask PC2 to FETCH the secret
                                 (one-command run plus fetch bob/carol/mallory). Needs the server
                                 endpoint POST /demo/secrets/{id}/fetch, which is still in the app.

The live copies in scripts/ are marked read-only and match these byte for byte.
MANIFEST.sha256 lists a checksum for every frozen file; verify with:
  python scripts/frozen/verify_frozen.py
To unfreeze a live copy:  attrib -R scripts\share_holders.py
