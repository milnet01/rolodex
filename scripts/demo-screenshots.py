#!/usr/bin/env python3
"""Open Rolodex on a throwaway vault of made-up entries, for screenshots.

Never touches contacts.vault or .rolodex.conf: the vault and the config both live in a fresh
temporary directory that is removed on exit. Run it under a private display, e.g.

    demoreel shot -o docs/screenshots/detail.png -s 1280x800 -- \
        python3 scripts/demo-screenshots.py detail

Scenes: detail (an entry with a live 2FA code), health, edit, unlock, preferences.
Optional second and third arguments pick the theme and the accent (ROLO-0015), e.g.
`detail light teal`.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import rolodex
from rolodex import GLib

DEMO_PASSWORD = "correct horse battery staple"
# A made-up base32 seed. It belongs to no account anywhere.
DEMO_TOTP_SEED = "JBSWY3DPEHPK3PXPJBSWY3DP"


def f(label, value, sensitive=None):
    return {"label": label, "value": value,
            "sensitive": rolodex.is_sensitive_label(label) if sensitive is None else sensitive}


def populate(vault):
    vault["categories"] = ["Email", "Games", "Shopping", "Work"]
    add = rolodex.add_entry
    add(vault, "Fastmail", [
        f("Email", "sam.rivers@example.com"),
        f("Password", "tulip-Harbour-42-quartz"),
        f("2FA seed", DEMO_TOTP_SEED, True),
        f("URL", "https://www.example.com/login"),
    ], notes="Recovery codes are in the fire safe.", category="Email")
    add(vault, "Old webmail", [
        f("Username", "sriver"),
        f("Password", "password1"),
    ], category="Email")
    add(vault, "Steam", [
        f("Username", "samrivers_plays"),
        f("Password", "password1"),
        f("Account ID", "7656119800000000"),
    ], category="Games")
    add(vault, "GOG", [
        f("Email", "sam.rivers@example.com"),
        f("Password", "Lantern!Moss!Cobalt!7"),
    ], category="Games")
    add(vault, "Bookshop", [
        f("Email", "sam.rivers@example.com"),
        f("Password", "shelf2024"),
        f("Card expiry", "2027-08"),
    ], category="Shopping")
    add(vault, "Office VPN", [
        f("Username", "s.rivers"),
        f("Password", "Granite_Otter_Violet_19"),
        f("API token", "tok_demo_4f9a1c7e2b"),
        f("Server", "https://vpn.example.org"),
    ], notes="Ask IT before changing the MFA device.", category="Work")


def main():
    scene = sys.argv[1] if len(sys.argv) > 1 else "detail"
    theme = sys.argv[2] if len(sys.argv) > 2 else rolodex.DEFAULT_THEME
    accent = sys.argv[3] if len(sys.argv) > 3 else rolodex.DEFAULT_ACCENT
    tmp = tempfile.mkdtemp(prefix="rolodex-demo-")
    rolodex.CONFIG_FILE = os.path.join(tmp, ".rolodex.conf")
    rolodex.save_config({"window_width": 1280, "window_height": 800,
                         rolodex.UPDATE_ENABLED_KEY: False,
                         rolodex.THEME_KEY: theme, rolodex.ACCENT_KEY: accent})
    vault_path = os.path.join(tmp, "demo.vault")
    vault, salt, key = rolodex.create_vault_with_key(DEMO_PASSWORD, vault_path)
    populate(vault)
    rolodex.save_vault_with_key(vault, key, salt, vault_path)

    class DemoApp(rolodex.RolodexApp):
        # Replaces the normal activate, which shows the unlock screen for the real vault path.
        def do_activate(self):
            if scene == "unlock":
                rolodex.UnlockDialog(self, vault_path, False).present()
                return
            self.open_main(vault, salt, DEMO_PASSWORD, vault_path, key)
            win = self.props.active_window
            first = next(i for i, e in vault["entries"].items() if e["name"] == "Fastmail")
            win._refresh_list(select_id=first)
            if scene == "health":
                GLib.timeout_add(500, lambda: rolodex.PasswordHealthDialog(win).present(win))
            elif scene == "edit":
                GLib.timeout_add(500, lambda: win._on_edit(None, first))
            elif scene == "preferences":
                GLib.timeout_add(500, lambda: win._on_preferences())

    app = DemoApp()
    app.vault_path = vault_path
    try:
        sys.exit(app.run([sys.argv[0]]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
