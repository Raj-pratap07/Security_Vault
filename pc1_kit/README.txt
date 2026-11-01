FIREFLY PC1 KIT
===============
PC1 holds NO project code, keys or data. It only talks to the Firefly server on PC2.

ONCE
 1. Install Python 3.9+ (tick "Add python.exe to PATH").
 2. Open SETTINGS.bat in Notepad. Put PC2's address and the token:
        set PC2_URL=http://<PC2 IP>:8011        (the IP PC2's window prints)
        set TOKEN=<token from PC2's window>     (run PC2 with --show-token)
    No < > brackets and no quotes in the file.
 3. Double-click 0_SETUP_run_once.bat. You should see {"status":"ok"}.

PART 1  (PC2: python -m scripts.pc2_server --show-token)
 - 1_ALICE_authorized_user.bat   Alice stores a secret and reads it back.
 - 2_ATTACKER.bat                The attacker tries everything and gets nothing.

PART 2  (PC2: python -m scripts.pc2_server --share-demo --show-token)
 - 3_PART2_dealer_hands_out_shares.bat
 - 4_PART2_bob_has_1_share.bat       Bob cannot rebuild the secret.
 - 5_PART2_carol_has_2_shares.bat    Carol rebuilds it.
 - 6_PART2_cleanup.bat               Deletes the demo data from the clouds.
 Run part 2 steps 3 to 5 in order, from this same folder.

Do not run the attacker (part 1) while PC2 is in --share-demo mode.
If the symbols look broken, use Windows Terminal.
