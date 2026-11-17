#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @pechex

import sys
import os
import json
import argparse
import urllib.request
import urllib.error
from datetime import datetime

CONFIG_DIR = "/home/lava/printer_data/config/cloudflared"
ACCESS_META_FILE = os.path.join(CONFIG_DIR, "access.json")

def cf_api(method, endpoint, token, data=None):
    url = f"https://api.cloudflare.com/client/v4{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "SnapmakerU1-Cloudflared/1.0"
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_content = e.read().decode("utf-8")
        try:
            err_json = json.loads(err_content)
            errors = err_json.get("errors", [])
            err_msg = "; ".join([err.get("message", "") for err in errors if err.get("message")])
            if not err_msg:
                err_msg = err_content
        except Exception:
            err_msg = err_content
        raise RuntimeError(f"Cloudflare API Error ({e.code}): {err_msg}")
    except Exception as e:
        raise RuntimeError(f"Connection error: {e}")

def setup_access(account_id, domain, email, token):
    print("============================================================")
    print("  CONFIGURING CLOUDFLARE ZERO TRUST ACCESS (OTP PIN)")
    print("============================================================")
    print(f"Domain:      {domain}")
    print(f"Admin Email: {email}")
    print(f"Account ID:  {account_id}")
    print("------------------------------------------------------------")

    # Step 1: Check or create One-Time PIN (OTP) Identity Provider
    print(">> Step 1/3: Checking Identity Providers...")
    idps_resp = cf_api("GET", f"/accounts/{account_id}/access/identity_providers", token)
    existing_idps = idps_resp.get("result", [])
    
    otp_idp = next((idp for idp in existing_idps if idp.get("type") == "onetimepin"), None)
    otp_idp_id = None

    if otp_idp:
        otp_idp_id = otp_idp.get("id")
        print(f"  [✓] One-Time PIN Identity Provider already active ({otp_idp.get('name')}).")
    else:
        print("  >> Activating One-Time PIN Identity Provider...")
        create_idp_payload = {
            "name": "One-Time PIN",
            "type": "onetimepin",
            "config": {}
        }
        res = cf_api("POST", f"/accounts/{account_id}/access/identity_providers", token, create_idp_payload)
        otp_idp_id = res.get("result", {}).get("id")
        print("  [✓] One-Time PIN Identity Provider created successfully.")

    # Step 2: Check or create Access Application
    print(f">> Step 2/3: Configuring Access Application for '{domain}'...")
    apps_resp = cf_api("GET", f"/accounts/{account_id}/access/apps", token)
    existing_apps = apps_resp.get("result", [])
    
    app = next((a for a in existing_apps if a.get("domain") == domain), None)
    app_id = None

    if app:
        app_id = app.get("id")
        print(f"  [✓] Found existing Access Application '{app.get('name')}' (ID: {app_id}).")
        # Preserve existing allowed_idps if configured
        allowed_idps = app.get("allowed_idps")
        if allowed_idps and isinstance(allowed_idps, list):
            if otp_idp_id and otp_idp_id not in allowed_idps:
                allowed_idps.append(otp_idp_id)
                cf_api("PUT", f"/accounts/{account_id}/access/apps/{app_id}", token, {"allowed_idps": allowed_idps})
                print("  [✓] Added One-Time PIN to allowed IdPs while preserving existing authentication methods.")
    else:
        print(f"  >> Creating new Access Application for {domain}...")
        create_app_payload = {
            "name": f"Snapmaker U1 ({domain})",
            "domain": domain,
            "type": "self_hosted",
            "session_duration": "24h",
            "auto_redirect_to_identity": False
        }
        res = cf_api("POST", f"/accounts/{account_id}/access/apps", token, create_app_payload)
        app_id = res.get("result", {}).get("id")
        print(f"  [✓] Created Access Application (ID: {app_id}).")

    # Step 3: Check or configure Access Policy (Preserving existing rules)
    print(">> Step 3/3: Configuring Access Policy...")
    policies_resp = cf_api("GET", f"/accounts/{account_id}/access/apps/{app_id}/policies", token)
    policies = policies_resp.get("result", [])
    
    allow_policy = next((p for p in policies if p.get("decision") == "allow"), None)

    if allow_policy:
        policy_id = allow_policy.get("id")
        include_rules = allow_policy.get("include", [])
        
        email_already_allowed = any(
            rule.get("email", {}).get("email", "").lower() == email.lower()
            for rule in include_rules
        )

        if email_already_allowed:
            print(f"  [✓] Email '{email}' is already authorized in policy '{allow_policy.get('name')}'.")
        else:
            print(f"  >> Adding '{email}' to existing policy '{allow_policy.get('name')}'...")
            include_rules.append({"email": {"email": email}})
            update_payload = {
                "name": allow_policy.get("name", "Allow Authorized Users"),
                "decision": "allow",
                "include": include_rules
            }
            if "require" in allow_policy:
                update_payload["require"] = allow_policy["require"]
            if "exclude" in allow_policy:
                update_payload["exclude"] = allow_policy["exclude"]
                
            cf_api("PUT", f"/accounts/{account_id}/access/apps/{app_id}/policies/{policy_id}", token, update_payload)
            print("  [✓] Email added successfully. All prior access rules & users were preserved.")
    else:
        print(f"  >> Creating default Allow Policy for '{email}'...")
        create_policy_payload = {
            "name": "Allow Authorized Users",
            "decision": "allow",
            "include": [
                {"email": {"email": email}}
            ]
        }
        cf_api("POST", f"/accounts/{account_id}/access/apps/{app_id}/policies", token, create_policy_payload)
        print("  [✓] Access Policy created successfully.")

    # Save local metadata for status display and diagnostics
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        meta = {
            "account_id": account_id,
            "domain": domain,
            "email": email,
            "app_id": app_id,
            "configured_at": datetime.utcnow().isoformat() + "Z"
        }
        with open(ACCESS_META_FILE, "w") as f:
            json.dump(meta, f, indent=2)
        try:
            import pwd
            lava_uid = pwd.getpwnam("lava").pw_uid
            lava_gid = pwd.getpwnam("lava").pw_gid
            os.chown(ACCESS_META_FILE, lava_uid, lava_gid)
        except Exception:
            pass
    except Exception as e:
        print(f"Notice: Could not write access metadata: {e}")

    print("============================================================")
    print("  🎉 ZERO TRUST ACCESS ACTIVE!")
    print("============================================================")
    print(f"  Public URL:  https://{domain}")
    print(f"  Auth Method: Cloudflare One-Time PIN (OTP)")
    print(f"  Recipient:   {email}")
    print("  When you access your printer, Cloudflare will ask for this")
    print("  email and send a 6-digit access code.")
    print("============================================================")

