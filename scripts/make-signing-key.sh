#!/usr/bin/env bash
# Create the one signing key every VSCodroid build is signed with, and store it in
# the repository's GitHub secrets. Run it ONCE, by hand, from a machine with a JDK:
#
#     bash scripts/make-signing-key.sh
#
# A new APK can update an installed app only when both are signed with the same key.
# Lose this key, or make a second one, and the next update fails with "App not
# installed" until the app is uninstalled, which deletes all of its data. So the
# script refuses to overwrite a key that exists, and tells you what to back up.
set -euo pipefail

KEYSTORE="${1:-$HOME/vscodroid-release.jks}"
ALIAS="vscodroid"

for tool in keytool openssl base64; do
    command -v "$tool" >/dev/null 2>&1 || { echo "ERROR: $tool is not installed (a JDK provides keytool)."; exit 1; }
done
if [ -e "$KEYSTORE" ]; then
    echo "ERROR: $KEYSTORE already exists. Not overwriting it: replacing the key would stop every"
    echo "       installed copy of the app from being updated. Use that key, or move it away on purpose."
    exit 1
fi

PASSWORD="$(openssl rand -base64 36 | tr -dc 'A-Za-z0-9' | cut -c1-24)"
keytool -genkeypair -noprompt -storetype JKS -keystore "$KEYSTORE" -alias "$ALIAS" \
    -keyalg RSA -keysize 4096 -validity 36500 \
    -storepass "$PASSWORD" -keypass "$PASSWORD" \
    -dname "CN=VSCodroid, OU=Mobile, O=VSCodroid, C=IN"

BASE64_FILE="$KEYSTORE.base64.txt"
SECRETS_FILE="$KEYSTORE.secrets.txt"
base64 -w0 "$KEYSTORE" > "$BASE64_FILE" 2>/dev/null || base64 "$KEYSTORE" | tr -d '\n' > "$BASE64_FILE"
{
    echo "KEYSTORE_PASSWORD=$PASSWORD"
    echo "KEY_ALIAS=$ALIAS"
    echo "KEY_PASSWORD=$PASSWORD"
    echo "KEYSTORE_BASE64 is the whole content of $BASE64_FILE"
} > "$SECRETS_FILE"
chmod 600 "$KEYSTORE" "$BASE64_FILE" "$SECRETS_FILE"

set_secrets() {
    gh secret set KEYSTORE_BASE64 < "$BASE64_FILE" &&
    printf '%s' "$PASSWORD" | gh secret set KEYSTORE_PASSWORD &&
    printf '%s' "$ALIAS" | gh secret set KEY_ALIAS &&
    printf '%s' "$PASSWORD" | gh secret set KEY_PASSWORD
}

echo
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1 && set_secrets; then
    echo "The four repository secrets are set: KEYSTORE_BASE64, KEYSTORE_PASSWORD, KEY_ALIAS, KEY_PASSWORD."
else
    echo "The secrets could not be set automatically (no gh CLI, not logged in, or no permission)."
    echo "Add these four in GitHub: repository > Settings > Secrets and variables > Actions > New repository secret:"
    echo "  KEYSTORE_BASE64   = the whole content of $BASE64_FILE"
    echo "  KEYSTORE_PASSWORD, KEY_ALIAS, KEY_PASSWORD = the values in $SECRETS_FILE"
fi
echo
echo "BACK THESE UP somewhere safe and private (not in the repository):"
echo "  $KEYSTORE"
echo "  $SECRETS_FILE"
echo "Without them nobody can ever update an installed app again without uninstalling it."
