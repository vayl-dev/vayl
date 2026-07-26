#!/usr/bin/env python3
"""
Vendor-side license tool (M3) — YOU run this, not your customers.

  keygen : generate your vendor Ed25519 keypair (do this ONCE). Keep the private seed SECRET; embed
           the public key in your product (license.DEFAULT_VENDOR_PUBKEY or VAYL_VENDOR_PUBKEY).
  mint   : sign a per-customer license blob with your private seed.

Examples
  python mint_license.py keygen
  python mint_license.py mint --seed <PRIVATE_SEED_HEX> \\
      --customer "Acme GmbH" --edition enterprise --seats 25 \\
      --features sso,kms,postgres --expires 2027-01-01

The minted blob goes to the customer, who sets VAYL_LICENSE=<blob> (or a file path) on their server.
"""
import argparse
import datetime
import secrets
import sys

from vayl.licensing import license as lic
from vayl.security import crypto


def cmd_keygen(_args):
    seed = secrets.token_bytes(32)
    pub = crypto.Signer(seed).public_key_hex()
    print("Vendor keypair generated. Do this ONCE and keep the seed offline & secret.\n")
    print("PRIVATE SEED (hex) — SECRET, never commit, store in your vault:")
    print(" ", seed.hex())
    print("\nPUBLIC KEY (hex) — safe to embed/ship (license.DEFAULT_VENDOR_PUBKEY or VAYL_VENDOR_PUBKEY):")
    print(" ", pub)


def cmd_mint(args):
    if not args.seed:
        print("error: --seed <PRIVATE_SEED_HEX> is required (from `keygen`).", file=sys.stderr)
        return 2
    claims = {
        "customer": args.customer,
        "edition": args.edition,
        "seats": int(args.seats),
        "features": [f for f in (args.features or "").split(",") if f],
        "issued": datetime.date.today().isoformat(),
        "expires": args.expires,
    }
    blob = lic.mint(args.seed, claims)
    print(f"# License for {args.customer} — {args.edition}, {args.seats} seats, "
          f"expires {args.expires}, features: {claims['features'] or '(none)'}")
    print(blob)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Vayl vendor license tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("keygen", help="generate the vendor keypair (once)")

    m = sub.add_parser("mint", help="sign a customer license")
    m.add_argument("--seed", required=True, help="vendor PRIVATE seed hex (from keygen)")
    m.add_argument("--customer", required=True)
    m.add_argument("--edition", default="enterprise", choices=["enterprise", "business"])
    m.add_argument("--seats", default="10")
    m.add_argument("--features", default="", help="comma-separated (e.g. sso,kms,postgres)")
    m.add_argument("--expires", required=True, help="ISO date YYYY-MM-DD")

    args = ap.parse_args()
    if args.cmd == "keygen":
        return cmd_keygen(args)
    if args.cmd == "mint":
        return cmd_mint(args)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