def status():
    if os.path.exists(ACCESS_META_FILE):
        try:
            with open(ACCESS_META_FILE, "r") as f:
                meta = json.load(f)
            print(f"Zero Trust Access: ENABLED (Email: {meta.get('email', 'N/A')}, Domain: {meta.get('domain', 'N/A')})")
            return 0
        except Exception:
            pass
    print("Zero Trust Access: NOT CONFIGURED")
    return 1

def main():
    parser = argparse.ArgumentParser(description="Cloudflare Zero Trust Access Setup Helper")
    subparsers = parser.add_subparsers(dest="command")

    setup_parser = subparsers.add_parser("setup", help="Configure Zero Trust Access for domain")
    setup_parser.add_argument("--account-id", required=True, help="Cloudflare Account ID")
    setup_parser.add_argument("--domain", required=True, help="Domain/Subdomain")
    setup_parser.add_argument("--email", required=True, help="Authorized Admin Email for OTP")
    setup_parser.add_argument("--token", required=True, help="Cloudflare API Token")

    subparsers.add_parser("status", help="Check local Zero Trust Access status")

    args = parser.parse_args()

    if args.command == "setup":
        try:
            setup_access(args.account_id, args.domain, args.email, args.token)
        except Exception as e:
            print(f"Error configuring Cloudflare Access: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "status":
        sys.exit(status())
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
