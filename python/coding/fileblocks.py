"""File blocks: writing files in the format the model is already fluent in.

WHY THIS EXISTS
---------------
Across four real sessions with qwen2.5-coder:7b, `read_file`, `list_files` and
`find_files` were called correctly every time, and `write_file` / `edit_file`
were called ZERO times. Not once. The split is not "small models are unreliable
at tool use" — it is argument size:

    read_file   {"path": "login.css"}                        ~5 tokens
    write_file  {"path": ..., "content": "<the whole file,
                 one JSON string, every newline escaped>"}   ~1500 tokens

The first is trivial. The second means emitting a long escaped string inside the
tool-call channel without one formatting slip, against a lifetime of training
that says file contents go in a ```fence```. So the model does the easy correct
thing, hits the hard thing, falls back to what it is fluent at — prints the
file — and then reports the job done, because from where it sits the file IS
right there.

Four rescue attempts were built on top of that failure (recover prose calls,
force a structured call, force a write after a printed block, detect a claimed
change). Each caught the model falling. None stopped it falling.

THE FIX IS TO CHANGE THE PROTOCOL, NOT THE MODEL
------------------------------------------------
A fenced block whose info line carries the path is a format coder models emit
naturally and have seen constantly:

    ```css app/static/login.css
    ...the whole file...
    ```

Parsing that is deterministic Python. No JSON, no escaping, no grammar, no
timeout, no tool-call channel. This path is also robust to my diagnosis being
wrong: it does not use tool calling for writes AT ALL, so it survives whatever
is really wrong with tool calling on this model.

WHOLE FILES ONLY
----------------
The dangerous case is real and observed: asked to recolour an 82-line
stylesheet, the model printed the two rules it changed. Treating that as the
file would delete sixty lines, including a background image the user had
explicitly asked to keep. So a block that is much smaller than the file it names
is REFUSED and reported back, never staged — see is_excerpt(). The model is
perfectly capable of reprinting the whole file when told; it is not capable of
undoing a silent truncation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ```lang path/to/file.css     or     ```path/to/file.css
#
# The language token is optional and so is the path, so this matches every
# fence and the path is sorted out afterwards. Tilde fences (~~~) are legal
# CommonMark and cost nothing to accept.
_FENCE = re.compile(
    r"^[ \t]*(?P<fence>`{3,}|~{3,})[ \t]*(?P<info>[^\n]*)\n"
    r"(?P<body>.*?)"
    r"^[ \t]*(?P=fence)[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

# A bare word like `css`, `python`, `js` is a LANGUAGE, not a path. A path has a
# separator or an extension on something that looks like a filename.
_LOOKS_LIKE_PATH = re.compile(r"^[\w./\\@+-]+$")

# Fence info can arrive as `css path=app/x.css`, `css title="app/x.css"`, or
# just `css app/x.css`. All three appear in the wild; all three are accepted
# because rejecting a correct-but-differently-spelled answer trains nothing.
_KEYED = re.compile(r'\b(?:path|file|filename|title)\s*=\s*["\']?([^"\'\s]+)["\']?', re.I)

# How much smaller than the file it names a block may be before it is treated as
# an excerpt rather than a rewrite. Same number as the agent loop's guard, and
# for the same reason.
WHOLE_FILE_RATIO = 0.6


@dataclass
class FileBlock:
    """One fenced block that named a file."""

    path: str
    content: str

    def __post_init__(self) -> None:
        # A fenced block always ends with a newline before the closing fence;
        # that newline is part of the fence syntax, not of the file. Keeping it
        # would append a blank line to the file on every single round trip.
        if self.content.endswith("\n"):
            self.content = self.content[:-1]
        # Files end with a newline. Every editor and every POSIX tool assumes
        # it, and a diff whose only change is "\ No newline at end of file" is
        # noise the user has to read past.
        self.content += "\n"


def _path_from_info(info: str) -> str | None:
    """The file path in a fence info line, or None if it only named a language.

    Deliberately strict about what counts as a path: mistaking the word `css`
    for a filename would stage a file called "css" in the project root, and a
    wrong file created silently is worse than a block we decline to parse.
    """
    info = info.strip()
    if not info:
        return None

    keyed = _KEYED.search(info)
    if keyed:
        return _clean(keyed.group(1))

    parts = info.split()
    # First token is the language when there are two; with one token it could be
    # either, and only a path-shaped token counts.
    candidates = parts[1:] if len(parts) > 1 else parts
    for token in candidates:
        token = _clean(token)
        if not token or not _LOOKS_LIKE_PATH.match(token):
            continue
        if "/" in token or "\\" in token or ("." in token[1:] and not token.startswith(".")):
            return token
    return None


def _clean(token: str) -> str:
    """Trim the punctuation a model wraps a path in, and a leading './'.

    NOT `lstrip("./")`. That takes a CHARACTER SET, so it eats every leading dot
    and slash — turning '../../etc/passwd' into 'etc/passwd', which is not a
    rejected path but a DIFFERENT VALID one inside the workspace. safe_path
    would have refused the original; it has no reason to refuse the laundered
    version. Normalisation that quietly converts an invalid path into a valid
    one is worse than no normalisation at all.
    """
    if not token:
        return token
    token = token.strip().strip("`\"'<>()[],")
    while token.startswith("./"):
        token = token[2:]
    return token


def parse_file_blocks(text: str) -> list[FileBlock]:
    """Every fenced block in `text` that named a file, in order.

    Blocks without a path are ignored rather than guessed at: an illustrative
    snippet in an explanation is not a file, and the cost of a false positive
    here is writing to the user's project.
    """
    out: list[FileBlock] = []
    for m in _FENCE.finditer(text or ""):
        path = _path_from_info(m.group("info"))
        if path:
            out.append(FileBlock(path=path, content=m.group("body")))
    return out


def strip_file_blocks(text: str) -> str:
    """Remove the blocks that were staged, leaving the model's prose.

    WHY THE CONTENT COMES OFF THE SCREEN. Once a block is staged it is visible
    as a diff, which is a better rendering of the same information — line
    numbers, +/- markers, only what changed. Leaving the raw file in the
    transcript as well means the user scrolls past 82 lines of CSS to reach the
    next sentence, and it doubles the size of the history replayed to the model
    on the next turn, which on an 8k context is real money.
    """
    if not text:
        return text
    out, last = [], 0
    for m in _FENCE.finditer(text):
        if not _path_from_info(m.group("info")):
            continue
        out.append(text[last:m.start()])
        last = m.end()
    if not out:
        return text
    out.append(text[last:])
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def is_excerpt(block: FileBlock, current: str | None) -> bool:
    """Is this only PART of the file it claims to be?

    THE DESTRUCTIVE CASE, observed for real: asked to recolour login.css, the
    model printed the two rules it had changed. Staging that as the file would
    have deleted sixty lines of working CSS.

    A model showing you the part it changed is behaving correctly and normally.
    The mistake would be ours, in reading an excerpt as a replacement — so the
    check is on our side, and it fails towards "refuse and ask again" rather
    than towards "write and hope".
    """
    if current is None:
        return False        # nothing to lose: a new file is whole by definition
    return len(block.content) < len(current) * WHOLE_FILE_RATIO
