#!/bin/sh
# Refresh the translation template and catalogs (ROLO-0028). Needs GNU gettext.
#   1. Extract every _() / ngettext() string from rolodex.py into po/rolodex.pot.
#   2. Merge the new template into each po/<lang>.po.
#   3. Compile each into locale/<lang>/LC_MESSAGES/rolodex.mo, which rolodex.py loads.
# A new language: msginit --input=po/rolodex.pot --locale=<lang> --output=po/<lang>.po
set -eu
cd "$(dirname "$0")/.."

version=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' rolodex.py)
[ -n "$version" ] || { echo "update-translations: no __version__ in rolodex.py" >&2; exit 1; }

mkdir -p po
xgettext --language=Python --keyword=_ --keyword=ngettext:1,2 \
    --add-comments=Translators: --add-location=file --from-code=UTF-8 \
    --package-name=Rolodex --package-version="$version" \
    -o po/rolodex.pot rolodex.py

for po in po/*.po; do
    [ -e "$po" ] || continue  # no languages yet
    lang=$(basename "$po" .po)
    msgmerge --quiet --update --backup=none "$po" po/rolodex.pot
    mkdir -p "locale/$lang/LC_MESSAGES"
    msgfmt --check -o "locale/$lang/LC_MESSAGES/rolodex.mo" "$po"
done
