from collections.abc import Iterator

from argon2 import PasswordHasher

from proxy_api import admin


def test_hash_password_reads_secret_from_prompt_only(monkeypatch, capsys) -> None:
    password = "synthetic-password-for-test"
    responses: Iterator[str] = iter([password, password])
    monkeypatch.setattr(admin.getpass, "getpass", lambda _: next(responses))

    assert admin.main(["hash-password"]) == 0

    captured = capsys.readouterr()
    assert password not in captured.out
    assert password not in captured.err
    PasswordHasher().verify(captured.out.strip(), password)


def test_cli_rejects_password_argument_without_echoing_it(capsys) -> None:
    password = "must-not-be-echoed"

    assert admin.main(["hash-password", password]) == 2

    captured = capsys.readouterr()
    assert password not in captured.out
    assert password not in captured.err
    assert captured.err.strip() == "Usage: proxy-api-admin hash-password"
