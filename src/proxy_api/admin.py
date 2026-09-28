from __future__ import annotations

import getpass
import sys
from collections.abc import Sequence

from proxy_api.auth.service import hash_password


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments != ["hash-password"]:
        print("Usage: proxy-api-admin hash-password", file=sys.stderr)
        return 2

    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        print("Passwords do not match", file=sys.stderr)
        return 1
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(password_hash)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
