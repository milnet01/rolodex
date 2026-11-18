# The name "Rolodex" and the app ID `com.rolodex.Contacts`

Genre: investigation
Status: decided — the user chose on 2026-11-18 to rename to Tinbox, with app ID
`io.github.milnet01.Tinbox`, in one release (ROLO-0106)

Answers ROLO-0104: is the name safe to keep, is the app ID, and what would a change cost?
ROLO-0103 publishes the app ID to users, so this decision comes first.

**Not legal advice.** Everything below comes from web searches and third-party trademark
sites, run 2026-11-18. The USPTO's own register (TSDR) refused automated reads, and no
European or South African register was searched.

## Findings

### The trademark

- ROLODEX is a live US registration. Third-party sites list registration 3313636 (serial
  78608946) in class 9 (electronics and software), status "Registered And Renewed", and
  registration 3403707 in class 16 (paper goods). The goods wording of 3313636 is
  unverified; one site gives "computer accessories" such as monitor stands.
- The owner of record is Berol Corporation. Newell bought Berol in 1995
  ([Wikipedia: Berol](https://en.wikipedia.org/wiki/Berol)), so the mark sits with Newell
  Brands.
- An older class 9 registration (1631413) covered an "electronic apparatus for use as a
  personal organizer for the storage of files of names, addresses, telephone numbers". It is
  cancelled ([Trademarkia](https://www.trademarkia.com/rolodex-74021471)). That shows the
  owner has claimed this exact kind of product before.
- No enforcement against a software project was found. Absence of a search hit proves
  nothing either way.

**Assessment.** The name is a famous mark, used here for the product category the mark
describes: a file of contact cards. That is the plainest case for a "likely to confuse"
complaint, and a famous mark is also protected outside its registered classes. The chance
of a complaint against a small free app is low. If one comes, the likely route is a
trademark takedown request to GitHub.

### The app ID

`com.rolodex.Contacts` is reverse-DNS for the domain `rolodex.com`, which this project does
not control. Flathub refuses such IDs (know-how topic `flathub-generative-ai-policy`), and
the reverse-DNS convention assumes the same everywhere. A GitHub-hosted app uses
`io.github.milnet01.<Name>`.

This is a problem whatever the name. It is also cheap to fix today:

- No Flatpak repository exists yet; ROLO-0103 builds it.
- A `rolodex.flatpak` bundle never updates itself (know-how topic `flatpak-sandboxed-testing`).
- Download counts on 2026-11-18 (`gh release view <tag> --json assets`): 2 for the 2.1.0
  bundle, 1 for the 2.2.0 bundle.

After ROLO-0103 ships, a new ID needs Flatpak's `--end-of-life-rebase`, and Discover has had bugs
with it ([KDE bug 467981](https://bugs.kde.org/show_bug.cgi?id=467981)).

## What a change touches

**An app ID change only:**

- `APP_ID` in `rolodex.py`.
- `packaging/`: the metainfo file (name and `<id>`), the Flatpak manifest, its `.desktop`
  and icon, and `packaging/flatpak/build.sh`.
- `StartupWMClass` in `rolodex.desktop`. A user who copied the old launcher keeps the old
  value until they copy it again.
- `tests/test_packaging.py`, `tests/test_regressions.py`, `docs/file-naming.md`, `CLAUDE.md`.
- A Flatpak install gets a new data folder, `~/.var/app/<new id>/`. A bundle user moves
  their vault with Backup, then Restore.

Source and PyInstaller builds keep their data where it is: `APP_DIR` is named `Rolodex`
and does not use the ID.

**A rename adds:**

- The window titles, every translatable string that names the app, and each `po/*.po`
  (`scripts/update-translations.sh`, then translate).
- README, metainfo `<name>`, screenshots, the website (the `ants-projects-hub-website`
  session).
- The GitHub repository name. GitHub redirects the old name until another repository takes
  it. The updater's redirect handler accepts a hop that stays on an allowed host; whether
  GitHub's API redirect for a renamed repository passes it is untested.
- The release asset names. An installed build looks for its asset by exact name
  (`PLATFORM_ASSETS`). A build that finds none offers no update and says nothing.

Things that can keep the old name, because users never see them: the `Rolodex` data folder,
`.rolodex.conf`, `contacts.vault`, the gettext domain, the CSV format tag and the
`rolodex.py` file name. Keeping them means no user data moves.

## Candidate names

Checked by web search only, 2026-11-18. No trademark register was searched.

| Name | What turned up | Verdict |
|---|---|---|
| Tinbox | A charity phone app ([AlternativeTo](https://alternativeto.net/software/tinbox/about)) | Looks clear |
| Cardcase | A news headline about Square's "Card Case" payment app; whether it still exists is unverified | Looks clear |
| Pigeonhole | Dovecot's Sieve plugin, a distro package ([GitHub](https://github.com/dovecot/pigeonhole)) | Minor clash |
| Coffer | Several small secrets-vault projects on GitHub | Crowded |
| Cardvault | Card-wallet phone apps of the same name | Crowded |
| Cardfile | Microsoft's old Windows card-file program | Same kind of complaint as Rolodex |
| Locket, Keepsake, Satchel | Registered class 9 marks held by others | Avoid |
| Tumbler | Xfce's thumbnail service, installed on many Linux desktops | Avoid |

Before adopting a finalist, search the USPTO, EUIPO and South African (CIPC) registers, GitHub,
Flathub and distro package lists for it.

### Tinbox, checked 2026-11-18 (ROLO-0105)

- Flathub search API (`POST https://flathub.org/api/v2/search`): no app of that name.
- Repology (`/api/v1/projects/?search=tinbox`) and openSUSE (`zypper search tinbox`): no
  package.
- GitHub (`gh search repos tinbox`): small unrelated projects. The largest is a translation
  tool, `strickvl/tinbox`.
- PyPI: the project name `tinbox` is taken. Rolodex does not publish to PyPI.
- Web search found no TINBOX word mark. It found brands using the word:
  [Tinbox Transfers](https://tinbox.app/), a Canadian money-transfer service with an app
  that says its trademarks are protected; Tinbox Energy Software Inc., a Canadian maker of
  oil-well monitoring software
  ([Justia](https://trademarks.justia.com/owners/tinbox-energy-software-inc-3168189)); and
  a Paris charity app.
- The USPTO, EUIPO, CIPC and WIPO registers were not searched: their search pages need a
  browser.

No password manager or vault uses the name. The nearest risk is Tinbox Transfers, a
finance app in a different trade.

## Recommendation

1. **Change the app ID now, whatever the name**, to `io.github.milnet01.<Name>`. Do it
   before ROLO-0103, while it costs a few bundle users one Backup and Restore.
2. **Rename at the same time.** Both changes then share one migration, while the user base
   is smallest. Keeping the name is a defensible choice: a complaint is unlikely. Its cost,
   if one came, is a forced rename on someone else's deadline. A GitHub takedown would
   also stop update checks for every installed copy.
3. **If renaming**, do it in one release:
   - Change the display name, the strings, the translations, the packaging and the README.
   - Keep the internal names listed under § What a change touches.
   - Rename the GitHub repository.
   - For a transition period, publish each binary and its `.sig` under both the old and the
     new asset name, so installed builds still find their update. Before the release, test
     the updater against the renamed repository's redirect.
   - Then point ROLO-0103 at the new ID.
