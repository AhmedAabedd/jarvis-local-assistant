"""Deterministic, user-configured path discovery for local file tools.

Resolution follows the same staged pattern used by desktop search interfaces:
exact paths first, then XDG known-folder aliases, case-insensitive traversal,
and finally a bounded fuzzy search.  Broad filesystem crawls are intentionally
avoided; searches stay inside the current directory, the user's home, and
discovered XDG folders.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal


ExpectedKind = Literal["file", "directory", "any"]
MAX_SEARCH_DEPTH = 5
MAX_SEARCH_ENTRIES = 12_000
MAX_CANDIDATES = 8

_XDG_ALIASES = {
    "DESKTOP": {"desktop"},
    "DOCUMENTS": {"documents", "document", "docs", "doc"},
    "DOWNLOAD": {"downloads", "download"},
    "MUSIC": {"music", "audio"},
    "PICTURES": {"pictures", "picture", "images", "image", "photos", "photo"},
    "PUBLICSHARE": {"public", "publicshare", "shared"},
    "TEMPLATES": {"templates", "template"},
    "VIDEOS": {"videos", "video", "movies", "movie"},
}
_QUERY_FILLER = {
    "a", "an", "at", "called", "directory", "file", "folder", "from", "in",
    "inside", "located", "my", "named", "of", "on", "the", "under",
}


@dataclass(frozen=True)
class Resolution:
    path: Path | None
    candidates: tuple[Path, ...] = ()
    message: str = ""


def _dedupe(paths: list[Path]) -> list[Path]:
    result = []
    seen = set()
    for path in paths:
        try:
            key = str(path.expanduser().resolve(strict=False))
        except OSError:
            key = str(path.expanduser().absolute())
        if key in seen:
            continue
        seen.add(key)
        result.append(Path(key))
    return result


def xdg_user_directories() -> dict[str, Path]:
    """Discover configured XDG user directories without assuming English paths."""
    home = Path.home()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser()
    config_path = config_home / "user-dirs.dirs"
    discovered: dict[str, Path] = {}
    try:
        lines = config_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    pattern = re.compile(r'^XDG_([A-Z]+)_DIR=["\'](.+)["\']$')
    for line in lines:
        match = pattern.match(line.strip())
        if not match:
            continue
        value = match.group(2).replace("$HOME", str(home))
        candidate = Path(os.path.expandvars(value)).expanduser()
        if candidate.is_dir():
            discovered[match.group(1)] = candidate

    # XDG is the standard. These are compatibility fallbacks only when an
    # installation has no user-dirs file, and only existing folders are used.
    fallback_names = {
        "DESKTOP": "Desktop", "DOCUMENTS": "Documents", "DOWNLOAD": "Downloads",
        "MUSIC": "Music", "PICTURES": "Pictures", "PUBLICSHARE": "Public",
        "TEMPLATES": "Templates", "VIDEOS": "Videos",
    }
    for key, name in fallback_names.items():
        candidate = home / name
        if key not in discovered and candidate.is_dir():
            discovered[key] = candidate
    return discovered


def known_directory_aliases() -> dict[str, Path]:
    aliases: dict[str, Path] = {
        "home": Path.home(),
        "~": Path.home(),
        "cwd": Path.cwd(),
        "current": Path.cwd(),
    }
    for key, path in xdg_user_directories().items():
        names = set(_XDG_ALIASES.get(key, set()))
        names.update({key.casefold(), path.name.casefold()})
        for name in names:
            aliases[name.casefold()] = path
    return aliases


def search_roots() -> list[Path]:
    return _dedupe([Path.cwd(), *xdg_user_directories().values(), Path.home()])


def _matches_kind(path: Path, expected: ExpectedKind) -> bool:
    if expected == "file":
        return path.is_file()
    if expected == "directory":
        return path.is_dir()
    return path.exists()


def _casefold_child(
    parent: Path, name: str, expected: ExpectedKind = "any"
) -> Path | None:
    try:
        matches = [
            child
            for child in parent.iterdir()
            if child.name.casefold() == name.casefold()
            and _matches_kind(child, expected)
        ]
    except OSError:
        return None
    return matches[0] if len(matches) == 1 else None


def _traverse_casefold(
    base: Path, parts: tuple[str, ...], expected: ExpectedKind
) -> Path | None:
    current = base
    for index, part in enumerate(parts):
        direct = current / part
        part_kind: ExpectedKind = expected if index == len(parts) - 1 else "directory"
        if _matches_kind(direct, part_kind):
            current = direct
            continue
        matched = _casefold_child(current, part, part_kind)
        if matched is None:
            return None
        current = matched
    return current if _matches_kind(current, expected) else None


def _query_terms(value: str, aliases: dict[str, Path]) -> tuple[list[str], Path | None]:
    words = re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE)
    base = next((aliases[word] for word in words if word in aliases), None)
    terms = [word for word in words if word not in aliases and word not in _QUERY_FILLER]
    return terms, base


def _score(path: Path, terms: list[str]) -> float:
    name = path.name.casefold()
    stem = path.stem.casefold()
    path_text = " ".join(part.casefold() for part in path.parts)
    query = " ".join(terms)
    if not terms:
        return 0.0
    if name == query or stem == query:
        return 1.0
    if all(term in path_text for term in terms):
        return 0.94
    path_components = [part.casefold() for part in path.parts]
    word_components = [
        word
        for component in path_components
        for word in re.findall(r"[^\W_]+", component, flags=re.UNICODE)
    ]
    components = [name, stem, *path_components, *word_components]
    ratios = [
        max(SequenceMatcher(None, term, component).ratio() for component in components)
        for term in terms
    ]
    return sum(ratios) / len(ratios)


def _bounded_candidates(
    roots: list[Path],
    terms: list[str],
    expected: ExpectedKind,
    *,
    include_hidden: bool = False,
) -> list[tuple[float, Path]]:
    ranked = []
    unique_roots = _dedupe(roots)
    # Give every root a fair share of the bound. A very large current working
    # tree must not consume the whole budget before Documents is inspected.
    per_root_limit = max(1_000, MAX_SEARCH_ENTRIES // max(len(unique_roots), 1))
    for root in unique_roots:
        if not root.is_dir():
            continue
        visited = 0
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            try:
                depth = len(current_path.relative_to(root).parts)
            except ValueError:
                continue
            if depth >= MAX_SEARCH_DEPTH:
                directories[:] = []
            if not include_hidden:
                directories[:] = [name for name in directories if not name.startswith(".")]
                files = [name for name in files if not name.startswith(".")]
            names = (
                directories if expected == "directory"
                else files if expected == "file"
                else [*directories, *files]
            )
            for name in names:
                visited += 1
                if visited > per_root_limit:
                    break
                candidate = current_path / name
                score = _score(candidate, terms)
                if score >= 0.58:
                    ranked.append((score, candidate))
            if visited > per_root_limit:
                break
    unique: dict[str, tuple[float, Path]] = {}
    for score, path in ranked:
        key = str(path.resolve(strict=False))
        if key not in unique or score > unique[key][0]:
            unique[key] = (score, path)
    ordered = sorted(
        unique.values(),
        key=lambda item: (-item[0], len(item[1].parts), str(item[1]).casefold()),
    )
    return ordered[:MAX_CANDIDATES]


def _bounded_basename_matches(
    roots: list[Path],
    basename: str,
    expected: ExpectedKind,
    *,
    case_sensitive: bool,
) -> list[Path]:
    """Find an exact leaf name without trusting the supplied parent path."""
    unique_roots = _dedupe(roots)
    per_root_limit = max(1_000, MAX_SEARCH_ENTRIES // max(len(unique_roots), 1))
    target = basename if case_sensitive else basename.casefold()
    matches: list[Path] = []
    for root in unique_roots:
        if not root.is_dir():
            continue
        visited = 0
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            try:
                depth = len(current_path.relative_to(root).parts)
            except ValueError:
                continue
            if depth >= MAX_SEARCH_DEPTH:
                directories[:] = []
            directories[:] = [name for name in directories if not name.startswith(".")]
            files = [name for name in files if not name.startswith(".")]
            names = (
                directories if expected == "directory"
                else files if expected == "file"
                else [*directories, *files]
            )
            for name in names:
                visited += 1
                if visited > per_root_limit:
                    break
                comparable = name if case_sensitive else name.casefold()
                if comparable == target:
                    matches.append(current_path / name)
            if visited > per_root_limit:
                break
    return sorted(
        _dedupe(matches),
        key=lambda path: (len(path.parts), str(path).casefold()),
    )[:MAX_CANDIDATES]


def _basename_resolution(paths: list[Path], basename: str) -> Resolution | None:
    if not paths:
        return None
    if len(paths) == 1:
        return Resolution(paths[0], tuple(paths))
    rendered = "\n".join(f"  {path}" for path in paths)
    return Resolution(
        None,
        tuple(paths),
        f"The filename {basename!r} exists in multiple locations:\n{rendered}",
    )


def find_matches(
    directory: Path,
    query: str,
    expected: ExpectedKind = "any",
    *,
    include_hidden: bool = False,
) -> list[Path]:
    """Return bounded fuzzy matches below one already-resolved directory."""
    aliases = known_directory_aliases()
    terms, _hinted_root = _query_terms(query, aliases)
    if not terms:
        terms = [str(query or "").casefold().strip()]
    return [
        path
        for _score_value, path in _bounded_candidates(
            [directory], terms, expected, include_hidden=include_hidden
        )
    ]


def resolve_existing(value: str, expected: ExpectedKind = "any") -> Resolution:
    """Resolve a path or natural location hint without crawling the whole disk."""
    raw = str(value or "").strip()
    if not raw:
        return Resolution(None, message="No path was provided.")
    expanded = Path(raw).expanduser()
    exact_candidates = (
        [expanded]
        if expanded.is_absolute()
        else [Path.cwd() / expanded, Path.home() / expanded]
    )
    for candidate in _dedupe(exact_candidates):
        if _matches_kind(candidate, expected):
            return Resolution(candidate)

    aliases = known_directory_aliases()
    parts = expanded.parts
    if parts:
        alias_root = aliases.get(parts[0].casefold())
        if alias_root is not None:
            traversed = _traverse_casefold(alias_root, parts[1:], expected)
            if traversed is not None:
                return Resolution(traversed)

    for root in search_roots():
        traversed = _traverse_casefold(root, parts, expected)
        if traversed is not None:
            return Resolution(traversed)

    # The parent supplied by a caller may be wrong while the leaf filename is
    # exact. Recover that common case before fuzzy scoring can introduce nearby
    # dates, typos, or similarly named files.
    basename = expanded.name
    if basename and basename not in {".", ".."}:
        exact_name = _basename_resolution(
            _bounded_basename_matches(
                search_roots(), basename, expected, case_sensitive=True
            ),
            basename,
        )
        if exact_name is not None:
            return exact_name
        casefold_name = _basename_resolution(
            _bounded_basename_matches(
                search_roots(), basename, expected, case_sensitive=False
            ),
            basename,
        )
        if casefold_name is not None:
            return casefold_name

    terms, hinted_root = _query_terms(raw, aliases)
    if not terms and parts:
        terms = [parts[-1].casefold()]
    roots = [hinted_root] if hinted_root is not None else search_roots()
    ranked = _bounded_candidates(roots, terms, expected)
    if not ranked:
        return Resolution(None, message=f"No matching {expected} found for: {raw}")
    best_score, best_path = ranked[0]
    candidates = tuple(path for _score_value, path in ranked)
    if len(ranked) == 1 and best_score >= 0.72:
        return Resolution(best_path, candidates)
    rendered = "\n".join(f"  {path}" for path in candidates)
    return Resolution(
        None,
        candidates,
        f"The location is ambiguous. Matching candidates:\n{rendered}",
    )


def resolve_output(value: str) -> Resolution:
    """Resolve known/fuzzy parent directories while allowing a new leaf file."""
    raw = str(value or "").strip()
    if not raw:
        return Resolution(None, message="No output path was provided.")
    expanded = Path(raw).expanduser()
    if expanded.is_absolute():
        return Resolution(expanded)
    if expanded.parent == Path("."):
        return Resolution(Path.cwd() / expanded.name)

    aliases = known_directory_aliases()
    parts = expanded.parts
    alias_root = aliases.get(parts[0].casefold()) if parts else None
    if alias_root is not None:
        parent_parts = parts[1:-1]
        parent = alias_root
        for index, part in enumerate(parent_parts):
            exact = _traverse_casefold(parent, (part,), "directory")
            if exact is not None:
                parent = exact
                continue
            fuzzy = find_matches(parent, part, "directory")
            if len(fuzzy) == 1:
                parent = fuzzy[0]
                continue
            if len(fuzzy) > 1:
                rendered = "\n".join(f"  {candidate}" for candidate in fuzzy)
                return Resolution(
                    None,
                    tuple(fuzzy),
                    f"The output folder is ambiguous. Matching candidates:\n{rendered}",
                )
            parent = parent.joinpath(*parent_parts[index:])
            break
        return Resolution(parent / parts[-1])

    parent_resolution = resolve_existing(str(expanded.parent), "directory")
    if parent_resolution.path is not None:
        return Resolution(parent_resolution.path / expanded.name, parent_resolution.candidates)
    # Preserve explicit relative paths as new directory trees when no existing
    # or semantically named parent can be resolved.
    if not parent_resolution.candidates:
        return Resolution(Path.cwd() / expanded)
    return Resolution(None, parent_resolution.candidates, parent_resolution.message)
