"""Repository paths that may be used as code-review finding targets."""

from pathlib import PurePath, PurePosixPath

README_DOCUMENTATION_SUFFIXES = (".adoc", ".markdown", ".md", ".rst", ".txt")


def is_readme_path(file_path: str | PurePath) -> bool:
    """Return whether a repository path is a README documentation file."""

    normalized_path = str(file_path).replace("\\", "/")
    filename = PurePosixPath(normalized_path).name.casefold()
    return filename == "readme" or (
        filename.startswith("readme.")
        and filename.endswith(README_DOCUMENTATION_SUFFIXES)
    )


def is_review_target_path(file_path: str | PurePath) -> bool:
    """Return whether findings may target this repository path."""

    return not is_readme_path(file_path)
