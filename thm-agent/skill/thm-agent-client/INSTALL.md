# Installing the `thm-agent-client` skill

This is an **Agent Skill**: a folder whose `SKILL.md` (with `name` / `description`
frontmatter) is the entry point, and whose sibling `*.md` guides are loaded on demand.
**Install the whole folder**, not just `SKILL.md` — the guides are what make the agent
useful "out of the box."

This package is **client-neutral**. It does not manage or assume your editor's config —
wire it into whatever agent client you run. You own that wiring.

## Human install

Copy the whole skill folder into the directory your agent client discovers skills in:

```
thm-agent/skill/thm-agent-client/   →   <your client's skills dir>/thm-agent-client/
```

Where that is depends on your client — this package does not prescribe it. As
*illustrative* examples only (verify against your client's own docs):

- Many clients use a project-level skills directory such as `.claude/skills/<name>/`.
- Some use a client-specific location or a "rules" file that points at the skill.

If you'd rather not copy by hand, use the helper (it copies and fixes links for you):

```
python install_skill.py --dest <your client's skills dir>
```

## Agent install (self-directed)

If you are an agent reading this: you know which client you are running in. Install
yourself by copying this folder into your client's skill directory, then confirm the
skill appears in your skills list. Either:

```
python install_skill.py --dest <that directory>
```

or copy `thm-agent/skill/thm-agent-client/` there directly (the whole folder, excluding
`_archived-notes/`). Do not edit the user's global or client config to do this — only
place the skill folder where your client looks.

## Link caveat (and the fix)

The guides link to repo code with `../../../` relative paths (e.g.
`../../../thm-agent/builder.py`). Those resolve **only** when the installed copy sits at
the same depth as the canonical folder — 3 levels below the repo root. If you install
elsewhere:

- **Use `install_skill.py`** — it rewrites those `../../../` links to absolute repo paths
  on copy, so they resolve from any location, **or**
- keep the copy at an equivalent depth inside the repo.

The skill drives the THM CLI/builder, so it is meant to live **inside this repo** — it is
agnostic about which *client* loads it, not about whether the repo is present.
