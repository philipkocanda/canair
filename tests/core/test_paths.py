"""Where the package, its shipped data and the repo are, in a checkout and in a wheel."""

from pathlib import Path

from canair.core import paths


def _checkout(tmp_path: Path) -> Path:
    package = tmp_path / "repo" / "src" / "canair"
    package.mkdir(parents=True)
    (tmp_path / "repo" / "pyproject.toml").write_text("")
    return package


def test_a_checkout_keeps_its_data_at_the_repo_root(tmp_path):
    package = _checkout(tmp_path)
    assert paths.resolve_roots(package) == (tmp_path / "repo", tmp_path / "repo")


def test_a_wheel_keeps_its_data_inside_the_package_and_has_no_repo(tmp_path):
    package = tmp_path / "site-packages" / "canair"
    (package / "_bundled").mkdir(parents=True)
    assert paths.resolve_roots(package) == (None, package / "_bundled")


def test_a_package_directory_named_src_without_a_pyproject_is_not_a_checkout(tmp_path):
    package = tmp_path / "x" / "src" / "canair"
    package.mkdir(parents=True)
    assert paths.resolve_roots(package) == (None, package.parent)


def test_site_packages_is_never_taken_for_the_repo(tmp_path):
    package = tmp_path / "lib" / "python3.12" / "site-packages" / "canair"
    package.mkdir(parents=True)
    repo, _data = paths.resolve_roots(package)
    assert repo is None


def test_this_checkout_resolves_the_shipped_data():
    assert paths.REPO_ROOT == paths.PACKAGE_DIR.parent.parent
    assert paths.DATA_ROOT == paths.REPO_ROOT
    assert (paths.BUNDLED_PROFILES_DIR / "hyundai-ioniq-ae-ev" / "profile.yaml").is_file()
    assert (paths.TEMPLATES_DIR / "profile.yaml.tmpl").is_file()
    assert paths.CONFIG_EXAMPLE_FILE.is_file()
    assert (paths.SCHEMA_DIR / "pids_schema.yaml").is_file()
