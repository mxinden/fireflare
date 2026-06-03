"""Throwaway: confirm we can create + verify an FxA account via PyFxA."""
import secrets
from fxa.core import Client
from fxa.tests.utils import TestEmailAccount

local = f"fireflare-{secrets.token_hex(4)}"
acct = TestEmailAccount(email=f"{local}@restmail.net")
print(f"email: {acct.email}")

password = "MxFireflareTest!" + secrets.token_hex(4)
print(f"password: {password}")

client = Client("https://api.accounts.firefox.com")
print("creating account...")
session = client.create_account(acct.email, password)
print(f"  uid={session.uid}")
print(f"  verified={session.verified}")

import time
deadline = time.monotonic() + 60
print("polling restmail for verification code...")
while time.monotonic() < deadline:
    acct.fetch()
    codes = [m["headers"].get("x-verify-code") for m in acct.messages if "x-verify-code" in m["headers"]]
    if codes:
        print(f"  got code: {codes[0]}")
        session.verify_email_code(codes[0])
        print("  verified!")
        break
    time.sleep(2)
else:
    raise SystemExit("no verification code arrived in 60s")

session.fetch_keys(password)
print("session.fetch_keys ok")

print("\nDONE — credentials:")
print(f"  email:    {acct.email}")
print(f"  password: {password}")
print(f"  uid:      {session.uid}")
