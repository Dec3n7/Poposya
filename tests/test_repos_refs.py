"""Разбор пользовательского ввода репозитория и парсинг ответов GitHub —
чистые функции без сети и БД."""

from datetime import UTC, datetime

from src.domain.repos.refs import parse_repo_ref
from src.infrastructure.github.client import _parse_dt, _parse_release, _parse_repo

# --- parse_repo_ref ---------------------------------------------------------


def test_plain_owner_name():
    assert parse_repo_ref("psf/requests") == ("psf", "requests")


def test_full_url_with_git_suffix():
    assert parse_repo_ref("https://github.com/psf/requests.git") == ("psf", "requests")


def test_url_with_extra_path_and_trailing_slash():
    assert parse_repo_ref("github.com/torvalds/linux/tree/master") == ("torvalds", "linux")
    assert parse_repo_ref("owner/name/") == ("owner", "name")


def test_www_and_scheme_variants():
    assert parse_repo_ref("http://www.github.com/a/b") == ("a", "b")


def test_name_with_dots_and_dashes():
    assert parse_repo_ref("owner/some.cool-repo_v2") == ("owner", "some.cool-repo_v2")


def test_rejects_garbage():
    assert parse_repo_ref("") is None
    assert parse_repo_ref("just-one-part") is None
    assert parse_repo_ref("owner/") is None
    assert parse_repo_ref("bad owner/name") is None
    assert parse_repo_ref("owner/..") is None


# --- парсинг GitHub-ответов -------------------------------------------------


def test_parse_dt_iso_z():
    dt = _parse_dt("2024-01-15T10:00:00Z")
    assert dt == datetime(2024, 1, 15, 10, 0, tzinfo=UTC)


def test_parse_dt_bad_values():
    assert _parse_dt("") is None
    assert _parse_dt("not-a-date") is None
    assert _parse_dt(None) is None
    assert _parse_dt(12345) is None


def test_parse_repo_uses_canonical_owner_login():
    info = _parse_repo(
        "PSF",
        "Requests",
        {
            "owner": {"login": "psf"},
            "name": "requests",
            "description": "HTTP for Humans",
            "stargazers_count": 52000,
            "language": "Python",
            "html_url": "https://github.com/psf/requests",
            "default_branch": "main",
        },
    )
    assert (info.owner, info.name) == ("psf", "requests")
    assert info.stars == 52000
    assert info.language == "Python"
    assert info.full_name == "psf/requests"


def test_parse_release_fields():
    release = _parse_release(
        {
            "id": 42,
            "tag_name": "v2.0.0",
            "name": "Version 2",
            "body": "notes",
            "html_url": "https://github.com/x/y/releases/tag/v2.0.0",
            "published_at": "2024-01-15T10:00:00Z",
            "author": {"login": "bob"},
            "prerelease": True,
            "draft": False,
        }
    )
    assert release is not None
    assert release.id == 42
    assert release.tag_name == "v2.0.0"
    assert release.author == "bob"
    assert release.prerelease is True
    assert release.published_at == datetime(2024, 1, 15, 10, 0, tzinfo=UTC)


def test_parse_release_none_without_timestamp():
    assert _parse_release({"id": 1, "tag_name": "v1"}) is None
